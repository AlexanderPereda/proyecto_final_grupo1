"""Mediciones de la etapa 7 que pide la revisión técnica del 28 de octubre (punto 1.8 y protocolo).

notebooks/etapa7_similitud_aproximada.ipynb mide la etapa en sus propias celdas. Este módulo agrega
cuatro mediciones sobre las mismas salidas y los mismos datos remuestreados, sin volver a construir
el HNSW del catálogo completo:

1. recall: recall@K de HNSW frente al coseno exacto en TF-IDF. El 0.946 del paso 7 se mide frente
   al KNN exacto en los vectores proyectados a DIM; aquí se mide también frente al espacio TF-IDF
   original, con la misma muestra de N_CONSULTAS productos que usa ejecutar_etapa. Los similares de
   HNSW se leen de similares.parquet.
2. fuerza_bruta: knn_fuerza_bruta de la etapa 5 convierte los vectores a float64 en cada consulta.
   Se compara con el producto punto en float32 sobre los mismos 1M vectores remuestreados del paso 9.
3. indice_invertido: Jaccard exacta con un índice invertido usuario → productos, frente a
   MinHash + LSH y a la fuerza bruta, con la muestra de conjuntos de ejecutar_etapa.
4. construccion: 100 repeticiones de las construcciones del paso 9 que con el presupuesto de 120 s
   tuvieron menos, cuando las 100 caben en menos de una hora.

Protocolo del notebook: tiempo con time.perf_counter y el recolector de basura desactivado;
consultas en REPETICIONES bloques; memoria con tracemalloc en una pasada aparte.

Uso:
    python -m src.etapa_7.mediciones_revision [recall] [fuerza_bruta] [indice_invertido] [construccion]
Salida: results/etapa_7/recall_tfidf.csv, fuerza_bruta_float32.csv, indice_invertido.csv y
        construccion_100_repeticiones.csv
"""

import gc
import json
import platform
import statistics
import sys
import time
import tracemalloc

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .. import config
from ..etapa_3.storage import RUTA_CATALOGO, RUTA_MATRIZ, AlmacenCatalogo, cargar_matriz
from ..etapa_5.kdtree import KDTree
from ..etapa_6.probabilistic import RUTA_ALERTAS
from .similarity import (BANDAS, EF_BUSQUEDA, EF_CONSTRUCCION, FILAS, HNSW, K, LSH, M, MIN_USUARIOS, N_CONSULTAS,
                         N_HASHES, RUTA_METRICAS, RUTA_SIMILARES, RUTA_VECTORES, SELECCION, ConjuntosProductos,
                         MatrizTFIDF, MinHash, k_mejores, recall_at_k, relacionados_lsh, similares_exactos,
                         usuarios_bloqueados)

SEED = config.SEED
CARPETA_RESULTADOS = config.CARPETA_RESULTADOS / "etapa_7"
PLATAFORMA = {"procesador": platform.processor() or platform.machine(), "python": platform.python_version()}

# Los mismos valores del paso 9 del notebook
REPETICIONES = 100            # bloques de consultas y construcciones
PRESUPUESTO_S = 120           # tiempo máximo por medición de la construcción en el notebook
BLOQUE = 10                   # consultas por bloque
N_RECALL = 200                # consultas por tamaño
RUIDO_CONJUNTOS = 0.05
RUIDO_VECTORES = 0.05

N_FUERZA_BRUTA = 1_000_000    # el mayor tamaño del paso 9
CONSTRUCCIONES = [("MinHash + LSH", 10_000), ("MinHash + LSH", 100_000), ("HNSW", 10_000), ("KD-Tree", 1_000_000)]


# ----- Protocolo del notebook (celdas de los pasos 6 y 9) ------------------------------

def medir(funcion, *args):
    """(segundos, resultado) de funcion(*args), con el recolector de basura desactivado."""
    gc.collect()
    gc.disable()
    try:
        t0 = time.perf_counter()
        resultado = funcion(*args)
        segundos = time.perf_counter() - t0
    finally:
        gc.enable()
    return segundos, resultado


def medir_memoria(funcion, *args):
    """(resultado, memoria actual, memoria pico) en bytes. Solo se rastrea lo que asigna funcion."""
    gc.collect()
    tracemalloc.start()
    resultado = funcion(*args)
    actual, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return resultado, actual, pico


def medir_consultas(funcion, consultas, bloque):
    """Milisegundos por consulta de funcion(i) en REPETICIONES bloques de `bloque` consultas, en orden circular."""
    tiempos = []
    for r in range(REPETICIONES):
        ids = [consultas[(r * bloque + j) % len(consultas)] for j in range(bloque)]
        gc.disable()
        try:
            t0 = time.perf_counter()
            for i in ids:
                funcion(i)
            tiempos.append((time.perf_counter() - t0) / bloque * 1e3)
        finally:
            gc.enable()
    return tiempos


def estadisticos(prefijo, tiempos):
    """Promedio, desviación estándar y repeticiones de una lista de tiempos."""
    return {prefijo: statistics.mean(tiempos), f"{prefijo}_std": statistics.stdev(tiempos) if len(tiempos) > 1 else 0.0,
            f"{prefijo}_repeticiones": len(tiempos)}


def repeticiones_construccion(segundos):
    """Repeticiones de la construcción que caben en PRESUPUESTO_S, entre 1 y REPETICIONES."""
    return max(1, min(REPETICIONES, int(PRESUPUESTO_S // max(segundos, 1e-9))))


def remuestrear_conjuntos(base, n, semilla, ruido=RUIDO_CONJUNTOS):
    """n conjuntos: el de un producto real al azar, con cada usuario reemplazado por uno al azar con probabilidad ruido."""
    rng = np.random.default_rng(semilla)
    origen = rng.integers(0, len(base), n)
    largos = base.tam[origen]
    inicios = np.cumsum(largos) - largos
    pos = np.arange(largos.sum()) - np.repeat(inicios, largos) + np.repeat(base.indptr[origen], largos)
    indices = base.indices[pos].astype(np.int64)
    cambiar = rng.random(len(indices)) < ruido
    indices[cambiar] = rng.integers(0, base.n_usuarios, int(cambiar.sum()))
    fila = np.repeat(np.arange(n), largos)
    orden = np.lexsort((indices, fila))                   # usuarios ordenados dentro de cada conjunto
    fila, indices = fila[orden], indices[orden]
    distinto = np.r_[True, (fila[1:] != fila[:-1]) | (indices[1:] != indices[:-1])]
    fila, indices = fila[distinto], indices[distinto]     # sin usuarios repetidos dentro de un conjunto
    indptr = np.concatenate(([0], np.cumsum(np.bincount(fila, minlength=n))))
    return ConjuntosProductos([f"s{i}" for i in range(n)], indptr, indices, base.n_usuarios)


def remuestrear_vectores(base, n, semilla, ruido=RUIDO_VECTORES):
    """n vectores: uno real al azar más ruido normal en cada coordenada, normalizado a norma 1."""
    rng = np.random.default_rng(semilla)
    X = base[rng.integers(0, len(base), n)] + rng.normal(scale=ruido, size=(n, base.shape[1])).astype(np.float32)
    return (X / np.linalg.norm(X, axis=1, keepdims=True)).astype(np.float32)


# ----- Datos de las etapas 3, 6 y 7 ---------------------------------------------------

def cargar_vectores():
    """(asins, V) de vectores.npz: los vectores con que ejecutar_etapa construyó el HNSW."""
    datos = np.load(RUTA_VECTORES)
    return [str(a) for a in datos["asins"]], datos["V"]


def conjuntos_con_exclusion():
    """Los conjuntos de ejecutar_etapa y del notebook: sin los usuarios bloqueados, con MIN_USUARIOS."""
    return ConjuntosProductos.desde_matriz(RUTA_MATRIZ, usuarios_bloqueados(RUTA_ALERTAS), MIN_USUARIOS)


def muestras_ejecutar_etapa(n_conjuntos, n_vectores, n_consultas=N_CONSULTAS, semilla=SEED):
    """Las dos muestras de ejecutar_etapa, sacadas en el mismo orden y con el mismo generador."""
    rng = np.random.default_rng(semilla)
    conjuntos = rng.choice(n_conjuntos, size=min(n_consultas, n_conjuntos), replace=False)
    vectores = rng.choice(n_vectores, size=min(n_consultas, n_vectores), replace=False)
    return conjuntos.tolist(), vectores.tolist()


def guardar(tabla, nombre):
    tabla = tabla.assign(**PLATAFORMA)
    tabla.to_csv(CARPETA_RESULTADOS / nombre, index=False)
    print(f"Resultados guardados en {nombre}", flush=True)
    return tabla


# ----- 1. Recall de HNSW frente al coseno exacto en TF-IDF ----------------------------

def aciertos_con_empates(respuesta, exacto, coseno):
    """Recall que cuenta como acierto todo vecino con coseno igual o mayor que el k-ésimo exacto.

    En TF-IDF hay productos con el mismo título, la misma categoría y la misma tienda: empatan en
    el coseno y cualquiera de ellos es un vecino exacto correcto.
    """
    if not exacto:
        return np.nan
    corte = exacto[-1][0]
    aciertos = sum(1 for _, j in respuesta if coseno[j] > 0 and coseno[j] >= corte - 1e-6)
    return min(aciertos, len(exacto)) / len(exacto)


def medir_recall_tfidf(k=K):
    _, _, productos = cargar_matriz(RUTA_MATRIZ)
    asins, V = cargar_vectores()
    if asins != productos:
        raise ValueError("vectores.npz no corresponde a la matriz de la etapa 3")
    almacen = AlmacenCatalogo(RUTA_CATALOGO)
    registros = [almacen.leer(asin) or {} for asin in productos]
    almacen.cerrar()
    tfidf = MatrizTFIDF(registros)

    n_conjuntos = json.loads(RUTA_METRICAS.read_text(encoding="utf-8"))["conjuntos"]["productos"]
    _, muestra = muestras_ejecutar_etapa(n_conjuntos, len(V))
    posicion = {a: i for i, a in enumerate(asins)}
    similares = pq.read_table(RUTA_SIMILARES).to_pandas()
    similares = similares[similares["parent_asin"].isin([asins[i] for i in muestra])].sort_values(["parent_asin", "rango"])
    hnsw = {posicion[a]: [(float(d), posicion[v]) for d, v in zip(g["distancia"], g["vecino"])]
            for a, g in similares.groupby("parent_asin")}

    filas, t_tfidf, t_bruta = [], 0.0, 0.0
    for i in muestra:
        t = time.perf_counter()
        exacto_tfidf = tfidf.similares_exactos(i, k)
        t_tfidf += time.perf_counter() - t
        t = time.perf_counter()
        exacto_dim = similares_exactos(V, i, k)
        t_bruta += time.perf_counter() - t
        coseno = tfidf.coseno_contra_todos(i)
        filas.append({
            "hnsw_dim": recall_at_k(hnsw[i], exacto_dim),
            "dim_tfidf": recall_at_k(exacto_dim, exacto_tfidf),
            "hnsw_tfidf": recall_at_k(hnsw[i], exacto_tfidf),
            "dim_tfidf_empates": aciertos_con_empates(exacto_dim, exacto_tfidf, coseno),
            "hnsw_tfidf_empates": aciertos_con_empates(hnsw[i], exacto_tfidf, coseno),
            "coseno_exacto": np.mean([c for c, _ in exacto_tfidf]) if exacto_tfidf else np.nan,
            "coseno_dim": np.mean([coseno[j] for _, j in exacto_dim]) if exacto_tfidf else np.nan,
            "coseno_hnsw": np.mean([coseno[j] for _, j in hnsw[i]]) if exacto_tfidf else np.nan,
        })
    d = pd.DataFrame(filas)
    comunes = {"consultas": len(muestra), "consultas_sin_vecinos": int(d["dim_tfidf"].isna().sum())}
    tabla = pd.DataFrame([
        {"respuesta": "HNSW", "referencia": "KNN exacto en DIM", "recall_at_k": d["hnsw_dim"].mean(),
         "recall_con_empates": np.nan, "coseno_tfidf_medio": d["coseno_hnsw"].mean(), "ms_por_consulta": np.nan},
        {"respuesta": "KNN exacto en DIM", "referencia": "coseno exacto en TF-IDF", "recall_at_k": d["dim_tfidf"].mean(),
         "recall_con_empates": d["dim_tfidf_empates"].mean(), "coseno_tfidf_medio": d["coseno_dim"].mean(),
         "ms_por_consulta": t_bruta / len(muestra) * 1e3},
        {"respuesta": "HNSW", "referencia": "coseno exacto en TF-IDF", "recall_at_k": d["hnsw_tfidf"].mean(),
         "recall_con_empates": d["hnsw_tfidf_empates"].mean(), "coseno_tfidf_medio": d["coseno_hnsw"].mean(),
         "ms_por_consulta": np.nan},
        {"respuesta": "coseno exacto en TF-IDF", "referencia": "coseno exacto en TF-IDF", "recall_at_k": 1.0,
         "recall_con_empates": 1.0, "coseno_tfidf_medio": d["coseno_exacto"].mean(),
         "ms_por_consulta": t_tfidf / len(muestra) * 1e3},
    ]).assign(**comunes)
    print(tabla.round(4).to_string(index=False), flush=True)
    return guardar(tabla, "recall_tfidf.csv")


# ----- 2. Fuerza bruta en float64 frente a float32 ------------------------------------

def similares_float32(X, i, k=K):
    """Los k vectores más cercanos a X[i] sin convertir a float64: entre vectores de norma 1,
    d² = 2 − 2·x·q, así que basta un producto matriz-vector. Lista de (distancia, índice)."""
    producto = X @ X[i]
    producto[i] = -np.inf
    corte = np.argpartition(-producto, k - 1)[:k]
    corte = corte[np.lexsort((corte, -producto[corte]))]
    return [(float(np.sqrt(max(0.0, 2.0 - 2.0 * producto[j]))), int(j)) for j in corte]


def medir_fuerza_bruta(n=N_FUERZA_BRUTA, k=K):
    _, V = cargar_vectores()
    X = remuestrear_vectores(V, n, SEED + n)               # los mismos datos del paso 9
    consultas = np.random.default_rng(SEED).choice(n, size=min(N_RECALL, n), replace=False).tolist()
    filas = []
    for nombre, funcion in [("float64 (knn_fuerza_bruta de la etapa 5)", lambda i: similares_exactos(X, i, k)),
                            ("float32 (producto punto)", lambda i: similares_float32(X, i, k))]:
        tiempos = medir_consultas(funcion, consultas, 1)
        _, _, pico = medir_memoria(funcion, consultas[0])
        filas.append({"implementacion": nombre, "n": n, **estadisticos("consulta_ms", tiempos), "pico_consulta_bytes": pico})
        print(f"{nombre}: {filas[-1]['consulta_ms']:.1f} ms por consulta", flush=True)
    coinciden = np.mean([recall_at_k(similares_float32(X, i, k), similares_exactos(X, i, k)) for i in consultas[:50]])
    tabla = pd.DataFrame(filas).assign(recall_float32_frente_a_float64=coinciden)
    return guardar(tabla, "fuerza_bruta_float32.csv")


# ----- 3. Índice invertido usuario → productos -----------------------------------------

class IndiceInvertido:
    """Productos de cada usuario en CSR: la transpuesta de los conjuntos. Memoria O(usuarios + nnz).

    Una consulta recorre las listas de los usuarios del producto, cuenta en cuántas aparece cada
    candidato (la intersección) y calcula la Jaccard exacta solo con los productos que comparten
    algún usuario. Es exacta: recall 1 frente a la fuerza bruta.
    """

    def __init__(self, conjuntos):
        fila = np.repeat(np.arange(len(conjuntos), dtype=np.int32), conjuntos.tam)
        self.productos = fila[np.argsort(conjuntos.indices, kind="stable")]
        self.indptr = np.concatenate(([0], np.cumsum(np.bincount(conjuntos.indices, minlength=conjuntos.n_usuarios))))
        self.conjuntos = conjuntos
        self.candidatos_evaluados = 0

    @property
    def memoria_bytes(self):
        return self.productos.nbytes + self.indptr.nbytes

    def relacionados(self, i, k=K):
        """Los k productos de mayor Jaccard con i, sin incluir i: lista de (jaccard, índice)."""
        c = self.conjuntos
        u = c.usuarios(i).astype(np.int64)
        largos = self.indptr[u + 1] - self.indptr[u]
        inicios = np.cumsum(largos) - largos
        pos = np.arange(largos.sum()) - np.repeat(inicios, largos) + np.repeat(self.indptr[u], largos)
        candidatos, interseccion = np.unique(self.productos[pos], return_counts=True)
        self.candidatos_evaluados = len(candidatos) - 1
        jaccard = interseccion / (c.tam[candidatos] + c.tam[i] - interseccion)
        jaccard[candidatos == i] = -1.0
        return k_mejores(jaccard, candidatos.astype(np.int64), k)


def medir_indice_invertido(k=K):
    conjuntos = conjuntos_con_exclusion()
    matriz = np.load(RUTA_MATRIZ)
    productos_por_usuario = matriz["indices"].size / len(matriz["usuarios"])
    muestra, _ = muestras_ejecutar_etapa(len(conjuntos), 1)

    def construir_lsh():
        f = MinHash(N_HASHES, SEED).firmas(conjuntos.indptr, conjuntos.indices)
        return f, LSH(f, BANDAS, FILAS)

    t_lsh, (firmas, lsh) = medir(construir_lsh)
    t_indice, indice = medir(IndiceInvertido, conjuntos)
    metodos = [("MinHash + LSH", lambda i: relacionados_lsh(conjuntos, lsh, i, k), lsh, t_lsh,
                firmas.nbytes + lsh.memoria_bytes),
               ("índice invertido", lambda i: indice.relacionados(i, k), indice, t_indice, indice.memoria_bytes),
               ("fuerza bruta", lambda i: conjuntos.fuerza_bruta(i, k), None, np.nan,
                conjuntos.indptr.nbytes + conjuntos.indices.nbytes)]
    exactos = {i: conjuntos.fuerza_bruta(i, k) for i in muestra}
    filas = []
    for nombre, funcion, estructura, t_construccion, memoria in metodos:
        tiempos = medir_consultas(funcion, muestra, BLOQUE)
        recall, candidatos = [], 0
        for i in muestra:
            recall.append(recall_at_k(funcion(i), exactos[i]))
            candidatos += estructura.candidatos_evaluados if estructura is not None else len(conjuntos) - 1
        filas.append({"metodo": nombre, "productos": len(conjuntos), "consultas": len(muestra),
                      "construccion_s": t_construccion, **estadisticos("consulta_ms", tiempos),
                      "recall_at_k": float(np.nanmean(recall)), "candidatos_por_consulta": candidatos / len(muestra),
                      "memoria_bytes": memoria, "productos_por_usuario_matriz": productos_por_usuario})
        print(f"{nombre}: {filas[-1]['consulta_ms']:.2f} ms por consulta, recall {filas[-1]['recall_at_k']:.3f}", flush=True)
    return guardar(pd.DataFrame(filas), "indice_invertido.csv")


# ----- 4. Construcción con 100 repeticiones --------------------------------------------

def medir_construcciones(construcciones=CONSTRUCCIONES):
    """REPETICIONES construcciones de cada (familia, n), con los datos remuestreados del paso 9.

    Compara la media de las 100 con la de las primeras que caben en PRESUPUESTO_S, que son las
    que mide el notebook en la misma máquina.
    """
    base_conjuntos = conjuntos_con_exclusion()
    _, V = cargar_vectores()
    filas = []
    for familia, n in construcciones:
        if familia == "MinHash + LSH":
            datos = remuestrear_conjuntos(base_conjuntos, n, SEED + n)

            def construir():
                f = MinHash(N_HASHES, SEED).firmas(datos.indptr, datos.indices)
                return f, LSH(f, BANDAS, FILAS)
        else:
            datos = remuestrear_vectores(V, n, SEED + n)
            if familia == "HNSW":
                def construir():
                    return HNSW(datos.shape[1], M, EF_CONSTRUCCION, EF_BUSQUEDA, SELECCION, SEED, capacidad=n).agregar_todos(datos)
            else:
                def construir():
                    return KDTree(datos)
        tiempos, t_inicio = [], time.perf_counter()
        for _ in range(REPETICIONES):
            segundos, estructura = medir(construir)
            del estructura
            tiempos.append(segundos)
        presupuesto = repeticiones_construccion(tiempos[0])
        media_presupuesto = statistics.mean(tiempos[:presupuesto])
        media = statistics.mean(tiempos)
        filas.append({"familia": familia, "n": n, **estadisticos("construccion_s", tiempos),
                      "construccion_s_min": min(tiempos), "construccion_s_max": max(tiempos),
                      "coeficiente_variacion": statistics.stdev(tiempos) / media,
                      "repeticiones_presupuesto": presupuesto, "construccion_s_presupuesto": media_presupuesto,
                      "diferencia_relativa_presupuesto": media_presupuesto / media - 1})
        print(f"{familia}, n = {n:,}: {media:.2f} ± {filas[-1]['construccion_s_std']:.2f} s en {REPETICIONES} "
              f"construcciones; con el presupuesto, {presupuesto} ({time.perf_counter() - t_inicio:.0f} s)", flush=True)
        guardar(pd.DataFrame(filas), "construccion_100_repeticiones.csv")
        del datos
    return pd.DataFrame(filas)


PARTES = {"recall": medir_recall_tfidf, "fuerza_bruta": medir_fuerza_bruta,
          "indice_invertido": medir_indice_invertido, "construccion": medir_construcciones}


def main(partes=()):
    CARPETA_RESULTADOS.mkdir(parents=True, exist_ok=True)
    print(f"Plataforma: {PLATAFORMA['procesador']}, Python {PLATAFORMA['python']}, semilla {SEED}", flush=True)
    for nombre in partes or PARTES:
        t0 = time.perf_counter()
        PARTES[nombre]()
        print(f"{nombre}: {time.perf_counter() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
