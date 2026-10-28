"""
Suite de benchmarking y evaluación experimental para la Etapa 9 (Servicio y Visualización).

Evalúa:
1. Escalabilidad del Trie (árbol de prefijos):
   - Inserción y consulta de autocompletado para N in [1K, 10K, 50K, 100K, 250K, 500K] términos.
   - Memoria con tracemalloc y latencia por consulta (microsegundos).
2. Escalabilidad de Top-K con MinHeap frente a ordenamiento completo (sorted / np.sort):
   - N in [1K, 10K, 100K, 1M] elementos con K in [5, 10, 20, 50].
   - Verificación de la cota teórica O(N log K) frente a O(N log N).
3. Autocompletado difuso (auditoría 2026-10-27): latencia y acierto de autocompletar_difuso con
   d = 1 y d = 2 sobre consultas con un error de tipeo sintético (sustitución, inserción,
   borrado o transposición).
4. Generación de tablas CSV y gráficos PNG en results/etapa_9/.

Líneas base en su mejor caso (auditoría 2026-10-27): además de sorted() se mide heapq.nlargest
(C) y np.argpartition (O(N), C); el speedup de TopKHeap frente a sorted() no es la comparación
relevante cuando los scores ya están en un arreglo NumPy.
"""

import gc
import heapq
import json
from operator import itemgetter
import os
import platform
import time
import tracemalloc
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.etapa_9.service import TopKHeap, Trie, normalizar_texto

SEED = 42
RAIZ = Path(__file__).resolve().parents[2]
DIR_RESULTADOS = RAIZ / "results" / "etapa_9"
DIR_RESULTADOS.mkdir(parents=True, exist_ok=True)
DIR_INTERIM = RAIZ / "data" / "interim" / "Office_Products"

PLATAFORMA = {
    "procesador": platform.processor() or platform.machine(),
    "python": platform.python_version(),
    "sistema": platform.system(),
}


def medir_memoria(funcion, *args, **kwargs):
    """Mide resultado, memoria actual y memoria pico en bytes usando tracemalloc."""
    gc.collect()
    tracemalloc.start()
    resultado = funcion(*args, **kwargs)
    actual, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return resultado, actual, pico


# ============================================================================
# EXPERIMENTO 1: ESCALABILIDAD DEL TRIE
# ============================================================================

def extraer_vocabulario_catalogo(max_terminos: int = 500_000) -> list[tuple[str, float]]:
    """Extrae palabras y frases del catálogo real para alimentar el Trie."""
    ruta_cat = DIR_INTERIM / "etapa_3" / "catalogo.jsonl"
    vocabulario: dict[str, float] = {}

    if not ruta_cat.exists():
        # Fallback sintético si no existe catálogo
        rng = np.random.default_rng(SEED)
        return [(f"termino_{i}_{rng.integers(1000, 9999)}", float(rng.uniform(1.0, 100.0))) for i in range(max_terminos)]

    with open(ruta_cat, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            title = item.get("title") or ""
            store = item.get("store") or ""
            cat = item.get("categoria_hoja") or ""

            if store:
                vocabulario[store] = vocabulario.get(store, 0.0) + 10.0
            if cat:
                vocabulario[cat] = vocabulario.get(cat, 0.0) + 5.0

            palabras = normalizar_texto(title).split()
            # Unigramas y bigramas
            for w in palabras:
                if len(w) >= 3:
                    vocabulario[w] = vocabulario.get(w, 0.0) + 1.0
            for i in range(len(palabras) - 1):
                bg = f"{palabras[i]} {palabras[i+1]}"
                vocabulario[bg] = vocabulario.get(bg, 0.0) + 0.5

            if len(vocabulario) >= max_terminos:
                break

    items = list(vocabulario.items())[:max_terminos]
    print(f"Vocabulario extraído del catálogo: {len(items):,} términos")
    return items


def benchmark_trie(terminos_pool: list[tuple[str, float]]):
    """Evalúa construcción, memoria y latencia de autocompletado en el Trie."""
    tamanos = [1_000, 10_000, 50_000, 100_000, 250_000]
    if len(terminos_pool) >= 400_000:
        tamanos.append(min(500_000, len(terminos_pool)))

    prefijos_prueba = ["ton", "bro", "pap", "off", "sta", "pri", "not", "ink", "car", "pen"]
    filas = []

    print("\n--- Ejecutando Benchmark de Escalabilidad del Trie ---")
    for n in tamanos:
        muestra = terminos_pool[:n]

        # 1. Medir memoria de inserción
        def _construir():
            t = Trie(capacidad_cache=10)
            for termino, freq in muestra:
                t.insertar(termino, frecuencia=freq)
            return t

        trie_obj, mem_actual, mem_pico = medir_memoria(_construir)

        # 2. Medir tiempo de construcción puro con repeticiones
        gc.collect()
        t0 = time.perf_counter()
        _construir()
        t_construccion = time.perf_counter() - t0

        # 3. Medir latencia de autocompletado (100 consultas por prefijo)
        latencias_us = []
        for pref in prefijos_prueba:
            for _ in range(50):
                t_sub0 = time.perf_counter()
                trie_obj.autocompletar(pref, k=5)
                latencias_us.append((time.perf_counter() - t_sub0) * 1_000_000.0)  # microsegundos

        lat_p50 = float(np.median(latencias_us))
        lat_p95 = float(np.percentile(latencias_us, 95))
        lat_media = float(np.mean(latencias_us))

        print(f"  N = {n:,} | Construcción: {t_construccion*1000:.1f} ms | "
              f"Memoria: {mem_pico/(1024*1024):.2f} MB | Latencia p50: {lat_p50:.2f} µs")

        filas.append({
            "n_terminos": n,
            "tiempo_construccion_ms": t_construccion * 1000.0,
            "tiempo_por_insercion_us": (t_construccion / n) * 1_000_000.0,
            "nodos_radix": trie_obj.n_nodos,
            "memoria_actual_bytes": mem_actual,
            "memoria_pico_bytes": mem_pico,
            "latencia_p50_us": lat_p50,
            "latencia_p95_us": lat_p95,
            "latencia_media_us": lat_media,
        })

    df = pd.DataFrame(filas)
    ruta_csv = DIR_RESULTADOS / "escala_trie.csv"
    df.to_csv(ruta_csv, index=False)
    print(f"Resultados guardados en {ruta_csv}")
    return df


# ============================================================================
# EXPERIMENTO 2: TOP-K HEAP VS ORDENAMIENTO COMPLETO
# ============================================================================

def benchmark_topk():
    """
    Compara la complejidad práctica de TopKHeap (O(N log K)) frente a:
    1. Python sorted() completo: O(N log N)
    2. NumPy np.sort() / np.partition(): baseline optimizado en C
    """
    tamanos_n = [1_000, 10_000, 50_000, 100_000, 500_000, 1_000_000]
    valores_k = [5, 10, 20, 50]
    rng = np.random.default_rng(SEED)

    filas_escala = []
    filas_comparacion = []

    print("\n--- Ejecutando Benchmark TopKHeap frente a Sorting Completo ---")

    for n in tamanos_n:
        # Generar datos sintéticos de prueba: pares (id, score)
        scores = rng.random(size=n).astype(np.float64)
        items = [f"item_{i}" for i in range(n)]
        pares = list(zip(items, scores))

        # Medir ordenamiento completo en Python (sorted)
        gc.collect()
        t0 = time.perf_counter()
        _ = sorted(pares, key=itemgetter(1), reverse=True)[:10]
        t_sorted_ms = (time.perf_counter() - t0) * 1000.0

        # Línea base de la biblioteca estándar (heap acotado implementado en C)
        gc.collect()
        t0 = time.perf_counter()
        _ = heapq.nlargest(10, pares, key=itemgetter(1))
        t_nlargest_ms = (time.perf_counter() - t0) * 1000.0

        # Reemplazos reales en el heap frente a la esperanza K · (H_N - H_K) ≈ K ln(N / K)
        raiz = []
        reemplazos = 0
        for _, sc in pares:
            if len(raiz) < 10:
                heapq.heappush(raiz, sc)
            elif sc > raiz[0]:
                heapq.heapreplace(raiz, sc)
                reemplazos += 1
        reemplazos_teoricos = 10 * (np.log(n) - np.log(10))

        # Medir NumPy np.partition + sort
        gc.collect()
        t0 = time.perf_counter()
        top_idx = np.argpartition(-scores, 10)[:10]
        top_sorted = top_idx[np.argsort(-scores[top_idx])]
        _ = [items[i] for i in top_sorted]
        t_numpy_ms = (time.perf_counter() - t0) * 1000.0

        for k in valores_k:
            # Medir TopKHeap
            def _ejecutar_heap():
                h = TopKHeap(k=k)
                for item, sc in pares:
                    h.agregar(item, sc)
                return h.obtener_top()

            # Medición de memoria
            _, mem_actual, mem_pico = medir_memoria(_ejecutar_heap)

            # Medición de tiempo puro con GC deshabilitado
            gc.collect()
            gc.disable()
            try:
                t0 = time.perf_counter()
                _ejecutar_heap()
                t_heap_ms = (time.perf_counter() - t0) * 1000.0
            finally:
                gc.enable()

            speedup_vs_sorted = t_sorted_ms / t_heap_ms if t_heap_ms > 0 else 1.0

            filas_escala.append({
                "n": n,
                "k": k,
                "ms_topk_heap": t_heap_ms,
                "mem_pico_bytes": mem_pico,
                "ms_sorted": t_sorted_ms,
                "ms_numpy": t_numpy_ms,
                "ms_nlargest": t_nlargest_ms,
                "speedup_vs_sorted": speedup_vs_sorted,
                "speedup_numpy_vs_heap": t_heap_ms / t_numpy_ms if t_numpy_ms > 0 else float("nan"),
                "reemplazos_heap_k10": reemplazos,
                "reemplazos_teoricos_k10": reemplazos_teoricos,
            })

            if k == 10:
                print(f"  N = {n:,} (K={k}) | TopKHeap: {t_heap_ms:.2f} ms | "
                      f"sorted(): {t_sorted_ms:.2f} ms | Speedup: {speedup_vs_sorted:.2f}×")

    df_escala = pd.DataFrame(filas_escala)
    ruta_escala = DIR_RESULTADOS / "escala_topk.csv"
    df_escala.to_csv(ruta_escala, index=False)

    # Tabla resumen de comparación para K=10
    df_comp = df_escala[df_escala["k"] == 10][[
        "n", "ms_topk_heap", "ms_sorted", "ms_nlargest", "ms_numpy", "speedup_vs_sorted",
        "speedup_numpy_vs_heap", "reemplazos_heap_k10", "reemplazos_teoricos_k10"
    ]].copy()
    ruta_comp = DIR_RESULTADOS / "comparacion_sorting.csv"
    df_comp.to_csv(ruta_comp, index=False)

    print(f"Resultados guardados en {ruta_escala} y {ruta_comp}")
    return df_escala, df_comp


# ============================================================================
# GENERACIÓN DE GRÁFICAS TÉCNICAS
# ============================================================================

def generar_graficos(df_trie: pd.DataFrame, df_topk: pd.DataFrame, df_comp: pd.DataFrame):
    """Genera las gráficas PNG de evaluación para el notebook e informe técnico."""
    print("\n--- Generando Gráficas Técnicas en results/etapa_9/ ---")

    # 1. Gráfica Escalabilidad del Trie
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.5))

    ax1.plot(df_trie["n_terminos"], df_trie["tiempo_construccion_ms"], "o-", color="#1f77b4", linewidth=2)
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_title("Trie: Tiempo de Construcción vs N Términos", fontsize=11)
    ax1.set_xlabel("Número de términos indexados ($N$)", fontsize=10)
    ax1.set_ylabel("Tiempo de construcción (ms)", fontsize=10)
    ax1.grid(True, which="both", linestyle="--", alpha=0.5)

    ax2.plot(df_trie["n_terminos"], df_trie["latencia_p50_us"], "s-", color="#2ca02c", label="p50 (Mediana)", linewidth=2)
    ax2.plot(df_trie["n_terminos"], df_trie["latencia_p95_us"], "^--", color="#d62728", label="p95", linewidth=1.5)
    ax2.set_xscale("log")
    ax2.set_title("Trie: Latencia de Autocompletado por Prefijo", fontsize=11)
    ax2.set_xlabel("Número de términos indexados ($N$)", fontsize=10)
    ax2.set_ylabel("Latencia de consulta (µs)", fontsize=10)
    ax2.grid(True, which="both", linestyle="--", alpha=0.5)
    ax2.legend(fontsize=9)

    fig.suptitle("Etapa 9. Rendimiento y Escalabilidad del Trie con Top-K Caché", fontsize=12)
    fig.tight_layout()
    fig.savefig(DIR_RESULTADOS / "escala_trie.png", dpi=150)
    plt.close(fig)

    # 2. Gráfica Escalabilidad TopKHeap vs K
    fig, ax = plt.subplots(figsize=(7, 4.5))
    colores = {5: "#1f77b4", 10: "#ff7f0e", 20: "#2ca02c", 50: "#d62728"}
    for k_val in [5, 10, 20, 50]:
        sub = df_topk[df_topk["k"] == k_val]
        ax.plot(sub["n"], sub["ms_topk_heap"], "o-", label=f"K = {k_val}", color=colores.get(k_val), linewidth=1.8)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_title("TopKHeap: Tiempo de Selección vs Número de Candidatos ($N$)", fontsize=11)
    ax.set_xlabel("Número de candidatos evaluados ($N$)", fontsize=10)
    ax.set_ylabel("Tiempo de ejecución (ms)", fontsize=10)
    ax.grid(True, which="both", linestyle="--", alpha=0.5)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(DIR_RESULTADOS / "escala_topk.png", dpi=150)
    plt.close(fig)

    # 3. Comparación TopKHeap vs sorted() completo
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.5))

    ax1.plot(df_comp["n"], df_comp["ms_sorted"], "o--", label="Python sorted() O(N log N)", color="#d62728", linewidth=2)
    ax1.plot(df_comp["n"], df_comp["ms_topk_heap"], "s-", label="TopKHeap O(N log K)", color="#1f77b4", linewidth=2)
    ax1.plot(df_comp["n"], df_comp["ms_nlargest"], "d:", label="heapq.nlargest (C)", color="#ff7f0e", linewidth=1.5)
    ax1.plot(df_comp["n"], df_comp["ms_numpy"], "^-.", label="NumPy argpartition O(N)", color="#2ca02c", linewidth=1.5)
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_title("Tiempo de Ejecución: Top-10 Selection", fontsize=11)
    ax1.set_xlabel("Número de candidatos ($N$)", fontsize=10)
    ax1.set_ylabel("Tiempo (ms)", fontsize=10)
    ax1.grid(True, which="both", linestyle="--", alpha=0.5)
    ax1.legend(fontsize=9)

    ax2.plot(df_comp["n"], df_comp["speedup_vs_sorted"], "o-", color="#9467bd", linewidth=2)
    ax2.set_xscale("log")
    ax2.set_title("Speedup de TopKHeap frente a sorted() Completo", fontsize=11)
    ax2.set_xlabel("Número de candidatos ($N$)", fontsize=10)
    ax2.set_ylabel("Factor de aceleración (×)", fontsize=10)
    ax2.grid(True, which="both", linestyle="--", alpha=0.5)

    fig.suptitle("Etapa 9. Eficiencia Asintótica de Selección: MinHeap vs Ordenamiento Completo", fontsize=12)
    fig.tight_layout()
    fig.savefig(DIR_RESULTADOS / "comparacion_sorting.png", dpi=150)
    plt.close(fig)

    print("✓ Gráficas técnicas generadas exitosamente en results/etapa_9/.")


def _error_tipeo(texto: str, rng: np.random.Generator) -> str:
    """Aplica un error de edición aleatorio (posición >= 1) para simular un tipeo."""
    letras = "abcdefghijklmnopqrstuvwxyz"
    pos = int(rng.integers(1, len(texto) - 1))
    tipo = int(rng.integers(0, 4))
    if tipo == 0:
        return texto[:pos] + letras[int(rng.integers(0, 26))] + texto[pos + 1:]
    if tipo == 1:
        return texto[:pos] + letras[int(rng.integers(0, 26))] + texto[pos:]
    if tipo == 2:
        return texto[:pos] + texto[pos + 1:]
    return texto[:pos] + texto[pos + 1] + texto[pos] + texto[pos + 2:]


def benchmark_difuso(terminos_pool: list[tuple[str, float]], n_consultas: int = 300):
    """Latencia y acierto del autocompletado difuso sobre el vocabulario del catálogo."""
    trie = Trie(capacidad_cache=10)
    for termino, freq in terminos_pool:
        trie.insertar(termino, frecuencia=freq)
    rng = np.random.default_rng(SEED)
    claves = [normalizar_texto(t) for t, _ in terminos_pool]
    claves = [c for c in claves if len(c) >= 7]
    filas = []
    print("\n--- Ejecutando Benchmark de Autocompletado Difuso ---")
    for d in (1, 2):
        latencias, aciertos, exactas_vacias = [], 0, 0
        for i in range(n_consultas):
            original = claves[int(rng.integers(0, len(claves)))]
            prefijo = original[: int(rng.integers(5, min(len(original), 12) + 1))]
            consulta = _error_tipeo(prefijo, rng)
            exactas_vacias += not trie.autocompletar(consulta, k=5)
            t0 = time.perf_counter()
            res = trie.autocompletar_difuso(consulta, k=5, max_dist=d)
            latencias.append((time.perf_counter() - t0) * 1e6)
            aciertos += any(normalizar_texto(r["termino"]).startswith(prefijo) for r in res)
        filas.append({
            "max_dist": d, "consultas": n_consultas, "terminos": len(trie), "nodos_radix": trie.n_nodos,
            "latencia_p50_us": float(np.median(latencias)), "latencia_p95_us": float(np.percentile(latencias, 95)),
            "latencia_p99_us": float(np.percentile(latencias, 99)),
            "acierto_prefijo_original_top5": aciertos / n_consultas,
            "consultas_sin_resultado_exacto": exactas_vacias / n_consultas,
        })
        print(f"  d = {d}: p50 {filas[-1]['latencia_p50_us']:.0f} µs | p95 {filas[-1]['latencia_p95_us']:.0f} µs | "
              f"acierto {filas[-1]['acierto_prefijo_original_top5']:.1%}")
    df = pd.DataFrame(filas)
    df.to_csv(DIR_RESULTADOS / "difuso_trie.csv", index=False)
    return df


def main():
    print(f"Entorno de benchmarking: {PLATAFORMA}")
    terminos = extraer_vocabulario_catalogo(max_terminos=300_000)
    df_trie = benchmark_trie(terminos)
    benchmark_difuso(terminos)
    df_topk, df_comp = benchmark_topk()
    generar_graficos(df_trie, df_topk, df_comp)
    print("\n=== Benchmark de la Etapa 9 concluido exitosamente ===")


if __name__ == "__main__":
    main()
