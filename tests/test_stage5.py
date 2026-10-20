"""Tests del KD-Tree y del KNN exacto de la etapa 5.

Las distancias del KD-Tree se comparan con las de la fuerza bruta. Los índices pueden diferir
cuando dos puntos están exactamente a la misma distancia, así que se comparan las distancias.

Ejecutar desde la raíz del proyecto:
    python -m pytest tests -v
"""

import numpy as np
import pytest

from src.stage5_kdtree import IndiceProductos, KDTree, knn_fuerza_bruta


def _distancias(resultado):
    return [round(d, 9) for d, _ in resultado]


@pytest.mark.parametrize("n, d, tam_hoja", [(1, 2, 4), (50, 2, 4), (2_000, 3, 8), (5_000, 6, 32), (500, 20, 16)])
@pytest.mark.parametrize("k", [1, 7, 60])
def test_knn_coincide_con_fuerza_bruta(n, d, tam_hoja, k):
    rng = np.random.default_rng(n + d + k)
    X = rng.normal(size=(n, d))
    kd = KDTree(X, tam_hoja=tam_hoja)
    for q in rng.normal(size=(20, d)):
        esperado = knn_fuerza_bruta(X, q, k)
        obtenido = kd.knn(q, k)
        assert len(obtenido) == min(k, n)
        assert _distancias(obtenido) == _distancias(esperado)
        assert all(abs(dist - np.linalg.norm(X[i] - q)) < 1e-9 for dist, i in obtenido)


def test_knn_con_puntos_repetidos():
    rng = np.random.default_rng(1)
    X = np.repeat(rng.integers(0, 3, size=(40, 2)).astype(float), 25, axis=0)     # muchos empates
    kd = KDTree(X, tam_hoja=4)
    for q in rng.integers(0, 3, size=(10, 2)).astype(float):
        assert _distancias(kd.knn(q, 30)) == _distancias(knn_fuerza_bruta(X, q, 30))


def test_knn_con_etiqueta():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(3_000, 4))
    etiquetas = rng.integers(0, 10, size=3_000)
    kd = KDTree(X, tam_hoja=16, etiquetas=etiquetas)
    for q in rng.normal(size=(10, 4)):
        obtenido = kd.knn(q, 15, etiqueta=3)
        assert all(etiquetas[i] == 3 for _, i in obtenido)
        assert _distancias(obtenido) == _distancias(knn_fuerza_bruta(X, q, 15, etiquetas, 3))


def test_poda_evalua_menos_puntos_que_la_fuerza_bruta():
    rng = np.random.default_rng(3)
    X = rng.uniform(size=(100_000, 3))
    kd = KDTree(X)
    kd.knn(rng.uniform(size=3), 10)
    assert kd.puntos_evaluados < len(X) / 50


def test_rango_coincide_con_filtro():
    rng = np.random.default_rng(4)
    X = rng.normal(size=(5_000, 3))
    kd = KDTree(X, tam_hoja=8)
    for _ in range(20):
        a, b = np.sort(rng.normal(size=(2, 3)), axis=0)
        esperado = np.flatnonzero(np.all((X >= a) & (X <= b), axis=1))
        assert sorted(kd.rango(a, b)) == esperado.tolist()


def test_arbol_balanceado():
    X = np.random.default_rng(5).normal(size=(10_000, 2))
    kd = KDTree(X, tam_hoja=10)
    assert kd.altura() <= np.ceil(np.log2(10_000 / 10)) + 2
    assert sorted(kd.perm.tolist()) == list(range(10_000))


def test_indice_productos_similares():
    rng = np.random.default_rng(6)
    asins = [f"B{i:09d}" for i in range(300)]
    X = rng.normal(loc=[3, 4, 5, 1], scale=[1, 0.5, 2, 1], size=(300, 4))
    categorias = ["Pens" if i % 2 else "Paper" for i in range(300)]
    indice = IndiceProductos(asins, X, categorias, tam_hoja=8)
    assert np.allclose(indice.Z.mean(axis=0), 0) and np.allclose(indice.Z.std(axis=0), 1)
    similares = indice.similares("B000000004", k=5)
    assert len(similares) == 5
    assert "B000000004" not in [a for _, a in similares]
    assert all(int(a[1:]) % 2 == 0 for _, a in similares)              # misma categoría (Paper)
    assert indice.similares("B999999999") is None
