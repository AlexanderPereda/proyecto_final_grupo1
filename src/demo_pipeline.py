"""Demo de punta a punta: un lote pequeño del stream atraviesa las diez etapas.

Las etapas se conectan en lote por archivos: cada una lee la salida completa de la anterior en
data/interim/<CATEGORIA>/ y escribe la suya. La demo sigue un lote pequeño de esa cadena:

- Etapas 0 a 2, en vivo. Toma N_REGISTROS registros crudos del stream que emitió la etapa 0
  (etapa0_lotes.jsonl), desde la posición INICIO, y los pasa por las estructuras reales: la
  ColaCircular con backpressure, el Preprocesador y la ColaPrioridadEventos con aging. Productor y
  consumidor se alternan en un solo hilo, como en src/trace.py, para que la traza sea reproducible.
- Etapas 3 y 6, en vivo. Los eventos que atiende la etapa 2, en su orden de servicio, actualizan
  EtapaAlmacenamiento y pasan por DetectorFraude.
- Etapas 4, 5, 7 y 8. Sus índices se construyen con el catálogo o el stream completos, así que la
  demo los consulta para los productos del lote.
- Etapa 9. ServicioEcommerce carga las salidas de las etapas 3 a 8 y responde.

notebooks/demo_pipeline.ipynb muestra la traza. Este módulo tiene la parte mecánica: leer el lote,
recorrer las etapas 0 a 2 y buscar los eventos en las salidas guardadas, para comparar lo que hace
la demo con lo que hizo el pipeline completo.
"""

import json
from collections import Counter, defaultdict

from . import config
from .etapa_0.queue import ColaCircular
from .etapa_1.preprocess import Preprocesador
from .etapa_2.heap import AGING_S, NOMBRE_CLASE, ColaPrioridadEventos
from .etapa_3.storage import RUTA_EVENTOS_ETAPA2
from .etapa_6.probabilistic import RUTA_ALERTAS

RUTA_LOTES_ETAPA0 = config.CARPETA_INTERIM / "etapa0_lotes.jsonl"
RUTA_LOTES_ETAPA1 = config.CARPETA_INTERIM / "etapa1_lotes.jsonl"

# Lote de la demo: posiciones 2 150 a 2 349 del stream de Office_Products. Tiene una ráfaga de
# fraude de una cuenta señalada (órdenes o00000292 a o00000301), órdenes legítimas de dos
# productos, duplicados exactos y un registro con un tipo de evento desconocido.
INICIO = 2_150
N_REGISTROS = 200
EVENTO_COMPRA = "e000001981"     # compra legítima de la orden o00000313, que tiene dos productos
EVENTO_FRAUDE = "e000001899"     # primera compra de la ráfaga de la cuenta señalada

# Alternancia de productor y consumidor, con los valores de src/trace.py
CAPACIDAD_COLA = 32              # capacidad de la ColaCircular (etapa 0)
TAM_LOTE = 16                    # registros por micro-lote
CAPACIDAD_SERVICIO = 12          # eventos que la etapa 2 atiende por micro-lote
AGING_CADA = 2                   # pasada de aging cada 2 micro-lotes


def leer_registros_etapa0(inicio=INICIO, n=N_REGISTROS, ruta=RUTA_LOTES_ETAPA0):
    """Registros crudos de las posiciones [inicio, inicio + n) del stream que emitió la etapa 0."""
    registros = []
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            registros.extend(json.loads(linea)["registros"])
            if len(registros) >= inicio + n:
                break
    return registros[inicio:inicio + n]


def recorrer_etapas_0_a_2(registros, inicio=INICIO, capacidad_cola=CAPACIDAD_COLA, tam_lote=TAM_LOTE,
                          capacidad_servicio=CAPACIDAD_SERVICIO, aging_cada=AGING_CADA):
    """Pasa los registros por la ColaCircular (etapa 0), el Preprocesador (etapa 1) y la
    ColaPrioridadEventos (etapa 2).

    El productor encola los registros en orden. Si la cola está llena, espera a que el consumidor
    retire un micro-lote de hasta tam_lote registros (backpressure, sin descartar nada). Cada
    micro-lote pasa por la etapa 1, sus eventos limpios entran al heap y la etapa 2 atiende hasta
    capacidad_servicio eventos, con una pasada de aging cada aging_cada micro-lotes. Al final se
    vacía el heap.

    Devuelve {"pasos": event_id -> lista de pasos, "servidos": eventos en orden de servicio,
    "resumen": contadores del lote}. Un duplicado comparte el event_id de su original, así que sus
    pasos quedan en la misma lista.
    """
    pasos = defaultdict(list)
    c = Counter()

    def paso(event_id, etapa, accion, **detalle):
        pasos[event_id].append({"etapa": etapa, "accion": accion, **detalle})

    def observador(accion, **datos):
        if accion == "rechazado":
            paso(datos["registro"].get("event_id"), 1, "rechazado", motivo=datos["motivo"])
        elif accion == "valido":
            paso(datos["evento"].event_id, 1, "válido y normalizado")
        else:
            paso(datos["evento"].event_id, 1, f"duplicado {datos['tipo']} descartado ({datos['donde']})",
                 original=datos["original_id"])

    cola = ColaCircular(capacidad_cola)
    preprocesador = Preprocesador(observador=observador)
    heap = ColaPrioridadEventos(aging_s=AGING_S)
    servidos = []
    ahora = float("-inf")          # tiempo del stream de la etapa 2: el mayor ts visto

    def atender(evento, clase_salida, t_entrada):
        servidos.append(evento)
        paso(evento.event_id, 2, "atendido", orden_servicio=len(servidos),
             clase_salida=NOMBRE_CLASE[clase_salida], espera_s=round(ahora - t_entrada, 3))

    def micro_lote():
        nonlocal ahora
        lote = [cola.desencolar() for _ in range(min(tam_lote, len(cola)))]
        c["micro_lotes"] += 1
        for r in lote:
            paso(r.get("event_id"), 0, "sale de la cola", micro_lote=c["micro_lotes"])
        for evento in preprocesador.procesar_lote(lote):
            ahora = max(ahora, evento.ts)
            heap.push(evento, ahora)
            paso(evento.event_id, 2, "entra al heap", clase=NOMBRE_CLASE[evento.priority],
                 deadline=evento.deadline)
        if c["micro_lotes"] % aging_cada == 0:
            heap.envejecer(ahora)
        for _ in range(min(capacidad_servicio, len(heap))):
            atender(*heap.pop())

    for posicion, registro in enumerate(registros, inicio):
        if cola.esta_llena():
            c["productor_en_espera"] += 1
            micro_lote()
        cola.encolar(registro)
        paso(registro.get("event_id"), 0, "encolado en la ColaCircular", posicion_stream=posicion)
    while len(cola):
        micro_lote()
    while len(heap):
        atender(*heap.pop())

    m = preprocesador.metricas
    resumen = {
        "registros": len(registros),
        "micro_lotes": c["micro_lotes"],
        "productor_en_espera": c["productor_en_espera"],
        "rechazados": sum(preprocesador.motivos.values()),
        "duplicados": m["dup_lote_exactos"] + m["dup_lote_casi"] + m["dup_ventana_exactos"] + m["dup_ventana_casi"],
        "atendidos": len(servidos),
        "ascensos_por_aging": heap.ascensos,
    }
    return {"pasos": dict(pasos), "servidos": servidos, "resumen": resumen,
            "rechazos_por_motivo": dict(preprocesador.motivos),
            "atendidos_por_clase": dict(Counter(NOMBRE_CLASE[e.priority] for e in servidos))}


def buscar_en_salidas(event_ids, limite_registros=100_000):
    """Primera aparición de cada event_id en las salidas guardadas de las etapas 0, 1 y 2.

    Devuelve {etapa: {event_id: registro}}. Las salidas están en orden de llegada (etapas 0 y 1) o
    de servicio (etapa 2), así que basta leer el comienzo de cada archivo: la lectura se corta al
    encontrar todos los eventos o al pasar limite_registros registros.
    """
    archivos = ((0, RUTA_LOTES_ETAPA0, "registros"), (1, RUTA_LOTES_ETAPA1, "eventos"),
                (2, RUTA_EVENTOS_ETAPA2, "eventos"))
    encontrados = {}
    for etapa, ruta, campo in archivos:
        pendientes, hallados, leidos = set(event_ids), {}, 0
        with open(ruta, encoding="utf-8") as archivo:
            for linea in archivo:
                registros = json.loads(linea)[campo]
                leidos += len(registros)
                for r in registros:
                    if r.get("event_id") in pendientes:
                        hallados[r["event_id"]] = r
                        pendientes.discard(r["event_id"])
                if not pendientes or leidos >= limite_registros:
                    break
        encontrados[etapa] = hallados
    return encontrados


def alertas_guardadas(event_ids, ruta=RUTA_ALERTAS):
    """Alertas que escribió la etapa 6 para los event_id pedidos; un evento sin alerta no aparece."""
    pedidos = set(event_ids)
    alertas = {}
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            alerta = json.loads(linea)
            if alerta["event_id"] in pedidos:
                alertas[alerta["event_id"]] = alerta
    return alertas
