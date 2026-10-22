"""Regenera las salidas de las etapas 1 y 2 sin abrir sus notebooks.

Reproduce los pasos que escriben archivos en notebooks/etapa1_preprocesamiento.ipynb (paso 8) y
notebooks/etapa2_cola_prioridades.ipynb (simulación con incidente y aging), con las mismas
clases de src/ y los mismos parámetros. No calcula las gráficas ni las tablas de esos notebooks.

Entrada:  data/interim/<CATEGORIA>/etapa0_lotes.jsonl, listas_senalados.json
Salidas:  data/interim/<CATEGORIA>/etapa1_lotes.jsonl, etapa2_eventos.jsonl

Cada archivo se escribe con extensión .parcial y se renombra al terminar, así que un archivo
con su nombre final siempre está completo. Si etapa1_lotes.jsonl ya existe, la etapa 1 se salta.

Uso (desde la raíz del proyecto, en segundo plano):
    nohup caffeinate -i .venv/bin/python -u -m scripts.regenerar_etapas_1_2 > results/regenerar_etapas_1_2.log 2>&1 &
"""

import json
import time
from dataclasses import asdict

import numpy as np

from src import config
from src.events import Event
from src.stage1_preprocess import Preprocesador
from src.stage2_heap import AGING_S, ColaPrioridadEventos

RUTA_LOTES_ETAPA0 = config.CARPETA_INTERIM / "etapa0_lotes.jsonl"
RUTA_LOTES_ETAPA1 = config.CARPETA_INTERIM / "etapa1_lotes.jsonl"
RUTA_EVENTOS_ETAPA2 = config.CARPETA_INTERIM / "etapa2_eventos.jsonl"

# Parámetros de la simulación de la etapa 2 (iguales a los del notebook)
TICK_S = 60
AGING_CADA_TICKS = 5
DURACION_INCIDENTE_TICKS = 4 * 60
CAPACIDAD_INCIDENTE = 5


def log(mensaje, t0):
    print(f"[{time.strftime('%H:%M:%S')} +{(time.perf_counter() - t0) / 60:5.1f} min] {mensaje}", flush=True)


def etapa1(t0):
    preprocesador = Preprocesador()
    parcial = RUTA_LOTES_ETAPA1.with_suffix(".jsonl.parcial")
    with open(RUTA_LOTES_ETAPA0, encoding="utf-8") as entrada, open(parcial, "w", encoding="utf-8") as salida:
        for linea in entrada:
            lote = json.loads(linea)
            limpio = preprocesador.procesar_lote(lote["registros"])
            salida.write(json.dumps({"lote": lote["lote"], "eventos": [asdict(e) for e in limpio]}) + "\n")
            m = preprocesador.metricas
            if m["lotes"] % 2_000 == 0:
                log(f"etapa 1: {m['lotes']:,} lotes, {m['entrada']:,} registros -> {m['salida']:,} eventos", t0)
    parcial.rename(RUTA_LOTES_ETAPA1)
    m = preprocesador.metricas
    log(f"etapa 1 terminada: entrada {m['entrada']:,}, rechazados {len(preprocesador.rechazados):,}, "
        f"salida {m['salida']:,}", t0)


def leer_eventos_etapa1():
    with open(RUTA_LOTES_ETAPA1, encoding="utf-8") as archivo:
        for linea in archivo:
            for d in json.loads(linea)["eventos"]:
                yield Event(**d)


def etapa2(t0):
    # Primera pasada: ts de cada evento, para la capacidad normal y el incidente
    ts_lista = []
    with open(RUTA_LOTES_ETAPA1, encoding="utf-8") as archivo:
        for linea in archivo:
            ts_lista.extend(d["ts"] for d in json.loads(linea)["eventos"])
    ts_eventos = np.array(ts_lista, dtype=np.float64)
    del ts_lista
    n_eventos = len(ts_eventos)
    T0 = ts_eventos.min()
    llegadas_por_tick = np.bincount(((ts_eventos - T0) // TICK_S).astype(np.int64))
    capacidad_normal = int(np.ceil(llegadas_por_tick.max() * 1.1))
    llegadas_ventana = np.convolve(llegadas_por_tick, np.ones(DURACION_INCIDENTE_TICKS, dtype=int), mode="valid")
    inicio = int(np.argmax(llegadas_ventana))
    incidente = range(inicio, inicio + DURACION_INCIDENTE_TICKS)
    del ts_eventos
    log(f"etapa 2: {n_eventos:,} eventos, capacidad normal {capacidad_normal}/min, "
        f"incidente en los ticks {incidente.start}-{incidente.stop}", t0)

    # Simulación con incidente y aging, la que escribe etapa2_eventos.jsonl en el notebook
    cola = ColaPrioridadEventos(aging_s=AGING_S)
    parcial = RUTA_EVENTOS_ETAPA2.with_suffix(".jsonl.parcial")
    atendidos = tick = 0
    eventos = leer_eventos_etapa1()
    siguiente = next(eventos, None)
    with open(parcial, "w", encoding="utf-8") as salida:
        while siguiente is not None or len(cola):
            inicio_tick = T0 + tick * TICK_S
            ahora = inicio_tick + TICK_S
            while siguiente is not None and siguiente.ts < ahora:
                cola.push(siguiente, max(siguiente.ts, inicio_tick))
                siguiente = next(eventos, None)
            if tick % AGING_CADA_TICKS == 0:
                cola.envejecer(ahora)
            capacidad = CAPACIDAD_INCIDENTE if tick in incidente else capacidad_normal
            ciclo = [cola.pop()[0] for _ in range(min(capacidad, len(cola)))]
            if ciclo:
                salida.write(json.dumps({"ciclo": tick, "eventos": [asdict(e) for e in ciclo]}) + "\n")
                atendidos += len(ciclo)
                if atendidos // 1_000_000 != (atendidos - len(ciclo)) // 1_000_000:
                    log(f"etapa 2: {atendidos:,} eventos atendidos", t0)
            tick += 1
    assert atendidos == n_eventos, "la etapa 2 debe atender todos los eventos"
    parcial.rename(RUTA_EVENTOS_ETAPA2)
    log(f"etapa 2 terminada: {atendidos:,} eventos, ascensos por aging {cola.ascensos:,}", t0)


if __name__ == "__main__":
    t0 = time.perf_counter()
    if RUTA_LOTES_ETAPA1.exists():
        log(f"{RUTA_LOTES_ETAPA1.name} ya existe: se salta la etapa 1", t0)
    else:
        etapa1(t0)
    etapa2(t0)
    log("LISTO", t0)
