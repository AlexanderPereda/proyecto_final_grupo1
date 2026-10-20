"""Tests de las estructuras de la etapa 3: tabla hash, caché LRU, matriz dispersa y catálogo en disco.

Ejecutar desde la raíz del proyecto:
    python -m pytest tests -v
"""

import random

import pytest

from src.events import Event
from src.stage3_storage import (AlmacenCatalogo, CacheLRU, EtapaAlmacenamiento, MatrizDispersa,
                                TablaHash, _registro_producto, stock_inicial)


# ----- Tabla hash -----------------------------------------------------------------

@pytest.mark.parametrize("semilla", [0, 1, 2])
def test_tabla_hash_coincide_con_dict(semilla):
    """Una secuencia aleatoria de inserciones, actualizaciones y eliminaciones deja el mismo
    contenido que un dict."""
    rng = random.Random(semilla)
    tabla, referencia = TablaHash(), {}
    for _ in range(20_000):
        clave = f"k{rng.randrange(3_000)}"
        if rng.random() < 0.3:
            assert tabla.eliminar(clave) == (referencia.pop(clave, None) is not None)
        else:
            valor = rng.random()
            tabla.insertar(clave, valor)
            referencia[clave] = valor
    assert len(tabla) == len(referencia)
    assert dict(tabla.items()) == referencia
    for clave in (f"k{i}" for i in range(3_500)):
        assert tabla.obtener(clave) == referencia.get(clave)
        assert (clave in tabla) == (clave in referencia)


def test_tabla_hash_factor_de_carga_acotado():
    tabla = TablaHash()
    for i in range(10_000):
        tabla.insertar(i, i)
    assert tabla.factor_carga() <= tabla.carga_max
    assert tabla.redimensionamientos > 0


def test_tabla_hash_limpia_lapidas_sin_crecer():
    """Insertar y eliminar muchas claves distintas con pocas vivas no debe hacer crecer la tabla."""
    tabla = TablaHash(capacidad=100)
    capacidad = tabla.capacidad
    for i in range(50_000):
        tabla.insertar(f"x{i}", i)
        tabla.eliminar(f"x{i}")
    assert len(tabla) == 0
    assert tabla.capacidad == capacidad


# ----- Caché LRU ------------------------------------------------------------------

def test_lru_desaloja_el_menos_reciente():
    cache = CacheLRU(3)
    for clave in "abc":
        cache.poner(clave, clave.upper())
    assert cache.obtener("a") == "A"          # a pasa a ser el más reciente
    cache.poner("d", "D")                      # se desaloja b
    assert "b" not in cache
    assert list(cache.claves_por_uso()) == ["d", "a", "c"]
    assert cache.desalojos == 1


def test_lru_cuenta_aciertos_y_fallos():
    cache = CacheLRU(2)
    cache.poner("a", 1)
    assert cache.obtener("a") == 1
    assert cache.obtener("z") is None
    assert (cache.aciertos, cache.fallos) == (1, 1)


@pytest.mark.parametrize("capacidad", [1, 5, 50])
def test_lru_coincide_con_modelo_de_lista(capacidad):
    """Se compara contra un modelo simple: una lista ordenada por uso, de más a menos reciente."""
    rng = random.Random(capacidad)
    cache, modelo = CacheLRU(capacidad), []
    for _ in range(5_000):
        clave = rng.randrange(80)
        if rng.random() < 0.5:
            esperado = clave if clave in modelo else None
            assert cache.obtener(clave) == esperado
            if clave in modelo:
                modelo.remove(clave)
                modelo.insert(0, clave)
        else:
            cache.poner(clave, clave)
            if clave in modelo:
                modelo.remove(clave)
            modelo.insert(0, clave)
            del modelo[capacidad:]
        assert list(cache.claves_por_uso()) == modelo


# ----- Matriz dispersa ------------------------------------------------------------

def _matriz_aleatoria(semilla, n_usuarios=40, n_productos=25, n_eventos=600):
    rng = random.Random(semilla)
    matriz, densa = MatrizDispersa(), {}
    for _ in range(n_eventos):
        u, p = f"u{rng.randrange(n_usuarios)}", f"p{rng.randrange(n_productos)}"
        peso = rng.choice([1.0, 2.0, 4.0, -4.0])
        matriz.sumar(u, p, peso)
        densa[(u, p)] = densa.get((u, p), 0.0) + peso
    return matriz, densa


@pytest.mark.parametrize("semilla", [0, 1])
def test_csr_coincide_con_la_matriz_densa(semilla):
    matriz, densa = _matriz_aleatoria(semilla)
    csr = matriz.a_csr()
    valores = csr.a_densa()
    for i, u in enumerate(matriz.usuarios):
        for j, p in enumerate(matriz.productos):
            esperado = densa.get((u, p), 0.0)
            assert valores[i][j] == (esperado if esperado > 0 else 0.0)
            assert csr.valor(i, j) == valores[i][j]


def test_transpuesta():
    matriz, _ = _matriz_aleatoria(3)
    csr = matriz.a_csr()
    t = csr.transpuesta()
    densa, densa_t = csr.a_densa(), t.a_densa()
    assert t.nnz == csr.nnz
    assert all(densa[i][j] == densa_t[j][i] for i in range(csr.n_filas) for j in range(csr.n_columnas))


# ----- Catálogo y etapa -----------------------------------------------------------

def _fila(asin, precio):
    return {"parent_asin": asin, "title": f"Producto {asin}", "main_category": "Office Products",
            "categories": ["Office Products", "Paper"], "price": precio, "average_rating": 4.0,
            "rating_number": 10, "store": "Tienda"}


@pytest.fixture
def almacen(tmp_path):
    import json
    ruta = tmp_path / "catalogo.jsonl"
    with open(ruta, "wb") as f:
        for asin, precio in [("B001", "2.68"), ("B002", None), ("B003", "from 14.99"), ("B004", "—")]:
            f.write(json.dumps(_registro_producto(_fila(asin, precio)), ensure_ascii=False).encode("utf-8") + b"\n")
    a = AlmacenCatalogo(ruta)
    yield a
    a.cerrar()


def test_almacen_lee_registros_por_offset(almacen):
    assert len(almacen) == 4
    assert almacen.leer("B001")["precio"] == 2.68
    assert almacen.leer("B003")["precio"] == 14.99
    assert almacen.leer("B004")["precio"] is None
    assert almacen.leer("B004")["categoria_hoja"] == "Paper"
    assert almacen.leer("B999") is None


def _evento(tipo, asin, ts, precio=None, rating=None, usuario="U1"):
    return Event(event_id="e", event_type=tipo, user_id=usuario, parent_asin=asin, order_id="o",
                 order_type="standard", price=precio, payment_id=None, device_id=None, ts=ts, rating=rating)


def test_etapa_actualiza_estado_cache_y_matriz(almacen):
    etapa = EtapaAlmacenamiento(almacen, capacidad_cache=2)
    for tipo in ["view", "cart", "purchase"]:
        etapa.procesar(_evento(tipo, "B002", 100, precio=9.5 if tipo == "purchase" else None))
    etapa.procesar(_evento("review", "B002", 200, rating=5.0))
    e = etapa.estado("B002")
    assert (e.vistas, e.carritos, e.compras, e.resenas) == (1, 1, 1, 1)
    assert e.stock == stock_inicial("B002") - 1
    assert etapa.ficha("B002")["precio"] == 9.5          # el catálogo no tiene precio: se usa el observado
    assert etapa.matriz.valor("U1", "B002") == 1 + 2 + 4 + 1
    assert almacen.lecturas == 1                         # las demás consultas fueron aciertos de la caché


def test_etapa_agota_y_repone(almacen):
    etapa = EtapaAlmacenamiento(almacen)
    for _ in range(stock_inicial("B001")):
        etapa.procesar(_evento("purchase", "B001", 0, precio=2.68))
    assert not etapa.estado("B001").disponible
    etapa.procesar(_evento("purchase", "B001", 10, precio=2.68))
    assert etapa.estado("B001").ventas_sin_stock == 1
    etapa.procesar(_evento("view", "B001", 48 * 3600))
    assert etapa.estado("B001").disponible
