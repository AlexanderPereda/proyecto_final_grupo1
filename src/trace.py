"""Traza paso a paso de N eventos a través de las etapas 0, 1 y 2.

Usa las implementaciones de src/ (GeneradorEventos, ColaCircular, Preprocesador,
ColaPrioridadEventos) y registra cada decisión en una lista de pasos que después
consume el visualizador HTML.

Diferencia con el notebook de la etapa 0: allí productor y consumidor corren en hilos,
y el orden entre hilos cambia en cada ejecución. Para que la traza sea reproducible con
la misma semilla, aquí se alternan por turnos fijos (ticks) en un solo hilo. La cola es
la ColaCircular real y la política es la misma: si la cola está llena, el productor espera.

Medición de memoria en dos pasadas. Si la traza guardara sus datos mientras tracemalloc
mide, su propia memoria (que crece en cada paso) se mezclaría con la del pipeline. Por eso:
  1. Pasada de medición: se ejecuta el pipeline con tracemalloc activo y la traza no guarda
     nada; solo anota la memoria actual y el pico de cada paso en arreglos reservados antes.
  2. Pasada de registro: se repite el mismo pipeline sin tracemalloc y se guarda la traza.
El pipeline es determinista, así que las dos pasadas recorren los mismos pasos.

Uso:
    python -m src.trace            # imprime el resumen de la traza
"""

import itertools
import tracemalloc
from array import array
from collections import Counter
from dataclasses import asdict
from . import config
from .generator import SEED, GeneradorEventos, cargar_catalogo, leer_resenas
from .stage0_queue import ColaCircular
from .stage1_preprocess import Preprocesador
from .stage2_heap import AGING_S, ColaPrioridadEventos

MAX_PASOS_POR_EVENTO = 20      # cota para reservar los arreglos de memoria de la pasada de medición

# Eventos iniciales del stream que se generan pero no se trazan. Al inicio solo hay búsquedas y
# vistas, porque cada recorrido empieza unos 25 minutos antes de su reseña. Con 13 000, la ventana
# de 200 eventos de Office_Products tiene compras, 8 alertas de pago, devoluciones, 5 registros
# malformados, duplicados exactos y casi exactos, y 10 eventos fuera de orden por llegadas tardías.
SALTAR = 13_000


class _Descarte:
    """Diccionario que no guarda nada. En la pasada de medición sustituye a los datos de la
    traza, para que sus escrituras no reserven memoria que se confunda con la del pipeline."""

    def __setitem__(self, clave, valor):
        pass

    def __getitem__(self, clave):
        return self

    def update(self, *args, **kwargs):
        pass


def _ejecutar(catalogo, ruta_resenas, n_eventos, semilla, saltar, capacidad_cola,
              llegadas_por_tick, consumir_cada, tam_lote, capacidad_servicio, aging_cada,
              medir_memoria):
    """Ejecuta el pipeline una vez.

    medir_memoria=True: devuelve (memoria_actual, memoria_pico, n_pasos) por paso.
    medir_memoria=False: devuelve (eventos, pasos, contadores, preprocesador).
    """
    if medir_memoria:
        limite = MAX_PASOS_POR_EVENTO * n_eventos + 1000
        mem_actual = array("q", [0]) * limite      # reservados antes de empezar a medir
        mem_pico = array("q", [0]) * limite
        n_medidos = [0]
        tracemalloc.start()                         # el catálogo queda fuera de la medición

    generador = GeneradorEventos(catalogo, semilla=semilla)
    fuente = itertools.islice(generador.eventos(leer_resenas(ruta_resenas, n_ventana=config.N_RESENAS)),
                              saltar, saltar + n_eventos)

    cola = ColaCircular(capacidad_cola)
    cola_prioridad = ColaPrioridadEventos(aging_s=AGING_S)

    eventos = _Descarte() if medir_memoria else {}   # tid -> datos del evento para el visualizador
    pasos = []                                       # registro de cada paso (vacío al medir)
    # id(objeto) -> tid. Solo se consulta para objetos que siguen dentro de alguna estructura,
    # y cada objeto nuevo sobrescribe su entrada, así que la reutilización de id() no causa errores.
    tid_de = {}
    tid_aceptado = {}       # event_id aceptado por la etapa 1 -> tid (para duplicados entre lotes)
    c = Counter()           # contadores acumulados
    estado = {"tick": 0, "lote": 0}

    def foto_estructura(etapa):
        """Contenido real de la estructura de la etapa, traducido a tids (solo lectura).

        Etapa 0: ranuras del arreglo de la ColaCircular y posición del frente.
        Etapa 2: arreglo interno del MinHeap (orden de heap) y la clave completa de cada
        entrada, (clase efectiva, deadline, seq), para poder verificar la propiedad de heap.
        """
        if etapa == 0:
            return {"ranuras": [None if x is None else tid_de[id(x)] for x in cola._datos],
                    "inicio": cola._inicio}
        if etapa == 2:
            entradas = cola_prioridad._heap.elementos()
            return {"heap": [tid_de[id(e[5])] for e in entradas],
                    "clases": [e[0] for e in entradas],
                    "deadlines": [e[1] for e in entradas],
                    "seqs": [e[2] for e in entradas]}
        return None

    def registrar(etapa, accion, tid=None, **detalle):
        """Pasada de medición: anota la memoria. Pasada de registro: guarda el paso."""
        if medir_memoria:
            i = n_medidos[0]
            mem_actual[i], mem_pico[i] = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()                 # el siguiente pico se mide desde este paso
            n_medidos[0] = i + 1
            return
        estructura = foto_estructura(etapa)
        pasos.append({
            "paso": len(pasos),
            "tick": estado["tick"],
            "lote": estado["lote"],
            "etapa": etapa,
            "accion": accion,
            "tid": tid,
            "detalle": detalle,
            **({"estructura": estructura} if estructura is not None else {}),
            "contadores": {
                "en_cola": len(cola),
                "bloqueos": c["bloqueos"],
                "lotes": c["lotes"],
                "validos": c["validos"],
                "rechazados": c["rechazados"],
                "duplicados": c["duplicados"],
                "en_heap": len(cola_prioridad),
                "atendidos": c["atendidos"],
            },
        })

    # ----- Observador de la etapa 1 -----------------------------------------------
    def observador(accion, **datos):
        if accion == "rechazado":
            tid = tid_de[id(datos["registro"])]
            c["rechazados"] += 1
            eventos[tid]["estado"] = "rechazado"
            eventos[tid]["motivo"] = datos["motivo"]
            registrar(1, "rechazado", tid, motivo=datos["motivo"])
        elif accion == "valido":
            tid = tid_de[id(datos["registro"])]
            evento = datos["evento"]
            tid_de[id(evento)] = tid
            c["validos"] += 1
            if not medir_memoria:
                eventos[tid]["normalizado"] = asdict(evento)
            registrar(1, "valido", tid)
        elif accion == "duplicado":
            evento = datos["evento"]
            tid = tid_de[id(evento)]
            c["duplicados"] += 1
            if datos["original"] is not None:             # duplicado dentro del mismo lote
                original = tid_de[id(datos["original"])]
            else:                                         # duplicado de un lote anterior (ventana)
                original = tid_aceptado.get(datos["original_id"])
            eventos[tid]["estado"] = "duplicado"
            eventos[tid]["motivo"] = f"duplicado {datos['tipo']} ({datos['donde']})"
            registrar(1, "duplicado", tid, donde=datos["donde"], tipo=datos["tipo"],
                      original_event_id=datos["original_id"], original_tid=original)

    preprocesador = Preprocesador(observador=observador)

    # ----- Bucle de ticks -----------------------------------------------------------
    pendiente = None          # registro que el productor no pudo encolar (bloqueado)
    fuente_agotada = False
    n_registros = 0
    orden_salida = 0
    ahora = float("-inf")     # tiempo del stream de la etapa 2 (mayor ts visto)

    while not fuente_agotada or pendiente is not None or len(cola) or len(cola_prioridad):
        estado["tick"] += 1

        # Etapa 0, productor: intenta encolar hasta llegadas_por_tick registros
        for _ in range(llegadas_por_tick):
            if pendiente is None:
                registro = next(fuente, None)
                if registro is None:
                    fuente_agotada = True
                    break
                tid = n_registros
                n_registros += 1
                tid_de[id(registro)] = tid
                if not medir_memoria:
                    eventos[tid] = {"tid": tid, "crudo": dict(registro), "normalizado": None,
                                    "estado": "en_cola", "motivo": None, "prioridad": None,
                                    "clase_salida": None, "orden_salida": None, "lote": None}
                pendiente = registro
            if cola.esta_llena():
                c["bloqueos"] += 1
                registrar(0, "backpressure", tid_de[id(pendiente)], capacidad=cola.capacidad)
                break                                 # el productor espera al siguiente tick
            cola.encolar(pendiente)
            registrar(0, "encolado", tid_de[id(pendiente)])
            pendiente = None

        # Etapa 0, consumidor: cada consumir_cada ticks forma un micro-lote
        if estado["tick"] % consumir_cada != 0 and not (fuente_agotada and pendiente is None):
            continue
        if len(cola) == 0 and len(cola_prioridad) == 0:
            continue

        lote = []
        while len(lote) < tam_lote and not cola.esta_vacia():
            lote.append(cola.desencolar())
        if lote:
            c["lotes"] += 1
            estado["lote"] = c["lotes"]
            motivo = "tamaño" if len(lote) == tam_lote else ("fin" if fuente_agotada else "tiempo")
            registrar(0, "lote_formado", None, tamano=len(lote), motivo=motivo,
                      tids=[tid_de[id(r)] for r in lote])
            for r in lote:
                eventos[tid_de[id(r)]]["lote"] = c["lotes"]
                registrar(0, "sale_en_lote", tid_de[id(r)])

        # Etapa 1: el Preprocesador real procesa el lote; el observador registra cada decisión
        salida = preprocesador.procesar_lote(lote) if lote else []
        for posicion, evento in enumerate(salida):
            tid = tid_de[id(evento)]
            tid_aceptado[evento.event_id] = tid
            registrar(1, "limpio", tid, posicion=posicion)

        # Etapa 2: los eventos limpios entran al heap con su clase y deadline
        for evento in salida:
            ahora = max(ahora, evento.ts)
            cola_prioridad.push(evento, ahora)
            tid = tid_de[id(evento)]
            if not medir_memoria:
                eventos[tid]["prioridad"] = evento.priority
                eventos[tid]["normalizado"] = asdict(evento)
                eventos[tid]["estado"] = "en_heap"
            registrar(2, "entra_heap", tid, prioridad=evento.priority, deadline=evento.deadline)

        if lote and c["lotes"] % aging_cada == 0 and len(cola_prioridad):
            antes = cola_prioridad.ascensos
            cola_prioridad.envejecer(ahora)
            registrar(2, "aging", None, ascensos=cola_prioridad.ascensos - antes)

        # Etapa 2: se atiende según la capacidad; al final se vacía el heap
        for _ in range(min(capacidad_servicio, len(cola_prioridad))):
            evento, clase_salida, t_entrada = cola_prioridad.pop()
            tid = tid_de[id(evento)]
            orden_salida += 1
            c["atendidos"] += 1
            eventos[tid].update(estado="atendido", clase_salida=clase_salida, orden_salida=orden_salida)
            registrar(2, "sale_heap", tid, orden=orden_salida, clase_salida=clase_salida,
                      espera_s=round(ahora - t_entrada, 3))

    if medir_memoria:
        tracemalloc.stop()
        n = n_medidos[0]
        return mem_actual[:n].tolist(), mem_pico[:n].tolist(), n
    return eventos, pasos, c, preprocesador


def generar_traza(n_eventos=200, semilla=SEED,
                  saltar=SALTAR,            # eventos iniciales del stream que se generan pero no se trazan
                  capacidad_cola=32,        # capacidad de la ColaCircular (etapa 0)
                  llegadas_por_tick=8,      # eventos que el productor intenta encolar en cada tick
                  consumir_cada=3,          # el consumidor forma un lote cada 3 ticks
                  tam_lote=16,              # tamaño máximo del micro-lote
                  capacidad_servicio=12,    # eventos que la etapa 2 atiende por lote
                  aging_cada=2):            # pasada de aging cada 2 lotes
    """Procesa n_eventos por las etapas 0 -> 1 -> 2 y devuelve un diccionario con la traza.

    Con estos parámetros el productor ofrece 8 eventos por tick y el consumidor retira 16
    cada 3 ticks (unos 5.3 por tick), así que la cola se llena y aparece el backpressure.
    La etapa 2 atiende 12 eventos por lote, menos de los que llegan, así que el heap crece.

    Los datos, la categoría y la ventana de reseñas se toman de `src/config.py`.

    saltar: ver SALTAR. Los eventos saltados se generan con la misma semilla, para que el estado del generador sea
    el real, pero no pasan por las etapas. Por eso un reenvío cuyo original fue saltado se
    trata como un evento nuevo.
    """
    catalogo = cargar_catalogo(config.RUTA_META)
    argumentos = dict(catalogo=catalogo, ruta_resenas=config.RUTA_RESENAS, n_eventos=n_eventos,
                      semilla=semilla, saltar=saltar, capacidad_cola=capacidad_cola,
                      llegadas_por_tick=llegadas_por_tick, consumir_cada=consumir_cada,
                      tam_lote=tam_lote, capacidad_servicio=capacidad_servicio, aging_cada=aging_cada)

    # Pasada 1: memoria. Pasada 2: traza completa (sin tracemalloc).
    mem_actual, mem_pico_intervalo, n_medidos = _ejecutar(**argumentos, medir_memoria=True)
    eventos, pasos, c, preprocesador = _ejecutar(**argumentos, medir_memoria=False)
    assert n_medidos == len(pasos), "las dos pasadas deben recorrer los mismos pasos"

    # La memoria se expresa desde el primer paso: lo anterior es el estado inicial del
    # generador (incluidos los eventos saltados), común a todas las etapas.
    base = mem_actual[0]
    pico = 0
    for paso, actual, pico_intervalo in zip(pasos, mem_actual, mem_pico_intervalo):
        pico = max(pico, pico_intervalo - base)
        paso["contadores"]["mem_actual"] = actual - base
        paso["contadores"]["mem_pico"] = pico

    resumen = {
        "eventos": len(eventos),
        "pasos": len(pasos),
        "ticks": pasos[-1]["tick"],
        "lotes": c["lotes"],
        "bloqueos_productor": c["bloqueos"],
        "validos": c["validos"],
        "rechazados": c["rechazados"],
        "duplicados": c["duplicados"],
        "atendidos": c["atendidos"],
        "memoria_base_bytes": base,
        "memoria_pico_bytes": pico,
        "rechazos_por_motivo": dict(preprocesador.motivos),
        "estados_finales": dict(Counter(e["estado"] for e in eventos.values())),
    }
    parametros = {"n_eventos": n_eventos, "semilla": semilla, "categoria": config.CATEGORIA, "saltar": saltar,
                  "capacidad_cola": capacidad_cola, "llegadas_por_tick": llegadas_por_tick,
                  "consumir_cada": consumir_cada, "tam_lote": tam_lote,
                  "capacidad_servicio": capacidad_servicio, "aging_cada": aging_cada}
    return {"parametros": parametros, "resumen": resumen,
            "eventos": [eventos[t] for t in sorted(eventos)], "pasos": pasos}


if __name__ == "__main__":
    import json

    traza = generar_traza()
    print(json.dumps(traza["resumen"], indent=2, ensure_ascii=False))
