"""Etapa 8: grafo de dominio (grafo de co-compra, PageRank y comunidades).

Construcción y análisis del grafo de co-compra para el escenario B (comercio electrónico):
- Grafo de co-compra: grafo no dirigido y ponderado donde cada nodo es un producto (`parent_asin`)
  y cada arista conecta dos productos adquiridos dentro de una misma orden legítima de compra.
  El peso de la arista w_{uv} es el número de órdenes distintas que contuvieron a ambos productos.
  Se excluyen rigurosamente las órdenes bloqueadas por el detector de fraude de la etapa 6
  (RUTA_BLOQUEADAS) para evitar que compras anómalas o fraudulentas contaminen la topología.
- Representación en memoria: formato CSR (Compressed Sparse Row) simétrico sobre NumPy
  (indptr, indices, data), logrando complejidad espacial O(|V| + |E|) y operaciones vectorizadas.
- Algoritmos de influencia (ranking de centralidad):
  - PageRank (Brin y Page, 1998): cálculo iterativo del vector estacionario (Power Iteration)
    con factor de amortiguamiento alpha = 0.85 y redistribución estricta para nodos sumidero
    (dangling nodes). Complejidad temporal O(|V| + |E|) por iteración y O(k · (|V| + |E|)) en
    total, con k = O(log(1/tol) / log(1/alpha)) iteraciones.
    En un grafo no dirigido sin teletransporte el estado estacionario es exactamente k_i / 2m,
    por lo que PageRank aquí aporta poco más que el grado ponderado (Spearman medido: 0.956).
  - Grado ponderado (línea base): suma local de pesos de aristas incidentes, complejidad O(|V| + |E|).
- Algoritmos de comunidades (agrupaciones de co-compra / cross-selling):
  - Louvain (Blondel, Guillaume, Lambiotte y Lefebvre, 2008): optimización voraz multinivel de la
    modularidad de Newman-Girvan con resolución gamma (Reichardt y Bornholdt, 2006).
  - Label Propagation (LPA; Raghavan, Albert y Kumara, 2007, línea base): propagación de etiquetas
    por votación ponderada local en tiempo casi lineal O(|E|).
  - Garantía de conexión (auditoría 2026-10-27): Louvain y LPA pueden devolver comunidades
    internamente desconectadas (Traag, Waltman y van Eck, 2019). Ambas particiones se post-procesan
    con dividir_comunidades_desconectadas en O(|V| + |E|); dividir nunca reduce Q.
- Modularidad exacta Q: evaluación cuantitativa de las particiones encontradas.
- ejecutar_etapa: procesa el stream de eventos de compras, genera el grafo, calcula PageRank y
  comunidades, y escribe los artefactos para el servicio y visualización de la etapa 9.

notebooks/etapa8_grafo_dominio.ipynb importa este código, ejecuta benchmarks y lo documenta.
"""

import json
import math
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse
from scipy.sparse.csgraph import connected_components

from .. import config
from ..etapa_3.storage import RUTA_EVENTOS_ETAPA2, TablaHash, leer_eventos
from ..etapa_6.probabilistic import RUTA_BLOQUEADAS

# Salidas de la etapa en data/interim/<CATEGORIA>/etapa_8/
CARPETA_ETAPA8 = config.CARPETA_INTERIM / "etapa_8"
RUTA_GRAFO = CARPETA_ETAPA8 / "grafo_cocompra.npz"              # grafo CSR serializado
RUTA_PAGERANK = CARPETA_ETAPA8 / "pagerank.parquet"            # scores y rankings de influencia
RUTA_COMUNIDADES = CARPETA_ETAPA8 / "comunidades.parquet"      # particiones y modularidad
RUTA_METRICAS = CARPETA_ETAPA8 / "metricas.json"              # resumen experimental y comparativo

# ----- Parámetros por defecto de la etapa -----------------------------------------
MIN_PESO = 1                  # peso mínimo de co-ocurrencia para incluir la arista
ALPHA = 0.85                  # factor de amortiguamiento estándar de PageRank
TOL_PAGERANK = 1e-6           # cota de convergencia en norma L1 para PageRank
MAX_ITER_PAGERANK = 100       # iteraciones máximas de Power Iteration
RESOLUCION_LOUVAIN = 1.0      # parámetro gamma de modularidad en Louvain
MAX_NIVELES_LOUVAIN = 10      # niveles máximos de contracción de super-nodos en Louvain
TOL_LOUVAIN = 1e-4            # ganancia mínima de modularidad por ciclo en Fase 1
MAX_ITER_LPA = 30             # iteraciones máximas de Label Propagation


# ----- Carga de órdenes bloqueadas ------------------------------------------------

def ordenes_bloqueadas(ruta_bloqueadas=RUTA_BLOQUEADAS):
    """Devuelve el conjunto de order_ids bloqueados por la etapa 6 por fraude."""
    if not Path(ruta_bloqueadas).exists():
        return set()
    with open(ruta_bloqueadas, encoding="utf-8") as f:
        return set(json.load(f))


# ----- Estructura de Datos del Grafo de Co-compra (CSR) ---------------------------

class GrafoCoCompra:
    """Grafo no dirigido y ponderado en formato Compressed Sparse Row (CSR).

    Nodos: 0 a N - 1, mapeados a identificadores parent_asin de Amazon.
    Aristas: simétricas (si u está conectado con v con peso w, v está conectado con u con peso w).
    indptr: arreglo de tamaño N + 1 con el inicio de los vecinos de cada nodo en indices.
    indices: arreglo de vecinos de tamaño 2 · |E|.
    data: arreglo de pesos (float64) de tamaño 2 · |E|.
    """

    def __init__(self, asins, indptr, indices, data):
        self.asins = list(asins)
        self.indptr = np.asarray(indptr, dtype=np.int64)
        self.indices = np.asarray(indices, dtype=np.int32)
        self.data = np.asarray(data, dtype=np.float64)
        self.n = len(self.asins)

        if len(self.indptr) != self.n + 1:
            raise ValueError(f"indptr debe tener tamaño {self.n + 1}")
        if len(self.indices) != len(self.data):
            raise ValueError("indices y data deben tener la misma longitud")

        # Diccionario / TablaHash para búsqueda O(1) de asin -> índice int
        self.posicion = TablaHash(max(self.n, 16))
        for i, asin in enumerate(self.asins):
            self.posicion.insertar(asin, i)

        # Precalcular grados ponderados k_i = sum_j w_{ij}
        if self.n > 0 and len(self.data) > 0:
            filas = np.repeat(np.arange(self.n), np.diff(self.indptr))
            self._grados = np.bincount(filas, weights=self.data, minlength=self.n).astype(np.float64)
        else:
            self._grados = np.zeros(self.n, dtype=np.float64)
        self._peso_total = float(self._grados.sum() / 2.0)

    def __len__(self):
        return self.n

    @property
    def n_nodos(self):
        return self.n

    @property
    def n_aristas(self):
        """Número de aristas no dirigidas únicas (excluyendo auto-aristas o contadas una vez)."""
        auto_aristas = int((self.indices == np.repeat(np.arange(self.n), np.diff(self.indptr))).sum()) if self.n > 0 else 0
        aristas_simetricas = (len(self.indices) - auto_aristas) // 2
        return aristas_simetricas + auto_aristas

    @property
    def peso_total(self):
        """Suma de pesos de todas las aristas no dirigidas (m = 0.5 * sum k_i)."""
        return self._peso_total

    @property
    def densidad(self):
        """Densidad del grafo: 2 * |E| / (V * (V - 1))."""
        if self.n <= 1:
            return 0.0
        return (2.0 * self.n_aristas) / (self.n * (self.n - 1))

    @property
    def m(self):
        """Alias de n_aristas."""
        return self.n_aristas

    @property
    def grados(self):
        """Grados no ponderados de cada nodo (número de vecinos)."""
        return np.diff(self.indptr)

    def grado_ponderado(self, i):
        """Suma de pesos de las aristas incidentes en el nodo i."""
        return float(self._grados[i])

    def grados_ponderados(self):
        """Arreglo con los grados ponderados de todos los nodos."""
        return self._grados.copy()

    def vecinos(self, i):
        """Devuelve (vecinos_indices, pesos) para el nodo i."""
        inicio, fin = self.indptr[i], self.indptr[i + 1]
        return self.indices[inicio:fin], self.data[inicio:fin]

    def aristas(self):
        """Generador de ternas (u, v, peso) con u < v."""
        for u in range(self.n):
            vecs, ws = self.vecinos(u)
            for v, w in zip(vecs.tolist(), ws.tolist()):
                if u < v:
                    yield u, v, w

    def subgrafo(self, asins_muestra):
        """Construye un nuevo GrafoCoCompra inducido por un subconjunto de asins."""
        asins_set = set(asins_muestra)
        asins_filtrados = [a for a in self.asins if a in asins_set]
        nuevo_idx = {a: i for i, a in enumerate(asins_filtrados)}

        adj_temp = [[] for _ in range(len(asins_filtrados))]
        pesos_temp = [[] for _ in range(len(asins_filtrados))]

        for u_nuevo, asin_u in enumerate(asins_filtrados):
            u_orig = self.posicion.obtener(asin_u)
            vecs, ws = self.vecinos(u_orig)
            for v_orig, w in zip(vecs.tolist(), ws.tolist()):
                asin_v = self.asins[v_orig]
                if asin_v in nuevo_idx:
                    v_nuevo = nuevo_idx[asin_v]
                    adj_temp[u_nuevo].append(v_nuevo)
                    pesos_temp[u_nuevo].append(w)

        tam = [len(v) for v in adj_temp]
        indptr = np.concatenate(([0], np.cumsum(tam))).astype(np.int64)
        indices = np.concatenate(adj_temp) if sum(tam) > 0 else np.empty(0, dtype=np.int32)
        data = np.concatenate(pesos_temp) if sum(tam) > 0 else np.empty(0, dtype=np.float64)

        return GrafoCoCompra(asins_filtrados, indptr, indices, data)

    def guardar_npz(self, ruta=RUTA_GRAFO):
        """Guarda la representación CSR y los metadatos en un archivo .npz comprimido."""
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            ruta,
            asins=np.array(self.asins),
            indptr=self.indptr,
            indices=self.indices,
            data=self.data
        )

    @classmethod
    def cargar_npz(cls, ruta=RUTA_GRAFO):
        """Carga el GrafoCoCompra desde un archivo .npz."""
        d = np.load(ruta)
        return cls(d["asins"], d["indptr"], d["indices"], d["data"])

    @classmethod
    def desde_aristas(cls, asins, aristas_dict):
        """Construye un GrafoCoCompra a partir de una lista de asins y un dict (u, v) -> peso.

        aristas_dict contiene claves (u, v) con u < v o u == v.
        """
        asins = list(asins)
        n = len(asins)
        pos = {a: i for i, a in enumerate(asins)}

        adj = [[] for _ in range(n)]
        pesos = [[] for _ in range(n)]

        for (a_u, a_v), w in aristas_dict.items():
            if w <= 0 or a_u not in pos or a_v not in pos:
                continue
            u, v = pos[a_u], pos[a_v]
            adj[u].append(v)
            pesos[u].append(float(w))
            if u != v:
                adj[v].append(u)
                pesos[v].append(float(w))

        # Ordenar vecinos de cada nodo por índice para consistencia
        for i in range(n):
            if adj[i]:
                orden = np.argsort(adj[i])
                adj[i] = [adj[i][o] for o in orden]
                pesos[i] = [pesos[i][o] for o in orden]

        tam = [len(v) for v in adj]
        indptr = np.concatenate(([0], np.cumsum(tam))).astype(np.int64)
        indices = np.concatenate(adj).astype(np.int32) if sum(tam) > 0 else np.empty(0, dtype=np.int32)
        data = np.concatenate(pesos).astype(np.float64) if sum(tam) > 0 else np.empty(0, dtype=np.float64)

        return cls(asins, indptr, indices, data)


def sumar_grafos(g1, g2):
    """Grafo con la unión de nodos y la suma de pesos de las aristas de g1 y g2."""
    asins = np.union1d(np.asarray(g1.asins), np.asarray(g2.asins))
    filas, cols, pesos = [], [], []
    for g in (g1, g2):
        mapa = np.searchsorted(asins, np.asarray(g.asins))
        filas.append(mapa[np.repeat(np.arange(g.n_nodos), np.diff(g.indptr))])
        cols.append(mapa[g.indices])
        pesos.append(g.data)
    n = len(asins)
    clave = np.concatenate(filas).astype(np.int64) * n + np.concatenate(cols)
    unicas, inversa = np.unique(clave, return_inverse=True)
    w = np.bincount(inversa, weights=np.concatenate(pesos))
    f, c = unicas // n, unicas % n
    indptr = np.concatenate(([0], np.cumsum(np.bincount(f, minlength=n)))).astype(np.int64)
    return GrafoCoCompra(asins.tolist(), indptr, c.astype(np.int32), w)


# ----- Extracción del Grafo a partir del Stream de Eventos -------------------------

def extraer_cocompras(ruta_eventos=RUTA_EVENTOS_ETAPA2, ruta_bloqueadas=RUTA_BLOQUEADAS,
                      min_peso=MIN_PESO, max_eventos=None):
    """Extrae las compras legítimas del stream de eventos y construye el GrafoCoCompra.

    1. Filtra los eventos de tipo 'purchase'.
    2. Excluye cualquier evento cuyo order_id esté en RUTA_BLOQUEADAS (fraude de etapa 6).
    3. Agrupa los productos comprados por cada orden legítima.
    4. Acumula las parejas de productos que co-ocurrieron en al menos `min_peso` órdenes.
    """
    bloqueadas = ordenes_bloqueadas(ruta_bloqueadas)
    ordenes = defaultdict(set)
    n_compras = 0
    compras_bloqueadas = 0

    for i, ev in enumerate(leer_eventos(ruta_eventos)):
        if max_eventos is not None and i >= max_eventos:
            break
        if ev.event_type == "purchase":
            n_compras += 1
            if ev.order_id in bloqueadas:
                compras_bloqueadas += 1
                continue
            if ev.order_id and ev.parent_asin:
                ordenes[ev.order_id].add(ev.parent_asin)

    # Identificar parejas co-ocurrentes
    aristas_conteo = defaultdict(int)
    productos_en_cocompra = set()

    for prods in ordenes.values():
        if len(prods) < 2:
            continue
        lista = sorted(prods)
        for idx_u in range(len(lista)):
            u = lista[idx_u]
            productos_en_cocompra.add(u)
            for idx_v in range(idx_u + 1, len(lista)):
                v = lista[idx_v]
                productos_en_cocompra.add(v)
                aristas_conteo[(u, v)] += 1

    # Filtrar por peso mínimo
    if min_peso > 1:
        aristas_filtradas = {k: v for k, v in aristas_conteo.items() if v >= min_peso}
        productos_finales = set()
        for u, v in aristas_filtradas.keys():
            productos_finales.add(u)
            productos_finales.add(v)
    else:
        aristas_filtradas = aristas_conteo
        productos_finales = productos_en_cocompra

    asins_ordenados = sorted(productos_finales)
    grafo = GrafoCoCompra.desde_aristas(asins_ordenados, aristas_filtradas)
    grafo.compras_procesadas = n_compras
    grafo.compras_bloqueadas = compras_bloqueadas
    grafo.ordenes_legitimas = len(ordenes)
    grafo.ordenes_con_cocompra = sum(1 for p in ordenes.values() if len(p) >= 2)
    return grafo


# ----- Algoritmos de Influencia: PageRank y Grado Ponderado -----------------------

def pagerank(grafo, alpha=ALPHA, tol=TOL_PAGERANK, max_iter=MAX_ITER_PAGERANK):
    """Calcula el PageRank ponderado mediante el método de Power Iteration.

    Formulación:
    p^{(t+1)} = alpha * p^{(t)} * M + (alpha * S_dangling + (1 - alpha)) / N
    donde M_{ij} = w_{ij} / k_i es la matriz estocástica por filas.

    Como el grafo es no dirigido y w_{ij} = w_{ji}, el producto p * M equivale a
    calcular W * v donde v_i = p_i / k_i.
    Esto permite una multiplicación matriz-vector rala (SpMV) vectorizada en O(|E|).

    Devuelve: (scores, n_iteraciones, convergido)
    """
    n = grafo.n_nodos
    if n == 0:
        return np.empty(0, dtype=np.float64), 0, True

    k = grafo.grados_ponderados()
    dangling = (k == 0)
    inv_k = np.zeros(n, dtype=np.float64)
    inv_k[~dangling] = 1.0 / k[~dangling]

    # Distribución inicial uniforme
    p = np.full(n, 1.0 / n, dtype=np.float64)

    # Arreglos CSR para SpMV rápida
    indptr = grafo.indptr
    indices = grafo.indices
    data = grafo.data
    filas = np.repeat(np.arange(n), np.diff(indptr)) if len(data) > 0 else np.empty(0, dtype=np.int64)

    convergido = False
    it = 0

    for it in range(1, max_iter + 1):
        p_prev = p.copy()
        v = p * inv_k

        # Multiplicación vectorizada (W * v) sobre la estructura CSR
        # Para cada nodo i, sum_j (w_{ij} * v_j)
        if len(data) > 0:
            p_nuevo = np.bincount(filas, weights=data * v[indices], minlength=n).astype(np.float64)
        else:
            p_nuevo = np.zeros(n, dtype=np.float64)

        # Factor de teletransportación y distribución de nodos sumidero
        suma_dangling = float(p[dangling].sum()) if np.any(dangling) else 0.0
        salto = (alpha * suma_dangling + (1.0 - alpha)) / n
        p = alpha * p_nuevo + salto

        # Verificar convergencia en norma L1
        error = float(np.abs(p - p_prev).sum())
        if error < tol:
            convergido = True
            break

    # Normalizar para garantizar suma exacta 1.0
    s = p.sum()
    if s > 0:
        p /= s

    return p, it, convergido


def grado_ponderado_ranking(grafo):
    """Línea base de influencia: suma local de pesos de aristas incidentes."""
    grados = grafo.grados_ponderados()
    total = grados.sum()
    if total > 0:
        return grados / total
    if len(grados) > 0:
        return np.full(len(grados), 1.0 / len(grados))
    return np.empty(0, dtype=np.float64)


# ----- Detección de Comunidades: Louvain y Label Propagation ----------------------

def modularidad(grafo, comunidades, resolucion=RESOLUCION_LOUVAIN):
    """Calcula la modularidad de Newman-Girvan exacta para una partición.

    Q = sum_c [ L_c / m - resolucion * (k_c / (2m))^2 ]
    donde m es el peso total de aristas del grafo, L_c es la suma de pesos de las
    aristas internas de la comunidad c (contadas una vez), y k_c es la suma de grados
    ponderados de los nodos en la comunidad c.
    """
    n = grafo.n_nodos
    m = grafo.peso_total
    if n == 0 or m == 0:
        return 0.0

    comunidades = np.asarray(comunidades, dtype=np.int32)
    k = grafo.grados_ponderados()

    # Suma de grados por comunidad: k_c
    c_unicos, c_inv = np.unique(comunidades, return_inverse=True)
    n_c = len(c_unicos)
    k_c = np.bincount(c_inv, weights=k, minlength=n_c)

    # Aristas internas por comunidad: L_c
    # Recorremos aristas u < v
    u_repetido = np.repeat(np.arange(n), np.diff(grafo.indptr))
    v_vecinos = grafo.indices
    pesos = grafo.data

    filtro_aristas = u_repetido < v_vecinos
    u_f = u_repetido[filtro_aristas]
    v_f = v_vecinos[filtro_aristas]
    w_f = pesos[filtro_aristas]

    mismo_cluster = (comunidades[u_f] == comunidades[v_f])
    comunidades_internas = c_inv[u_f[mismo_cluster]]
    L_c = np.bincount(comunidades_internas, weights=w_f[mismo_cluster], minlength=n_c)

    # Auto-aristas (si existieran)
    auto = (u_repetido == v_vecinos)
    if np.any(auto):
        L_c_auto = np.bincount(c_inv[u_repetido[auto]], weights=pesos[auto], minlength=n_c)
        L_c += L_c_auto

    m2 = 2.0 * m
    q = np.sum((L_c / m) - resolucion * ((k_c / m2) ** 2))
    return float(q)


def componentes_conexas(grafo, comunidades=None):
    """Etiqueta las componentes conexas del grafo en O(|V| + |E|) (BFS de scipy.sparse.csgraph).

    Si se pasa una partición, solo se conservan las aristas internas a cada comunidad: el resultado
    son las componentes conexas de cada comunidad. Devuelve (n_componentes, etiquetas int32).
    """
    n = grafo.n_nodos
    if n == 0:
        return 0, np.empty(0, dtype=np.int32)
    filas = np.repeat(np.arange(n), np.diff(grafo.indptr))
    cols = grafo.indices
    if comunidades is not None:
        comunidades = np.asarray(comunidades)
        internas = comunidades[filas] == comunidades[cols]
        filas, cols = filas[internas], cols[internas]
    adyacencia = sparse.csr_matrix((np.ones(len(filas), dtype=np.int8), (filas, cols)), shape=(n, n))
    n_comp, etiquetas = connected_components(adyacencia, directed=False)
    return int(n_comp), etiquetas.astype(np.int32)


def dividir_comunidades_desconectadas(grafo, comunidades):
    """Parte cada comunidad en sus componentes conexas internas.

    Garantiza comunidades conexas (la propiedad que Leiden asegura y Louvain/LPA no) en O(|V| + |E|).
    Q nunca baja: L_c se conserva porque no hay aristas entre las partes y
    (k_a + k_b)^2 > k_a^2 + k_b^2, así que el término de penalización solo disminuye.
    Devuelve: (comunidades_conexas, n_comunidades_desconectadas_originales)
    """
    comunidades = np.asarray(comunidades)
    _, etiquetas = componentes_conexas(grafo, comunidades)
    n_original = len(np.unique(comunidades))
    pares = np.unique(np.stack([comunidades, etiquetas], axis=1), axis=0)
    partes_por_comunidad = np.unique(pares[:, 0], return_counts=True)[1]
    n_desconectadas = int((partes_por_comunidad > 1).sum())
    if len(pares) == n_original:
        _, comm = np.unique(comunidades, return_inverse=True)
        return comm.astype(np.int32), 0
    return etiquetas, n_desconectadas


def louvain(grafo, resolucion=RESOLUCION_LOUVAIN, max_niveles=MAX_NIVELES_LOUVAIN,
            tol=TOL_LOUVAIN, semilla=config.SEED, garantizar_conexion=True):
    """Algoritmo de Louvain multinivel para detección de comunidades (Blondel et al., 2008).

    Fase 1: Optimización voraz de modularidad moviendo nodos a comunidades vecinas.
    Fase 2: Contracción de comunidades en super-nodos para formar el grafo del siguiente nivel.
    Si garantizar_conexion, las comunidades desconectadas se parten en sus componentes conexas.

    Complejidad: O(pasadas · |E|) por nivel en tiempo; O(|V| + |E|) en memoria pero con
    diccionarios Python (≈ 9× la memoria del CSR medida con tracemalloc).

    Devuelve: (comunidades_finales, modularidad_alcanzada, n_niveles)
    """
    n_orig = grafo.n_nodos
    m = grafo.peso_total
    if n_orig == 0 or m == 0:
        return np.zeros(n_orig, dtype=np.int32), 0.0, 0

    m2 = 2.0 * m
    inv_m = 1.0 / m
    inv_m2_cuad = 1.0 / (m2 * m2)

    # Representación de adyacencia mutable para el grafo actual
    # current_adj[u] = {v: peso}
    current_adj = {}
    for u in range(n_orig):
        vecs, ws = grafo.vecinos(u)
        current_adj[u] = dict(zip(vecs.tolist(), ws.tolist()))

    current_k = grafo.grados_ponderados()
    orig_comm = np.arange(n_orig, dtype=np.int32)

    rng = np.random.default_rng(semilla)
    total_niveles = 0

    while total_niveles < max_niveles:
        num_nodos = len(current_k)
        comm = np.arange(num_nodos, dtype=np.int32)
        tot = current_k.copy()
        nodos = np.arange(num_nodos)

        mejora_nivel = False

        for _ in range(50):
            movimientos = 0
            rng.shuffle(nodos)

            for i in nodos:
                c_orig = comm[i]
                ki = current_k[i]
                tot[c_orig] -= ki

                # Pesos a comunidades vecinas
                c_weights = defaultdict(float)
                for j, w in current_adj[i].items():
                    if i == j:
                        continue  # auto-arista
                    c_weights[comm[j]] += w

                best_c = c_orig
                best_gain = 0.0

                for c_cand, kin in c_weights.items():
                    # Delta Q al mover i a c_cand:
                    # kin / m - resolucion * (2 * ki * tot[c_cand] + ki^2) / (4m^2)
                    # En la derivada exacta: Delta Q = kin / m - resolucion * (ki * tot[c_cand]) / (2m^2)
                    gain = (kin * inv_m) - resolucion * (ki * tot[c_cand] * 2.0 * inv_m2_cuad)
                    if gain > best_gain:
                        best_gain = gain
                        best_c = c_cand

                comm[i] = best_c
                tot[best_c] += ki
                if best_c != c_orig:
                    movimientos += 1
                    mejora_nivel = True

            if movimientos == 0:
                break

        total_niveles += 1

        # Re-etiquetar comunidades a índices continuos 0 .. C - 1
        unique_c, new_comm = np.unique(comm, return_inverse=True)
        orig_comm = new_comm[orig_comm]

        if not mejora_nivel or len(unique_c) == num_nodos:
            break

        # Fase 2: Contracción de comunidades en super-nodos
        C = len(unique_c)
        next_adj = defaultdict(lambda: defaultdict(float))
        for u in range(num_nodos):
            cu = new_comm[u]
            for v, w in current_adj[u].items():
                cv = new_comm[v]
                next_adj[cu][cv] += w

        current_adj = {c: dict(next_adj[c]) for c in range(C)}
        current_k = np.array([sum(current_adj[c].values()) for c in range(C)], dtype=np.float64)

    if garantizar_conexion:
        orig_comm, _ = dividir_comunidades_desconectadas(grafo, orig_comm)
    mod_final = modularidad(grafo, orig_comm, resolucion)
    return orig_comm, mod_final, total_niveles


def label_propagation(grafo, max_iter=MAX_ITER_LPA, semilla=config.SEED, garantizar_conexion=True):
    """Detección de comunidades por Label Propagation Algorithm (Raghavan et al., 2007).

    Línea base lineal O(|E|): cada nodo adopta la etiqueta con mayor peso acumulado entre sus vecinos.
    LPA puede dejar la misma etiqueta en regiones no conexas; garantizar_conexion las separa.
    Devuelve: (comunidades, n_iteraciones, modularidad)
    """
    n = grafo.n_nodos
    if n == 0:
        return np.empty(0, dtype=np.int32), 0, 0.0

    etiquetas = np.arange(n, dtype=np.int32)
    nodos = np.arange(n)
    rng = np.random.default_rng(semilla)

    it = 0
    for it in range(1, max_iter + 1):
        rng.shuffle(nodos)
        cambios = 0

        for i in nodos:
            vecs, ws = grafo.vecinos(i)
            if len(vecs) == 0:
                continue

            conteo_etiquetas = defaultdict(float)
            for v, w in zip(vecs.tolist(), ws.tolist()):
                conteo_etiquetas[etiquetas[v]] += w

            # Elegir la etiqueta de peso máximo; romper empates deterministamente con la menor
            max_w = -1.0
            best_lbl = etiquetas[i]
            for lbl, w in conteo_etiquetas.items():
                if w > max_w or (w == max_w and lbl < best_lbl):
                    max_w = w
                    best_lbl = lbl

            if best_lbl != etiquetas[i]:
                etiquetas[i] = best_lbl
                cambios += 1

        if cambios == 0:
            break

    # Normalizar etiquetas a 0 .. C - 1
    _, comm = np.unique(etiquetas, return_inverse=True)
    if garantizar_conexion:
        comm, _ = dividir_comunidades_desconectadas(grafo, comm)
    mod = modularidad(grafo, comm)
    return comm, it, mod


# ----- Ejecución y Generación de Entregables --------------------------------------

def ejecutar_etapa(ruta_eventos=RUTA_EVENTOS_ETAPA2, ruta_bloqueadas=RUTA_BLOQUEADAS,
                   ruta_grafo=RUTA_GRAFO, ruta_pagerank=RUTA_PAGERANK,
                   ruta_comunidades=RUTA_COMUNIDADES, ruta_metricas=RUTA_METRICAS,
                   min_peso=MIN_PESO, alpha=ALPHA, tol_pagerank=TOL_PAGERANK,
                   resolucion_louvain=RESOLUCION_LOUVAIN, semilla=config.SEED):
    """Ejecuta la etapa 8 completa sobre el stream de eventos y genera todos los artefactos.

    1. Extrae las órdenes legítimas de compra (descartando las órdenes bloqueadas en la etapa 6).
    2. Construye el grafo de co-compra en formato CSR y lo guarda en RUTA_GRAFO.
    3. Calcula PageRank ponderado y Grado Ponderado; exporta RUTA_PAGERANK (Parquet).
    4. Detecta comunidades con Louvain y compara con Label Propagation; exporta RUTA_COMUNIDADES (Parquet).
    5. Consolida tiempos de cómputo, memoria y métricas topológicas en RUTA_METRICAS (JSON).
    """
    CARPETA_ETAPA8.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    metricas = {}

    # 1. Extracción y construcción del grafo
    t = time.perf_counter()
    grafo = extraer_cocompras(ruta_eventos, ruta_bloqueadas, min_peso=min_peso)
    t_extraccion = time.perf_counter() - t
    grafo.guardar_npz(ruta_grafo)

    metricas["grafo"] = {
        "nodos": grafo.n_nodos,
        "aristas": grafo.n_aristas,
        "peso_total": grafo.peso_total,
        "densidad": grafo.densidad,
        "min_peso": min_peso,
        "compras_procesadas": getattr(grafo, "compras_procesadas", 0),
        "compras_bloqueadas_excluidas": getattr(grafo, "compras_bloqueadas", 0),
        "ordenes_legitimas": getattr(grafo, "ordenes_legitimas", 0),
        "ordenes_con_cocompra": getattr(grafo, "ordenes_con_cocompra", 0),
        "segundos_extraccion": t_extraccion,
        "memoria_csr_bytes": int(grafo.indptr.nbytes + grafo.indices.nbytes + grafo.data.nbytes)
    }

    # 2. Ranking de Influencia: PageRank vs Grado Ponderado
    t = time.perf_counter()
    pr_scores, pr_iters, pr_conv = pagerank(grafo, alpha=alpha, tol=tol_pagerank)
    t_pagerank = time.perf_counter() - t

    t = time.perf_counter()
    grado_scores = grado_ponderado_ranking(grafo)
    t_grado = time.perf_counter() - t

    # Guardar Parquet de PageRank
    rango_pr = np.argsort(-pr_scores).argsort() + 1
    rango_grado = np.argsort(-grado_scores).argsort() + 1

    tabla_pr = pa.table({
        "parent_asin": grafo.asins,
        "pagerank_score": pr_scores.astype(np.float64),
        "rango_pagerank": rango_pr.astype(np.int32),
        "grado_ponderado": grafo.grados_ponderados().astype(np.float64),
        "score_grado": grado_scores.astype(np.float64),
        "rango_grado": rango_grado.astype(np.int32)
    })
    pq.write_table(tabla_pr, ruta_pagerank)

    metricas["pagerank"] = {
        "alpha": alpha,
        "tol": tol_pagerank,
        "iteraciones": pr_iters,
        "convergido": pr_conv,
        "segundos_pagerank": t_pagerank,
        "segundos_grado": t_grado,
        "top1_asin": grafo.asins[int(np.argmax(pr_scores))] if len(pr_scores) > 0 else None,
        "top1_score": float(np.max(pr_scores)) if len(pr_scores) > 0 else 0.0
    }

    # 3. Detección de Comunidades: Louvain vs LPA
    t = time.perf_counter()
    comunidades_louvain, _, niveles_louvain = louvain(
        grafo, resolucion=resolucion_louvain, semilla=semilla, garantizar_conexion=False
    )
    comunidades_louvain, desconectadas_louvain = dividir_comunidades_desconectadas(grafo, comunidades_louvain)
    mod_louvain = modularidad(grafo, comunidades_louvain, resolucion_louvain)
    t_louvain = time.perf_counter() - t

    t = time.perf_counter()
    comunidades_lpa, iters_lpa, _ = label_propagation(grafo, semilla=semilla, garantizar_conexion=False)
    comunidades_lpa, desconectadas_lpa = dividir_comunidades_desconectadas(grafo, comunidades_lpa)
    mod_lpa = modularidad(grafo, comunidades_lpa)
    t_lpa = time.perf_counter() - t

    # Contraste con las componentes conexas: si coinciden, la partición no aporta estructura extra
    n_componentes, etiquetas_cc = componentes_conexas(grafo)
    mod_componentes = modularidad(grafo, etiquetas_cc, resolucion_louvain)
    louvain_igual_componentes = bool(
        len(np.unique(comunidades_louvain)) == n_componentes
        and len(np.unique(np.stack([comunidades_louvain, etiquetas_cc], axis=1), axis=0)) == n_componentes
    )

    # Guardar Parquet de Comunidades (conteo y modularidad)
    _, tamanos = np.unique(comunidades_louvain, return_counts=True)
    tamano_nodo = tamanos[comunidades_louvain]

    tabla_comm = pa.table({
        "parent_asin": grafo.asins,
        "comunidad_louvain": comunidades_louvain.astype(np.int32),
        "tamano_comunidad_louvain": tamano_nodo.astype(np.int32),
        "comunidad_lpa": comunidades_lpa.astype(np.int32)
    })
    pq.write_table(tabla_comm, ruta_comunidades)

    metricas["comunidades"] = {
        "resolucion": resolucion_louvain,
        "componentes_conexas": n_componentes,
        "modularidad_componentes": mod_componentes,
        "louvain_igual_componentes": louvain_igual_componentes,
        "louvain": {
            "modularidad": mod_louvain,
            "n_comunidades": int(len(np.unique(comunidades_louvain))),
            "comunidades_desconectadas_partidas": desconectadas_louvain,
            "niveles": niveles_louvain,
            "segundos": t_louvain
        },
        "lpa": {
            "modularidad": mod_lpa,
            "n_comunidades": int(len(np.unique(comunidades_lpa))),
            "comunidades_desconectadas_partidas": desconectadas_lpa,
            "iteraciones": iters_lpa,
            "segundos": t_lpa
        }
    }

    metricas["segundos_total"] = time.perf_counter() - t0
    Path(ruta_metricas).write_text(json.dumps(metricas, indent=2), encoding="utf-8")
    return metricas
