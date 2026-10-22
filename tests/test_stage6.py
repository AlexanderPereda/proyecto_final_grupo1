"""Tests del Bloom Filter de la etapa 6.

Las estructuras probabilísticas se comparan con un set y un Counter exactos.

Ejecutar desde la raíz del proyecto:
    python -m pytest tests/test_stage6.py -v
"""

import math
import random

import pytest

from src.stage6_probabilistic import BloomFilter


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
