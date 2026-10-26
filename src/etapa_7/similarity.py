"""Etapa 7: similitud aproximada (MinHash + LSH y HNSW).

Dos familias de búsqueda aproximada para los productos relacionados del escenario B:
- Conjuntos (comportamiento de compra). Cada producto es el conjunto de usuarios que
  interactuaron con él en la matriz usuario × producto de la etapa 3.
  - MinHash: resume cada conjunto en una firma de n_hashes enteros. La fracción de posiciones
    iguales entre dos firmas estima la similitud de Jaccard (Broder, 1997).
  - LSH: parte las firmas en bandas y solo compara los productos que coinciden en una banda
    completa (Indyk y Motwani, 1998). Los candidatos se verifican con la Jaccard exacta.
- Vectores (atributos del producto). Cada producto es un vector de dimensión DIM: los pesos
  TF-IDF del título, la categoría de último nivel y la tienda del catálogo de la etapa 3
  (MatrizTFIDF), reducidos a DIM con una proyección aleatoria o con SVD (LSA).
  - HNSW: grafo de proximidad por capas (Malkov y Yashunin, 2020). La búsqueda baja por las
    capas de forma voraz y en la capa 0 explora `ef` candidatos, así que calcula la distancia a
    una fracción de los puntos.
- Líneas base exactas: ConjuntosProductos.fuerza_bruta (Jaccard con todos los productos) y
  similares_exactos, que usa knn_fuerza_bruta de la etapa 5.
- ejecutar_etapa: construye los dos índices, escribe los productos relacionados y similares
  para la etapa 9 y mide recall@K, tiempos y memoria frente a las líneas base.

La entrada de conjuntos excluye a los usuarios con alguna orden bloqueada por la etapa 6, para
que el fraude no cree similitudes entre los productos que compró.

notebooks/etapa7_similitud_aproximada.ipynb importa este código y lo explica.
"""

import json
import math
import random
import re
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .. import config
from ..etapa_2.heap import MinHeap
from ..etapa_3.storage import RUTA_CATALOGO, RUTA_MATRIZ, AlmacenCatalogo, TablaHash, cargar_matriz, hash_clave
from ..etapa_5.kdtree import knn_fuerza_bruta
from ..etapa_6.probabilistic import RUTA_ALERTAS

# Salidas de la etapa en data/interim/<CATEGORIA>/etapa_7/
CARPETA_ETAPA7 = config.CARPETA_INTERIM / "etapa_7"
RUTA_RELACIONADOS = CARPETA_ETAPA7 / "relacionados.parquet"     # salida: etapa 9 (MinHash + LSH)
RUTA_SIMILARES = CARPETA_ETAPA7 / "similares.parquet"           # salida: etapa 9 (HNSW)
RUTA_VECTORES = CARPETA_ETAPA7 / "vectores.npz"                 # vectores de atributos, para el notebook
RUTA_METRICAS = CARPETA_ETAPA7 / "metricas.json"                # tiempos, memoria y recall@K

# ----- Parámetros de la etapa ----------------------------------------------------
# El paso 6 del notebook compara todas las combinaciones, y cada parámetro está fijado con la
# grilla que se indica: conjuntos (6.1), vectores (6.2) y HNSW (6.3).

MIN_USUARIOS = 5          # usuarios mínimos de un producto para compararlo por conjuntos (6.1)
N_HASHES = 256            # largo de la firma MinHash (6.1)
BANDAS, FILAS = 256, 1    # LSH: BANDAS × FILAS = N_HASHES, umbral ≈ (1/BANDAS)^(1/FILAS) (6.1)
DIM = 128                 # dimensión de los vectores de atributos (6.2)
REDUCCION = "proyeccion"  # reducción de TF-IDF a DIM: "proyeccion" (aleatoria) o "svd" (LSA) (6.2)
ITERACIONES_SVD = 2       # SVD aleatorizada: iteraciones de potencia y columnas de sobremuestreo,
SOBREMUESTREO_SVD = 10    # los valores que recomiendan Halko, Martinsson y Tropp (2011)
COLUMNAS_HASH = 1 << 18   # columnas del hashing trick antes de la proyección
M = 32                    # HNSW: vecinos por nodo en las capas superiores (2·M en la capa 0) (6.3)
EF_CONSTRUCCION = 100     # HNSW: candidatos explorados al insertar (6.3)
EF_BUSQUEDA = 50          # HNSW: candidatos explorados al consultar (6.3)
SELECCION = "heuristica"  # HNSW: selección de vecinos, "heuristica" (algoritmo 4) o "simple" (algoritmo 3) (6.3)
K = 10                    # productos relacionados y similares por producto
N_CONSULTAS = 1_000       # productos de la muestra con que se mide el recall@K

PRIMO = (1 << 31) - 1     # primo de Mersenne 2^31 - 1 del hash universal de MinHash


# ----- Conjuntos de usuarios por producto -------------------------------------------

def usuarios_bloqueados(ruta_alertas=RUTA_ALERTAS):
    """Usuarios con alguna orden bloqueada por la etapa 6."""
    bloqueados = set()
    with open(ruta_alertas, encoding="utf-8") as archivo:
        for linea in archivo:
            alerta = json.loads(linea)
            if alerta["decision"] == "bloquear":
                bloqueados.add(alerta["user_id"])
    return bloqueados


class ConjuntosProductos:
    """Conjunto de usuarios de cada producto en formato CSR.

    Los usuarios del producto i son indices[indptr[i]:indptr[i+1]], ordenados. Memoria
    O(productos + nnz).
    """

    def __init__(self, asins, indptr, indices, n_usuarios):
        self.asins = list(asins)
        self.indptr = np.asarray(indptr, dtype=np.int64)
        self.indices = np.asarray(indices, dtype=np.int32)
        self.n_usuarios = n_usuarios
        self.tam = np.diff(self.indptr)
        if np.any(self.tam == 0):
            raise ValueError("ningún conjunto puede estar vacío")
        self.posicion = TablaHash(len(self.asins))
        for i, asin in enumerate(self.asins):
            self.posicion.insertar(asin, i)

    @classmethod
    def desde_matriz(cls, ruta_matriz=RUTA_MATRIZ, excluidos=(), min_usuarios=MIN_USUARIOS):
        """Transpone la matriz usuario × producto de la etapa 3, quita los usuarios `excluidos` y
        conserva los productos con al menos `min_usuarios` usuarios.

        Guarda en `interacciones_excluidas` cuántas interacciones de los usuarios excluidos se quitaron.
        """
        csr, usuarios, productos = cargar_matriz(ruta_matriz)
        t = csr.transpuesta()                                   # producto × usuario
        indptr = np.frombuffer(t.indptr, dtype=np.int64)
        indices = np.frombuffer(t.indices, dtype=np.int32)
        excluido = np.isin(np.array(usuarios), list(excluidos))
        fila = np.repeat(np.arange(len(productos)), np.diff(indptr))
        quedan = ~excluido[indices]
        interacciones_excluidas = int(len(quedan) - quedan.sum())
        fila, indices = fila[quedan], indices[quedan]
        tam = np.bincount(fila, minlength=len(productos))
        conservar = tam >= min_usuarios
        quedan = conservar[fila]
        indptr = np.concatenate(([0], np.cumsum(tam[conservar])))
        asins = [p for p, c in zip(productos, conservar.tolist()) if c]
        conjuntos = cls(asins, indptr, indices[quedan], len(usuarios))
        conjuntos.interacciones_excluidas = interacciones_excluidas
        return conjuntos

    def __len__(self):
        return len(self.asins)

    @property
    def nnz(self):
        return len(self.indices)

    def usuarios(self, i):
        return self.indices[self.indptr[i]:self.indptr[i + 1]]

    def _marca(self, i):
        marca = np.zeros(self.n_usuarios, dtype=np.int32)
        marca[self.usuarios(i)] = 1
        return marca

    def jaccard(self, i, js):
        """Jaccard exacta entre el producto i y cada producto de js. O(suma de los tamaños de js)."""
        js = np.asarray(js, dtype=np.int64)
        if len(js) == 0:
            return np.empty(0)
        largos = self.tam[js]
        inicios = np.cumsum(largos) - largos
        # Posiciones en `indices` de los usuarios de todos los js, uno detrás de otro
        pos = np.arange(largos.sum()) - np.repeat(inicios, largos) + np.repeat(self.indptr[js], largos)
        interseccion = np.add.reduceat(self._marca(i)[self.indices[pos]], inicios)
        return interseccion / (largos + self.tam[i] - interseccion)

    def fuerza_bruta(self, i, k=K):
        """Línea base exacta: Jaccard de i con todos los productos, O(nnz). Devuelve los k más
        similares como lista de (jaccard, índice), de mayor a menor, sin incluir i."""
        interseccion = np.add.reduceat(self._marca(i)[self.indices], self.indptr[:-1])
        jaccard = interseccion / (self.tam + self.tam[i] - interseccion)
        jaccard[i] = -1.0
        return k_mejores(jaccard, np.arange(len(jaccard)), k)


def k_mejores(similitud, ids, k):
    """Los k de mayor similitud > 0, ordenados por similitud y luego por índice."""
    positivos = similitud > 0
    similitud, ids = similitud[positivos], ids[positivos]
    if len(ids) > k:
        corte = np.argpartition(-similitud, k - 1)[:k]
        # Un empate en el k-ésimo valor se resuelve por el índice menor, igual que en LSH
        limite = similitud[corte].min()
        corte = np.flatnonzero(similitud >= limite)
    else:
        corte = np.arange(len(ids))
    orden = np.lexsort((ids[corte], -similitud[corte]))[:k]
    return [(float(similitud[corte][o]), int(ids[corte][o])) for o in orden]


# ----- MinHash ---------------------------------------------------------------------

class MinHash:
    """n_hashes funciones h_i(x) = (a_i·x + b_i) mod p, con p = 2^31 - 1.

    La firma de un conjunto S es (min h_1(S), ..., min h_n(S)). Para dos conjuntos A y B,
    P[min h_i(A) = min h_i(B)] = J(A, B), así que la fracción de posiciones iguales estima J con
    error estándar sqrt(J·(1 - J) / n_hashes). Los elementos son enteros en [0, p); como a, b y x
    son menores que 2^31, a·x + b cabe en un int64.
    """

    def __init__(self, n_hashes=N_HASHES, semilla=config.SEED):
        rng = np.random.default_rng(semilla)
        self.n_hashes = n_hashes
        self.a = rng.integers(1, PRIMO, n_hashes, dtype=np.int64)
        self.b = rng.integers(0, PRIMO, n_hashes, dtype=np.int64)

    def firmas(self, indptr, indices, bloque=8):
        """Firmas de los conjuntos de un CSR: matriz conjuntos × n_hashes de uint32.

        O(nnz · n_hashes). Se calculan `bloque` funciones a la vez para acotar la memoria
        temporal a nnz × bloque enteros. Ningún conjunto puede estar vacío.
        """
        indptr = np.asarray(indptr, dtype=np.int64)
        x = np.asarray(indices, dtype=np.int64)[:, None]
        firmas = np.empty((len(indptr) - 1, self.n_hashes), dtype=np.uint32)
        for h in range(0, self.n_hashes, bloque):
            valores = (x * self.a[h:h + bloque] + self.b[h:h + bloque]) % PRIMO
            firmas[:, h:h + bloque] = np.minimum.reduceat(valores, indptr[:-1], axis=0)
        return firmas

    def firma(self, conjunto):
        x = np.asarray(conjunto, dtype=np.int64)[:, None]
        return ((x * self.a + self.b) % PRIMO).min(axis=0).astype(np.uint32)


def jaccard_estimada(firmas, i, js):
    """Fracción de posiciones iguales entre la firma de i y la de cada producto de js."""
    return (firmas[js] == firmas[i]).mean(axis=1)


# ----- LSH por bandas --------------------------------------------------------------

class LSH:
    """Índice LSH por bandas sobre firmas MinHash.

    La firma se parte en `bandas` bandas de `filas` valores. Dos productos son candidatos si
    coinciden en todos los valores de al menos una banda. Con Jaccard s, eso ocurre con
    probabilidad 1 - (1 - s^filas)^bandas, una curva en S con umbral ≈ (1/bandas)^(1/filas).

    En cada banda, los productos con los mismos valores forman una cubeta. Las cubetas se arman
    ordenando las filas de la banda (np.unique), así que no hay colisiones entre cubetas
    distintas. Por banda se guarda la cubeta de cada producto y los productos de cada cubeta en
    tramos contiguos: memoria O(bandas · productos).
    """

    def __init__(self, firmas, bandas=BANDAS, filas=FILAS):
        n, h = firmas.shape
        if bandas * filas > h:
            raise ValueError("bandas × filas no puede superar el largo de la firma")
        self.bandas, self.filas = bandas, filas
        self.cubeta = np.empty((bandas, n), dtype=np.int32)
        self.miembros, self.inicio = [], []
        for b in range(bandas):
            _, cubeta, tamanos = np.unique(firmas[:, b * filas:(b + 1) * filas], axis=0,
                                           return_inverse=True, return_counts=True)
            cubeta = cubeta.ravel()
            self.cubeta[b] = cubeta
            self.miembros.append(np.argsort(cubeta, kind="stable").astype(np.int32))
            self.inicio.append(np.concatenate(([0], np.cumsum(tamanos))).astype(np.int32))
        self.candidatos_evaluados = 0                   # candidatos de la última consulta

    def candidatos(self, i):
        """Productos que comparten al menos una cubeta con i, sin incluir i."""
        tramos = [self.miembros[b][self.inicio[b][c]:self.inicio[b][c + 1]]
                  for b, c in enumerate(self.cubeta[:, i].tolist())]
        candidatos = np.unique(np.concatenate(tramos))
        candidatos = candidatos[candidatos != i]
        self.candidatos_evaluados = len(candidatos)
        return candidatos

    @staticmethod
    def probabilidad(s, bandas=BANDAS, filas=FILAS):
        return 1 - (1 - np.asarray(s, dtype=np.float64) ** filas) ** bandas

    @staticmethod
    def umbral(bandas=BANDAS, filas=FILAS):
        return (1 / bandas) ** (1 / filas)

    @property
    def memoria_bytes(self):
        return self.cubeta.nbytes + sum(a.nbytes for a in self.miembros) + sum(a.nbytes for a in self.inicio)


def relacionados_lsh(conjuntos, lsh, i, k=K):
    """Los k productos más similares a i entre sus candidatos LSH, con la Jaccard exacta.

    Devuelve lista de (jaccard, índice), de mayor a menor, sin incluir i.
    """
    candidatos = lsh.candidatos(i)
    return k_mejores(conjuntos.jaccard(i, candidatos), candidatos, k)


# ----- Vectores de atributos ------------------------------------------------------

_PALABRA = re.compile(r"[a-z0-9]+")


def rasgos(registro):
    """Rasgos de un producto: las palabras del título, su categoría de último nivel y su tienda."""
    titulo = (registro.get("title") or "").lower()
    lista = [f"w:{p}" for p in _PALABRA.findall(titulo) if len(p) > 1]
    if registro.get("categoria_hoja"):
        lista.append(f"cat:{registro['categoria_hoja']}")
    if registro.get("store"):
        lista.append(f"tienda:{registro['store']}")
    return lista


class MatrizTFIDF:
    """Pesos TF-IDF de los rasgos de cada producto, con cada fila de norma 1.

    1. Hashing trick: cada rasgo va a la columna hash_clave(rasgo) mod `columnas`.
    2. TF-IDF: peso (1 + ln tf) · ln(n / df) de cada columna en el producto.
    3. Cada fila se divide por su norma, así que el producto punto entre dos filas es el coseno.

    Se guarda por filas (CSR, para proyectar) y por columnas (CSC, para el coseno exacto de un
    producto con todos). Un producto sin rasgos queda como fila vacía.
    """

    def __init__(self, registros, columnas=COLUMNAS_HASH):
        filas, cols = [], []
        for i, registro in enumerate(registros):
            for r in rasgos(registro):
                filas.append(i)
                cols.append(hash_clave(r) % columnas)
        self.n, self.columnas = len(registros), columnas
        codigos, tf = np.unique(np.array(filas, dtype=np.int64) * columnas + np.array(cols, dtype=np.int64),
                                return_counts=True)
        self.fila, self.col = codigos // columnas, codigos % columnas
        df = np.bincount(self.col, minlength=columnas)
        peso = (1 + np.log(tf)) * np.log(self.n / df[self.col])
        norma = np.sqrt(np.bincount(self.fila, weights=peso ** 2, minlength=self.n))
        norma[norma == 0] = 1.0
        self.peso = (peso / norma[self.fila]).astype(np.float32)
        self.indptr = np.searchsorted(self.fila, np.arange(self.n + 1))
        orden = np.argsort(self.col, kind="stable")
        self.col_indptr = np.searchsorted(self.col[orden], np.arange(columnas + 1))
        self.col_col, self.col_fila, self.col_peso = self.col[orden], self.fila[orden], self.peso[orden]
        self.valores_singulares = None             # de la última reducción con svd

    def por_derecha(self, B, bloque=20_000):
        """A·B, con A esta matriz (n × columnas) y B densa (columnas × l). O(nnz · l), por bloques de filas."""
        B = np.asarray(B, dtype=np.float32)
        R = np.zeros((self.n, B.shape[1]), dtype=np.float32)
        for a in range(0, self.n, bloque):
            i, j = self.indptr[a], self.indptr[min(a + bloque, self.n)]
            if i == j:
                continue
            fila = self.fila[i:j]
            inicios = np.flatnonzero(np.r_[True, fila[1:] != fila[:-1]])
            R[fila[inicios]] = np.add.reduceat(B[self.col[i:j]] * self.peso[i:j, None], inicios)
        return R

    def por_izquierda(self, Y, bloque=16_384):
        """Aᵀ·Y, con Y densa (n × l). O(nnz · l), por bloques de columnas."""
        Y = np.asarray(Y, dtype=np.float32)
        R = np.zeros((self.columnas, Y.shape[1]), dtype=np.float32)
        for a in range(0, self.columnas, bloque):
            i, j = self.col_indptr[a], self.col_indptr[min(a + bloque, self.columnas)]
            if i == j:
                continue
            col = self.col_col[i:j]
            inicios = np.flatnonzero(np.r_[True, col[1:] != col[:-1]])
            R[col[inicios]] = np.add.reduceat(Y[self.col_fila[i:j]] * self.col_peso[i:j, None], inicios)
        return R

    def proyectar(self, dim=DIM, semilla=config.SEED):
        """Proyección aleatoria gaussiana a `dim` columnas, que conserva aproximadamente las
        distancias de cualquier conjunto de puntos (Johnson y Lindenstrauss, 1984)."""
        proyeccion = np.random.default_rng(semilla).standard_normal((self.columnas, dim), dtype=np.float32)
        return _normalizar(self.por_derecha(proyeccion))

    def svd(self, dim=DIM, semilla=config.SEED, iteraciones=ITERACIONES_SVD, sobremuestreo=SOBREMUESTREO_SVD):
        """Filas de la matriz en sus `dim` direcciones principales (LSA; Deerwester et al., 1990).

        SVD truncada aleatorizada (Halko, Martinsson y Tropp, 2011), con l = dim + sobremuestreo:
        1. Y = A·Ω, con Ω gaussiana de columnas × l, y Q una base ortonormal de Y (QR).
        2. `iteraciones` de potencia, Q = base de A·(Aᵀ·Q), que separan los valores singulares
           grandes de los pequeños.
        3. Bᵀ = Aᵀ·Q es de columnas × l, y B·Bᵀ = U_B·S²·U_Bᵀ es una matriz pequeña de l × l.
        4. Las filas de A en las dim direcciones principales son Q·U_B·S (las dim primeras).
        Por Eckart y Young (1936), U·S·Vᵀ truncada a dim es la mejor aproximación de rango dim de A.
        Cuesta O(nnz · l · (2·iteraciones + 2)) más O((n + columnas) · l²) de las QR.
        """
        l = dim + sobremuestreo
        rng = np.random.default_rng(semilla)
        Q, _ = np.linalg.qr(self.por_derecha(rng.standard_normal((self.columnas, l), dtype=np.float32)))
        for _ in range(iteraciones):
            Z, _ = np.linalg.qr(self.por_izquierda(Q))
            Q, _ = np.linalg.qr(self.por_derecha(Z))
        Bt = self.por_izquierda(Q)
        valores, U_B = np.linalg.eigh((Bt.T @ Bt).astype(np.float64))
        orden = np.argsort(valores)[::-1][:dim]
        S = np.sqrt(np.maximum(valores[orden], 0.0))
        self.valores_singulares = S
        return _normalizar((Q @ U_B[:, orden].astype(np.float32)) * S.astype(np.float32))

    def reducir(self, dim=DIM, metodo=REDUCCION, semilla=config.SEED):
        """Vectores de dimensión `dim` y norma 1, con proyección aleatoria ("proyeccion") o SVD ("svd").

        Entre vectores de norma 1, la distancia euclidiana ordena igual que el coseno.
        """
        if metodo == "proyeccion":
            return self.proyectar(dim, semilla)
        if metodo == "svd":
            return self.svd(dim, semilla)
        raise ValueError('metodo debe ser "proyeccion" o "svd"')

    def coseno_contra_todos(self, i):
        """Coseno exacto de la fila i con todas, sumando las listas de las columnas de i."""
        a, b = self.indptr[i], self.indptr[i + 1]
        tramos = [np.arange(self.col_indptr[c], self.col_indptr[c + 1]) for c in self.col[a:b].tolist()]
        if not tramos:
            return np.zeros(self.n)
        pos = np.concatenate(tramos)
        pesos_i = np.repeat(self.peso[a:b], [len(t) for t in tramos])
        return np.bincount(self.col_fila[pos], weights=pesos_i * self.col_peso[pos], minlength=self.n)

    def similares_exactos(self, i, k=K):
        """Los k productos de mayor coseno con i en el espacio TF-IDF, sin incluir i: (coseno, índice)."""
        coseno = self.coseno_contra_todos(i)
        coseno[i] = -1.0
        return k_mejores(coseno, np.arange(self.n), k)


def _normalizar(V):
    """Cada fila a norma 1; una fila nula queda nula."""
    normas = np.linalg.norm(V, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return V / normas


def vectores_atributos(registros, dim=DIM, columnas=COLUMNAS_HASH, semilla=config.SEED, reduccion=REDUCCION):
    """Vector de dimensión `dim` y norma 1 para cada registro del catálogo (MatrizTFIDF reducida)."""
    return MatrizTFIDF(registros, columnas).reducir(dim, reduccion, semilla)


# ----- HNSW ------------------------------------------------------------------------

class HNSW:
    """Hierarchical Navigable Small World (Malkov y Yashunin, 2020) con distancia euclidiana.

    Cada nodo recibe un nivel máximo l = ⌊-ln(U)·mL⌋, con U uniforme en (0, 1] y mL = 1/ln(M),
    así que cada capa tiene en promedio 1/M de los nodos de la anterior. En cada capa hasta su
    nivel, el nodo se conecta con hasta M vecinos (2·M en la capa 0) elegidos según `seleccion`:
    - "heuristica" (algoritmo 4 del paper): un candidato se acepta si está más cerca del nodo
      que de todos los vecinos ya aceptados, lo que conserva aristas hacia regiones distintas.
    - "simple" (algoritmo 3): los M candidatos más cercanos.

    La búsqueda baja de forma voraz desde la capa superior hasta la 1 y en la capa 0 explora `ef`
    candidatos (algoritmo 2). Los candidatos y los resultados se guardan en el MinHeap de la
    etapa 2; el de resultados guarda la distancia negada, para que la raíz sea el peor. Las
    distancias a los vecinos nuevos de un nodo se calculan de una vez con numpy.
    """

    def __init__(self, dim, m=M, ef_construccion=EF_CONSTRUCCION, ef_busqueda=EF_BUSQUEDA,
                 seleccion=SELECCION, semilla=config.SEED, capacidad=1_024):
        if seleccion not in ("heuristica", "simple"):
            raise ValueError('seleccion debe ser "heuristica" o "simple"')
        self.seleccion = seleccion
        self.dim, self.m, self.m0 = dim, m, 2 * m
        self.ml = 1 / math.log(m)
        self.ef_construccion, self.ef_busqueda = ef_construccion, ef_busqueda
        self.X = np.empty((capacidad, dim), dtype=np.float32)
        self.n = 0
        self.vecinos = []                  # vecinos[i][l]: lista de vecinos de i en la capa l
        self.entrada, self.nivel_max = -1, -1
        self.rng = random.Random(semilla)
        self._visita = []                  # última búsqueda que visitó cada nodo
        self._busqueda = 0
        self.distancias_calculadas = 0     # en la última búsqueda o inserción

    def _distancias(self, q, ids):
        diferencia = self.X[ids] - q
        return np.einsum("ij,ij->i", diferencia, diferencia)

    def _buscar_capa(self, q, entradas, ef, capa):
        """Algoritmo 2: los ef nodos más cercanos a q en la capa, desde `entradas` [(d², i)].

        Devuelve lista de (d², i) ordenada de menor a mayor distancia.
        """
        self._busqueda += 1
        busqueda, visita, vecinos = self._busqueda, self._visita, self.vecinos
        candidatos = MinHeap()             # (d², i): la raíz es el más cercano sin expandir
        resultados = MinHeap()             # (-d², i): la raíz es el peor resultado
        for d, i in entradas:
            visita[i] = busqueda
            candidatos.push((d, i))
            resultados.push((-d, i))
        while len(resultados) > ef:
            resultados.pop()
        n_candidatos, n_resultados = len(candidatos), len(resultados)
        peor = -resultados.peek()[0]
        while n_candidatos:
            d, c = candidatos.pop()
            n_candidatos -= 1
            if d > peor:
                break                      # ningún candidato restante puede mejorar los resultados
            nuevos = [j for j in vecinos[c][capa] if visita[j] != busqueda]
            if not nuevos:
                continue
            for j in nuevos:
                visita[j] = busqueda
            self.distancias_calculadas += len(nuevos)
            for dj, j in zip(self._distancias(q, nuevos).tolist(), nuevos):
                if dj < peor or n_resultados < ef:
                    candidatos.push((dj, j))
                    resultados.push((-dj, j))
                    n_candidatos += 1
                    if n_resultados == ef:
                        resultados.pop()
                    else:
                        n_resultados += 1
                    peor = -resultados.peek()[0]
        return sorted((-d, i) for d, i in resultados.elementos())

    def _seleccionar(self, candidatos, m):
        """Hasta m vecinos de entre `candidatos` [(d², i)] ordenados por distancia."""
        if self.seleccion == "simple" or len(candidatos) <= m:
            return [i for _, i in candidatos[:m]]            # algoritmo 3
        return self._heuristica(candidatos, m)

    def _heuristica(self, candidatos, m):
        """Algoritmo 4: hasta m vecinos de entre `candidatos` [(d², i)] ordenados por distancia.

        Las distancias entre candidatos se calculan una sola vez, como matriz, con numpy.
        """
        ids = [i for _, i in candidatos]
        V = self.X[ids]
        normas = np.einsum("ij,ij->i", V, V)
        entre = (normas[:, None] + normas[None, :] - 2 * (V @ V.T)).tolist()
        elegidos = []
        for p, (d, _) in enumerate(candidatos):
            fila = entre[p]
            if any(fila[e] < d for e in elegidos):
                continue                   # está más cerca de un vecino ya elegido que del nodo
            elegidos.append(p)
            if len(elegidos) == m:
                break
        return [ids[p] for p in elegidos]

    def _bajar(self, q, hasta):
        """Búsqueda voraz (ef = 1) desde el punto de entrada hasta la capa `hasta` + 1."""
        entrada = [(float(self._distancias(q, [self.entrada])[0]), self.entrada)]
        for capa in range(self.nivel_max, hasta, -1):
            entrada = self._buscar_capa(q, entrada, 1, capa)
        return entrada

    def agregar(self, x):
        """Algoritmo 1: inserta x y devuelve su índice."""
        if self.n == len(self.X):
            self.X = np.concatenate((self.X, np.empty_like(self.X)))
        i = self.n
        self.X[i] = x
        q = self.X[i]
        self.n += 1
        nivel = int(-math.log(1.0 - self.rng.random()) * self.ml)
        self.vecinos.append([[] for _ in range(nivel + 1)])
        self._visita.append(0)
        self.distancias_calculadas = 0
        if self.entrada < 0:
            self.entrada, self.nivel_max = i, nivel
            return i

        entrada = self._bajar(q, nivel)
        for capa in range(min(nivel, self.nivel_max), -1, -1):
            encontrados = self._buscar_capa(q, entrada, self.ef_construccion, capa)
            elegidos = self._seleccionar(encontrados, self.m)
            self.vecinos[i][capa] = elegidos
            maximo = self.m0 if capa == 0 else self.m
            for j in elegidos:
                lista = self.vecinos[j][capa]
                lista.append(i)
                if len(lista) > maximo:    # el vecino superó su máximo: se recortan sus aristas
                    distancias = self._distancias(self.X[j], lista).tolist()
                    self.vecinos[j][capa] = self._seleccionar(sorted(zip(distancias, lista)), maximo)
            entrada = encontrados
        if nivel > self.nivel_max:
            self.entrada, self.nivel_max = i, nivel
        return i

    def agregar_todos(self, X):
        for x in X:
            self.agregar(x)
        return self

    def buscar(self, q, k, ef=None):
        """Los k vecinos aproximados de q: lista de (distancia, índice), de menor a mayor."""
        if self.n == 0:
            return []
        q = np.asarray(q, dtype=np.float32)
        self.distancias_calculadas = 0
        entrada = self._bajar(q, 0)
        resultado = self._buscar_capa(q, entrada, max(ef or self.ef_busqueda, k), 0)
        return [(math.sqrt(max(d, 0.0)), i) for d, i in resultado[:k]]

    def __len__(self):
        return self.n

    def n_aristas(self):
        return sum(len(lista) for nodo in self.vecinos for lista in nodo)

    def niveles(self):
        """Número de nodos en cada capa."""
        conteo = [0] * (self.nivel_max + 1)
        for nodo in self.vecinos:
            for capa in range(len(nodo)):
                conteo[capa] += 1
        return conteo

    @property
    def memoria_bytes(self):
        """Vectores más aristas, contando 4 bytes por arista como en un arreglo de int32."""
        return self.X[:self.n].nbytes + 4 * self.n_aristas()


def recall_at_k(aproximado, exacto):
    """Fracción de los vecinos exactos que aparecen en la respuesta aproximada.

    Si la línea base exacta no tiene vecinos, el recall no está definido y vale NaN; los
    promedios se toman con np.nanmean, sin esas consultas.
    """
    if not exacto:
        return math.nan
    return len({i for _, i in aproximado} & {i for _, i in exacto}) / len(exacto)


def similares_hnsw(hnsw, V, i, k=K, ef=None):
    """Los k productos más cercanos a V[i] según el HNSW, sin incluir i: lista de (distancia, índice)."""
    ef = max(ef or hnsw.ef_busqueda, k + 1)
    return [(d, j) for d, j in hnsw.buscar(V[i], k + 1, ef) if j != i][:k]


def similares_exactos(V, i, k=K):
    """Línea base exacta: los k productos más cercanos a V[i] por fuerza bruta (etapa 5), sin incluir i."""
    return [(d, j) for d, j in knn_fuerza_bruta(V, V[i], k + 1) if j != i][:k]


# ----- Ejecución sobre las salidas de las etapas anteriores ---------------------------

def _escribir_vecinos(ruta, asins, vecinos, metrica):
    """Parquet con una fila por (producto, rango): parent_asin, rango, vecino y `metrica`.

    Usa los mismos nombres de columna que vecinos.parquet de la etapa 5.
    """
    filas = [(asins[i], r, asins[j], valor) for i, lista in vecinos for r, (valor, j) in enumerate(lista, 1)]
    tabla = pa.table({"parent_asin": [f[0] for f in filas], "rango": [f[1] for f in filas],
                      "vecino": [f[2] for f in filas], metrica: [f[3] for f in filas]})
    pq.write_table(tabla, ruta)


def ejecutar_etapa(ruta_matriz=RUTA_MATRIZ, ruta_alertas=RUTA_ALERTAS, ruta_catalogo=RUTA_CATALOGO,
                   ruta_relacionados=RUTA_RELACIONADOS, ruta_similares=RUTA_SIMILARES,
                   ruta_vectores=RUTA_VECTORES, ruta_metricas=RUTA_METRICAS, min_usuarios=MIN_USUARIOS,
                   n_hashes=N_HASHES, bandas=BANDAS, filas=FILAS, dim=DIM, reduccion=REDUCCION, m=M,
                   ef_construccion=EF_CONSTRUCCION,
                   ef_busqueda=EF_BUSQUEDA, seleccion=SELECCION, k=K, n_consultas=N_CONSULTAS,
                   semilla=config.SEED):
    """Ejecuta la etapa 7 sobre las salidas de las etapas 3 y 6 y evalúa las dos familias.

    1. Conjuntos: usuarios por producto sin los usuarios bloqueados por la etapa 6, firmas
       MinHash, índice LSH y los k productos relacionados de cada producto.
    2. Vectores: vectores de atributos de todos los productos de la matriz (reducidos con
       `reduccion`), índice HNSW y los k
       productos similares de cada producto.
    3. Evaluación: en una muestra de `n_consultas` productos se comparan las dos respuestas con
       sus líneas base exactas por fuerza bruta (recall@K y tiempo por consulta). El producto
       consultado se excluye de las dos respuestas.

    Los tiempos se miden sin tracemalloc; la memoria que se reporta es la de las estructuras.
    Escribe las salidas de la etapa y devuelve las métricas.
    """
    rng = np.random.default_rng(semilla)
    CARPETA_ETAPA7.mkdir(parents=True, exist_ok=True)
    metricas = {}
    t0 = time.perf_counter()

    # 1. Conjuntos: MinHash + LSH
    bloqueados = usuarios_bloqueados(ruta_alertas)
    conjuntos = ConjuntosProductos.desde_matriz(ruta_matriz, bloqueados, min_usuarios)
    t = time.perf_counter()
    firmas = MinHash(n_hashes, semilla).firmas(conjuntos.indptr, conjuntos.indices)
    t_firmas = time.perf_counter() - t
    t = time.perf_counter()
    lsh = LSH(firmas, bandas, filas)
    t_lsh = time.perf_counter() - t

    t = time.perf_counter()
    relacionados = [(i, relacionados_lsh(conjuntos, lsh, i, k)) for i in range(len(conjuntos))]
    t_relacionados = time.perf_counter() - t
    _escribir_vecinos(ruta_relacionados, conjuntos.asins, relacionados, "jaccard")

    muestra = rng.choice(len(conjuntos), size=min(n_consultas, len(conjuntos)), replace=False)
    recall, t_lsh_consulta, t_bruta, candidatos = [], 0.0, 0.0, 0
    for i in muestra.tolist():
        t = time.perf_counter()
        aproximado = relacionados_lsh(conjuntos, lsh, i, k)
        t_lsh_consulta += time.perf_counter() - t
        candidatos += lsh.candidatos_evaluados
        t = time.perf_counter()
        exacto = conjuntos.fuerza_bruta(i, k)
        t_bruta += time.perf_counter() - t
        recall.append(recall_at_k(aproximado, exacto))
    metricas["conjuntos"] = {
        "usuarios_bloqueados_excluidos": len(bloqueados),
        "interacciones_excluidas": conjuntos.interacciones_excluidas, "productos": len(conjuntos),
        "nnz": conjuntos.nnz, "min_usuarios": min_usuarios, "n_hashes": n_hashes, "bandas": bandas,
        "filas": filas, "umbral_lsh": LSH.umbral(bandas, filas),
        "segundos_firmas": t_firmas, "segundos_indice": t_lsh, "segundos_relacionados": t_relacionados,
        "memoria_firmas_bytes": firmas.nbytes, "memoria_lsh_bytes": lsh.memoria_bytes,
        "productos_con_relacionados": sum(1 for _, lista in relacionados if lista),
        "consultas": len(muestra), "consultas_sin_vecinos": int(np.isnan(recall).sum()),
        "recall_at_k": float(np.nanmean(recall)),
        "candidatos_por_consulta": candidatos / len(muestra),
        "ms_por_consulta_lsh": t_lsh_consulta / len(muestra) * 1e3,
        "ms_por_consulta_fuerza_bruta": t_bruta / len(muestra) * 1e3,
    }
    print(f"Conjuntos: {len(conjuntos):,} productos, recall@{k} = {metricas['conjuntos']['recall_at_k']:.3f} "
          f"({time.perf_counter() - t0:.0f} s)", flush=True)

    # 2. Vectores: HNSW
    _, _, productos = cargar_matriz(ruta_matriz)
    almacen = AlmacenCatalogo(ruta_catalogo)
    registros = [almacen.leer(asin) or {} for asin in productos]
    almacen.cerrar()
    V = vectores_atributos(registros, dim, semilla=semilla, reduccion=reduccion)
    np.savez_compressed(ruta_vectores, asins=np.array(productos), V=V)

    t = time.perf_counter()
    hnsw = HNSW(V.shape[1], m, ef_construccion, ef_busqueda, seleccion, semilla, capacidad=len(V))
    for i, x in enumerate(V, 1):
        hnsw.agregar(x)
        if i % 50_000 == 0:
            print(f"HNSW: {i:,} productos insertados ({time.perf_counter() - t:.0f} s)", flush=True)
    t_hnsw = time.perf_counter() - t

    t = time.perf_counter()
    similares = [(i, similares_hnsw(hnsw, V, i, k)) for i in range(len(V))]
    t_similares = time.perf_counter() - t
    _escribir_vecinos(ruta_similares, productos, similares, "distancia")

    muestra = rng.choice(len(V), size=min(n_consultas, len(V)), replace=False)
    recall, t_hnsw_consulta, t_bruta, distancias = [], 0.0, 0.0, 0
    for i in muestra.tolist():
        t = time.perf_counter()
        aproximado = similares_hnsw(hnsw, V, i, k)
        t_hnsw_consulta += time.perf_counter() - t
        distancias += hnsw.distancias_calculadas
        t = time.perf_counter()
        exacto = similares_exactos(V, i, k)
        t_bruta += time.perf_counter() - t
        recall.append(recall_at_k(aproximado, exacto))
    metricas["vectores"] = {
        "productos": len(V), "dim": V.shape[1], "reduccion": reduccion, "m": m, "ef_construccion": ef_construccion,
        "ef_busqueda": ef_busqueda, "seleccion": seleccion, "nodos_por_capa": hnsw.niveles(),
        "aristas": hnsw.n_aristas(), "segundos_construccion": t_hnsw, "segundos_similares": t_similares,
        "memoria_hnsw_bytes": hnsw.memoria_bytes,
        "consultas": len(muestra), "consultas_sin_vecinos": int(np.isnan(recall).sum()),
        "recall_at_k": float(np.nanmean(recall)),
        "distancias_por_consulta": distancias / len(muestra),
        "ms_por_consulta_hnsw": t_hnsw_consulta / len(muestra) * 1e3,
        "ms_por_consulta_fuerza_bruta": t_bruta / len(muestra) * 1e3,
    }
    metricas["k"] = k
    metricas["segundos_total"] = time.perf_counter() - t0
    ruta_metricas.write_text(json.dumps(metricas, indent=2), encoding="utf-8")
    return metricas
