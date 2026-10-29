"""
Experimentos de la etapa 9 (servicio y visualización).

notebooks/etapa9_servicio_visualizacion.ipynb llama a estas funciones a través de `resultados()`,
que guarda cada tabla en results/etapa_9/ y la reutiliza en las ejecuciones siguientes. También se
pueden medir desde la terminal con `python -m src.etapa_9.benchmark`.

1. Escalabilidad del Trie radix: construcción, memoria y latencia de autocompletado para
   N in [1K, 10K, 50K, 100K, 250K] términos del catálogo real.
2. Top-K con MinHeap frente al ordenamiento completo: N in [1K, 10K, 50K, 100K, 500K, 1M] y
   K in [5, 10, 20, 50]. Líneas base en su mejor caso (auditoría 2026-10-27): además de sorted()
   se mide heapq.nlargest (C) y np.argpartition (O(N), C); el speedup frente a sorted() no es la
   comparación relevante cuando los scores ya están en un arreglo NumPy.
3. Autocompletado difuso (auditoría 2026-10-27): latencia y acierto con d = 1 y d = 2 sobre
   consultas con un error de tipeo sintético (sustitución, inserción, borrado o transposición).

Protocolo (el de las etapas 5, 7 y 8, funciones de src/etapa_8/benchmark.py): tiempo con
time.perf_counter y el recolector de basura desactivado; la primera medición fija cuántas
repeticiones caben en PRESUPUESTO_S, entre 1 y REPETICIONES; memoria con tracemalloc en una pasada
aparte. Revisión técnica 2026-10-28: la versión anterior medía cada tiempo una sola vez.
"""

import gc
import heapq
import json
import time
from operator import itemgetter

import numpy as np
import pandas as pd

from .. import config
from ..etapa_8.benchmark import REPETICIONES, estadisticos, medir_memoria, medir_tiempos
from .service import TopKHeap, Trie, normalizar_texto

DIR_RESULTADOS = config.CARPETA_RESULTADOS / "etapa_9"
TAMANOS_TRIE = (1_000, 10_000, 50_000, 100_000, 250_000)
PREFIJOS_PRUEBA = ("ton", "bro", "pap", "off", "sta", "pri", "not", "ink", "car", "pen")
TAMANOS_TOPK = (1_000, 10_000, 50_000, 100_000, 500_000, 1_000_000)
VALORES_K = (5, 10, 20, 50)


# ============================================================================
# EXPERIMENTO 1: ESCALABILIDAD DEL TRIE
# ============================================================================

def extraer_vocabulario_catalogo(max_terminos: int = 300_000) -> list[tuple[str, float]]:
    """Extrae palabras y frases del catálogo real para alimentar el Trie."""
    ruta_cat = config.CARPETA_INTERIM / "etapa_3" / "catalogo.jsonl"
    vocabulario: dict[str, float] = {}

    if not ruta_cat.exists():
        # Fallback sintético si no existe catálogo
        rng = np.random.default_rng(config.SEED)
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


def benchmark_trie(terminos_pool: list[tuple[str, float]], tamanos=TAMANOS_TRIE) -> pd.DataFrame:
    """Construcción con repeticiones, memoria en una pasada aparte y latencia de autocompletado.

    La latencia se mide en REPETICIONES bloques; cada bloque consulta una vez los diez prefijos de
    prueba. Se informan el promedio por consulta entre bloques y los percentiles de las consultas.
    """
    filas = []
    print("\n--- Ejecutando Benchmark de Escalabilidad del Trie ---")
    for n in tamanos:
        muestra = terminos_pool[:n]

        def construir():
            t = Trie(capacidad_cache=10)
            for termino, freq in muestra:
                t.insertar(termino, frecuencia=freq)
            return t

        tiempos, trie_obj = medir_tiempos(construir)
        _, mem_actual, mem_pico = medir_memoria(construir)

        bloques, individuales = [], []
        for _ in range(REPETICIONES):
            gc.disable()
            try:
                t0 = time.perf_counter()
                for pref in PREFIJOS_PRUEBA:
                    t1 = time.perf_counter()
                    trie_obj.autocompletar(pref, k=5)
                    individuales.append((time.perf_counter() - t1) * 1e6)
                bloques.append((time.perf_counter() - t0) / len(PREFIJOS_PRUEBA) * 1e6)
            finally:
                gc.enable()

        fila = {"n_terminos": n, "nodos_radix": trie_obj.n_nodos}
        fila.update(estadisticos("tiempo_construccion_ms", tiempos, 1e3))
        fila["tiempo_por_insercion_us"] = fila["tiempo_construccion_ms"] * 1e3 / n
        fila.update(memoria_actual_bytes=mem_actual, memoria_pico_bytes=mem_pico)
        fila.update(estadisticos("latencia_media_us", bloques))
        fila.update(latencia_p50_us=float(np.median(individuales)),
                    latencia_p95_us=float(np.percentile(individuales, 95)))
        filas.append(fila)
        print(f"  N = {n:,} | Construcción: {fila['tiempo_construccion_ms']:.1f} ms ({len(tiempos)} repeticiones) | "
              f"Memoria pico: {mem_pico / 2**20:.2f} MB | Latencia p50: {fila['latencia_p50_us']:.2f} µs")
    return pd.DataFrame(filas)


# ============================================================================
# EXPERIMENTO 2: TOP-K HEAP VS ORDENAMIENTO COMPLETO
# ============================================================================

def benchmark_topk(tamanos=TAMANOS_TOPK, valores_k=VALORES_K) -> pd.DataFrame:
    """
    Compara la complejidad práctica de TopKHeap (O(N log K)) frente a:
    1. Python sorted() completo: O(N log N)
    2. heapq.nlargest, el heap acotado de la biblioteca estándar en C: O(N log K)
    3. NumPy np.argpartition: O(N) en C
    Los scores son floats de Python, como los del servicio. Las líneas base se miden con K = 10.
    """
    rng = np.random.default_rng(config.SEED)
    filas = []
    print("\n--- Ejecutando Benchmark TopKHeap frente a Sorting Completo ---")

    for n in tamanos:
        scores = rng.random(size=n)
        items = [f"item_{i}" for i in range(n)]
        pares = list(zip(items, scores.tolist()))

        def numpy_top10():
            top_idx = np.argpartition(-scores, 10)[:10]
            return [items[i] for i in top_idx[np.argsort(-scores[top_idx])]]

        t_sorted, _ = medir_tiempos(lambda: sorted(pares, key=itemgetter(1), reverse=True)[:10])
        t_nlargest, _ = medir_tiempos(heapq.nlargest, 10, pares, key=itemgetter(1))
        t_numpy, _ = medir_tiempos(numpy_top10)

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

        for k in valores_k:
            def top_heap():
                h = TopKHeap(k=k)
                for item, sc in pares:
                    h.agregar(item, sc)
                return h.obtener_top()

            t_heap, _ = medir_tiempos(top_heap)
            _, _, mem_pico = medir_memoria(top_heap)

            fila = {"n": n, "k": k}
            fila.update(estadisticos("ms_topk_heap", t_heap, 1e3))
            fila["mem_pico_bytes"] = mem_pico
            fila.update(estadisticos("ms_sorted", t_sorted, 1e3))
            fila.update(estadisticos("ms_nlargest", t_nlargest, 1e3))
            fila.update(estadisticos("ms_numpy", t_numpy, 1e3))
            fila.update(speedup_vs_sorted=fila["ms_sorted"] / fila["ms_topk_heap"],
                        speedup_numpy_vs_heap=fila["ms_topk_heap"] / fila["ms_numpy"],
                        reemplazos_heap_k10=reemplazos, reemplazos_teoricos_k10=reemplazos_teoricos)
            filas.append(fila)

            if k == 10:
                print(f"  N = {n:,} (K={k}) | TopKHeap: {fila['ms_topk_heap']:.2f} ms ({len(t_heap)} repeticiones) | "
                      f"sorted(): {fila['ms_sorted']:.2f} ms | Speedup: {fila['speedup_vs_sorted']:.2f}×")
    return pd.DataFrame(filas)


# ============================================================================
# EXPERIMENTO 3: AUTOCOMPLETADO DIFUSO
# ============================================================================

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


def benchmark_difuso(terminos_pool: list[tuple[str, float]], n_consultas: int = 300) -> pd.DataFrame:
    """Latencia y acierto del autocompletado difuso sobre el vocabulario del catálogo."""
    trie = Trie(capacidad_cache=10)
    for termino, freq in terminos_pool:
        trie.insertar(termino, frecuencia=freq)
    rng = np.random.default_rng(config.SEED)
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
    return pd.DataFrame(filas)


def main():
    """Mide los tres experimentos y guarda las tablas en results/etapa_9/."""
    DIR_RESULTADOS.mkdir(parents=True, exist_ok=True)
    terminos = extraer_vocabulario_catalogo(max_terminos=300_000)
    benchmark_trie(terminos).to_csv(DIR_RESULTADOS / "escala_trie.csv", index=False)
    benchmark_difuso(terminos).to_csv(DIR_RESULTADOS / "difuso_trie.csv", index=False)
    escala_topk = benchmark_topk()
    escala_topk.to_csv(DIR_RESULTADOS / "escala_topk.csv", index=False)
    escala_topk[escala_topk["k"] == 10].to_csv(DIR_RESULTADOS / "comparacion_sorting.csv", index=False)
    print("\n=== Benchmark de la Etapa 9 concluido exitosamente ===")


if __name__ == "__main__":
    main()
