"""Lectura del dataset como stream y generador sintético de eventos (parte de la etapa 0).

Código copiado de notebooks/etapa0_ingesta_buffer.ipynb (pasos 2, 3 y 4).
"""

import heapq                      # solo se usa dentro del simulador, no es una estructura evaluada
import json
import random
import re
import zlib
from collections import Counter, deque

SEED = 42
T0_MS = 1_672_531_200_000              # 2023-01-01 00:00:00 UTC en milisegundos
VENTANA_RECORRIDO_MS = 30 * 60 * 1000  # el recorrido empieza como máximo 30 min antes de la reseña
N_GRUPOS_CO_COMPRA = 500
CAMPOS = ["event_id", "event_type", "user_id", "parent_asin", "order_id", "order_type",
          "price", "payment_id", "device_id", "ts", "category", "query", "rating"]


def leer_resenas(ruta, limite=None):
    """Recorre el archivo JSONL de reseñas línea por línea y entrega un registro a la vez.

    limite: número máximo de reseñas a leer (None lee el archivo completo).
    """
    with open(ruta, "r", encoding="utf-8") as archivo:
        for i, linea in enumerate(archivo):
            if limite is not None and i >= limite:
                break
            r = json.loads(linea)
            # Solo se guardan los campos necesarios para generar eventos
            yield {
                "user_id": r["user_id"],
                "parent_asin": r["parent_asin"],
                "rating": r["rating"],
                "verified_purchase": r["verified_purchase"],
            }


def cargar_catalogo(ruta):
    """Lee los metadatos línea por línea y devuelve {parent_asin: (precio, categoria, titulo)}."""
    catalogo = {}
    with open(ruta, "r", encoding="utf-8") as archivo:
        for linea in archivo:
            m = json.loads(linea)
            catalogo[m["parent_asin"]] = (m.get("price"), m.get("main_category"), m.get("title"))
    return catalogo


def hash_estable(texto):
    """Hash determinista. El hash() de Python cambia en cada ejecución y rompería la reproducibilidad."""
    return zlib.crc32(texto.encode("utf-8"))


class GeneradorEventos:
    """Convierte reseñas reales en un stream sintético de eventos de comercio electrónico."""

    def __init__(self, catalogo, semilla=SEED,
                 intervalo_medio_s=2.0,       # tiempo medio entre reseñas en el reloj de reproducción
                 p_duplicado_exacto=0.03,
                 p_duplicado_casi=0.015,
                 p_malformado=0.01,
                 p_tardio=0.05,
                 p_producto_extra=0.25,       # orden con más de un producto
                 p_rafaga_fraude=0.002,
                 p_alerta_fraude=0.30,
                 p_alerta_legitima=0.003):
        self.catalogo = catalogo
        self.rng = random.Random(semilla)
        self.intervalo_medio_ms = intervalo_medio_s * 1000
        self.p_duplicado_exacto = p_duplicado_exacto
        self.p_duplicado_casi = p_duplicado_casi
        self.p_malformado = p_malformado
        self.p_tardio = p_tardio
        self.p_producto_extra = p_producto_extra
        self.p_rafaga_fraude = p_rafaga_fraude
        self.p_alerta_fraude = p_alerta_fraude
        self.p_alerta_legitima = p_alerta_legitima

        self.reloj_ms = T0_MS            # hora de la reseña actual en el reloj de reproducción
        self.n_eventos = 0               # contador para event_id
        self.n_ordenes = 0               # contador para order_id
        self.pendientes = []             # montículo (hora_llegada, secuencia, registro)
        self.n_programados = 0           # desempate dentro del montículo
        self.recientes_por_grupo = {}    # grupo -> últimos productos vistos (para co-compra)
        self.estadisticas = Counter()    # verdad de referencia de lo inyectado

        # Listas de señalados para la detección de fraude (etapa 6)
        self.cuentas_fraude = ["A" + "".join(self.rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567", k=27))
                               for _ in range(30)]
        self.tarjetas_fraude = [f"pay_{self.rng.getrandbits(32):08x}" for _ in range(200)]
        self.dispositivos_fraude = [f"dev_{self.rng.getrandbits(32):08x}" for _ in range(50)]

    # ----- Datos auxiliares de cada evento ------------------------------------

    def _nuevo_event_id(self):
        self.n_eventos += 1
        return f"e{self.n_eventos:09d}"

    def _nuevo_order_id(self):
        self.n_ordenes += 1
        return f"o{self.n_ordenes:08d}"

    def _registro(self, event_type, user_id, ts, **campos):
        """Crea un registro con todas las claves; las que no se indican quedan en None."""
        r = dict.fromkeys(CAMPOS)
        r.update(event_id=self._nuevo_event_id(), event_type=event_type, user_id=user_id, ts=int(ts))
        r.update(campos)
        self.estadisticas[f"tipo_{event_type}"] += 1
        return r

    def _precio_crudo(self, asin):
        """Precio del catálogo como texto. Si falta, se usa un precio sintético estable por producto."""
        precio = self.catalogo.get(asin, (None, None, None))[0]
        if precio is None:
            precio = 5 + (hash_estable(asin) % 9500) / 100      # entre 5.00 y 99.99
            self.estadisticas["precio_sintetico"] += 1
        u = self.rng.random()
        if u < 0.10:
            return f"${precio:.2f}"                           # con símbolo de moneda
        if u < 0.15:
            return f"{precio:.2f}".replace(".", ",")          # coma decimal
        return f"{precio:.2f}"

    def _categoria_cruda(self, asin):
        """Categoría del catálogo, a veces con mayúsculas o espacios distintos."""
        categoria = self.catalogo.get(asin, (None, None, None))[1]
        if categoria is None:
            return None
        u = self.rng.random()
        if u < 0.05:
            return categoria.upper()
        if u < 0.10:
            return "  " + categoria.lower() + " "
        return categoria

    def _consulta(self, asin):
        """Texto de búsqueda: las primeras 2 o 3 palabras del título del producto."""
        titulo = self.catalogo.get(asin, (None, None, None))[2]
        if not titulo:
            return None
        palabras = re.findall(r"[A-Za-z]+", titulo)
        return " ".join(palabras[:self.rng.randint(2, 3)]) or None

    def _medios_del_usuario(self, user_id):
        """Cada usuario tiene 1 o 2 tarjetas y un dispositivo, fijos entre ejecuciones."""
        n_tarjetas = 1 + hash_estable(user_id + "|n") % 2
        tarjeta = f"pay_{hash_estable(user_id + '|' + str(self.rng.randrange(n_tarjetas))):08x}"
        dispositivo = f"dev_{hash_estable(user_id + '|dev'):08x}"
        return tarjeta, dispositivo

    def _tipo_orden(self):
        return self.rng.choices(["express", "standard", "scheduled"], weights=[15, 70, 15])[0]

    def _productos_extra(self, asin):
        """De 1 a 3 productos vistos recientemente en el mismo grupo de co-compra."""
        grupo = hash_estable(asin) % N_GRUPOS_CO_COMPRA
        candidatos = [p for p in self.recientes_por_grupo.get(grupo, ()) if p != asin]
        candidatos = list(dict.fromkeys(candidatos))           # sin repetidos, conserva el orden
        if not candidatos:
            return []
        return self.rng.sample(candidatos, min(len(candidatos), self.rng.randint(1, 3)))

    def _recordar_producto(self, asin):
        grupo = hash_estable(asin) % N_GRUPOS_CO_COMPRA
        self.recientes_por_grupo.setdefault(grupo, deque(maxlen=20)).append(asin)

    # ----- Recorridos ---------------------------------------------------------

    def _recorrido(self, resena):
        """Convierte una reseña en su recorrido de compra (lista de registros limpios)."""
        A = self.reloj_ms
        minuto = 60_000
        usuario, asin = resena["user_id"], resena["parent_asin"]
        tarjeta, dispositivo = self._medios_del_usuario(usuario)
        categoria = self._categoria_cruda(asin)
        eventos = []

        # Búsqueda: entre 30 y 20 minutos antes de la reseña
        ts_busqueda = A - self.rng.uniform(20, 30) * minuto
        eventos.append(self._registro("search", usuario, ts_busqueda, device_id=dispositivo,
                                      query=self._consulta(asin)))

        # Vistas del producto: entre la búsqueda y el carrito (o la reseña)
        compra = resena["verified_purchase"]
        fin_vistas = A - (13 if compra else 1) * minuto
        for _ in range(self.rng.randint(1, 3)):
            ts_vista = self.rng.uniform(ts_busqueda + minuto, fin_vistas)
            eventos.append(self._registro("view", usuario, ts_vista, parent_asin=asin,
                                          device_id=dispositivo, category=categoria))

        if compra:
            orden = self._nuevo_order_id()
            tipo_orden = self._tipo_orden()
            productos = [asin]
            if self.rng.random() < self.p_producto_extra:
                productos += self._productos_extra(asin)
                self.estadisticas["ordenes_multiproducto"] += len(productos) > 1

            ts_carrito = A - self.rng.uniform(8, 12) * minuto
            ts_compra = A - self.rng.uniform(2, 6) * minuto
            for p in productos:
                comunes = dict(parent_asin=p, order_id=orden, order_type=tipo_orden,
                               device_id=dispositivo, category=self._categoria_cruda(p))
                eventos.append(self._registro("cart", usuario, ts_carrito + self.rng.uniform(0, 30_000),
                                              **comunes))
                eventos.append(self._registro("purchase", usuario, ts_compra, payment_id=tarjeta,
                                              price=self._precio_crudo(p), **comunes))

            # Falsa alarma de la pasarela de pago sobre una compra legítima
            if self.rng.random() < self.p_alerta_legitima:
                eventos.append(self._registro("payment_alert", usuario, ts_compra + 2_000, parent_asin=asin,
                                              order_id=orden, order_type=tipo_orden,
                                              payment_id=tarjeta, device_id=dispositivo))
                self.estadisticas["alertas_legitimas"] += 1

        # Reseña: en la hora de reproducción A
        eventos.append(self._registro("review", usuario, A, parent_asin=asin, device_id=dispositivo,
                                      category=categoria, rating=resena["rating"]))

        # Devolución: más probable con calificación baja
        if compra and self.rng.random() < (0.30 if resena["rating"] <= 2 else 0.01):
            eventos.append(self._registro("return", usuario, A + self.rng.uniform(5, 60) * minuto,
                                          parent_asin=asin, order_id=orden, order_type=tipo_orden,
                                          payment_id=tarjeta, device_id=dispositivo))

        self._recordar_producto(asin)
        return eventos

    def _rafaga_fraude(self):
        """Cuenta, tarjeta y dispositivo señalados hacen de 5 a 20 compras rápidas."""
        cuenta = self.rng.choice(self.cuentas_fraude)
        tarjeta = self.rng.choice(self.tarjetas_fraude)
        dispositivo = self.rng.choice(self.dispositivos_fraude)
        ts = self.reloj_ms - self.rng.uniform(0, 10) * 60_000
        grupos = list(self.recientes_por_grupo.values())
        eventos = []
        for _ in range(self.rng.randint(5, 20)):
            ts += self.rng.uniform(5, 40) * 1000                       # cada 5 a 40 segundos
            asin = self.rng.choice(self.rng.choice(grupos))
            orden = self._nuevo_order_id()
            tipo_orden = "express" if self.rng.random() < 0.7 else "standard"
            eventos.append(self._registro("purchase", cuenta, ts, parent_asin=asin, order_id=orden,
                                          order_type=tipo_orden, price=self._precio_crudo(asin),
                                          payment_id=tarjeta, device_id=dispositivo,
                                          category=self._categoria_cruda(asin)))
            self.estadisticas["compras_fraudulentas"] += 1
            if self.rng.random() < self.p_alerta_fraude:
                eventos.append(self._registro("payment_alert", cuenta, ts + self.rng.uniform(1, 3) * 1000,
                                              parent_asin=asin, order_id=orden, order_type=tipo_orden,
                                              payment_id=tarjeta, device_id=dispositivo))
        self.estadisticas["rafagas_fraude"] += 1
        return eventos

    # ----- Ruido y orden de llegada -------------------------------------------

    def _corromper(self, r):
        """Devuelve una copia del registro con un error de esquema."""
        r = dict(r)
        tipo = self.rng.choice(["sin_user_id", "ts_invalido", "ts_nulo", "tipo_desconocido",
                                "asin_vacio", "precio_negativo"])
        if tipo == "sin_user_id":
            del r["user_id"]
        elif tipo == "ts_invalido":
            r["ts"] = "2023-13-45T99:99"
        elif tipo == "ts_nulo":
            r["ts"] = None
        elif tipo == "tipo_desconocido":
            r["event_type"] = "clik"
        elif tipo == "asin_vacio":
            r["parent_asin"] = ""
        elif tipo == "precio_negativo":
            r["price"] = "-12.50"
        self.estadisticas[f"malformado_{tipo}"] += 1
        self.estadisticas["malformados"] += 1
        return r

    def _casi_duplicado(self, r):
        """Copia del registro con diferencias de formato que no cambian su significado."""
        c = dict(r)
        c["event_id"] = self._nuevo_event_id()
        c["ts"] = r["ts"] + self.rng.randint(-400, 400)
        c["user_id"] = " " + r["user_id"].lower()
        if c["category"]:
            c["category"] = c["category"].upper()
        if c["price"] and not c["price"].startswith("$"):
            c["price"] = "$" + c["price"].replace(",", ".")
        return c

    def _encolar_llegada(self, registro, llegada_ms):
        heapq.heappush(self.pendientes, (llegada_ms, self.n_programados, registro))
        self.n_programados += 1

    def _programar(self, registro):
        """Calcula la hora de llegada del registro e inyecta ruido."""
        if self.rng.random() < self.p_tardio:
            retraso = self.rng.uniform(30_000, 300_000)        # llega entre 30 s y 5 min tarde
            self.estadisticas["tardios"] += 1
        else:
            retraso = self.rng.uniform(50, 2_000)
        llegada = registro["ts"] + retraso

        u = self.rng.random()
        if u < self.p_malformado:
            self._encolar_llegada(self._corromper(registro), llegada)
            return
        self._encolar_llegada(registro, llegada)

        u = self.rng.random()
        if u < self.p_duplicado_exacto:
            self._encolar_llegada(dict(registro), llegada + self.rng.uniform(100, 10_000))
            self.estadisticas["duplicados_exactos"] += 1
        elif u < self.p_duplicado_exacto + self.p_duplicado_casi:
            self._encolar_llegada(self._casi_duplicado(registro), llegada + self.rng.uniform(100, 10_000))
            self.estadisticas["duplicados_casi"] += 1

    def eventos(self, resenas):
        """Generador principal: recibe reseñas y entrega registros crudos en orden de llegada."""
        for resena in resenas:
            self.reloj_ms += int(self.rng.expovariate(1 / self.intervalo_medio_ms))
            for r in self._recorrido(resena):
                self._programar(r)
            if self.rng.random() < self.p_rafaga_fraude and self.recientes_por_grupo:
                for r in self._rafaga_fraude():
                    self._programar(r)

            # Ningún evento futuro puede llegar antes de (reloj - 30 min); lo anterior ya se puede entregar
            limite = self.reloj_ms - VENTANA_RECORRIDO_MS
            while self.pendientes and self.pendientes[0][0] <= limite:
                self.estadisticas["emitidos"] += 1
                yield heapq.heappop(self.pendientes)[2]

        # Fin del archivo: se entrega lo que queda
        while self.pendientes:
            self.estadisticas["emitidos"] += 1
            yield heapq.heappop(self.pendientes)[2]

    def listas_senalados(self):
        """Listas de cuentas, tarjetas y dispositivos señalados (entrada de la etapa 6)."""
        return {"cuentas": self.cuentas_fraude, "tarjetas": self.tarjetas_fraude,
                "dispositivos": self.dispositivos_fraude}
