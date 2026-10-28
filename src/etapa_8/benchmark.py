"""Script de benchmarking y generación de resultados para la Etapa 8.

Ejecuta:
1. Grilla de sensibilidad de PageRank (alpha in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]).
2. Grilla de resolución de Louvain sobre el grafo completo (gamma in [0.5, 1, 2, 5, 10, 20]),
   contrastada con las componentes conexas para exponer el límite de resolución.
3. Experimentos de escalabilidad (1K, 10K, 50K, 100K y Grafo Completo ~150K):
   - PageRank vs Grado Ponderado (tiempo y memoria tracemalloc).
   - Louvain vs Label Propagation (tiempo, memoria actual/pico, modularidad, # comunidades).
   - Comparación con NetworkX como implementación de referencia.
4. Generación de figuras PNG y archivos CSV en results/etapa_8/.
"""

import json
import time
import tracemalloc
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd

from .. import config
from .graph import (
    CARPETA_ETAPA8,
    GrafoCoCompra,
    RUTA_GRAFO,
    componentes_conexas,
    grado_ponderado_ranking,
    label_propagation,
    louvain,
    modularidad,
    pagerank,
)

CARPETA_RESULTADOS_E8 = config.CARPETA_RESULTADOS / "etapa_8"


def medir_memoria_y_tiempo(func, *args, **kwargs):
    """Mide tiempo de CPU (perf_counter) y memoria pico con tracemalloc."""
    tracemalloc.start()
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    resultado = func(*args, **kwargs)
    t = time.perf_counter() - t0
    actual, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return resultado, t, actual, pico


def generar_subgrafos_muestra(grafo, tamanos=(1_000, 10_000, 50_000, 100_000), semilla=config.SEED):
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


def ejecutar_benchmarks(semilla=config.SEED):
    CARPETA_RESULTADOS_E8.mkdir(parents=True, exist_ok=True)
    grafo = GrafoCoCompra.cargar_npz(RUTA_GRAFO)
    print(f"Grafo base cargado: {grafo.n_nodos:,} nodos, {grafo.n_aristas:,} aristas")

    # ----- 1. Grilla de sensibilidad de PageRank (Alpha) -------------------------
    print("\n--- Ejecutando Grilla Alpha PageRank ---")
    alphas = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    res_alpha = []
    top_asins_por_alpha = {}

    for a in alphas:
        pr, iters, conv = pagerank(grafo, alpha=a, tol=1e-6)
        top10_idx = np.argsort(-pr)[:10]
        top_asins_por_alpha[a] = [grafo.asins[i] for i in top10_idx]
        res_alpha.append({
            "alpha": a,
            "iteraciones": iters,
            "convergido": conv,
            "max_score": float(pr.max()),
            "min_score": float(pr.min()),
            "std_score": float(pr.std()),
            "top1_asin": grafo.asins[top10_idx[0]],
            "top1_score": float(pr[top10_idx[0]])
        })

    df_alpha = pd.DataFrame(res_alpha)
    df_alpha.to_csv(CARPETA_RESULTADOS_E8 / "grilla_alpha.csv", index=False)
    print(df_alpha[["alpha", "iteraciones", "top1_asin", "top1_score"]])

    # ----- 2. Grilla de resolución de Louvain ------------------------------------
    print("\n--- Ejecutando Grilla Resolución Louvain ---")
    # Auditoría 2026-10-27: la grilla anterior usaba asins[:10_000] (orden alfabético), un subgrafo
    # casi sin aristas (8 488 comunidades de mediana 1 para todo gamma). Ahora se usa el grafo
    # completo y se reporta cuántas comunidades caen dentro de cada componente conexa.
    n_cc, etiquetas_cc = componentes_conexas(grafo)
    gammas = [0.5, 1.0, 2.0, 5.0, 10.0, 20.0]
    res_gamma = []

    for g in gammas:
        comm, mod, niveles = louvain(grafo, resolucion=g, semilla=semilla)
        n_comm = len(np.unique(comm))
        _, counts = np.unique(comm, return_counts=True)
        res_gamma.append({
            "resolucion": g,
            "modularidad": mod,
            "n_comunidades": n_comm,
            "componentes_conexas": n_cc,
            "comunidades_por_componente": n_comm / n_cc,
            "niveles": niveles,
            "comunidad_max_nodos": int(counts.max()),
            "comunidad_mediana_nodos": float(np.median(counts))
        })

    df_gamma = pd.DataFrame(res_gamma)
    df_gamma.to_csv(CARPETA_RESULTADOS_E8 / "grilla_resolucion.csv", index=False)
    print(df_gamma)

    # ----- 3. Subgrafos y Escalabilidad (1K, 10K, 50K, 100K, 150K) --------------
    print("\n--- Generando Subgrafos Inducidos para Escala ---")
    subgrafos = generar_subgrafos_muestra(grafo, tamanos=(1_000, 10_000, 50_000, 100_000), semilla=semilla)

    escala_pr = []
    escala_comm = []
    ref_nx = []

    for n_tam, g_sub in sorted(subgrafos.items()):
        print(f"\n--- Evaluando n = {n_tam:,} (|V|={g_sub.n_nodos:,}, |E|={g_sub.n_aristas:,}) ---")

        # PageRank y Grado Ponderado
        _, t_pr, mem_act_pr, mem_pico_pr = medir_memoria_y_tiempo(pagerank, g_sub, alpha=0.85, tol=1e-6)
        _, t_grado, mem_act_gr, mem_pico_gr = medir_memoria_y_tiempo(grado_ponderado_ranking, g_sub)

        escala_pr.append({
            "n": g_sub.n_nodos,
            "aristas": g_sub.n_aristas,
            "ms_pagerank": t_pr * 1e3,
            "ms_grado": t_grado * 1e3,
            "mem_actual_pr_bytes": mem_act_pr,
            "mem_pico_pr_bytes": mem_pico_pr,
            "mem_actual_grado_bytes": mem_act_gr,
            "mem_pico_grado_bytes": mem_pico_gr
        })

        # Louvain y LPA
        (comm_louv, mod_louv, niveles_louv), t_louv, mem_act_l, mem_pico_l = medir_memoria_y_tiempo(
            louvain, g_sub, resolucion=1.0, semilla=semilla
        )
        (comm_lpa, iters_lpa, mod_lpa), t_lpa, mem_act_lpa, mem_pico_lpa = medir_memoria_y_tiempo(
            label_propagation, g_sub, semilla=semilla
        )

        escala_comm.append({
            "n": g_sub.n_nodos,
            "aristas": g_sub.n_aristas,
            "segundos_louvain": t_louv,
            "modularidad_louvain": mod_louv,
            "comunidades_louvain": len(np.unique(comm_louv)),
            "niveles_louvain": niveles_louv,
            "mem_actual_louvain_bytes": mem_act_l,
            "mem_pico_louvain_bytes": mem_pico_l,
            "segundos_lpa": t_lpa,
            "modularidad_lpa": mod_lpa,
            "comunidades_lpa": len(np.unique(comm_lpa)),
            "iteraciones_lpa": iters_lpa,
            "mem_actual_lpa_bytes": mem_act_lpa,
            "mem_pico_lpa_bytes": mem_pico_lpa
        })

        # NetworkX comparación (hasta 50K por tiempo de construcción de objetos NetworkX)
        if g_sub.n_nodos <= 50_000:
            print(f"  Ejecutando NetworkX de referencia para {g_sub.n_nodos:,}...")
            # Construir grafo NetworkX
            G_nx = nx.Graph()
            for u, v, w in g_sub.aristas():
                G_nx.add_edge(u, v, weight=w)

            # PageRank NX
            t_nx_pr0 = time.perf_counter()
            pr_nx = nx.pagerank(G_nx, alpha=0.85, tol=1e-6, weight="weight")
            t_nx_pr = time.perf_counter() - t_nx_pr0

            # Louvain NX
            t_nx_l0 = time.perf_counter()
            comms_nx_l = list(nx.community.louvain_communities(G_nx, seed=semilla, weight="weight"))
            t_nx_l = time.perf_counter() - t_nx_l0
            mod_nx_l = nx.community.modularity(G_nx, comms_nx_l, weight="weight")

            ref_nx.append({
                "n": g_sub.n_nodos,
                "aristas": g_sub.n_aristas,
                "ms_pagerank_propio": t_pr * 1e3,
                "ms_pagerank_nx": t_nx_pr * 1e3,
                "speedup_pagerank": (t_nx_pr * 1e3) / max(t_pr * 1e3, 1e-4),
                "segundos_louvain_propio": t_louv,
                "segundos_louvain_nx": t_nx_l,
                "speedup_louvain": t_nx_l / max(t_louv, 1e-4),
                "modularidad_propia": mod_louv,
                "modularidad_nx": mod_nx_l,
                "comunidades_propia": len(np.unique(comm_louv)),
                "comunidades_nx": len(comms_nx_l)
            })

    # Guardar CSVs
    pd.DataFrame(escala_pr).to_csv(CARPETA_RESULTADOS_E8 / "escala_pagerank.csv", index=False)
    pd.DataFrame(escala_comm).to_csv(CARPETA_RESULTADOS_E8 / "escala_comunidades.csv", index=False)
    pd.DataFrame(ref_nx).to_csv(CARPETA_RESULTADOS_E8 / "referencia_networkx.csv", index=False)

    print("\nResultados CSV guardados con éxito en results/etapa_8/.")

    # ----- 4. Generación de Gráficas Técnicas -------------------------------------
    generar_graficas(grafo, df_alpha, pd.DataFrame(escala_pr), pd.DataFrame(escala_comm), pd.DataFrame(ref_nx))


def generar_graficas(grafo, df_alpha, df_pr, df_comm, df_ref):
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")

    # 1. Distribución de Grados (Ley de Potencias)
    grados = grafo.grados_ponderados()
    grados_pos = grados[grados > 0]
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    conteo = Counter(grados_pos.astype(int))
    x_val = sorted(conteo.keys())
    y_val = [conteo[k] for k in x_val]
    ax.loglog(x_val, y_val, "o", markersize=3, color="#1f77b4", alpha=0.7)
    ax.set_title("Distribución de Grado Ponderado en Grafo de Co-compra (Escala Log-Log)", fontsize=11, fontweight="bold")
    ax.set_xlabel("Grado ponderado ($k_i = \sum w_{ij}$)", fontsize=10)
    ax.set_ylabel("Frecuencia (# de productos)", fontsize=10)
    plt.tight_layout()
    plt.savefig(CARPETA_RESULTADOS_E8 / "distribucion_grados.png")
    plt.close()

    # 2. PageRank vs Grado Ponderado (Discrepancia y Centralidad)
    pr_scores, _, _ = pagerank(grafo, alpha=0.85, tol=1e-6)
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    ax.scatter(grados[:10_000], pr_scores[:10_000], alpha=0.3, s=8, color="#2ca02c")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_title("PageRank vs Grado Ponderado (Muestra 10K productos)", fontsize=11, fontweight="bold")
    ax.set_xlabel("Grado Ponderado (Local)", fontsize=10)
    ax.set_ylabel("Score de PageRank (Global)", fontsize=10)
    plt.tight_layout()
    plt.savefig(CARPETA_RESULTADOS_E8 / "pagerank_vs_grado.png")
    plt.close()

    # 3. Escalabilidad de PageRank frente a Grado
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    ax.plot(df_pr["n"], df_pr["ms_pagerank"], "o-", label="PageRank (Power Iteration, CSR)", color="#d62728", lw=1.8)
    ax.plot(df_pr["n"], df_pr["ms_grado"], "s--", label="Grado Ponderado (Línea Base)", color="#7f7f7f", lw=1.5)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_title("Escalabilidad Temporal: PageRank vs Grado Ponderado", fontsize=11, fontweight="bold")
    ax.set_xlabel("Número de nodos (|V|)", fontsize=10)
    ax.set_ylabel("Tiempo de ejecución (ms)", fontsize=10)
    ax.legend(frameon=True)
    plt.tight_layout()
    plt.savefig(CARPETA_RESULTADOS_E8 / "escala_pagerank.png")
    plt.close()

    # 4. Escalabilidad de Louvain vs LPA
    fig, ax1 = plt.subplots(figsize=(7, 4.5), dpi=150)
    ax1.plot(df_comm["n"], df_comm["segundos_louvain"], "o-", label="Louvain (Tiempo s)", color="#1f77b4", lw=1.8)
    ax1.plot(df_comm["n"], df_comm["segundos_lpa"], "s--", label="LPA (Tiempo s)", color="#ff7f0e", lw=1.5)
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_title("Escalabilidad Temporal y Calidad: Louvain vs LPA", fontsize=11, fontweight="bold")
    ax1.set_xlabel("Número de nodos (|V|)", fontsize=10)
    ax1.set_ylabel("Tiempo de ejecución (segundos)", fontsize=10)
    ax1.legend(loc="upper left", frameon=True)
    plt.tight_layout()
    plt.savefig(CARPETA_RESULTADOS_E8 / "escala_comunidades.png")
    plt.close()

    # 5. Comparación Propia vs NetworkX (Speedup)
    if len(df_ref) > 0:
        fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
        ax.plot(df_ref["n"], df_ref["speedup_pagerank"], "o-", label="Speedup PageRank Propio vs NetworkX", color="#2ca02c", lw=1.8)
        ax.plot(df_ref["n"], df_ref["speedup_louvain"], "s-", label="Speedup Louvain Propio vs NetworkX", color="#1f77b4", lw=1.8)
        ax.axhline(1.0, color="gray", linestyle="--", alpha=0.6)
        ax.set_xscale("log")
        ax.set_title("Speedup de Implementaciones Propias en CSR frente a NetworkX", fontsize=11, fontweight="bold")
        ax.set_xlabel("Número de nodos (|V|)", fontsize=10)
        ax.set_ylabel("Aceleración ($T_{NetworkX} / T_{Propio}$)", fontsize=10)
        ax.legend(frameon=True)
        plt.tight_layout()
        plt.savefig(CARPETA_RESULTADOS_E8 / "referencia_networkx.png")
        plt.close()

    print("Gráficas técnicas PNG generadas en results/etapa_8/.")


if __name__ == "__main__":
    ejecutar_benchmarks()
