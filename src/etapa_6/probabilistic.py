"""Etapa 6: filtrado probabilístico (Bloom Filter y Count-Min Sketch).

Estructuras propias de la etapa:
- BloomFilter: arreglo de m bits y k funciones hash. Responde "posiblemente observado" o
  "seguro que no observado": puede dar falsos positivos, nunca falsos negativos.
- CountMinSketch: matriz de d filas × w contadores. Estima la frecuencia de una clave como el
  mínimo de sus d contadores, así que nunca subestima, y sobreestima a lo más ε·N con
  probabilidad 1 - δ.
- CMSVentana: un CountMinSketch por ventana de tiempo del stream (ventanas fijas de
  `ventana_s`). Retiene las últimas `n_ventanas` para aceptar eventos que llegan tarde.
- DetectorFraude: aplica las dos estructuras a cada compra y alerta de pago que entrega la
  etapa 2. El Bloom Filter reconoce cuentas, tarjetas y dispositivos señalados; el
  Count-Min Sketch estima las compras por tarjeta en la ventana. Toda alerta se verifica con
  una estructura exacta antes de bloquear la operación.

Las k (o d) posiciones de una clave se obtienen por doble hashing, g_i(x) = h1(x) + i·h2(x),
con h1 y h2 tomados de un único BLAKE2b de 128 bits (Kirsch y Mitzenmacher, 2006). Es
determinista entre ejecuciones, a diferencia del hash() de Python.

`set` y `dict` se usan solo como líneas base exactas en los experimentos.

notebooks/etapa6_filtrado_probabilistico.ipynb importa este código y lo explica.
"""

import hashlib
import json
import math
from array import array
from collections import Counter

from .. import config
from ..etapa_3.storage import TablaHash

RUTA_LISTAS = config.CARPETA_INTERIM / "listas_senalados.json"          # entrada: listas de la etapa 0

# Salidas de la etapa en data/interim/<CATEGORIA>/etapa_6/
CARPETA_ETAPA6 = config.CARPETA_INTERIM / "etapa_6"
RUTA_ALERTAS = CARPETA_ETAPA6 / "alertas.jsonl"                         # salida: etapas 8 y 9
RUTA_BLOQUEADAS = CARPETA_ETAPA6 / "ordenes_bloqueadas.json"

# ----- Parámetros de la etapa ----------------------------------------------------

P_FP = 0.01               # tasa de falsos positivos objetivo del Bloom Filter
EPSILON = 0.01            # error del CMS: sobreestimación <= ε·N, con N = compras de la ventana
DELTA = 0.01              # probabilidad de superar esa cota
VENTANA_S = 5 * 60        # duración de una ventana del CMS
N_VENTANAS = 288          # ventanas retenidas (24 h) para tolerar eventos que llegan tarde
UMBRAL = 5                # compras con la misma tarjeta en la ventana actual + la anterior


# ----- Hash ------------------------------------------------------------------------

def hashes_base(clave):
    """(h1, h2) de 64 bits a partir de un BLAKE2b de 128 bits. h2 es impar para que recorra
    todas las posiciones cuando el tamaño es potencia de 2."""
    d = hashlib.blake2b(clave.encode("utf-8"), digest_size=16).digest()
    return int.from_bytes(d[:8], "little"), int.from_bytes(d[8:], "little") | 1


# ----- Bloom Filter ----------------------------------------------------------------

class BloomFilter:
    """Bloom Filter sobre un bytearray de m bits con k funciones hash.

    Insertar y consultar cuestan O(k). Tras insertar n claves, la probabilidad de falso
    positivo es (1 - e^(-k·n/m))^k, mínima con k = (m/n)·ln 2.
    """

    def __init__(self, m, k):
        if m < 1 or k < 1:
            raise ValueError("m y k deben ser al menos 1")
        self.m, self.k = m, k
        self.bits = bytearray((m + 7) // 8)
        self.n = 0                                   # claves insertadas

    @classmethod
    def para(cls, n, p=P_FP):
        """Bloom Filter con m y k óptimos para n claves y tasa de falsos positivos p."""
        n = max(1, n)
        m = math.ceil(-n * math.log(p) / math.log(2) ** 2)
        k = max(1, round(m / n * math.log(2)))
        return cls(m, k)

    def _posiciones(self, clave):
        h1, h2 = hashes_base(clave)
        m = self.m
        return [(h1 + i * h2) % m for i in range(self.k)]

    def agregar(self, clave):
        bits = self.bits
        for p in self._posiciones(clave):
            bits[p >> 3] |= 1 << (p & 7)
        self.n += 1

    def contiene(self, clave):
        bits = self.bits
        for p in self._posiciones(clave):
            if not bits[p >> 3] & (1 << (p & 7)):
                return False
        return True

    __contains__ = contiene

    def tasa_fp_teorica(self):
        return (1 - math.exp(-self.k * self.n / self.m)) ** self.k

    def bits_encendidos(self):
        return sum(bin(b).count("1") for b in self.bits)

    @property
    def memoria_bytes(self):
        return len(self.bits)


# ----- Count-Min Sketch ------------------------------------------------------------

class CountMinSketch:
    """Matriz de d filas × w contadores de 64 bits.

    Actualizar y estimar cuestan O(d). Con w = ⌈e/ε⌉ y d = ⌈ln(1/δ)⌉ (Cormode y
    Muthukrishnan, 2005), la estimación f̂ cumple f <= f̂ <= f + ε·N con probabilidad
    1 - δ, donde N es la suma de todas las cuentas.
    """

    def __init__(self, w, d):
        if w < 1 or d < 1:
            raise ValueError("w y d deben ser al menos 1")
        self.w, self.d = w, d
        self.filas = [array("q", bytes(8 * w)) for _ in range(d)]
        self.total = 0                               # N

    @classmethod
    def para(cls, epsilon=EPSILON, delta=DELTA):
        return cls(math.ceil(math.e / epsilon), math.ceil(math.log(1 / delta)))

    def agregar(self, clave, cuenta=1):
        h1, h2 = hashes_base(clave)
        w = self.w
        for i, fila in enumerate(self.filas):
            fila[(h1 + i * h2) % w] += cuenta
        self.total += cuenta

    def estimar(self, clave):
        h1, h2 = hashes_base(clave)
        w = self.w
        return min(fila[(h1 + i * h2) % w] for i, fila in enumerate(self.filas))

    def limpiar(self):
        for fila in self.filas:
            fila[:] = array("q", bytes(8 * self.w))
        self.total = 0

    @property
    def epsilon(self):
        return math.e / self.w

    @property
    def memoria_bytes(self):
        return 8 * self.w * self.d


class CMSVentana:
    """Count-Min Sketch por ventanas fijas de tiempo del stream.

    La ventana de un evento es ⌊ts / ventana_s⌋. Se retienen `n_ventanas` sketches en un
    arreglo circular. La ventana v ocupa la posición v mod n_ventanas, y cuando llega una
    ventana más nueva que la guardada, el sketch se limpia y se reutiliza. Un evento cuya
    ventana ya salió del arreglo se descarta (llegó demasiado tarde) y se cuenta aparte.

    estimar(clave, ts) suma la ventana del evento y la anterior. Así, una ráfaga que cruza el
    borde entre dos ventanas no queda partida en dos.
    """

    def __init__(self, w, d, ventana_s=VENTANA_S, n_ventanas=N_VENTANAS):
        self.ventana_s = ventana_s
        self.sketches = [CountMinSketch(w, d) for _ in range(n_ventanas)]
        self.ids = [None] * n_ventanas               # ventana guardada en cada posición
        self.descartados = 0                         # eventos más viejos que las ventanas retenidas

    def _sketch(self, v, crear):
        pos = v % len(self.sketches)
        actual = self.ids[pos]
        if actual == v:
            return self.sketches[pos]
        if actual is not None and actual > v:
            return None                              # la posición ya tiene una ventana más nueva
        if not crear:
            return None
        self.sketches[pos].limpiar()
        self.ids[pos] = v
        return self.sketches[pos]

    def agregar(self, clave, ts, cuenta=1):
        sketch = self._sketch(int(ts // self.ventana_s), crear=True)
        if sketch is None:
            self.descartados += 1
            return False
        sketch.agregar(clave, cuenta)
        return True

    def estimar(self, clave, ts):
        v = int(ts // self.ventana_s)
        total = 0
        for u in (v, v - 1):
            sketch = self._sketch(u, crear=False)
            if sketch is not None:
                total += sketch.estimar(clave)
        return total

    def total(self, ts):
        """N de la ventana del evento y la anterior, con el que la cota de error es ε·N."""
        v = int(ts // self.ventana_s)
        return sum(s.total for u in (v, v - 1) if (s := self._sketch(u, crear=False)) is not None)

    @property
    def memoria_bytes(self):
        return sum(s.memoria_bytes for s in self.sketches)


# ----- Detección de fraude ---------------------------------------------------------

def cargar_listas(ruta=RUTA_LISTAS):
    """{"cuentas": [...], "tarjetas": [...], "dispositivos": [...]} generadas por la etapa 0."""
    return json.loads(ruta.read_text(encoding="utf-8"))


class DetectorFraude:
    """Revisa las compras y alertas de pago, en el orden de servicio de la etapa 2.

    1. Lista de señalados: la cuenta, la tarjeta y el dispositivo del evento se consultan en el
       Bloom Filter. Si alguno da positivo, se confirma en la TablaHash exacta de la etapa 3;
       un positivo no confirmado es un falso positivo del filtro y no bloquea nada.
    2. Intensidad: cada intento de pago (una orden completa) suma 1 a su
       tarjeta en el CMSVentana. Si la estimación llega a `umbral`, se cuentan exactamente las
       órdenes de esa tarjeta en las dos ventanas; solo si el conteo exacto también llega al
       umbral se emite la alerta, y la orden queda retenida para revisión.

    Los productos de una orden comparten tarjeta, ts, clase y plazo, así que la etapa 2 los
    entrega seguidos, y basta recordar la última orden contada para no contarla otra vez.

    La verificación exacta de la ráfaga recorre las órdenes de dos ventanas, O(órdenes en la
    ventana), pero solo se hace para las pocas tarjetas que el CMS marca.
    """

    def __init__(self, listas, p_fp=P_FP, epsilon=EPSILON, delta=DELTA, ventana_s=VENTANA_S,
                 n_ventanas=N_VENTANAS, umbral=UMBRAL):
        claves = ([f"cuenta:{c}" for c in listas["cuentas"]] + [f"tarjeta:{t}" for t in listas["tarjetas"]]
                  + [f"dispositivo:{d}" for d in listas["dispositivos"]])
        self.bloom = BloomFilter.para(len(claves), p_fp)
        self.exacta = TablaHash(len(claves))
        for c in claves:
            self.bloom.agregar(c)
            self.exacta.insertar(c, True)
        cms = CountMinSketch.para(epsilon, delta)
        self.cms = CMSVentana(cms.w, cms.d, ventana_s, n_ventanas)
        self.umbral = umbral
        self._ultima_orden = None
        # Órdenes de cada ventana retenida (tarjeta por orden), solo para verificar alertas
        self._compras = [[] for _ in range(n_ventanas)]
        self._ids_compras = [None] * n_ventanas
        self.metricas = Counter()

    def _registrar_compra(self, tarjeta, ts):
        v = int(ts // self.cms.ventana_s)
        pos = v % len(self._compras)
        if self._ids_compras[pos] != v:
            if self._ids_compras[pos] is not None and self._ids_compras[pos] > v:
                return
            self._compras[pos] = []
            self._ids_compras[pos] = v
        self._compras[pos].append(tarjeta)

    def conteo_exacto(self, tarjeta, ts):
        v = int(ts // self.cms.ventana_s)
        total = 0
        for u in (v, v - 1):
            pos = u % len(self._compras)
            if self._ids_compras[pos] == u:
                total += self._compras[pos].count(tarjeta)
        return total

    def procesar(self, e):
        """Devuelve la alerta del evento (dict) o None."""
        if e.event_type not in ("purchase", "payment_alert"):
            return None
        m = self.metricas
        m["revisados"] += 1

        # 1. Lista de señalados
        senalado = None
        for tipo, valor in (("cuenta", e.user_id), ("tarjeta", e.payment_id), ("dispositivo", e.device_id)):
            if valor is None:
                continue
            clave = f"{tipo}:{valor}"
            m["bloom_consultas"] += 1
            if clave in self.bloom:
                m["bloom_positivos"] += 1
                if clave in self.exacta:
                    senalado = senalado or clave
                else:
                    m["bloom_falsos_positivos"] += 1

        # 2. Intensidad por tarjeta: intentos de pago (órdenes) en la ventana
        estimado = exacto = None
        if e.event_type == "purchase" and e.payment_id is not None:
            if e.order_id is None or e.order_id != self._ultima_orden:
                m["intentos"] += 1
                if self.cms.agregar(e.payment_id, e.ts):
                    self._registrar_compra(e.payment_id, e.ts)
            self._ultima_orden = e.order_id
            estimado = self.cms.estimar(e.payment_id, e.ts)
            if estimado >= self.umbral:
                m["cms_alertas"] += 1
                exacto = self.conteo_exacto(e.payment_id, e.ts)
                if exacto < self.umbral:
                    m["cms_falsas_alertas"] += 1
        rafaga = exacto is not None and exacto >= self.umbral

        if senalado is None and not rafaga:
            return None
        # Una clave señalada confirmada bloquea. Una ráfaga sin clave señalada indica un patrón
        # sospechoso que no basta para probar el fraude, y la orden queda retenida para revisión.
        decision = "bloquear" if senalado else "revisar"
        motivos = ([senalado.split(":")[0]] if senalado else []) + (["rafaga"] if rafaga else [])
        m["alertas"] += 1
        m[decision] += 1
        for motivo in motivos:
            m[f"alertas_{motivo}"] += 1
        return {"event_id": e.event_id, "event_type": e.event_type, "ts": e.ts, "user_id": e.user_id,
                "order_id": e.order_id, "payment_id": e.payment_id, "device_id": e.device_id,
                "motivos": motivos, "clave_senalada": senalado, "estimado_cms": estimado,
                "conteo_exacto": exacto, "decision": decision}

    @property
    def memoria_bytes(self):
        """Memoria de las estructuras probabilísticas (sin la verificación exacta)."""
        return self.bloom.memoria_bytes + self.cms.memoria_bytes
