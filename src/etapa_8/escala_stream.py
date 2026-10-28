"""Escala medida de la etapa 8 sobre el stream real, sin extrapolar (auditoría 2026-10-27).

En lugar de proyectar desde 150 534 nodos, se regenera el stream de la etapa 0 con ventanas
crecientes de reseñas reales (las últimas N del archivo; N = 12 845 712 es el dataset completo) y
se corre la etapa 8 entera en un subproceso por tamaño, midiendo tiempo y memoria pico (RSS).

Diferencias con el pipeline oficial, declaradas:
- El filtro de fraude usa la verdad de referencia del generador (cuentas, tarjetas y dispositivos
  señalados) en lugar del detector de la etapa 6, que no se recorre a esta escala.
- No se aplica la limpieza de la etapa 1: los duplicados no afectan (las órdenes son conjuntos) y
  los registros malformados sin order_id o parent_asin de texto se descartan.

Ejecutar: .venv/bin/python -m src.etapa_8.escala_stream [N ...]
Salida: results/etapa_8/escala_stream.csv (una fila por tamaño, se agrega al terminar cada uno)
"""

import itertools
import json
import resource
import subprocess
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from .. import config
from ..etapa_0.generator import GeneradorEventos, cargar_catalogo, leer_resenas
from .graph import (
    GrafoCoCompra, componentes_conexas, dividir_comunidades_desconectadas, label_propagation, louvain,
    modularidad, pagerank,
)

RUTA_CSV = config.RAIZ / "results" / "etapa_8" / "escala_stream.csv"
TAMANOS = [1_500_000, 3_000_000, 6_000_000, 12_845_712]


def rss_pico_gb():
    """Memoria residente máxima del proceso (macOS reporta bytes; Linux, KB)."""
    pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return pico / 2**30 if sys.platform == "darwin" else pico / 2**20


def grafo_desde_stream(n_resenas, ts_max=None):
    """Genera el stream de la etapa 0 con las últimas n_resenas reseñas y arma el grafo de co-compra.

    Con ts_max (ms) solo se usan reseñas y compras anteriores a ese instante (grafo sin futuro).
    Devuelve (grafo, estadísticas).
    """
    t0 = time.perf_counter()
    generador = GeneradorEventos(cargar_catalogo())
    senalados = generador.listas_senalados()
    cuentas, tarjetas = set(senalados["cuentas"]), set(senalados["tarjetas"])
    dispositivos = set(senalados["dispositivos"])
    resenas = leer_resenas(n_ventana=n_resenas)
    if ts_max is not None:
        resenas = itertools.takewhile(lambda r: r["timestamp"] < ts_max, resenas)

    ordenes = defaultdict(set)
    n_eventos = n_compras = n_fraude = 0
    for r in generador.eventos(resenas):
        n_eventos += 1
        if r.get("event_type") != "purchase":
            continue
        n_compras += 1
        orden, asin = r.get("order_id"), r.get("parent_asin")
        if not isinstance(orden, str) or not isinstance(asin, str):
            continue
        if ts_max is not None and isinstance(r.get("ts"), (int, float)) and r["ts"] >= ts_max:
            continue
        if r.get("user_id") in cuentas or r.get("payment_id") in tarjetas or r.get("device_id") in dispositivos:
            n_fraude += 1
            continue
        ordenes[orden].add(asin)
    stats = dict(eventos=n_eventos, compras=n_compras, compras_fraude_excluidas=n_fraude,
                 segundos_stream=time.perf_counter() - t0)

    t = time.perf_counter()
    aristas = defaultdict(int)
    for prods in ordenes.values():
        if len(prods) < 2:
            continue
        lista = sorted(prods)
        for i in range(len(lista)):
            for j in range(i + 1, len(lista)):
                aristas[(lista[i], lista[j])] += 1
    asins = sorted({a for par in aristas for a in par})
    grafo = GrafoCoCompra.desde_aristas(asins, aristas)
    stats["segundos_grafo"] = time.perf_counter() - t
    return grafo, stats


def medir(n_resenas):
    fila = {"resenas": n_resenas}
    t0 = time.perf_counter()
    grafo, stats = grafo_desde_stream(n_resenas)
    fila.update(eventos=stats["eventos"], compras=stats["compras"],
                compras_fraude_excluidas=stats["compras_fraude_excluidas"], segundos_stream=stats["segundos_stream"])
    fila.update(nodos=grafo.n_nodos, aristas=grafo.n_aristas, peso_total=grafo.peso_total,
                memoria_csr_mb=(grafo.indptr.nbytes + grafo.indices.nbytes + grafo.data.nbytes) / 2**20,
                segundos_grafo=stats["segundos_grafo"])

    t = time.perf_counter()
    pr, iters, _ = pagerank(grafo)
    fila.update(segundos_pagerank=time.perf_counter() - t, iteraciones_pagerank=iters)
    t = time.perf_counter()
    n_cc, etiquetas = componentes_conexas(grafo)
    fila.update(segundos_componentes=time.perf_counter() - t, componentes=n_cc,
                componente_max=int(np.bincount(etiquetas).max()))
    t = time.perf_counter()
    comm, q, niveles = louvain(grafo, garantizar_conexion=False)
    comm, desconectadas = dividir_comunidades_desconectadas(grafo, comm)
    fila.update(segundos_louvain=time.perf_counter() - t, modularidad_louvain=modularidad(grafo, comm),
                comunidades_louvain=int(len(np.unique(comm))), louvain_desconectadas=desconectadas,
                louvain_igual_componentes=bool(len(np.unique(comm)) == n_cc),
                modularidad_componentes=modularidad(grafo, etiquetas))
    t = time.perf_counter()
    comm_lpa, iters_lpa, q_lpa = label_propagation(grafo, garantizar_conexion=False)
    _, desconectadas_lpa = dividir_comunidades_desconectadas(grafo, comm_lpa)
    fila.update(segundos_lpa=time.perf_counter() - t, modularidad_lpa=q_lpa,
                comunidades_lpa=int(len(np.unique(comm_lpa))), lpa_desconectadas=desconectadas_lpa)
    fila.update(rss_pico_gb=rss_pico_gb(), segundos_total=time.perf_counter() - t0)
    return fila


def main(tamanos):
    RUTA_CSV.parent.mkdir(parents=True, exist_ok=True)
    for n in tamanos:
        print(f"--- {n:,} reseñas ---", flush=True)
        salida = subprocess.run([sys.executable, "-m", "src.etapa_8.escala_stream", "--uno", str(n)],
                                capture_output=True, text=True, cwd=config.RAIZ)
        if salida.returncode != 0:
            print(salida.stderr[-3000:], flush=True)
            break
        fila = json.loads(salida.stdout.strip().splitlines()[-1])
        print(json.dumps(fila, indent=1), flush=True)
        previo = pd.read_csv(RUTA_CSV) if RUTA_CSV.exists() else pd.DataFrame()
        if len(previo):
            previo = previo[previo.resenas != n]
        pd.concat([previo, pd.DataFrame([fila])]).sort_values("resenas").to_csv(RUTA_CSV, index=False)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--uno":
        print(json.dumps(medir(int(sys.argv[2]))))
    else:
        main([int(x) for x in sys.argv[1:]] or TAMANOS)
