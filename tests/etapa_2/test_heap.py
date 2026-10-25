"""Tests de la propiedad de heap (etapa 2): cada padre es <= que sus hijos.

Ejecutar desde la raíz del proyecto:
    python -m pytest tests -v
"""

import random

import pytest

from src import config
from src.events import Event
from src.etapa_2.heap import ColaPrioridadEventos, MinHeap, es_heap, violaciones_heap

SEED = config.SEED


# ----- La función de verificación ------------------------------------------------

def test_violaciones_heap_vacia_en_heaps_validos():
    assert violaciones_heap([]) == []
    assert violaciones_heap([5]) == []
    assert violaciones_heap([1, 3, 2, 7, 4, 5]) == []
    assert violaciones_heap([2, 2, 2, 2]) == []          # los empates no rompen la regla


def test_violaciones_heap_detecta_cada_par_roto():
    # posición 3 (valor 0) es menor que su padre, posición 1 (valor 3)
    assert violaciones_heap([1, 3, 2, 0]) == [(1, 3)]
    # la raíz es mayor que sus dos hijos
    assert violaciones_heap([9, 1, 2]) == [(0, 1), (0, 2)]
    assert not es_heap([9, 1, 2])


def test_violaciones_heap_con_claves_tupla():
    # misma clase: decide el deadline; mismo deadline: decide seq
    assert violaciones_heap([(2, 100.0, 5), (2, 100.0, 7), (3, 50.0, 1)]) == []
    assert violaciones_heap([(2, 100.0, 7), (2, 100.0, 5)]) == [(0, 1)]


# ----- El MinHeap propio mantiene la propiedad -----------------------------------

def test_minheap_mantiene_la_propiedad_tras_cada_operacion():
    rng = random.Random(SEED)
    heap = MinHeap()
    for _ in range(2_000):
        if len(heap) and rng.random() < 0.4:
            heap.pop()
        else:
            heap.push(rng.randint(0, 100))
        assert violaciones_heap(heap.elementos()) == []


@pytest.mark.parametrize("n", [0, 1, 2, 3, 10, 257, 5_000])
def test_heapify_produce_un_heap_valido(n):
    rng = random.Random(SEED)
    datos = [rng.random() for _ in range(n)]
    heap = MinHeap(datos)
    assert violaciones_heap(heap.elementos()) == []
    assert [heap.pop() for _ in range(n)] == sorted(datos)


def test_heapify_en_el_peor_caso_decreciente():
    heap = MinHeap(list(range(1_000, 0, -1)))
    assert violaciones_heap(heap.elementos()) == []


# ----- La cola de prioridades de eventos -----------------------------------------

def _evento(event_id, tipo, ts, order_type=None):
    return Event(event_id, tipo, "U", "B", None, order_type, None, None, None, ts)


def _claves(cola):
    """Claves (clase, deadline, seq) del arreglo interno de la cola."""
    return [entrada[:3] for entrada in cola._heap.elementos()]


def test_cola_prioridad_mantiene_la_propiedad_con_aging():
    rng = random.Random(SEED)
    tipos = [("payment_alert", None), ("purchase", "express"), ("purchase", "standard"),
             ("purchase", "scheduled"), ("view", None), ("search", None)]
    cola = ColaPrioridadEventos()
    ahora = 0.0
    for i in range(1_500):
        ahora += rng.uniform(0, 30)
        tipo, orden = rng.choice(tipos)
        cola.push(_evento(f"e{i}", tipo, ahora, orden), ahora)
        if i % 50 == 0:
            cola.envejecer(ahora)                         # reconstruye el heap con heapify
        if rng.random() < 0.3:
            cola.pop()
        assert violaciones_heap(_claves(cola)) == []
    assert cola.ascensos > 0                              # el aging sí cambió clases


# ----- La traza que usa el visualizador ------------------------------------------

DATOS_DISPONIBLES = config.RUTA_RESENAS.exists() and config.RUTA_META.exists()


@pytest.mark.skipif(not DATOS_DISPONIBLES, reason="faltan los Parquet: ejecutar notebooks/00-download-data.ipynb")
def test_cada_foto_del_heap_en_la_traza_es_valida():
    from src.trace import generar_traza

    traza = generar_traza()
    fotos = [p["estructura"] for p in traza["pasos"] if "heap" in p.get("estructura", {})]
    assert fotos, "la traza debe tener fotos del heap"
    for foto in fotos:
        claves = list(zip(foto["clases"], foto["deadlines"], foto["seqs"]))
        assert violaciones_heap(claves) == []
