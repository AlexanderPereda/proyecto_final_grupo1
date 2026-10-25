"""Tests del Bloom Filter, el Count-Min Sketch y el detector de fraude de la etapa 6.

Las estructuras probabilísticas se comparan con un set y un Counter exactos.

Ejecutar desde la raíz del proyecto:
    python -m pytest tests/etapa_6/test_probabilistic.py -v
"""

import math
import random
from collections import Counter

import pytest

from src.events import Event
from src.etapa_6.probabilistic import BloomFilter, CMSVentana, CountMinSketch, DetectorFraude


def _claves(n, prefijo, semilla):
    rng = random.Random(semilla)
    return [f"{prefijo}_{rng.getrandbits(64):016x}" for _ in range(n)]


# ----- Bloom Filter ----------------------------------------------------------------

def test_bloom_dimensiones_optimas():
    bloom = BloomFilter.para(1_000, 0.01)
    assert bloom.m == math.ceil(-1_000 * math.log(0.01) / math.log(2) ** 2)      # 9 586 bits
    assert bloom.k == 7
    assert bloom.memoria_bytes == (bloom.m + 7) // 8


@pytest.mark.parametrize("n", [1, 100, 10_000])
def test_bloom_sin_falsos_negativos(n):
    bloom = BloomFilter.para(n, 0.01)
    claves = _claves(n, "pay", n)
    for c in claves:
        bloom.agregar(c)
    assert all(c in bloom for c in claves)


@pytest.mark.parametrize("p", [0.1, 0.01, 0.001])
def test_bloom_tasa_de_falsos_positivos_cercana_a_la_teorica(p):
    n = 20_000
    bloom = BloomFilter.para(n, p)
    insertadas = _claves(n, "pay", 1)
    for c in insertadas:
        bloom.agregar(c)
    ausentes = set(_claves(100_000, "otra", 2)) - set(insertadas)
    tasa = sum(c in bloom for c in ausentes) / len(ausentes)
    assert abs(tasa - bloom.tasa_fp_teorica()) < max(3 * p / 10, 0.0005)
    assert bloom.tasa_fp_teorica() <= p * 1.1


def test_bloom_vacio_y_saturado():
    bloom = BloomFilter(64, 3)
    assert "x" not in bloom
    for c in _claves(1_000, "pay", 3):
        bloom.agregar(c)
    assert bloom.bits_encendidos() == 64          # saturado, todo da positivo
    assert "cualquier cosa" in bloom


# ----- Count-Min Sketch ------------------------------------------------------------

def test_cms_dimensiones():
    cms = CountMinSketch.para(0.001, 0.01)
    assert (cms.w, cms.d) == (2_719, 5)
    assert cms.epsilon <= 0.001


def test_cms_nunca_subestima_y_respeta_la_cota():
    rng = random.Random(4)
    universo = _claves(5_000, "pay", 5)
    flujo = [universo[min(int(rng.paretovariate(1.1)) - 1, 4_999)] for _ in range(100_000)]   # sesgado
    exacto = Counter(flujo)
    cms = CountMinSketch.para(0.001, 0.01)
    for c in flujo:
        cms.agregar(c)
    assert cms.total == len(flujo)
    errores = [cms.estimar(c) - f for c, f in exacto.items()]
    assert min(errores) >= 0                                     # nunca subestima
    cota = cms.epsilon * cms.total
    assert sum(e > cota for e in errores) / len(errores) <= 0.01  # a lo más δ de las claves
    assert cms.estimar("nunca_vista") <= cota * 3


def test_cms_cuenta_exacta_sin_colisiones():
    cms = CountMinSketch(1 << 16, 4)
    for c, f in [("a", 3), ("b", 7), ("c", 1)]:
        cms.agregar(c, f)
    assert [cms.estimar(c) for c in "abc"] == [3, 7, 1]
    cms.limpiar()
    assert cms.estimar("b") == 0 and cms.total == 0


# ----- Ventanas --------------------------------------------------------------------

def test_cms_ventana_suma_la_ventana_anterior_y_olvida_las_viejas():
    cms = CMSVentana(1 << 12, 4, ventana_s=300, n_ventanas=4)
    for ts in (0, 100, 290):
        cms.agregar("t1", ts)
    cms.agregar("t1", 310)                       # ventana siguiente
    assert cms.estimar("t1", 310) == 4           # actual + anterior
    assert cms.estimar("t1", 650) == 1           # solo la ventana 1 sigue siendo "anterior"
    assert cms.estimar("t1", 2_000) == 0
    cms.agregar("t2", 1_500)                     # ventana 5 reemplaza a la 1 en el arreglo
    assert cms.agregar("t1", 320) is False       # la ventana 1 ya salió y el evento llegó tarde
    assert cms.descartados == 1


def test_cms_ventana_acepta_eventos_tardios_retenidos():
    cms = CMSVentana(1 << 12, 4, ventana_s=300, n_ventanas=10)
    cms.agregar("t", 2_000)
    assert cms.agregar("t", 100) is True         # 6 ventanas atrás, todavía retenida
    assert cms.estimar("t", 100) == 1


# ----- Detector --------------------------------------------------------------------

LISTAS = {"cuentas": ["AFRAUDE"], "tarjetas": ["pay_malo"], "dispositivos": ["dev_malo"]}


def _compra(i, usuario, tarjeta, dispositivo, ts, tipo="purchase"):
    return Event(f"e{i}", tipo, usuario, "B1", f"o{i}", "express", 10.0, tarjeta, dispositivo, ts)


def test_detector_bloquea_lista_y_no_toca_legitimos():
    detector = DetectorFraude(LISTAS)
    assert detector.procesar(_compra(1, "AUSUARIO", "pay_ok", "dev_ok", 0)) is None
    alerta = detector.procesar(_compra(2, "AUSUARIO", "pay_malo", "dev_ok", 10))
    assert alerta["motivos"] == ["tarjeta"] and alerta["decision"] == "bloquear"
    alerta = detector.procesar(_compra(3, "AFRAUDE", "pay_ok2", "dev_ok2", 20, tipo="payment_alert"))
    assert alerta["motivos"] == ["cuenta"]
    vista = Event("e4", "view", "AFRAUDE", "B1", None, None, None, None, "dev_malo", 30)
    assert detector.procesar(vista) is None      # solo se revisan compras y alertas de pago


def test_detector_rafaga_desde_la_quinta_compra():
    detector = DetectorFraude(LISTAS, umbral=5)
    resultados = [detector.procesar(_compra(i, "AOTRO", "pay_rafaga", "dev_x", 1_000 + 20 * i)) for i in range(8)]
    assert resultados[:4] == [None] * 4
    assert all(r["motivos"] == ["rafaga"] and r["conteo_exacto"] >= 5 for r in resultados[4:])
    assert all(r["decision"] == "revisar" for r in resultados[4:])       # sin lista, la orden se retiene para revisión


def test_detector_cuenta_una_vez_cada_orden():
    detector = DetectorFraude(LISTAS, umbral=5)
    for orden in range(4):                       # 4 órdenes de 3 productos: 12 compras, 4 intentos
        for producto in range(3):
            e = _compra(10 * orden + producto, "AOTRO", "pay_multi", "dev_x", 1_000 + 30 * orden)
            e.order_id = f"o{orden}"
            assert detector.procesar(e) is None
    assert detector.metricas["intentos"] == 4
    assert detector.cms.estimar("pay_multi", 1_100) == 4


def test_detector_verifica_falsas_alertas_del_cms():
    detector = DetectorFraude(LISTAS, epsilon=1.0, delta=0.5, umbral=3)    # w = 3: casi todo colisiona
    for i in range(30):
        detector.procesar(_compra(i, f"AU{i}", f"pay_{i}", f"dev_{i}", 1_000 + i))
    m = detector.metricas
    assert m["cms_alertas"] > 0
    assert m["cms_falsas_alertas"] == m["cms_alertas"]    # ninguna tarjeta compra 3 veces
    assert m["alertas"] == 0
