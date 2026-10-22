"""Experimentos de la etapa 6: Bloom Filter frente a set y Count-Min Sketch frente a dict.

Para cada tamaño n de 1K a 10M se mide el tiempo por operación (promedio y desviación estándar
sobre varias repeticiones), la memoria actual y pico con tracemalloc (en una pasada aparte, sin
medir tiempo), la tasa de falsos positivos del Bloom Filter y el error de frecuencia del CMS.

Cada fila se agrega al CSV en cuanto se calcula, así que si el proceso se corta no se pierde
lo ya medido. Los gráficos se hacen en notebooks/etapa6_experimentos.ipynb.

Salidas en results/: etapa6_bloom.csv, etapa6_cms.csv, etapa6_cms_epsilon.csv, etapa6_plataforma.json

Uso (desde la raíz del proyecto, en segundo plano):
    nohup caffeinate -i .venv/bin/python -u -m scripts.etapa6_experimentos > results/etapa6_experimentos.log 2>&1 &
"""

import csv
import json
import platform
import statistics
import sys
import time
import tracemalloc
from collections import Counter

import numpy as np
import psutil

from src import config
from src.stage6_probabilistic import BloomFilter, CountMinSketch

TAMANOS = [1_000, 10_000, 100_000, 1_000_000, 10_000_000]
REPETICIONES = {1_000: 100, 10_000: 100, 100_000: 20, 1_000_000: 5, 10_000_000: 3}
N_CONSULTAS = 100_000          # claves ausentes para medir la tasa de falsos positivos
P_FP = 0.01
EPSILON, DELTA = 0.001, 0.01
ZIPF_A = 1.2                   # sesgo del stream del CMS: pocas claves muy frecuentes
CARPETA = config.CARPETA_RESULTADOS


def log(mensaje):
    print(f"[{time.strftime('%H:%M:%S')}] {mensaje}", flush=True)


def claves_unicas(n, prefijo, semilla):
    """n claves distintas con forma de identificador de tarjeta."""
    valores = np.random.default_rng(semilla).choice(2 ** 62, size=n, replace=False)
    return [f"{prefijo}_{v:016x}" for v in valores.tolist()]


def flujo_zipf(n, semilla):
    """n eventos sobre un universo de n/10 claves con frecuencias tipo Zipf."""
    universo = max(100, n // 10)
    rng = np.random.default_rng(semilla)
    rangos = rng.zipf(ZIPF_A, size=n)
    rangos = np.where(rangos > universo, rng.integers(1, universo + 1, size=n), rangos)
    return [f"pay_{r:09d}" for r in rangos.tolist()]


def tamano_claves(claves):
    return sum(sys.getsizeof(c) for c in claves)


class CSV:
    def __init__(self, nombre, campos):
        self.ruta = CARPETA / nombre
        self.campos = campos
        with open(self.ruta, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, campos).writeheader()

    def agregar(self, fila):
        with open(self.ruta, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, self.campos).writerow(fila)


def medir(funcion, repeticiones):
    """Tiempos en segundos de `repeticiones` ejecuciones de funcion()."""
    tiempos = []
    for _ in range(repeticiones):
        t0 = time.perf_counter()
        funcion()
        tiempos.append(time.perf_counter() - t0)
    return tiempos


def memoria(funcion):
    """(actual, pico) en bytes de lo que funcion() deja reservado y de lo que reserva como máximo."""
    tracemalloc.start()
    resultado = funcion()
    actual, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del resultado
    return actual, pico


# ----- Bloom Filter frente a set ---------------------------------------------------

def experimento_bloom(salida):
    for n in TAMANOS:
        reps = REPETICIONES[n]
        insertadas = claves_unicas(n, "pay", config.SEED + n)
        ausentes = claves_unicas(N_CONSULTAS, "aus", config.SEED + n + 1)
        consultadas = insertadas[:N_CONSULTAS]

        def construir_bloom():
            bloom = BloomFilter.para(n, P_FP)
            for c in insertadas:
                bloom.agregar(c)
            return bloom

        def construir_set():
            s = set()
            for c in insertadas:
                s.add(c)
            return s

        bloom, exacto = construir_bloom(), construir_set()
        t_bloom = medir(construir_bloom, reps)
        t_set = medir(construir_set, reps)
        t_bloom_q = medir(lambda: [c in bloom for c in ausentes], reps)
        t_set_q = medir(lambda: [c in exacto for c in ausentes], reps)
        falsos_negativos = sum(c not in bloom for c in consultadas)
        falsos_positivos = sum(c in bloom for c in ausentes)
        mem_bloom, pico_bloom = memoria(construir_bloom)
        mem_set, pico_set = memoria(construir_set)

        fila = {"n": n, "repeticiones": reps, "m_bits": bloom.m, "k": bloom.k,
                "insercion_bloom_us": statistics.mean(t_bloom) / n * 1e6,
                "insercion_bloom_std_us": (statistics.stdev(t_bloom) if reps > 1 else 0) / n * 1e6,
                "insercion_set_us": statistics.mean(t_set) / n * 1e6,
                "insercion_set_std_us": (statistics.stdev(t_set) if reps > 1 else 0) / n * 1e6,
                "consulta_bloom_us": statistics.mean(t_bloom_q) / N_CONSULTAS * 1e6,
                "consulta_bloom_std_us": (statistics.stdev(t_bloom_q) if reps > 1 else 0) / N_CONSULTAS * 1e6,
                "consulta_set_us": statistics.mean(t_set_q) / N_CONSULTAS * 1e6,
                "consulta_set_std_us": (statistics.stdev(t_set_q) if reps > 1 else 0) / N_CONSULTAS * 1e6,
                "memoria_bloom_bytes": mem_bloom, "pico_bloom_bytes": pico_bloom,
                "memoria_set_bytes": mem_set, "pico_set_bytes": pico_set,
                "memoria_claves_bytes": tamano_claves(insertadas),
                "falsos_negativos": falsos_negativos,
                "tasa_fp_medida": falsos_positivos / N_CONSULTAS,
                "tasa_fp_teorica": bloom.tasa_fp_teorica()}
        salida.agregar(fila)
        log(f"bloom n={n:,}: inserción {fila['insercion_bloom_us']:.2f} µs vs set {fila['insercion_set_us']:.2f} µs, "
            f"memoria {mem_bloom / 1e6:.2f} MB vs {(mem_set + fila['memoria_claves_bytes']) / 1e6:.1f} MB, "
            f"FP {fila['tasa_fp_medida']:.4f} (teórica {fila['tasa_fp_teorica']:.4f})")
        del bloom, exacto, insertadas, ausentes, consultadas


# ----- Count-Min Sketch frente a dict ----------------------------------------------

def errores_cms(cms, exacto):
    errores = np.array([cms.estimar(c) - f for c, f in exacto.items()], dtype=np.int64)
    top = [c for c, _ in exacto.most_common(10)]
    relativo_top = statistics.mean((cms.estimar(c) - exacto[c]) / exacto[c] for c in top)
    cota = cms.epsilon * cms.total
    return {"error_medio": float(errores.mean()), "error_max": int(errores.max()),
            "subestimaciones": int((errores < 0).sum()), "cota_eps_n": cota,
            "fraccion_sobre_cota": float((errores > cota).mean()), "error_relativo_top10": relativo_top}


def experimento_cms(salida):
    for n in TAMANOS:
        reps = REPETICIONES[n]
        flujo = flujo_zipf(n, config.SEED + n)

        def construir_cms():
            cms = CountMinSketch.para(EPSILON, DELTA)
            for c in flujo:
                cms.agregar(c)
            return cms

        def construir_dict():
            exacto = {}
            for c in flujo:
                exacto[c] = exacto.get(c, 0) + 1
            return exacto

        cms, exacto = construir_cms(), Counter(construir_dict())
        t_cms = medir(construir_cms, reps)
        t_dict = medir(construir_dict, reps)
        consultas = list(exacto)[:N_CONSULTAS]
        t_cms_q = medir(lambda: [cms.estimar(c) for c in consultas], reps)
        t_dict_q = medir(lambda: [exacto[c] for c in consultas], reps)
        mem_cms, pico_cms = memoria(construir_cms)
        mem_dict, pico_dict = memoria(construir_dict)
        claves_distintas = list(exacto)

        fila = {"n": n, "repeticiones": reps, "w": cms.w, "d": cms.d, "claves_distintas": len(exacto),
                "actualizacion_cms_us": statistics.mean(t_cms) / n * 1e6,
                "actualizacion_cms_std_us": (statistics.stdev(t_cms) if reps > 1 else 0) / n * 1e6,
                "actualizacion_dict_us": statistics.mean(t_dict) / n * 1e6,
                "actualizacion_dict_std_us": (statistics.stdev(t_dict) if reps > 1 else 0) / n * 1e6,
                "consulta_cms_us": statistics.mean(t_cms_q) / len(consultas) * 1e6,
                "consulta_dict_us": statistics.mean(t_dict_q) / len(consultas) * 1e6,
                "memoria_cms_bytes": mem_cms, "pico_cms_bytes": pico_cms,
                "memoria_dict_bytes": mem_dict, "pico_dict_bytes": pico_dict,
                "memoria_claves_bytes": tamano_claves(claves_distintas),
                **errores_cms(cms, exacto)}
        salida.agregar(fila)
        log(f"cms n={n:,}: actualización {fila['actualizacion_cms_us']:.2f} µs vs dict {fila['actualizacion_dict_us']:.2f} µs, "
            f"memoria {mem_cms / 1e6:.2f} MB vs {(mem_dict + fila['memoria_claves_bytes']) / 1e6:.1f} MB, "
            f"error medio {fila['error_medio']:.2f} (cota εN {fila['cota_eps_n']:.0f})")
        del cms, exacto, flujo, claves_distintas


def experimento_epsilon(salida, n=1_000_000):
    """Compromiso memoria-error: el mismo stream con distintos ε (δ fijo)."""
    flujo = flujo_zipf(n, config.SEED)
    exacto = Counter(flujo)
    for epsilon in [0.1, 0.03, 0.01, 0.003, 0.001, 0.0003, 0.0001]:
        cms = CountMinSketch.para(epsilon, DELTA)
        for c in flujo:
            cms.agregar(c)
        fila = {"n": n, "epsilon": epsilon, "w": cms.w, "d": cms.d, "memoria_cms_bytes": cms.memoria_bytes,
                **errores_cms(cms, exacto)}
        salida.agregar(fila)
        log(f"ε={epsilon}: w={cms.w}, error medio {fila['error_medio']:.2f}, máximo {fila['error_max']}")


if __name__ == "__main__":
    CARPETA.mkdir(exist_ok=True)
    plataforma = {"sistema": platform.platform(), "procesador": platform.processor(),
                  "nucleos": psutil.cpu_count(logical=True), "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
                  "python": platform.python_version(), "semilla": config.SEED, "p_fp": P_FP,
                  "epsilon": EPSILON, "delta": DELTA, "zipf_a": ZIPF_A, "repeticiones": REPETICIONES}
    (CARPETA / "etapa6_plataforma.json").write_text(json.dumps(plataforma, indent=2), encoding="utf-8")

    t0 = time.perf_counter()
    experimento_bloom(CSV("etapa6_bloom.csv", [
        "n", "repeticiones", "m_bits", "k", "insercion_bloom_us", "insercion_bloom_std_us", "insercion_set_us",
        "insercion_set_std_us", "consulta_bloom_us", "consulta_bloom_std_us", "consulta_set_us",
        "consulta_set_std_us", "memoria_bloom_bytes", "pico_bloom_bytes", "memoria_set_bytes", "pico_set_bytes",
        "memoria_claves_bytes", "falsos_negativos", "tasa_fp_medida", "tasa_fp_teorica"]))
    campos_error = ["error_medio", "error_max", "subestimaciones", "cota_eps_n", "fraccion_sobre_cota",
                    "error_relativo_top10"]
    experimento_cms(CSV("etapa6_cms.csv", [
        "n", "repeticiones", "w", "d", "claves_distintas", "actualizacion_cms_us", "actualizacion_cms_std_us",
        "actualizacion_dict_us", "actualizacion_dict_std_us", "consulta_cms_us", "consulta_dict_us",
        "memoria_cms_bytes", "pico_cms_bytes", "memoria_dict_bytes", "pico_dict_bytes", "memoria_claves_bytes",
        *campos_error]))
    experimento_epsilon(CSV("etapa6_cms_epsilon.csv", ["n", "epsilon", "w", "d", "memoria_cms_bytes", *campos_error]))
    log(f"LISTO en {(time.perf_counter() - t0) / 60:.1f} min")
