"""Tests de la lectura por ventana del Parquet, del desorden controlado y de las métricas de desorden.

Los tests crean sus propios datos pequeños, así que no necesitan los Parquet de data/raw/.
"""

import random

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src import config
from src.generator import GeneradorEventos, describir_ventana, leer_resenas
from src.metricas import contar_fuera_de_orden


# ----- Datos sintéticos ---------------------------------------------------------

@pytest.fixture
def parquet_resenas(tmp_path):
    """Parquet de 10 000 reseñas ordenadas por timestamp, en 10 grupos de 1 000 filas."""
    n = 10_000
    tabla = pa.table({
        "rating": [5.0] * n,
        "user_id": [f"U{i % 300}" for i in range(n)],
        "parent_asin": [f"B{i % 50}" for i in range(n)],
        "timestamp": list(range(n)),                 # el timestamp coincide con el número de fila
        "verified_purchase": [True] * n,
    })
    ruta = tmp_path / "resenas.parquet"
    pq.write_table(tabla, ruta, row_group_size=1_000)
    return ruta


def _resenas(n, semilla=1):
    """Reseñas en memoria, ordenadas por timestamp (separadas de 20 s a 3 min)."""
    rng = random.Random(semilla)
    ts = 1_700_000_000_000
    resenas = []
    for i in range(n):
        ts += rng.randint(20_000, 180_000)
        resenas.append({"user_id": f"U{i % 97}", "parent_asin": f"B{i % 40}", "rating": float(rng.randint(1, 5)),
                        "verified_purchase": rng.random() < 0.8, "timestamp": ts})
    return resenas


CATALOGO = {f"B{i}": (["2.68", None, "from 14.99", "—"][i % 4], "Office Products", f"Producto de prueba {i}")
            for i in range(40)}


def _ts_emitidos(generador, resenas):
    return [r["ts"] for r in generador.eventos(resenas)]


# ----- Ventana reciente ---------------------------------------------------------

def test_ventana_salta_los_grupos_anteriores(parquet_resenas):
    v = describir_ventana(parquet_resenas, n_ventana=2_500)
    assert (v["inicio"], v["fin"], v["filas"]) == (7_500, 10_000, 2_500)
    assert v["grupos_leidos"] == [7, 8, 9]          # los grupos 0 a 6 no se leen
    assert v["filas_a_descartar"] == 500            # filas 7 000 a 7 499 del grupo 7


def test_leer_resenas_entrega_las_ultimas_n(parquet_resenas):
    ts = [r["timestamp"] for r in leer_resenas(parquet_resenas, n_ventana=2_500, tam_bloque=300)]
    assert ts == list(range(7_500, 10_000))


def test_modo_desarrollo_toma_el_inicio_de_la_ventana(parquet_resenas):
    assert describir_ventana(parquet_resenas, n_ventana=2_500, limite=300)["grupos_leidos"] == [7]
    ts = [r["timestamp"] for r in leer_resenas(parquet_resenas, n_ventana=2_500, limite=300)]
    assert ts == list(range(7_500, 7_800))


def test_ventana_mayor_que_el_archivo(parquet_resenas):
    ts = [r["timestamp"] for r in leer_resenas(parquet_resenas, n_ventana=50_000)]
    assert ts == list(range(10_000))


def test_leer_resenas_solo_trae_las_columnas_necesarias(parquet_resenas):
    primera = next(leer_resenas(parquet_resenas, n_ventana=10))
    assert set(primera) == set(config.COLUMNAS_RESENAS)


# ----- Desorden controlado ------------------------------------------------------

def test_sin_tardios_el_stream_sale_ordenado():
    generador = GeneradorEventos(CATALOGO, porcentaje_tardios=0)
    metricas = contar_fuera_de_orden([_ts_emitidos(generador, _resenas(600))])
    assert generador.estadisticas["tardios"] == 0
    assert metricas["fuera_de_orden"] == 0


def test_porcentaje_real_de_tardios():
    generador = GeneradorEventos(CATALOGO, porcentaje_tardios=0.05, retraso_max_tardios_s=120)
    metricas = contar_fuera_de_orden([_ts_emitidos(generador, _resenas(2_000))])
    real = generador.estadisticas["tardios"] / generador.estadisticas["programados"]
    assert 0.04 < real < 0.06
    assert metricas["fuera_de_orden"] > 0


def test_ningun_evento_llega_mas_tarde_que_el_retraso_maximo():
    retraso_max_s = 120
    generador = GeneradorEventos(CATALOGO, porcentaje_tardios=0.2, retraso_max_tardios_s=retraso_max_s)
    maximo_previo, peor_atraso = float("-inf"), 0
    for ts in _ts_emitidos(generador, _resenas(1_000)):
        if isinstance(ts, int):
            peor_atraso = max(peor_atraso, maximo_previo - ts)
            maximo_previo = max(maximo_previo, ts)
    # el atraso no supera el retraso máximo (más los ±0.4 s de los duplicados casi exactos)
    assert peor_atraso <= retraso_max_s * 1000 + 400


def test_el_generador_es_reproducible():
    a = list(GeneradorEventos(CATALOGO, semilla=42).eventos(_resenas(300)))
    b = list(GeneradorEventos(CATALOGO, semilla=42).eventos(_resenas(300)))
    assert a == b


def test_el_precio_del_catalogo_pasa_crudo_a_la_etapa_1():
    generador = GeneradorEventos(CATALOGO, p_malformado=0, p_duplicado_exacto=0, p_duplicado_casi=0)
    precios = {r["parent_asin"]: r["price"] for r in generador.eventos(_resenas(400))
               if r["event_type"] == "purchase" and r["user_id"].startswith("U")}
    assert precios["B2"] == "from 14.99"            # texto crudo, sin limpiar
    assert precios["B3"] == "—"
    assert precios["B1"] is not None                 # sin precio en el catálogo: precio sintético


# ----- Métricas de desorden -----------------------------------------------------

def test_contar_fuera_de_orden_separa_dentro_y_entre_lotes():
    metricas = contar_fuera_de_orden([[1, 3, 2], [2.5, 4, "malformado", None]])
    assert metricas["eventos"] == 5
    assert metricas["dentro_del_lote"] == 1         # 2 llega después de 3 en el mismo lote
    assert metricas["entre_lotes"] == 1             # 2.5 es menor que el 3 del lote anterior
    assert metricas["fuera_de_orden"] == 2
