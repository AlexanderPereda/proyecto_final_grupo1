"""Experimentos de la etapa 8: grillas de parámetros, escalabilidad, referencia, estabilidad y exclusión.

notebooks/etapa8_grafo_dominio.ipynb llama a estas funciones a través de `resultados()`, que guarda
cada tabla en results/etapa_8/ y la reutiliza en las ejecuciones siguientes. El mismo conjunto se
puede medir desde la terminal con `python -m src.etapa_8.benchmark`.

1. Grilla de sensibilidad de PageRank (alpha in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]).
2. Grilla de resolución de Louvain sobre el grafo completo (gamma in [0.5, 1, 2, 5, 10, 20]),
   contrastada con las componentes conexas para exponer el límite de resolución.
3. Escalabilidad (1K, 10K, 50K, 100K y el grafo completo, ~150K nodos):
   - PageRank frente al grado ponderado.
   - Louvain frente a Label Propagation (tiempo, memoria actual y pico, modularidad, comunidades).
   - NetworkX como implementación de referencia.
4. Estabilidad de Louvain y LPA entre semillas (NMI y ARI).
5. Efecto de excluir las órdenes bloqueadas por la etapa 6.

Protocolo de medición (el mismo de las etapas 5 y 7):
- Tiempo con time.perf_counter y el recolector de basura desactivado, sin tracemalloc. La primera
  ejecución fija cuántas repeticiones caben en PRESUPUESTO_S, entre 1 y REPETICIONES.
- Memoria actual y pico con tracemalloc, en una pasada aparte. Revisión técnica 2026-10-28
  (punto 1.4): la versión anterior medía el tiempo con el rastreo activo, que hace más lenta cada
  asignación; Louvain tardaba 88 s en el grafo completo frente a 4 s sin rastreo.
"""

import gc
import statistics
import time
import tracemalloc

import networkx as nx
import numpy as np
import pandas as pd

from .. import config
from ..etapa_3.storage import RUTA_EVENTOS_ETAPA2
from ..etapa_6.probabilistic import RUTA_BLOQUEADAS
from .graph import (
    RUTA_GRAFO,
    GrafoCoCompra,
    componentes_conexas,
    extraer_cocompras,
    grado_ponderado_ranking,
    indice_rand_ajustado,
    informacion_mutua_normalizada,
    label_propagation,
    louvain,
    modularidad,
    pagerank,
)

CARPETA_RESULTADOS_E8 = config.CARPETA_RESULTADOS / "etapa_8"
TAMANOS = (1_000, 10_000, 50_000, 100_000)     # subgrafos; el grafo completo se agrega siempre
REPETICIONES = 100
PRESUPUESTO_S = 120                             # tiempo máximo por medición, como en las etapas 5 y 7
ALFAS = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
GAMMAS = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0)
N_MAX_NETWORKX = 50_000                         # NetworkX guarda un diccionario por nodo y por arista
SEMILLAS_ESTABILIDAD = tuple(range(10))


# ----- Medición ------------------------------------------------------------------

def medir(funcion, *args, **kwargs):
    """(segundos, resultado) de funcion(*args, **kwargs) con el recolector de basura desactivado."""
    gc.collect()
    gc.disable()
    try:
        t0 = time.perf_counter()
        resultado = funcion(*args, **kwargs)
        segundos = time.perf_counter() - t0
    finally:
        gc.enable()
    return segundos, resultado


def medir_memoria(funcion, *args, **kwargs):
    """(resultado, memoria actual, memoria pico) en bytes. Pasada aparte: no se mide el tiempo."""
    gc.collect()
    tracemalloc.start()
    resultado = funcion(*args, **kwargs)
    actual, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return resultado, actual, pico


def repeticiones_que_caben(segundos):
    """Repeticiones que caben en PRESUPUESTO_S según la primera medición, entre 1 y REPETICIONES."""
    return max(1, min(REPETICIONES, int(PRESUPUESTO_S // max(segundos, 1e-9))))


def medir_tiempos(funcion, *args, **kwargs):
    """(tiempos en segundos, resultado de la primera ejecución). La primera fija las repeticiones."""
    segundos, resultado = medir(funcion, *args, **kwargs)
    tiempos = [segundos]
    while len(tiempos) < repeticiones_que_caben(tiempos[0]):
        tiempos.append(medir(funcion, *args, **kwargs)[0])
    return tiempos, resultado


def estadisticos(prefijo, tiempos, escala=1.0):
    """Promedio, desviación estándar, repeticiones y primera medición, multiplicados por `escala`."""
    valores = [t * escala for t in tiempos]
    return {prefijo: statistics.mean(valores),
            f"{prefijo}_std": statistics.stdev(valores) if len(valores) > 1 else 0.0,
            f"{prefijo}_repeticiones": len(valores), f"{prefijo}_primera": valores[0]}


# ----- 1 y 2. Grillas de parámetros -------------------------------------------------

def grilla_alpha(grafo, alfas=ALFAS):
    """Sensibilidad de PageRank al factor de amortiguamiento: iteraciones, scores y producto top 1."""
    filas = []
    for a in alfas:
        pr, iters, conv = pagerank(grafo, alpha=a, tol=1e-6)
        top1 = int(np.argmax(pr))
        filas.append({
            "alpha": a,
            "iteraciones": iters,
            "convergido": conv,
            "max_score": float(pr.max()),
            "min_score": float(pr.min()),
            "std_score": float(pr.std()),
            "top1_asin": grafo.asins[top1],
            "top1_score": float(pr[top1]),
        })
    return pd.DataFrame(filas)


def grilla_resolucion(grafo, gammas=GAMMAS, semilla=config.SEED):
    """Louvain con resolución creciente sobre el grafo completo, contrastado con las componentes conexas.

    Auditoría 2026-10-27: la grilla anterior usaba asins[:10_000] (orden alfabético), un subgrafo
    casi sin aristas (8 488 comunidades de mediana 1 para todo gamma). Ahora se usa el grafo
    completo y se reporta cuántas comunidades caen dentro de cada componente conexa.
    """
    n_cc, _ = componentes_conexas(grafo)
    filas = []
    for g in gammas:
        comm, mod, niveles = louvain(grafo, resolucion=g, semilla=semilla)
        _, counts = np.unique(comm, return_counts=True)
        filas.append({
            "resolucion": g,
            "modularidad": mod,
            "n_comunidades": len(counts),
            "componentes_conexas": n_cc,
            "comunidades_por_componente": len(counts) / n_cc,
            "niveles": niveles,
            "comunidad_max_nodos": int(counts.max()),
            "comunidad_mediana_nodos": float(np.median(counts)),
        })
    return pd.DataFrame(filas)


# ----- 3. Escalabilidad ------------------------------------------------------------

def generar_subgrafos_muestra(grafo, tamanos=TAMANOS, semilla=config.SEED):
    """Genera subgrafos inducidos uniendo componentes conexas completas elegidas al azar.

    Auditoría 2026-10-27: el muestreo anterior (BFS desde un hub y relleno con nodos aleatorios)
    agotaba la componente del hub (~280 nodos) y completaba con nodos sueltos, de modo que los
    subgrafos eran casi vacíos (10 000 nodos con 5 131 aristas) y no comparables con el completo.
    Unir componentes enteras conserva el grado medio del grafo original en cada tamaño.
    """
    subgrafos = {}
    rng = np.random.default_rng(semilla)
    n = grafo.n_nodos
    _, etiquetas = componentes_conexas(grafo)
    orden_componentes = rng.permutation(etiquetas.max() + 1)
    tamanos_comp = np.bincount(etiquetas)

    for tam in tamanos:
        if tam >= n:
            subgrafos[tam] = grafo
            continue

        acumulado = np.cumsum(tamanos_comp[orden_componentes])
        n_comp = int(np.searchsorted(acumulado, tam)) + 1
        elegidas = np.zeros(len(tamanos_comp), dtype=bool)
        elegidas[orden_componentes[:n_comp]] = True
        visitados = np.nonzero(elegidas[etiquetas])[0]

        asins_sub = [grafo.asins[i] for i in visitados]
        subgrafos[tam] = grafo.subgrafo(asins_sub)
        print(f"Subgrafo {tam:,}: {subgrafos[tam].n_nodos:,} nodos, {subgrafos[tam].n_aristas:,} aristas")

    subgrafos[n] = grafo
    return subgrafos


def escala_pagerank(subgrafos):
    """PageRank y grado ponderado por tamaño: tiempo con repeticiones y memoria en una pasada aparte."""
    filas = []
    for _, g in sorted(subgrafos.items()):
        t_pr, _ = medir_tiempos(pagerank, g)
        t_gr, _ = medir_tiempos(grado_ponderado_ranking, g)
        _, act_pr, pico_pr = medir_memoria(pagerank, g)
        _, act_gr, pico_gr = medir_memoria(grado_ponderado_ranking, g)
        fila = {"n": g.n_nodos, "aristas": g.n_aristas}
        fila.update(estadisticos("ms_pagerank", t_pr, 1e3))
        fila.update(estadisticos("ms_grado", t_gr, 1e3))
        fila.update(mem_actual_pr_bytes=act_pr, mem_pico_pr_bytes=pico_pr,
                    mem_actual_grado_bytes=act_gr, mem_pico_grado_bytes=pico_gr)
        filas.append(fila)
        print(f"PageRank n = {g.n_nodos:,}: {fila['ms_pagerank']:.1f} ms ({len(t_pr)} repeticiones)", flush=True)
    return pd.DataFrame(filas)


def escala_comunidades(subgrafos, semilla=config.SEED):
    """Louvain y LPA por tamaño: tiempo con repeticiones, memoria aparte, modularidad y comunidades."""
    filas = []
    for _, g in sorted(subgrafos.items()):
        t_lv, (comm_lv, mod_lv, niveles_lv) = medir_tiempos(louvain, g, semilla=semilla)
        t_lpa, (comm_lpa, iters_lpa, mod_lpa) = medir_tiempos(label_propagation, g, semilla=semilla)
        _, act_lv, pico_lv = medir_memoria(louvain, g, semilla=semilla)
        _, act_lpa, pico_lpa = medir_memoria(label_propagation, g, semilla=semilla)
        fila = {"n": g.n_nodos, "aristas": g.n_aristas}
        fila.update(estadisticos("segundos_louvain", t_lv))
        fila.update(modularidad_louvain=mod_lv, comunidades_louvain=int(len(np.unique(comm_lv))),
                    niveles_louvain=niveles_lv, mem_actual_louvain_bytes=act_lv, mem_pico_louvain_bytes=pico_lv)
        fila.update(estadisticos("segundos_lpa", t_lpa))
        fila.update(modularidad_lpa=mod_lpa, comunidades_lpa=int(len(np.unique(comm_lpa))),
                    iteraciones_lpa=iters_lpa, mem_actual_lpa_bytes=act_lpa, mem_pico_lpa_bytes=pico_lpa)
        filas.append(fila)
        print(f"Comunidades n = {g.n_nodos:,}: Louvain {fila['segundos_louvain']:.2f} s ({len(t_lv)} repeticiones), "
              f"LPA {fila['segundos_lpa']:.2f} s ({len(t_lpa)} repeticiones)", flush=True)
    return pd.DataFrame(filas)


def referencia_networkx(subgrafos, escala_pr, escala_com, semilla=config.SEED, n_max=N_MAX_NETWORKX):
    """PageRank y Louvain de NetworkX con el mismo protocolo, frente a los tiempos propios de las tablas de escala.

    Hasta n_max nodos, porque NetworkX construye un objeto por nodo y por arista. También se
    informa el error máximo entre los dos PageRank y la modularidad de las dos particiones.
    """
    propio_pr = escala_pr.set_index("n")
    propio_com = escala_com.set_index("n")
    filas = []
    for _, g in sorted(subgrafos.items()):
        if g.n_nodos > n_max:
            continue
        G = nx.Graph()
        G.add_nodes_from(range(g.n_nodos))
        G.add_weighted_edges_from(g.aristas())
        t_pr, pr_nx = medir_tiempos(nx.pagerank, G, alpha=0.85, tol=1e-6, weight="weight")
        t_lv, comms_nx = medir_tiempos(nx.community.louvain_communities, G, seed=semilla, weight="weight")
        pr_propio, _, _ = pagerank(g)
        comm_propio, mod_propia, _ = louvain(g, semilla=semilla)
        fila = {"n": g.n_nodos, "aristas": g.n_aristas,
                "ms_pagerank_propio": propio_pr.loc[g.n_nodos, "ms_pagerank"],
                "segundos_louvain_propio": propio_com.loc[g.n_nodos, "segundos_louvain"]}
        fila.update(estadisticos("ms_pagerank_nx", t_pr, 1e3))
        fila.update(estadisticos("segundos_louvain_nx", t_lv))
        fila.update(speedup_pagerank=fila["ms_pagerank_nx"] / fila["ms_pagerank_propio"],
                    speedup_louvain=fila["segundos_louvain_nx"] / fila["segundos_louvain_propio"],
                    error_max_pagerank=float(max(abs(pr_propio[i] - pr_nx[i]) for i in range(g.n_nodos))),
                    modularidad_propia=mod_propia,
                    modularidad_nx=nx.community.modularity(G, comms_nx, weight="weight"),
                    comunidades_propia=int(len(np.unique(comm_propio))), comunidades_nx=len(comms_nx))
        filas.append(fila)
        print(f"NetworkX n = {g.n_nodos:,}: PageRank {fila['ms_pagerank_nx']:.1f} ms, "
              f"Louvain {fila['segundos_louvain_nx']:.2f} s", flush=True)
    return pd.DataFrame(filas)


# ----- 4. Estabilidad -------------------------------------------------------------

def estabilidad_comunidades(grafo, semillas=SEMILLAS_ESTABILIDAD):
    """Louvain y LPA con varias semillas: comunidades, tamaños, modularidad y acuerdo entre particiones.

    Los dos algoritmos visitan los nodos en un orden aleatorio, así que la partición puede cambiar
    con la semilla. Para cada semilla se informan el NMI y el ARI medios frente a las particiones de
    las demás semillas; un algoritmo estable da valores cercanos a 1.
    """
    filas = []
    for nombre, algoritmo in (("louvain", louvain), ("lpa", label_propagation)):
        particiones = []
        for s in semillas:
            segundos, resultado = medir(algoritmo, grafo, semilla=s)
            comm = resultado[0]
            tamanos = np.bincount(comm)
            particiones.append(comm)
            filas.append({"algoritmo": nombre, "semilla": s, "segundos": segundos,
                          "comunidades": int(len(tamanos)), "modularidad": modularidad(grafo, comm),
                          "comunidad_max": int(tamanos.max()), "comunidad_mediana": float(np.median(tamanos)),
                          "comunidad_p90": float(np.percentile(tamanos, 90)),
                          "comunidades_unitarias": int((tamanos == 1).sum())})
        inicio = len(filas) - len(semillas)
        for i in range(len(semillas)):
            otras = [j for j in range(len(semillas)) if j != i]
            filas[inicio + i]["nmi_media"] = float(np.mean(
                [informacion_mutua_normalizada(particiones[i], particiones[j]) for j in otras])) if otras else 1.0
            filas[inicio + i]["ari_media"] = float(np.mean(
                [indice_rand_ajustado(particiones[i], particiones[j]) for j in otras])) if otras else 1.0
        print(f"Estabilidad de {nombre}: {len(semillas)} semillas", flush=True)
    return pd.DataFrame(filas)


# ----- 5. Efecto de la exclusión de la etapa 6 --------------------------------------

def efecto_exclusion_etapa6(ruta_eventos=RUTA_EVENTOS_ETAPA2, ruta_bloqueadas=RUTA_BLOQUEADAS):
    """Compara el grafo con las órdenes bloqueadas por la etapa 6 excluidas y el grafo sin excluirlas.

    Revisión técnica 2026-10-28 (punto 1.6): solo una orden bloqueada con dos o más productos
    distintos puede aportar aristas, así que la tabla también cuenta esas órdenes.
    """
    con = extraer_cocompras(ruta_eventos, ruta_bloqueadas)
    sin = extraer_cocompras(ruta_eventos, None)

    def aristas(g):
        filas = np.repeat(np.arange(g.n_nodos), np.diff(g.indptr))
        asins = np.asarray(g.asins)
        mitad = filas < g.indices
        return dict(zip(zip(asins[filas[mitad]].tolist(), asins[g.indices[mitad]].tolist()), g.data[mitad].tolist()))

    aristas_con, aristas_sin = aristas(con), aristas(sin)
    comunes = aristas_con.keys() & aristas_sin.keys()
    return pd.DataFrame([{
        "compras_bloqueadas_excluidas": con.compras_bloqueadas,
        "ordenes_bloqueadas_excluidas": con.ordenes_bloqueadas_excluidas,
        "ordenes_bloqueadas_con_cocompra": con.ordenes_bloqueadas_con_cocompra,
        "nodos_con_exclusion": con.n_nodos, "nodos_sin_exclusion": sin.n_nodos,
        "aristas_con_exclusion": con.n_aristas, "aristas_sin_exclusion": sin.n_aristas,
        "peso_con_exclusion": con.peso_total, "peso_sin_exclusion": sin.peso_total,
        "nodos_solo_sin_exclusion": len(set(sin.asins) - set(con.asins)),
        "aristas_solo_sin_exclusion": len(aristas_sin.keys() - aristas_con.keys()),
        "aristas_con_peso_distinto": sum(1 for k in comunes if aristas_con[k] != aristas_sin[k]),
    }])


# ----- Ejecución desde la terminal ---------------------------------------------------

def ejecutar_benchmarks(semilla=config.SEED):
    """Mide los experimentos de los pasos 6, 9 y 10 del notebook y guarda las tablas en results/etapa_8/."""
    CARPETA_RESULTADOS_E8.mkdir(parents=True, exist_ok=True)
    grafo = GrafoCoCompra.cargar_npz(RUTA_GRAFO)
    print(f"Grafo base cargado: {grafo.n_nodos:,} nodos, {grafo.n_aristas:,} aristas")
    tablas = {"grilla_alpha": grilla_alpha(grafo), "grilla_resolucion": grilla_resolucion(grafo, semilla=semilla)}
    subgrafos = generar_subgrafos_muestra(grafo, semilla=semilla)
    tablas["escala_pagerank"] = escala_pagerank(subgrafos)
    tablas["escala_comunidades"] = escala_comunidades(subgrafos, semilla=semilla)
    tablas["referencia_networkx"] = referencia_networkx(subgrafos, tablas["escala_pagerank"],
                                                        tablas["escala_comunidades"], semilla=semilla)
    tablas["estabilidad_cocompra"] = estabilidad_comunidades(grafo)
    tablas["exclusion_etapa6"] = efecto_exclusion_etapa6()
    for nombre, tabla in tablas.items():
        tabla.to_csv(CARPETA_RESULTADOS_E8 / f"{nombre}.csv", index=False)
    print("Resultados CSV guardados en results/etapa_8/.")


if __name__ == "__main__":
    ejecutar_benchmarks()
