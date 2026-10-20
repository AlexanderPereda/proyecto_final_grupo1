"""Tests del B+Tree en disco de la etapa 4.

Se usan páginas de 128 bytes (3 entradas por hoja y 4 claves por nodo interno) para que el
árbol tenga varios niveles con pocos datos y se ejerciten todas las divisiones.

Ejecutar desde la raíz del proyecto:
    python -m pytest tests -v
"""

import random

import pytest

from src.stage4_bplustree import ArbolBMas, Hoja, Interno, capacidad_hoja, capacidad_interno, clave

PAGINA_CHICA = 128


def _entradas(n, semilla=0, precios_distintos=50):
    """n entradas con claves distintas y muchos precios repetidos."""
    rng = random.Random(semilla)
    asins = rng.sample(range(10**9), n)
    return [(rng.randrange(precios_distintos) / 4, f"B{a:09d}", i * 30, 30) for i, a in enumerate(asins)]


def _verificar_invariantes(arbol):
    """Claves ordenadas, separadores coherentes, nodos sin exceso y todas las hojas a la misma profundidad."""
    profundidades = set()

    def visitar(pagina, minimo, maximo, profundidad):
        nodo = arbol._leer(pagina)
        assert nodo.claves == sorted(nodo.claves)
        assert all((minimo is None or k >= minimo) and (maximo is None or k < maximo) for k in nodo.claves)
        if isinstance(nodo, Hoja):
            assert len(nodo.claves) <= arbol.max_hoja
            profundidades.add(profundidad)
            return
        assert isinstance(nodo, Interno)
        assert len(nodo.claves) <= arbol.max_interno
        assert len(nodo.hijos) == len(nodo.claves) + 1
        limites = [minimo] + nodo.claves + [maximo]
        for i, hijo in enumerate(nodo.hijos):
            visitar(hijo, limites[i], limites[i + 1], profundidad + 1)

    visitar(arbol.raiz, None, None, 1)
    assert profundidades == {arbol.altura}


def test_capacidades_de_pagina():
    assert capacidad_hoja(PAGINA_CHICA) == 3
    assert capacidad_interno(PAGINA_CHICA) == 4
    assert capacidad_hoja(4096) == 136
    assert capacidad_interno(4096) == 157


@pytest.mark.parametrize("semilla", [0, 1, 2])
def test_inserciones_y_busquedas(tmp_path, semilla):
    entradas = _entradas(500, semilla)
    with ArbolBMas(tmp_path / "a.bpt", tam_pagina=PAGINA_CHICA, capacidad_buffer=8, crear=True) as arbol:
        for e in entradas:
            arbol.insertar(*e)
        _verificar_invariantes(arbol)
        assert len(arbol) == len(entradas)
        assert arbol.altura > 3
        for p, a, o, l in entradas:
            assert arbol.buscar(p, a) == (o, l)
        assert arbol.buscar(0.0, "B999999999") is None
        assert [e[:2] for e in arbol] == sorted(e[:2] for e in entradas)


def test_insertar_clave_existente_actualiza(tmp_path):
    with ArbolBMas(tmp_path / "a.bpt", tam_pagina=PAGINA_CHICA, crear=True) as arbol:
        arbol.insertar(9.99, "B000000001", 0, 10)
        arbol.insertar(9.99, "B000000001", 50, 20)
        assert len(arbol) == 1
        assert arbol.buscar(9.99, "B000000001") == (50, 20)


@pytest.mark.parametrize("desde, hasta", [(0, 100), (2.5, 2.5), (3.1, 7.9), (11, 20), (-5, 0)])
def test_rango_coincide_con_filtro(tmp_path, desde, hasta):
    entradas = _entradas(400, 3)
    with ArbolBMas(tmp_path / "a.bpt", tam_pagina=PAGINA_CHICA, crear=True) as arbol:
        for e in entradas:
            arbol.insertar(*e)
        esperado = sorted((p, a) for p, a, _, _ in entradas if desde <= p <= hasta)
        assert [(p, a) for p, a, _, _ in arbol.rango(desde, hasta)] == esperado


@pytest.mark.parametrize("n", [0, 1, 3, 4, 50, 1_000])
def test_carga_masiva_equivale_a_inserciones(tmp_path, n):
    entradas = sorted(_entradas(n, 4), key=lambda e: clave(e[0], e[1]))
    with ArbolBMas(tmp_path / "a.bpt", tam_pagina=PAGINA_CHICA, crear=True) as arbol:
        arbol.carga_masiva(entradas)
        _verificar_invariantes(arbol)
        assert list(arbol) == [(p, a, o, l) for p, a, o, l in entradas]
        for p, a, o, l in entradas:
            assert arbol.buscar(p, a) == (o, l)
        for e in _entradas(200, 5):                     # el árbol sigue admitiendo inserciones
            arbol.insertar(*e)
        _verificar_invariantes(arbol)
        assert len(arbol) == len({e[:2] for e in entradas} | {e[:2] for e in _entradas(200, 5)})


def test_carga_masiva_rechaza_desorden(tmp_path):
    with ArbolBMas(tmp_path / "a.bpt", tam_pagina=PAGINA_CHICA, crear=True) as arbol:
        with pytest.raises(ValueError):
            arbol.carga_masiva([(2.0, "B000000002", 0, 1), (1.0, "B000000001", 1, 1)])


def test_eliminar(tmp_path):
    entradas = _entradas(300, 6)
    rng = random.Random(6)
    borradas = set(rng.sample(range(len(entradas)), 150))
    with ArbolBMas(tmp_path / "a.bpt", tam_pagina=PAGINA_CHICA, crear=True) as arbol:
        for e in entradas:
            arbol.insertar(*e)
        for i in borradas:
            assert arbol.eliminar(entradas[i][0], entradas[i][1])
        assert not arbol.eliminar(entradas[next(iter(borradas))][0], entradas[next(iter(borradas))][1])
        quedan = [e for i, e in enumerate(entradas) if i not in borradas]
        assert len(arbol) == len(quedan)
        assert [e[:2] for e in arbol] == sorted(e[:2] for e in quedan)
        assert [(p, a) for p, a, _, _ in arbol.rango(2, 6)] == sorted((p, a) for p, a, _, _ in quedan if 2 <= p <= 6)


def test_persistencia_en_disco(tmp_path):
    """Al reabrir el archivo, el árbol se lee desde las páginas guardadas."""
    ruta = tmp_path / "a.bpt"
    entradas = _entradas(300, 7)
    with ArbolBMas(ruta, tam_pagina=PAGINA_CHICA, crear=True) as arbol:
        for e in entradas:
            arbol.insertar(*e)
        altura = arbol.altura
    with ArbolBMas(ruta) as arbol:
        assert (arbol.tam_pagina, arbol.altura, len(arbol)) == (PAGINA_CHICA, altura, len(entradas))
        for p, a, o, l in entradas:
            assert arbol.buscar(p, a) == (o, l)
        assert arbol.lecturas > 0


def test_lecturas_por_busqueda_igual_a_la_altura(tmp_path):
    """Con el buffer pool de una sola página, cada búsqueda lee una página por nivel."""
    entradas = sorted(_entradas(2_000, 8), key=lambda e: clave(e[0], e[1]))
    ruta = tmp_path / "a.bpt"
    with ArbolBMas(ruta, tam_pagina=PAGINA_CHICA, crear=True) as arbol:
        arbol.carga_masiva(entradas)
    with ArbolBMas(ruta, capacidad_buffer=1) as arbol:
        p, a, _, _ = entradas[1234]
        antes = arbol.lecturas
        arbol.buscar(p, a)
        assert arbol.lecturas - antes == arbol.altura
