"""Grafo de co-reseñas sobre las 12.8 M reseñas reales (auditoría 2026-10-27).

Las co-compras del stream son sintéticas: el generador de la etapa 0 agrega a una orden productos
del mismo grupo crc32(asin) % 500, sin relación con el producto.
La única señal de comportamiento real del dataset son las reseñas: quién reseñó qué y cuándo.
Este script aplica los algoritmos de la etapa 8 a esa señal real:

- Arista u–v con peso w: w usuarios reseñaron v inmediatamente después de u (o al revés).
  Solo pares consecutivos de cada usuario, para que el costo sea O(reseñas) y un usuario con
  900 reseñas no genere 400 000 pares.
- Se compara con el grafo sintético: componentes, PageRank frente al grado, Louvain/LPA y la pureza
  de categoría y de tienda frente a etiquetas permutadas al azar.

El notebook de la etapa 8 llama a main y a grilla_resolucion (paso 10). Desde la terminal:
    python -m src.etapa_8.grafo_resenas_reales [--grilla]
Salida: results/etapa_8/grafo_resenas_reales.json y data/interim/<CATEGORIA>/etapa_8/grafo_resenas.npz;
con --grilla, results/etapa_8/grilla_resolucion_resenas.csv (Louvain con gamma creciente sobre el
grafo real, para ver el efecto del límite de resolución en la pureza temática).
"""

import json
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats import spearmanr

from .. import config
from .escala_stream import rss_pico_gb
from .graph import (
    CARPETA_ETAPA8, GrafoCoCompra, componentes_conexas, dividir_comunidades_desconectadas, label_propagation,
    louvain, modularidad, pagerank,
)

RUTA_SALIDA = config.RAIZ / "results" / "etapa_8" / "grafo_resenas_reales.json"
RUTA_GRAFO_RESENAS = CARPETA_ETAPA8 / "grafo_resenas.npz"
RUTA_CATALOGO = config.CARPETA_INTERIM / "etapa_3" / "catalogo.jsonl"


def cargar_resenas():
    tabla = pq.read_table(config.RUTA_RESENAS, columns=["user_id", "parent_asin", "timestamp"])
    return tabla.to_pandas()


def construir_grafo(df):
    """CSR simétrico con pares consecutivos por usuario, construido con NumPy en O(R log R)."""
    df = df.sort_values(["user_id", "timestamp"], kind="stable")
    codigos, asins = pd.factorize(df["parent_asin"], sort=True)
    usuarios = pd.factorize(df["user_id"])[0]
    mismo = usuarios[1:] == usuarios[:-1]
    u, v = codigos[:-1][mismo], codigos[1:][mismo]
    distinto = u != v
    u, v = u[distinto], v[distinto]
    a, b = np.minimum(u, v), np.maximum(u, v)
    n = len(asins)
    clave = a.astype(np.int64) * n + b
    unicas, pesos = np.unique(clave, return_counts=True)
    a, b = unicas // n, unicas % n
    filas = np.concatenate([a, b])
    cols = np.concatenate([b, a])
    w = np.concatenate([pesos, pesos]).astype(np.float64)
    orden = np.lexsort((cols, filas))
    filas, cols, w = filas[orden], cols[orden], w[orden]
    indptr = np.concatenate(([0], np.cumsum(np.bincount(filas, minlength=n)))).astype(np.int64)
    # Solo los productos con al menos una arista
    grado = np.diff(indptr)
    conservar = grado > 0
    nuevo = np.cumsum(conservar) - 1
    indptr = np.concatenate(([0], np.cumsum(grado[conservar]))).astype(np.int64)
    return GrafoCoCompra(np.asarray(asins)[conservar], indptr, nuevo[cols].astype(np.int32), w)


def pureza(etiquetas, atributo, rng):
    """Fracción de nodos cuyo atributo es el mayoritario de su comunidad, y lo mismo al azar."""
    df = pd.DataFrame({"c": etiquetas, "a": atributo}).dropna()

    def calc(d):
        conteo = d.groupby(["c", "a"]).size()
        return conteo.groupby(level=0).max().sum() / len(d)

    return float(calc(df)), float(calc(df.assign(c=rng.permutation(df["c"].to_numpy()))))


def main():
    t0 = time.perf_counter()
    res = {}
    df = cargar_resenas()
    res["resenas"] = len(df)
    t = time.perf_counter()
    grafo = construir_grafo(df)
    del df
    res.update(nodos=grafo.n_nodos, aristas=grafo.n_aristas, peso_total=grafo.peso_total,
               segundos_grafo=time.perf_counter() - t)
    grafo.guardar_npz(RUTA_GRAFO_RESENAS)
    print(json.dumps(res), flush=True)

    t = time.perf_counter()
    pr, iters, _ = pagerank(grafo)
    k = grafo.grados_ponderados()
    res.update(segundos_pagerank=time.perf_counter() - t, iteraciones_pagerank=iters,
               spearman_pr_grado=float(spearmanr(pr, k).correlation))

    n_cc, cc = componentes_conexas(grafo)
    tam = np.bincount(cc)
    res.update(componentes=n_cc, fraccion_componente_gigante=float(tam.max() / grafo.n_nodos))

    t = time.perf_counter()
    comm, _, niveles = louvain(grafo, garantizar_conexion=False)
    comm, desconectadas = dividir_comunidades_desconectadas(grafo, comm)
    tam_c = np.bincount(comm)
    res.update(segundos_louvain=time.perf_counter() - t, niveles_louvain=niveles,
               modularidad_louvain=modularidad(grafo, comm), comunidades_louvain=int(len(tam_c)),
               louvain_desconectadas_partidas=desconectadas,
               comunidades_louvain_10_o_mas=int((tam_c >= 10).sum()),
               mayor_comunidad_louvain=int(tam_c.max()))
    print(json.dumps(res), flush=True)

    t = time.perf_counter()
    comm_lpa, iters_lpa, q_lpa = label_propagation(grafo, garantizar_conexion=False)
    comm_lpa2, desc_lpa = dividir_comunidades_desconectadas(grafo, comm_lpa)
    res.update(segundos_lpa=time.perf_counter() - t, modularidad_lpa=modularidad(grafo, comm_lpa2),
               comunidades_lpa=int(len(np.unique(comm_lpa2))), lpa_desconectadas_partidas=desc_lpa)

    catalogo = pd.read_json(RUTA_CATALOGO, lines=True)[["parent_asin", "categoria_hoja", "store"]]
    attrs = pd.DataFrame({"parent_asin": grafo.asins}).merge(catalogo, on="parent_asin", how="left")
    rng = np.random.default_rng(config.SEED)
    for nombre, col in (("categoria", "categoria_hoja"), ("tienda", "store")):
        real, azar = pureza(comm, attrs[col].to_numpy(), rng)
        res[f"pureza_{nombre}_louvain"] = real
        res[f"pureza_{nombre}_azar"] = azar
    # Homofilia de aristas: fracción del peso que une productos de la misma categoría / tienda
    filas = np.repeat(np.arange(grafo.n_nodos), np.diff(grafo.indptr))
    for nombre, col in (("categoria", "categoria_hoja"), ("tienda", "store")):
        valores = attrs[col].to_numpy()
        ok = pd.notna(valores[filas]) & pd.notna(valores[grafo.indices])
        iguales = valores[filas][ok] == valores[grafo.indices][ok]
        res[f"homofilia_{nombre}"] = float(np.average(iguales, weights=grafo.data[ok]))
        freq = pd.Series(valores).value_counts(normalize=True)
        res[f"homofilia_{nombre}_azar"] = float((freq ** 2).sum())

    res["rss_pico_gb"] = rss_pico_gb()
    res["segundos_total"] = time.perf_counter() - t0
    RUTA_SALIDA.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2), flush=True)
    return res


GAMMAS = [1.0, 2.0, 5.0, 10.0, 20.0]


def grilla_resolucion():
    grafo = GrafoCoCompra.cargar_npz(RUTA_GRAFO_RESENAS)
    catalogo = pd.read_json(RUTA_CATALOGO, lines=True)[["parent_asin", "categoria_hoja", "store"]]
    attrs = pd.DataFrame({"parent_asin": grafo.asins}).merge(catalogo, on="parent_asin", how="left")
    filas = []
    for gamma in GAMMAS:
        t = time.perf_counter()
        comm, q, niveles = louvain(grafo, resolucion=gamma)
        tam = np.bincount(comm)
        fila = {"resolucion": gamma, "modularidad_gamma": q, "modularidad_gamma1": modularidad(grafo, comm),
                "comunidades": int(len(tam)), "comunidades_10_o_mas": int((tam >= 10).sum()),
                "mayor_comunidad": int(tam.max()), "segundos": time.perf_counter() - t}
        rng = np.random.default_rng(config.SEED)
        for nombre, col in (("categoria", "categoria_hoja"), ("tienda", "store")):
            fila[f"pureza_{nombre}"], fila[f"pureza_{nombre}_azar"] = pureza(comm, attrs[col].to_numpy(), rng)
        filas.append(fila)
        print(fila, flush=True)
        pd.DataFrame(filas).to_csv(config.RAIZ / "results" / "etapa_8" / "grilla_resolucion_resenas.csv", index=False)
    return pd.DataFrame(filas)


if __name__ == "__main__":
    if "--grilla" in sys.argv:
        grilla_resolucion()
    else:
        main()
