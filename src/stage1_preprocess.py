"""Etapa 1: validación, normalización, merge sort, deduplicación y búsqueda binaria.

Código copiado de notebooks/etapa1_preprocesamiento.ipynb (pasos 3 a 8). Se agregaron dos
parámetros opcionales para observar las decisiones de la etapa sin cambiar su comportamiento:
`al_descartar` en deduplicar_lote y `observador` en Preprocesador.
"""

import re
import time
from collections import Counter
from datetime import datetime, timezone

from .events import Event
from .stage0_queue import ColaEnlazada

TIPOS_EVENTO = {"search", "view", "cart", "purchase", "review", "return", "payment_alert"}
TIPOS_ORDEN = {"express", "standard", "scheduled"}
TIPOS_CON_ORDEN = {"cart", "purchase", "return", "payment_alert"}
TS_MIN = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
TS_MAX = datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp()
TOLERANCIA_S = 1.0
VENTANA_S = 600.0


# ----- Validación y normalización ---------------------------------------------

def _texto(valor):
    """Devuelve el texto sin espacios en los extremos, o None si está vacío o no es texto."""
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    return valor or None


def normalizar_ts(valor):
    """Convierte el timestamp a segundos. Devuelve None si no es válido."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    ts = valor / 1000 if valor > 1e11 else float(valor)     # ms -> s
    return ts if TS_MIN <= ts <= TS_MAX else None


_NUMERO = re.compile(r"-?\d[\d,]*(?:\.\d+)?")     # primer número del texto, con signo opcional


def normalizar_precio(valor):
    """Convierte el precio crudo a float, o a None si no contiene ningún número.

    El catálogo mezcla formatos: "2.68", None, "from 14.99", "—", "$1,299.00" y, en los
    duplicados sintéticos, "19,99". Se toma el primer número del texto:
    - "from 14.99" -> 14.99 y "14.99 - 29.99" -> 14.99 (precio desde);
    - una coma seguida de exactamente dos dígitos al final es decimal: "19,99" -> 19.99;
    - cualquier otra coma es de miles: "1,299.00" -> 1299.0;
    - "—", "", "None" o un texto sin dígitos -> None.
    El signo se conserva para que un precio negativo se rechace en la validación.
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)
    texto = str(valor).strip().replace("$", "")
    if texto.lower() in {"", "none", "null", "nan"}:
        return None
    coincidencia = _NUMERO.search(texto)
    if coincidencia is None:
        return None                          # "—", "N/A" u otro texto sin número
    numero = coincidencia.group()
    if re.fullmatch(r"-?\d+,\d{2}", numero):
        numero = numero.replace(",", ".")    # coma decimal: "19,99"
    else:
        numero = numero.replace(",", "")     # coma de miles: "1,299.00"
    return float(numero)


def normalizar_categoria(valor):
    texto = _texto(valor)
    return re.sub(r"\s+", "_", texto.lower()) if texto else None


def normalizar_query(valor):
    texto = _texto(valor)
    return re.sub(r"\s+", " ", texto.lower()) if texto else None


def validar_y_normalizar(r):
    """Devuelve (Event, None) si el registro es válido o (None, motivo) si se rechaza."""
    if not isinstance(r, dict):
        return None, "no_es_registro"

    tipo = _texto(r.get("event_type"))
    tipo = tipo.lower() if tipo else None
    if tipo not in TIPOS_EVENTO:
        return None, "tipo_desconocido"

    usuario = _texto(r.get("user_id"))
    if usuario is None:
        return None, "sin_user_id"

    ts = normalizar_ts(r.get("ts"))
    if ts is None:
        return None, "ts_invalido"

    asin = _texto(r.get("parent_asin"))
    if asin is None and tipo != "search":
        return None, "sin_parent_asin"

    orden = _texto(r.get("order_id"))
    if orden is None and tipo in TIPOS_CON_ORDEN:
        return None, "sin_order_id"

    tipo_orden = _texto(r.get("order_type"))
    tipo_orden = tipo_orden.lower() if tipo_orden else None
    if (tipo_orden is not None and tipo_orden not in TIPOS_ORDEN) or (tipo == "purchase" and tipo_orden is None):
        return None, "tipo_orden_invalido"

    try:
        precio = normalizar_precio(r.get("price"))
    except ValueError:
        return None, "precio_invalido"
    if (precio is not None and precio <= 0) or (tipo == "purchase" and precio is None):
        return None, "precio_invalido"

    rating = r.get("rating")
    if tipo == "review":
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 1 <= rating <= 5:
            return None, "rating_invalido"
        rating = float(rating)
    else:
        rating = None

    evento = Event(
        event_id=_texto(r.get("event_id")) or "",
        event_type=tipo,
        user_id=usuario.upper(),
        parent_asin=asin.upper() if asin else None,
        order_id=orden,
        order_type=tipo_orden,
        price=precio,
        payment_id=_texto(r.get("payment_id")),
        device_id=_texto(r.get("device_id")),
        ts=ts,
        category=normalizar_categoria(r.get("category")),
        query=normalizar_query(r.get("query")),
        rating=rating,
    )
    return evento, None


# ----- Merge sort ---------------------------------------------------------------

def merge_sort(items, clave=None):
    """Devuelve una lista nueva con items ordenados por clave(item). Estable. O(n log n)."""
    if clave is None:
        clave = lambda x: x
    pares = [(clave(x), x) for x in items]          # la clave se calcula una sola vez
    return [x for _, x in _merge_sort_pares(pares)]


def _merge_sort_pares(pares):
    if len(pares) <= 1:                             # caso base: ya está ordenado
        return pares
    medio = len(pares) // 2
    izquierda = _merge_sort_pares(pares[:medio])    # dividir y ordenar cada mitad
    derecha = _merge_sort_pares(pares[medio:])
    return _mezclar(izquierda, derecha)             # combinar


def _mezclar(izquierda, derecha):
    """Mezcla dos listas de pares ya ordenadas. O(len(izquierda) + len(derecha))."""
    resultado = []
    i = j = 0
    while i < len(izquierda) and j < len(derecha):
        if izquierda[i][0] <= derecha[j][0]:        # <= mantiene la estabilidad
            resultado.append(izquierda[i])
            i += 1
        else:
            resultado.append(derecha[j])
            j += 1
    resultado.extend(izquierda[i:])                 # lo que sobra ya está ordenado
    resultado.extend(derecha[j:])
    return resultado


# ----- Deduplicación dentro del lote --------------------------------------------

def clave_dedup(e):
    """Campos que identifican el hecho. None se reemplaza por '' para poder comparar tuplas."""
    return (e.event_type, e.user_id, e.parent_asin or "", e.order_id or "", e.query or "")


def deduplicar_lote(eventos, tolerancia_s=TOLERANCIA_S, al_descartar=None):
    """Ordena por (clave, ts) y elimina duplicados adyacentes.

    Devuelve (unicos ordenados por clave y ts, numero de duplicados exactos, numero de casi exactos).
    al_descartar(descartado, conservado, tipo): función opcional que se llama con cada duplicado.
    """
    ordenados = merge_sort(eventos, clave=lambda e: (clave_dedup(e), e.ts))
    unicos = []
    exactos = casi = 0
    clave_anterior = None
    for e in ordenados:
        clave = clave_dedup(e)
        if unicos and clave == clave_anterior and e.ts - unicos[-1].ts <= tolerancia_s:
            tipo = "exacto" if e.event_id == unicos[-1].event_id else "casi"
            if tipo == "exacto":
                exactos += 1
            else:
                casi += 1
            if al_descartar is not None:
                al_descartar(e, unicos[-1], tipo)
            continue                     # duplicado: no se conserva
        unicos.append(e)
        clave_anterior = clave
    return unicos, exactos, casi


# ----- Deduplicación entre lotes con búsqueda binaria ---------------------------

def busqueda_binaria_izq(arreglo, x):
    """Primera posición i tal que arreglo[i] >= x (arreglo ordenado). O(log n)."""
    bajo, alto = 0, len(arreglo)
    while bajo < alto:
        medio = (bajo + alto) // 2
        if arreglo[medio] < x:
            bajo = medio + 1          # x está a la derecha de medio
        else:
            alto = medio              # x está en medio o a su izquierda
    return bajo


class VentanaDeClaves:
    """Arreglo ordenado de (clave, ts, event_id) de los eventos aceptados recientemente."""

    def __init__(self, ventana_s=VENTANA_S, tolerancia_s=TOLERANCIA_S):
        self.entradas = []
        self.ventana_s = ventana_s
        self.tolerancia_s = tolerancia_s
        self.ts_max = float("-inf")

    def buscar(self, clave, ts):
        """Devuelve el event_id del evento ya visto con la misma clave y ts cercano, o None. O(log w)."""
        i = busqueda_binaria_izq(self.entradas, (clave, ts - self.tolerancia_s))
        if i < len(self.entradas):
            clave_i, ts_i, id_i = self.entradas[i]
            if clave_i == clave and ts_i <= ts + self.tolerancia_s:
                return id_i
        return None

    def agregar_lote(self, eventos_ordenados):
        """Agrega eventos ya ordenados por (clave, ts) y descarta los vencidos. O(w + b)."""
        nuevas = [(clave_dedup(e), e.ts, e.event_id) for e in eventos_ordenados]
        if nuevas:
            self.ts_max = max(self.ts_max, max(n[1] for n in nuevas))
        limite = self.ts_max - self.ventana_s
        vigentes = [x for x in self.entradas if x[1] >= limite]
        self.entradas = [x for _, x in _mezclar([(x, x) for x in vigentes], [(x, x) for x in nuevas])]

    def __len__(self):
        return len(self.entradas)


# ----- Etapa completa -----------------------------------------------------------

class Preprocesador:
    """Etapa 1: valida, normaliza, deduplica y reordena cada micro-lote.

    observador(accion, **datos): función opcional que recibe cada decisión de la etapa.
      - "rechazado": registro, motivo
      - "valido": registro, evento
      - "duplicado": evento, donde ("lote" o "ventana"), tipo ("exacto" o "casi"), original_id,
        original (el Event conservado si el duplicado está en el mismo lote; None si está en la ventana)
    """

    def __init__(self, ventana_s=VENTANA_S, tolerancia_s=TOLERANCIA_S, observador=None):
        self.rechazados = ColaEnlazada()          # (motivo, registro crudo)
        self.motivos = Counter()
        self.ventana = VentanaDeClaves(ventana_s, tolerancia_s)
        self.tolerancia_s = tolerancia_s
        self.metricas = Counter()
        self.tiempos = Counter()                  # segundos acumulados por paso
        self.observador = observador

    def _avisar(self, accion, **datos):
        if self.observador is not None:
            self.observador(accion, **datos)

    def procesar_lote(self, registros):
        m = self.metricas
        m["lotes"] += 1
        m["entrada"] += len(registros)

        # Pasos 1 y 2: validar y normalizar
        t0 = time.perf_counter()
        validos = []
        for r in registros:
            evento, motivo = validar_y_normalizar(r)
            if motivo:
                self.rechazados.encolar((motivo, r))
                self.motivos[motivo] += 1
                self._avisar("rechazado", registro=r, motivo=motivo)
            else:
                validos.append(evento)
                self._avisar("valido", registro=r, evento=evento)
        t1 = time.perf_counter()

        # Paso 3: ordenar por (clave, ts) y eliminar duplicados adyacentes
        def al_descartar(descartado, conservado, tipo):
            self._avisar("duplicado", evento=descartado, donde="lote", tipo=tipo,
                         original_id=conservado.event_id, original=conservado)

        unicos, exactos, casi = deduplicar_lote(validos, self.tolerancia_s, al_descartar)
        m["dup_lote_exactos"] += exactos
        m["dup_lote_casi"] += casi
        t2 = time.perf_counter()

        # Paso 4: eliminar duplicados de lotes anteriores (búsqueda binaria en la ventana)
        nuevos = []
        for e in unicos:
            id_visto = self.ventana.buscar(clave_dedup(e), e.ts)
            if id_visto is None:
                nuevos.append(e)
            else:
                tipo = "exacto" if id_visto == e.event_id else "casi"
                m["dup_ventana_exactos" if tipo == "exacto" else "dup_ventana_casi"] += 1
                self._avisar("duplicado", evento=e, donde="ventana", tipo=tipo, original_id=id_visto,
                             original=None)
        self.ventana.agregar_lote(nuevos)          # nuevos sigue ordenado por (clave, ts)
        m["tamano_ventana_max"] = max(m["tamano_ventana_max"], len(self.ventana))
        t3 = time.perf_counter()

        # Paso 5: reordenar por ts
        salida = merge_sort(nuevos, clave=lambda e: e.ts)
        t4 = time.perf_counter()

        m["salida"] += len(salida)
        self.tiempos["validar_normalizar"] += t1 - t0
        self.tiempos["ordenar_dedup_lote"] += t2 - t1
        self.tiempos["dedup_ventana"] += t3 - t2
        self.tiempos["reordenar_ts"] += t4 - t3
        return salida
