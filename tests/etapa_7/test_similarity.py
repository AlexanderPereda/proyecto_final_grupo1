"""Tests de MinHash, LSH y HNSW de la etapa 7.

Las estructuras aproximadas se comparan con la Jaccard exacta de conjuntos de Python y con el
KNN por fuerza bruta de la etapa 5.

Ejecutar desde la raíz del proyecto:
    python -m pytest tests/etapa_7/test_similarity.py -v
"""

import random

import numpy as np
import pytest

from src.etapa_5.kdtree import knn_fuerza_bruta
from src.etapa_7.similarity import (HNSW, LSH, ConjuntosProductos, MatrizTFIDF, MinHash, jaccard_estimada,
                                    recall_at_k, relacionados_lsh, similares_exactos, similares_hnsw,
                                    vectores_atributos)


def _conjuntos(n, n_usuarios, semilla):
    """n conjuntos aleatorios en grupos de 5 que comparten la mayoría de sus usuarios."""
    rng = random.Random(semilla)
    lista = []
    for g in range(n // 5):
        base = rng.sample(range(n_usuarios), 30)
        for _ in range(5):
            lista.append(sorted(set(base[:rng.randint(20, 30)]) | set(rng.sample(range(n_usuarios), 5))))
    return lista


def _csr(lista):
    indptr = np.concatenate(([0], np.cumsum([len(s) for s in lista])))
    return indptr, np.concatenate([np.array(s, dtype=np.int32) for s in lista])


def _jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b)


# ----- Conjuntos y fuerza bruta -------------------------------------------------------

def test_jaccard_exacta_igual_a_la_de_conjuntos():
    lista = _conjuntos(50, 500, 1)
    conjuntos = ConjuntosProductos([f"p{i}" for i in range(50)], *_csr(lista), 500)
    obtenida = conjuntos.jaccard(3, list(range(50)))
    assert np.allclose(obtenida, [_jaccard(lista[3], s) for s in lista])


def test_fuerza_bruta_ordena_por_jaccard_y_excluye_el_producto():
    lista = _conjuntos(50, 500, 2)
    conjuntos = ConjuntosProductos([f"p{i}" for i in range(50)], *_csr(lista), 500)
    resultado = conjuntos.fuerza_bruta(7, k=4)
    esperado = sorted(((_jaccard(lista[7], s), j) for j, s in enumerate(lista) if j != 7 and _jaccard(lista[7], s) > 0),
                      key=lambda x: (-x[0], x[1]))[:4]
    assert [j for _, j in resultado] == [j for _, j in esperado]
    assert np.allclose([s for s, _ in resultado], [s for s, _ in esperado])


def test_conjunto_vacio_no_se_acepta():
    with pytest.raises(ValueError):
        ConjuntosProductos(["a", "b"], [0, 2, 2], [1, 3], 5)


# ----- MinHash -------------------------------------------------------------------------

def test_firma_por_lotes_igual_a_firma_individual():
    lista = _conjuntos(20, 1_000, 3)
    minhash = MinHash(64, semilla=4)
    firmas = minhash.firmas(*_csr(lista))
    assert firmas.shape == (20, 64)
    for i in (0, 9, 19):
        assert np.array_equal(firmas[i], minhash.firma(lista[i]))


def test_jaccard_estimada_cerca_de_la_exacta():
    rng = random.Random(5)
    a = set(rng.sample(range(100_000), 2_000))
    for comunes in (200, 1_000, 1_800):
        b = set(list(a)[:comunes]) | set(rng.sample(range(100_000, 200_000), 2_000 - comunes))
        minhash = MinHash(512, semilla=6)
        firmas = minhash.firmas(*_csr([sorted(a), sorted(b)]))
        exacta = _jaccard(a, b)
        assert abs(jaccard_estimada(firmas, 0, [1])[0] - exacta) < 4 * np.sqrt(exacta * (1 - exacta) / 512)


def test_conjuntos_iguales_tienen_la_misma_firma():
    minhash = MinHash(32)
    assert np.array_equal(minhash.firma([5, 9, 40]), minhash.firma([40, 5, 9]))


# ----- LSH -----------------------------------------------------------------------------

def test_lsh_curva_en_s():
    assert LSH.umbral(32, 4) == pytest.approx((1 / 32) ** 0.25)
    assert LSH.probabilidad(0.0) == 0.0
    assert LSH.probabilidad(1.0) == 1.0
    assert LSH.probabilidad(0.9, 32, 4) > 0.999 > 0.1 > LSH.probabilidad(0.1, 32, 4)


def test_lsh_encuentra_los_productos_del_mismo_grupo():
    lista = _conjuntos(200, 5_000, 7)
    conjuntos = ConjuntosProductos([f"p{i}" for i in range(200)], *_csr(lista), 5_000)
    lsh = LSH(MinHash(128).firmas(conjuntos.indptr, conjuntos.indices), 32, 4)
    encontrados = total = 0
    for i in range(200):
        candidatos = set(lsh.candidatos(i).tolist())
        assert i not in candidatos
        grupo = {j for j in range(5 * (i // 5), 5 * (i // 5) + 5) if j != i}
        encontrados += len(grupo & candidatos)
        total += len(grupo)
    assert encontrados / total > 0.95
    recall = np.mean([recall_at_k(relacionados_lsh(conjuntos, lsh, i, 4), conjuntos.fuerza_bruta(i, 4))
                      for i in range(200)])
    assert recall > 0.95


def test_lsh_bandas_mas_largas_que_la_firma():
    with pytest.raises(ValueError):
        LSH(np.zeros((3, 16), dtype=np.uint32), 5, 4)


# ----- Vectores de atributos ------------------------------------------------------------

def test_vectores_de_norma_uno_y_titulos_parecidos_cercanos():
    registros = [
        {"title": "Blue ballpoint pen pack of 12", "categoria_hoja": "Ballpoint Pens", "store": "Bic"},
        {"title": "Blue ballpoint pens, pack of 24", "categoria_hoja": "Ballpoint Pens", "store": "Bic"},
        {"title": "Heavy duty stapler for desk", "categoria_hoja": "Desktop Staplers", "store": "Swingline"},
        {"title": "Copy paper letter size 500 sheets", "categoria_hoja": "Copy Paper", "store": "HP"},
        {},
    ]
    V = vectores_atributos(registros, dim=32)
    assert V.shape == (5, 32)
    assert np.allclose(np.linalg.norm(V[:4], axis=1), 1.0, atol=1e-5)
    assert np.all(V[4] == 0)
    assert knn_fuerza_bruta(V[:4], V[0], 2)[1][1] == 1


def test_coseno_tfidf_igual_al_de_la_matriz_densa():
    rng = random.Random(12)
    palabras = [f"palabra{i}" for i in range(40)]
    registros = [{"title": " ".join(rng.choices(palabras, k=rng.randint(1, 8))),
                  "categoria_hoja": rng.choice(["A", "B", "C"])} for _ in range(60)]
    tfidf = MatrizTFIDF(registros, columnas=1 << 12)
    densa = np.zeros((60, 1 << 12))
    np.add.at(densa, (tfidf.fila, tfidf.col), tfidf.peso)
    assert np.allclose(np.linalg.norm(densa, axis=1), 1.0, atol=1e-5)
    for i in (0, 17, 59):
        assert np.allclose(tfidf.coseno_contra_todos(i), densa @ densa[i], atol=1e-5)
        esperado = sorted(((float(c), j) for j, c in enumerate(densa @ densa[i]) if j != i and c > 1e-9),
                          key=lambda x: (-x[0], x[1]))[:5]
        assert [j for _, j in tfidf.similares_exactos(i, 5)] == [j for _, j in esperado]


def _tfidf_y_densa(semilla, n=80, columnas=1 << 10):
    rng = random.Random(semilla)
    temas = [[f"t{t}w{i}" for i in range(15)] for t in range(4)]       # 4 temas con vocabulario propio
    tamanos = [n * 8 // 15, n * 4 // 15, n * 2 // 15]                    # temas de tamaños distintos
    tema = [t for t, c in enumerate(tamanos + [n - sum(tamanos)]) for _ in range(c)]
    registros = [{"title": " ".join(rng.choices(temas[t], k=8))} for t in tema]
    tfidf = MatrizTFIDF(registros, columnas=columnas)
    densa = np.zeros((n, columnas))
    np.add.at(densa, (tfidf.fila, tfidf.col), tfidf.peso)
    return tfidf, densa


def test_productos_por_la_matriz_iguales_a_los_densos():
    tfidf, densa = _tfidf_y_densa(14)
    rng = np.random.default_rng(15)
    B = rng.standard_normal((densa.shape[1], 6)).astype(np.float32)
    Y = rng.standard_normal((densa.shape[0], 6)).astype(np.float32)
    assert np.allclose(tfidf.por_derecha(B), densa @ B, atol=1e-4)
    assert np.allclose(tfidf.por_izquierda(Y), densa.T @ Y, atol=1e-4)


def test_svd_aleatorizada_igual_a_la_exacta():
    # Matriz de rango 3: tres títulos distintos repetidos. La SVD de rango 3 es exacta, y los
    # productos con el mismo título quedan en el mismo vector, ortogonal a los de los otros títulos.
    titulos = ["red pen pack", "desk stapler heavy", "copy paper letter"]
    tema = [i % 3 for i in range(30)]
    tfidf = MatrizTFIDF([{"title": titulos[t]} for t in tema], columnas=1 << 10)
    V = tfidf.svd(3, semilla=17)
    assert np.allclose(V @ V.T, np.equal.outer(tema, tema).astype(float), atol=1e-4)
    assert np.allclose(np.linalg.norm(V, axis=1), 1.0, atol=1e-5)

    # Con ruido, los valores singulares aproximados quedan cerca y nunca por encima de los exactos
    tfidf, densa = _tfidf_y_densa(16)
    S = np.linalg.svd(densa, compute_uv=False)
    tfidf.svd(8, semilla=17)
    assert np.all(tfidf.valores_singulares <= S[:8] * (1 + 1e-5))
    assert np.allclose(tfidf.valores_singulares, S[:8], rtol=0.02)


def test_reducir_elige_el_metodo():
    tfidf, _ = _tfidf_y_densa(18)
    assert np.array_equal(tfidf.reducir(8, "proyeccion", 3), tfidf.proyectar(8, 3))
    assert np.array_equal(tfidf.reducir(8, "svd", 3), tfidf.svd(8, 3))
    with pytest.raises(ValueError):
        tfidf.reducir(8, "otro")


def test_recall_sin_vecinos_exactos_no_esta_definido():
    assert np.isnan(recall_at_k([(0.5, 1)], []))
    assert recall_at_k([(0.5, 1), (0.4, 2)], [(0.5, 1), (0.3, 3)]) == 0.5


def test_proyeccion_conserva_los_vecinos_con_dimension_alta():
    rng = random.Random(13)
    palabras = [f"p{i}" for i in range(300)]
    registros = [{"title": " ".join(rng.choices(palabras, k=10))} for _ in range(300)]
    tfidf = MatrizTFIDF(registros)
    recall = {dim: np.mean([recall_at_k(similares_exactos(V, i, 5), tfidf.similares_exactos(i, 5))
                            for i in range(50)])
              for dim, V in ((d, tfidf.proyectar(d)) for d in (8, 256))}
    assert recall[256] > recall[8]


# ----- HNSW ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def puntos():
    rng = np.random.default_rng(8)
    centros = rng.standard_normal((20, 16))
    X = centros[rng.integers(0, 20, 3_000)] + 0.3 * rng.standard_normal((3_000, 16))
    return X.astype(np.float32)


@pytest.fixture(scope="module")
def indice(puntos):
    return HNSW(16, m=8, ef_construccion=64, ef_busqueda=32, semilla=9).agregar_todos(puntos)


def test_hnsw_recall_alto_frente_a_fuerza_bruta(puntos, indice):
    rng = np.random.default_rng(10)
    consultas = (puntos[rng.integers(0, len(puntos), 100)] + 0.3 * rng.standard_normal((100, 16))).astype(np.float32)
    recall = np.mean([recall_at_k(indice.buscar(q, 10), knn_fuerza_bruta(puntos, q, 10)) for q in consultas])
    assert recall > 0.9


def test_hnsw_encuentra_el_punto_indexado(puntos, indice):
    for i in (0, 1_500, 2_999):
        d, j = indice.buscar(puntos[i], 1)[0]
        assert j == i and d == pytest.approx(0.0, abs=1e-3)


def test_hnsw_grado_acotado_y_capas_decrecientes(indice):
    for nodo in indice.vecinos:
        assert len(nodo[0]) <= indice.m0
        assert all(len(lista) <= indice.m for lista in nodo[1:])
    capas = indice.niveles()
    assert capas[0] == len(indice) == 3_000
    assert all(a >= b for a, b in zip(capas, capas[1:]))


def test_hnsw_visita_menos_puntos_que_la_fuerza_bruta(puntos, indice):
    indice.buscar(puntos[0], 10)
    assert indice.distancias_calculadas < len(puntos) / 2


def test_hnsw_resultados_ordenados_por_distancia(puntos, indice):
    resultado = indice.buscar(puntos[5] + 0.1, 10)
    distancias = [d for d, _ in resultado]
    assert distancias == sorted(distancias) and len(resultado) == 10


def test_similares_sin_el_producto_consultado(puntos, indice):
    for i in (3, 2_000):
        aproximado, exacto = similares_hnsw(indice, puntos, i, 10), similares_exactos(puntos, i, 10)
        assert len(aproximado) == len(exacto) == 10
        assert i not in {j for _, j in aproximado} | {j for _, j in exacto}
        assert recall_at_k(aproximado, exacto) >= 0.8


def test_hnsw_seleccion_simple(puntos):
    simple = HNSW(16, m=8, ef_construccion=64, ef_busqueda=32, seleccion="simple", semilla=9).agregar_todos(puntos)
    assert all(len(nodo[0]) <= simple.m0 for nodo in simple.vecinos)
    rng = np.random.default_rng(11)
    consultas = (puntos[rng.integers(0, len(puntos), 100)] + 0.3 * rng.standard_normal((100, 16))).astype(np.float32)
    recall = np.mean([recall_at_k(simple.buscar(q, 10), knn_fuerza_bruta(puntos, q, 10)) for q in consultas])
    assert recall > 0.8
    with pytest.raises(ValueError):
        HNSW(16, seleccion="otra")


def test_hnsw_vacio_y_un_punto():
    indice = HNSW(4)
    assert indice.buscar(np.zeros(4), 3) == []
    indice.agregar(np.ones(4))
    assert indice.buscar(np.zeros(4), 3) == [(pytest.approx(2.0), 0)]
