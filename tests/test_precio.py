"""Tests de la conversión del precio crudo del catálogo (etapa 1).

El catálogo de Office_Products guarda `price` como texto mezclado: "2.68", None,
"from 14.99", "—" y otros. Cada formato tiene su caso.
"""

import pytest

from src.stage1_preprocess import normalizar_precio, validar_y_normalizar


@pytest.mark.parametrize("crudo, esperado", [
    ("2.68", 2.68),                 # número simple
    (None, None),                   # sin precio
    ("from 14.99", 14.99),          # precio "desde"
    ("—", None),                    # guion largo, sin número
    ("", None),                     # texto vacío
    ("  ", None),                   # solo espacios
    ("None", None),                 # la palabra None como texto
    ("N/A", None),                  # texto sin dígitos
    ("$19.99", 19.99),              # símbolo de moneda
    ("19,99", 19.99),               # coma decimal
    ("$1,299.00", 1299.0),          # coma de miles con decimales
    ("1,299", 1299.0),              # coma de miles sin decimales
    ("14.99 - 29.99", 14.99),       # rango: se toma el primero
    ("-12.50", -12.5),              # negativo: se conserva para rechazarlo en la validación
    (3.5, 3.5),                     # número ya convertido
    (7, 7.0),                       # entero
])
def test_normalizar_precio(crudo, esperado):
    assert normalizar_precio(crudo) == esperado


def _compra(precio):
    return {"event_id": "e1", "event_type": "purchase", "user_id": "U1", "parent_asin": "B1",
            "order_id": "o1", "order_type": "standard", "price": precio, "payment_id": "p",
            "device_id": "d", "ts": 1_672_531_800_000, "category": "Office Products",
            "query": None, "rating": None}


def test_compra_con_precio_desde_es_valida():
    evento, motivo = validar_y_normalizar(_compra("from 14.99"))
    assert motivo is None and evento.price == 14.99


def test_compra_sin_numero_en_el_precio_se_rechaza():
    # una compra exige precio; "—" no tiene número
    assert validar_y_normalizar(_compra("—")) == (None, "precio_invalido")


def test_compra_con_precio_negativo_se_rechaza():
    assert validar_y_normalizar(_compra("-12.50")) == (None, "precio_invalido")


def test_vista_con_precio_sin_numero_es_valida():
    # fuera de las compras el precio es opcional
    vista = {**_compra("—"), "event_type": "view", "order_id": None, "order_type": None}
    evento, motivo = validar_y_normalizar(vista)
    assert motivo is None and evento.price is None
