"""Ejecuta la etapa 6 sobre el stream completo que entrega la etapa 2.

Lee data/interim/<CATEGORIA>/etapa2_eventos.jsonl en orden de servicio, pasa cada compra y
alerta de pago por el DetectorFraude y escribe:

- etapa6_alertas.jsonl: una línea por operación bloqueada (entrada de la etapa 9).
- etapa6_ordenes_bloqueadas.json: order_id bloqueados (la etapa 8 los excluye del grafo de co-compra).
- etapa6_metricas.json: conteos, tiempos, memoria y evaluación contra la verdad de referencia.
- etapa6_compras.npz: por compra, la estimación del CMS, el conteo exacto y si es fraude
  (análisis del umbral en notebooks/etapa6_filtrado_probabilistico.ipynb).

La verdad de referencia (cuenta del generador en la lista de cuentas fraudulentas) y el conteo
exacto por (tarjeta, ventana) se usan solo para evaluar; el detector no los consulta.

Uso (desde la raíz del proyecto, en segundo plano):
    nohup caffeinate -i .venv/bin/python -u -m scripts.etapa6_detectar > results/etapa6_detectar.log 2>&1 &
"""

import json
import time
import tracemalloc
from collections import Counter

import numpy as np

from src import config
from src.stage3_storage import RUTA_EVENTOS_ETAPA2, leer_eventos
from src.stage6_probabilistic import (RUTA_ALERTAS, RUTA_BLOQUEADAS, DetectorFraude, UMBRAL,
                                      cargar_listas)

RUTA_METRICAS = config.CARPETA_INTERIM / "etapa6_metricas.json"
RUTA_COMPRAS = config.CARPETA_INTERIM / "etapa6_compras.npz"


def log(mensaje, t0):
    print(f"[{time.strftime('%H:%M:%S')} +{(time.perf_counter() - t0) / 60:5.1f} min] {mensaje}", flush=True)


def ejecutar(ruta_eventos=RUTA_EVENTOS_ETAPA2, limite=None):
    t0 = time.perf_counter()
    listas = cargar_listas()
    cuentas_fraude = set(listas["cuentas"])              # solo para evaluar

    tracemalloc.start()
    detector = DetectorFraude(listas)
    memoria_detector = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    ventana_s = detector.cms.ventana_s
    exacto_ventana = Counter()                            # (tarjeta, ventana) -> órdenes distintas: línea base exacta
    ordenes_contadas = set()
    estimado, conteo, fraude = [], [], []                 # una posición por compra
    evaluacion = Counter()
    ordenes_bloqueadas = set()
    eventos = 0
    t_detector = 0.0

    with open(RUTA_ALERTAS, "w", encoding="utf-8") as salida:
        for e in leer_eventos(ruta_eventos):
            eventos += 1
            t = time.perf_counter()
            alerta = detector.procesar(e)
            t_detector += time.perf_counter() - t

            if e.event_type in ("purchase", "payment_alert"):
                es_fraude = e.user_id in cuentas_fraude
                decision = alerta["decision"] if alerta is not None else "aprobar"
                evaluacion[f"{decision}_{'fraude' if es_fraude else 'legitimo'}"] += 1
                evaluacion[f"{e.event_type}_{'fraude' if es_fraude else 'legitimo'}"] += 1
                if e.event_type == "purchase" and e.payment_id is not None:
                    v = int(e.ts // ventana_s)
                    if e.order_id not in ordenes_contadas:
                        ordenes_contadas.add(e.order_id)
                        exacto_ventana[(e.payment_id, v)] += 1
                    estimado.append(detector.cms.estimar(e.payment_id, e.ts))
                    conteo.append(exacto_ventana[(e.payment_id, v)] + exacto_ventana.get((e.payment_id, v - 1), 0))
                    fraude.append(es_fraude)
            if alerta is not None:
                alerta["fraude_real"] = e.user_id in cuentas_fraude
                salida.write(json.dumps(alerta) + "\n")
                if alerta["decision"] == "bloquear" and e.order_id is not None:
                    ordenes_bloqueadas.add(e.order_id)
            if eventos % 1_000_000 == 0:
                log(f"{eventos:,} eventos, {detector.metricas['alertas']:,} alertas", t0)
            if limite is not None and eventos >= limite:
                break

    estimado, conteo = np.array(estimado, dtype=np.int32), np.array(conteo, dtype=np.int32)
    fraude = np.array(fraude, dtype=bool)
    np.savez_compressed(RUTA_COMPRAS, estimado=estimado, conteo=conteo, fraude=fraude)
    RUTA_BLOQUEADAS.write_text(json.dumps(sorted(ordenes_bloqueadas)), encoding="utf-8")

    ev = evaluacion
    alertas_fraude = ev["bloquear_fraude"] + ev["revisar_fraude"]
    alertas_legitimas = ev["bloquear_legitimo"] + ev["revisar_legitimo"]
    total_fraude = alertas_fraude + ev["aprobar_fraude"]
    error = estimado.astype(np.int64) - conteo
    metricas = {
        "eventos": eventos, "detector": dict(detector.metricas), "evaluacion": dict(evaluacion),
        "precision_alertas": alertas_fraude / max(1, alertas_fraude + alertas_legitimas),
        "recall_alertas": alertas_fraude / max(1, total_fraude),
        "precision_bloqueos": ev["bloquear_fraude"] / max(1, ev["bloquear_fraude"] + ev["bloquear_legitimo"]),
        "recall_bloqueos": ev["bloquear_fraude"] / max(1, total_fraude),
        "ordenes_bloqueadas": len(ordenes_bloqueadas),
        "cms_descartados_por_tardios": detector.cms.descartados,
        "cms_error_medio": float(error.mean()), "cms_error_max": int(error.max()),
        "cms_subestimaciones": int((error < 0).sum()),
        "cms_compras_con_error": int((error > 0).sum()), "compras": len(error),
        "tiempo_total_s": time.perf_counter() - t0, "tiempo_detector_s": t_detector,
        "us_por_evento_revisado": t_detector / max(1, detector.metricas["revisados"]) * 1e6,
        "memoria_bloom_bytes": detector.bloom.memoria_bytes, "memoria_cms_bytes": detector.cms.memoria_bytes,
        "memoria_detector_tracemalloc_bytes": memoria_detector,
        "bloom": {"m": detector.bloom.m, "k": detector.bloom.k, "n": detector.bloom.n,
                  "tasa_fp_teorica": detector.bloom.tasa_fp_teorica()},
        "cms": {"w": detector.cms.sketches[0].w, "d": detector.cms.sketches[0].d,
                "ventana_s": ventana_s, "n_ventanas": len(detector.cms.sketches), "umbral": UMBRAL},
        "pares_tarjeta_ventana_exactos": len(exacto_ventana),
    }
    RUTA_METRICAS.write_text(json.dumps(metricas, indent=2), encoding="utf-8")
    log(f"LISTO: {eventos:,} eventos, {detector.metricas['bloquear']:,} bloqueos y "
        f"{detector.metricas['revisar']:,} revisiones; alertas: precisión {metricas['precision_alertas']:.4f}, "
        f"recall {metricas['recall_alertas']:.4f}", t0)
    return metricas


if __name__ == "__main__":
    ejecutar()
