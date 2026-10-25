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
- dividir_listas: parte las listas de señalados de la etapa 0 en las claves que conoce el
  detector y las nuevas, para que la evaluación no sea circular.
- ejecutar_etapa: recorre el stream de la etapa 2 con el detector, escribe las salidas de la
  etapa y evalúa las decisiones contra la verdad de referencia de la etapa 0.

Las k posiciones de una clave en el Bloom Filter y sus d posiciones en el Count-Min Sketch, una
por fila, son valores independientes de 64 bits tomados de un único BLAKE2b de la clave
(`posiciones`). Es determinista entre ejecuciones, a diferencia del hash() de Python.
`hashes_base` da los dos valores del doble hashing, g_i(x) = h1(x) + i·h2(x) (Kirsch y
Mitzenmacher, 2006), que el notebook usa solo para compararlo con las posiciones independientes.

`set` y `dict` se usan solo como líneas base exactas en los experimentos.

notebooks/etapa6_filtrado_probabilistico.ipynb importa este código y lo explica.
"""

import hashlib
import json
import math
import random
import struct
import time
import tracemalloc
from array import array
from collections import Counter

import numpy as np

from .. import config
from ..etapa_3.storage import RUTA_EVENTOS_ETAPA2, TablaHash, leer_eventos

RUTA_LISTAS = config.CARPETA_INTERIM / "listas_senalados.json"          # entrada: listas de la etapa 0

# Salidas de la etapa en data/interim/<CATEGORIA>/etapa_6/
CARPETA_ETAPA6 = config.CARPETA_INTERIM / "etapa_6"
RUTA_ALERTAS = CARPETA_ETAPA6 / "alertas.jsonl"                         # salida: etapas 8 y 9
RUTA_BLOQUEADAS = CARPETA_ETAPA6 / "ordenes_bloqueadas.json"
RUTA_METRICAS = CARPETA_ETAPA6 / "metricas.json"                        # conteos, tiempos, memoria y evaluación
RUTA_COMPRAS = CARPETA_ETAPA6 / "compras.npz"                           # por compra: CMS, conteo exacto y verdad

# ----- Parámetros de la etapa ----------------------------------------------------

P_FP = 0.01               # tasa de falsos positivos objetivo del Bloom Filter
EPSILON = 0.01            # error del CMS: sobreestimación <= ε·N, con N = compras de la ventana
DELTA = 0.01              # probabilidad de superar esa cota
VENTANA_S = 5 * 60        # duración de una ventana del CMS
N_VENTANAS = 288          # ventanas retenidas (24 h) para tolerar eventos que llegan tarde
UMBRAL = 5                # compras con la misma tarjeta en la ventana actual + la anterior
FRACCION_CONOCIDA = 0.5   # fracción de cada lista de señalados que conoce el detector


# ----- Hash ------------------------------------------------------------------------

def hashes_base(clave):
    """(h1, h2) de 64 bits a partir de un BLAKE2b de 128 bits. h2 es impar para que recorra
    todas las posiciones cuando el tamaño es potencia de 2."""
    d = hashlib.blake2b(clave.encode("utf-8"), digest_size=16).digest()
    return int.from_bytes(d[:8], "little"), int.from_bytes(d[8:], "little") | 1


_VALORES_64 = [struct.Struct(f"<{i}Q") for i in range(9)]        # i enteros de 64 bits


def posiciones(clave, cantidad, modulo):
    """`cantidad` posiciones independientes en [0, modulo) para una clave.

    Cada posición es un valor de 64 bits de un BLAKE2b de la clave, reducido módulo `modulo`.
    Un BLAKE2b de 64 bytes da 8 valores; si hacen falta más, se calcula otro con un salt distinto.
    """
    datos = clave.encode("utf-8")
    if cantidad <= 8:                                                  # caso habitual: un solo BLAKE2b
        digest = hashlib.blake2b(datos, digest_size=8 * cantidad).digest()
        return [v % modulo for v in _VALORES_64[cantidad].unpack(digest)]
    resultado = []
    for bloque in range(math.ceil(cantidad / 8)):
        faltan = min(cantidad - len(resultado), 8)
        digest = hashlib.blake2b(datos, digest_size=8 * faltan, salt=bloque.to_bytes(16, "little")).digest()
        resultado.extend(v % modulo for v in _VALORES_64[faltan].unpack(digest))
    return resultado


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
        return posiciones(clave, self.k, self.m)

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
    1 - δ, donde N es la suma de todas las cuentas. La posición de una clave en cada fila es
    un valor independiente de `posiciones`, como pide esa cota.
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
        for fila, p in zip(self.filas, posiciones(clave, self.d, self.w)):
            fila[p] += cuenta
        self.total += cuenta

    def estimar(self, clave):
        return min(fila[p] for fila, p in zip(self.filas, posiciones(clave, self.d, self.w)))

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


TIPOS_SENALADOS = (("cuentas", "cuenta"), ("tarjetas", "tarjeta"), ("dispositivos", "dispositivo"))


def claves_senaladas(listas):
    """Claves del Bloom Filter: cada valor con su tipo como prefijo, por ejemplo "tarjeta:pay_…"."""
    return [f"{prefijo}:{valor}" for tipo, prefijo in TIPOS_SENALADOS for valor in listas[tipo]]


def dividir_listas(listas, fraccion=FRACCION_CONOCIDA, semilla=config.SEED):
    """Parte cada lista de señalados en las claves que conoce el detector y las nuevas.

    La etapa 0 usa las claves de las listas en todas sus ráfagas de fraude. Si el detector las
    conociera todas, detectaría todo el fraude por construcción. Con una fracción conocida, las
    demás representan defraudadores que todavía no están en la lista y que solo la regla de
    ráfaga puede detectar. Devuelve (conocidas, nuevas), con la misma forma que listas.
    """
    rng = random.Random(semilla)
    conocidas, nuevas = {}, {}
    for tipo, _ in TIPOS_SENALADOS:
        valores = sorted(listas[tipo])
        rng.shuffle(valores)
        corte = round(fraccion * len(valores))
        conocidas[tipo], nuevas[tipo] = valores[:corte], valores[corte:]
    return conocidas, nuevas


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
        claves = claves_senaladas(listas)
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


# ----- Ejecución sobre el stream ---------------------------------------------------

def ejecutar_etapa(conocidas, listas, ruta_eventos=RUTA_EVENTOS_ETAPA2, ruta_alertas=RUTA_ALERTAS,
                   ruta_bloqueadas=RUTA_BLOQUEADAS, ruta_metricas=RUTA_METRICAS, ruta_compras=RUTA_COMPRAS):
    """Ejecuta la etapa 6 sobre el stream de la etapa 2, en su orden de servicio, y evalúa el detector.

    El detector conoce solo `conocidas`. La evaluación usa `listas`, las listas completas de la
    etapa 0: un evento es fraude si su cuenta está en la lista de cuentas, porque solo las ráfagas
    de fraude usan esas cuentas. El fraude se separa según tenga alguna clave conocida o sus tres
    claves nuevas. La verdad de referencia, el conteo exacto de órdenes por (tarjeta, ventana) y
    las claves distintas consultadas se usan solo para evaluar; el detector no los consulta.

    Escribe las alertas, las órdenes bloqueadas, las métricas y, por compra, la estimación del CMS,
    el conteo exacto, si es fraude y si tiene alguna clave conocida. Devuelve las métricas.
    """
    cuentas_fraude = set(listas["cuentas"])                        # solo para evaluar
    claves_conocidas = set(claves_senaladas(conocidas))            # solo para evaluar

    tracemalloc.start()
    detector = DetectorFraude(conocidas)
    memoria_detector = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    ventana_s = detector.cms.ventana_s
    exacto_ventana = Counter()                     # (tarjeta, ventana) -> órdenes distintas: línea base exacta
    ordenes_contadas = set()
    estimado, conteo, fraude, conocida = [], [], [], []           # una posición por compra
    evaluacion = Counter()
    ordenes_bloqueadas = set()
    en_bloom = {}                                  # clave no conocida -> si da positivo en el Bloom Filter
    eventos = 0
    t_detector = 0.0
    t0 = time.perf_counter()

    ruta_alertas.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta_alertas, "w", encoding="utf-8") as salida:
        for e in leer_eventos(ruta_eventos):
            eventos += 1
            t = time.perf_counter()
            alerta = detector.procesar(e)
            t_detector += time.perf_counter() - t

            if e.event_type in ("purchase", "payment_alert"):
                es_fraude = e.user_id in cuentas_fraude
                claves = [f"{tipo}:{valor}" for tipo, valor in (("cuenta", e.user_id), ("tarjeta", e.payment_id),
                                                                ("dispositivo", e.device_id)) if valor is not None]
                con_clave_conocida = any(c in claves_conocidas for c in claves)
                for c in claves:
                    if c not in claves_conocidas and c not in en_bloom:
                        en_bloom[c] = c in detector.bloom
                decision = alerta["decision"] if alerta is not None else "aprobar"
                evaluacion[f"{decision}_{'fraude' if es_fraude else 'legitimo'}"] += 1
                if es_fraude:
                    evaluacion[f"{decision}_fraude_{'conocido' if con_clave_conocida else 'nuevo'}"] += 1
                evaluacion[f"{e.event_type}_{'fraude' if es_fraude else 'legitimo'}"] += 1
                if e.event_type == "purchase" and e.payment_id is not None:
                    v = int(e.ts // ventana_s)
                    if e.order_id not in ordenes_contadas:
                        ordenes_contadas.add(e.order_id)
                        exacto_ventana[(e.payment_id, v)] += 1
                    estimado.append(detector.cms.estimar(e.payment_id, e.ts))
                    conteo.append(exacto_ventana[(e.payment_id, v)] + exacto_ventana.get((e.payment_id, v - 1), 0))
                    fraude.append(es_fraude)
                    conocida.append(con_clave_conocida)
            if alerta is not None:
                alerta["fraude_real"] = e.user_id in cuentas_fraude
                salida.write(json.dumps(alerta) + "\n")
                if alerta["decision"] == "bloquear" and e.order_id is not None:
                    ordenes_bloqueadas.add(e.order_id)
            if eventos % 2_000_000 == 0:
                print(f"{eventos:,} eventos ({time.perf_counter() - t0:.0f} s)", flush=True)

    estimado, conteo = np.array(estimado, dtype=np.int32), np.array(conteo, dtype=np.int32)
    fraude, conocida = np.array(fraude, dtype=bool), np.array(conocida, dtype=bool)
    np.savez_compressed(ruta_compras, estimado=estimado, conteo=conteo, fraude=fraude, conocida=conocida)
    ruta_bloqueadas.write_text(json.dumps(sorted(ordenes_bloqueadas)), encoding="utf-8")

    ev = evaluacion
    alertas_fraude = ev["bloquear_fraude"] + ev["revisar_fraude"]
    alertas_legitimas = ev["bloquear_legitimo"] + ev["revisar_legitimo"]
    total_fraude = alertas_fraude + ev["aprobar_fraude"]
    error = estimado.astype(np.int64) - conteo
    metricas = {
        "eventos": eventos, "detector": dict(detector.metricas), "evaluacion": dict(evaluacion),
        "precision_alertas": alertas_fraude / max(1, alertas_fraude + alertas_legitimas),
        "recall_alertas": alertas_fraude / max(1, total_fraude),
        "precision_bloqueos": ev["bloquear_fraude"] / max(1, ev["bloquear_fraude"] + ev["bloquear_legitimo"]),
        "recall_bloqueos": ev["bloquear_fraude"] / max(1, total_fraude),
        "ordenes_bloqueadas": len(ordenes_bloqueadas),
        "cms_descartados_por_tardios": detector.cms.descartados,
        "cms_error_medio": float(error.mean()), "cms_error_max": int(error.max()),
        "cms_subestimaciones": int((error < 0).sum()),
        "cms_compras_con_error": int((error > 0).sum()), "compras": len(error),
        "tiempo_total_s": time.perf_counter() - t0, "tiempo_detector_s": t_detector,
        "us_por_evento_revisado": t_detector / max(1, detector.metricas["revisados"]) * 1e6,
        "memoria_bloom_bytes": detector.bloom.memoria_bytes, "memoria_cms_bytes": detector.cms.memoria_bytes,
        "memoria_detector_tracemalloc_bytes": memoria_detector,
        "bloom": {"m": detector.bloom.m, "k": detector.bloom.k, "n": detector.bloom.n,
                  "tasa_fp_teorica": detector.bloom.tasa_fp_teorica()},
        "bloom_claves_no_conocidas": len(en_bloom), "bloom_falsos_positivos_distintos": sum(en_bloom.values()),
        "cms": {"w": detector.cms.sketches[0].w, "d": detector.cms.sketches[0].d,
                "ventana_s": ventana_s, "n_ventanas": len(detector.cms.sketches), "umbral": detector.umbral},
        "pares_tarjeta_ventana_exactos": len(exacto_ventana),
    }
    ruta_metricas.write_text(json.dumps(metricas, indent=2), encoding="utf-8")
    return metricas
