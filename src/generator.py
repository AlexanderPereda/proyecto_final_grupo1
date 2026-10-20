"""Lectura del dataset como stream y generador sintético de eventos (parte de la etapa 0).

Las reseñas vienen en un Parquet ordenado por `timestamp` (ver `notebooks/00-download-data.ipynb`).
Se leen por bloques, una reseña a la vez, dentro de una ventana reciente: las últimas N reseñas
del archivo. Cada reseña se convierte en un recorrido de compra cuyos eventos ocurren en los
minutos previos al timestamp real de la reseña.
"""

import heapq                      # solo se usa dentro del simulador, no es una estructura evaluada
import random
import re
import zlib
from collections import Counter, deque

import pyarrow.parquet as pq

from . import config

SEED = config.SEED
VENTANA_RECORRIDO_MS = 30 * 60 * 1000  # el recorrido empieza como máximo 30 min antes de la reseña
MARGEN_LIBERACION_MS = 1_000           # cubre el desplazamiento de ±0.4 s de los duplicados casi exactos
N_GRUPOS_CO_COMPRA = 500
CAMPOS = ["event_id", "event_type", "user_id", "parent_asin", "order_id", "order_type",
          "price", "payment_id", "device_id", "ts", "category", "query", "rating"]


# ----- Lectura de reseñas -------------------------------------------------------

def describir_ventana(ruta=config.RUTA_RESENAS, n_ventana=config.N_RESENAS, limite=None):
    """Calcula qué filas y qué grupos de filas del Parquet forman la ventana, sin leer datos.

    La ventana son las últimas `n_ventana` filas del archivo. Si se indica `limite`, se toman
    solo las primeras `limite` filas de esa ventana (pruebas y demostraciones). Solo se leen los
    metadatos del Parquet (número de filas de cada grupo de filas).
    """
    metadatos = pq.ParquetFile(ruta).metadata
    total = metadatos.num_rows
    inicio = max(0, total - n_ventana)
    fin = total if limite is None else min(total, inicio + limite)

    grupos = []                  # grupos de filas que se cruzan con [inicio, fin)
    primera_fila_grupos = None   # fila global donde empieza el primer grupo que se lee
    acumulado = 0
    for g in range(metadatos.num_row_groups):
        filas = metadatos.row_group(g).num_rows
        if acumulado + filas > inicio and acumulado < fin:
            if primera_fila_grupos is None:
                primera_fila_grupos = acumulado
            grupos.append(g)
        acumulado += filas

    return {
        "total_filas": total,
        "inicio": inicio,
        "fin": fin,
        "filas": fin - inicio,
        "grupos_totales": metadatos.num_row_groups,
        "grupos_leidos": grupos,
        "filas_a_descartar": inicio - primera_fila_grupos if grupos else 0,
    }


def leer_resenas(ruta=config.RUTA_RESENAS, n_ventana=config.N_RESENAS, limite=None,
                 tam_bloque=10_000):
    """Entrega las reseñas de la ventana reciente una a la vez, en orden de timestamp.

    Lee el Parquet por bloques de `tam_bloque` filas con `iter_batches` y solo las columnas
    necesarias. Los grupos de filas anteriores a la ventana no se leen; dentro del primer grupo
    se descartan las filas previas al inicio. La memoria usada depende del tamaño del bloque,
    no del número de reseñas.
    """
    ventana = describir_ventana(ruta, n_ventana, limite)
    if not ventana["grupos_leidos"] or ventana["filas"] <= 0:
        return
    por_descartar = ventana["filas_a_descartar"]
    restantes = ventana["filas"]
    archivo = pq.ParquetFile(ruta)
    for bloque in archivo.iter_batches(batch_size=tam_bloque, row_groups=ventana["grupos_leidos"],
                                       columns=config.COLUMNAS_RESENAS):
        if por_descartar >= bloque.num_rows:          # el bloque entero está antes de la ventana
            por_descartar -= bloque.num_rows
            continue
        if por_descartar:
            bloque = bloque.slice(por_descartar)
            por_descartar = 0
        for r in bloque.to_pylist():
            yield r
            restantes -= 1
            if restantes == 0:
                return


def cargar_catalogo(ruta=config.RUTA_META):
    """Lee solo las columnas del catálogo y devuelve {parent_asin: (precio, categoria, titulo)}.

    El precio se conserva como texto crudo (puede ser "2.68", None, "from 14.99" o "—");
    su conversión a número corresponde a la etapa 1.
    """
    tabla = pq.read_table(ruta, columns=config.COLUMNAS_META)
    asins = tabla.column("parent_asin").to_pylist()
    precios = tabla.column("price").to_pylist()
    categorias = tabla.column("main_category").to_pylist()
    titulos = tabla.column("title").to_pylist()
    return {a: (p, c, t) for a, p, c, t in zip(asins, precios, categorias, titulos)}


def hash_estable(texto):
    """Hash determinista. El hash() de Python cambia en cada ejecución y rompería la reproducibilidad."""
    return zlib.crc32(texto.encode("utf-8"))


# ----- Generador de eventos -----------------------------------------------------

class GeneradorEventos:
    """Convierte reseñas reales en un stream sintético de eventos de comercio electrónico."""

    def __init__(self, catalogo, semilla=SEED,
                 porcentaje_tardios=config.PORCENTAJE_TARDIOS,
                 retraso_max_tardios_s=config.RETRASO_MAX_TARDIOS_S,
                 p_duplicado_exacto=0.03,
                 p_duplicado_casi=0.015,
                 p_malformado=0.01,
                 p_producto_extra=0.25,       # orden con más de un producto
                 p_rafaga_fraude=0.002,
                 p_alerta_fraude=0.30,
                 p_alerta_legitima=0.003):
        self.catalogo = catalogo
        self.rng = random.Random(semilla)
        self.porcentaje_tardios = porcentaje_tardios
        self.retraso_max_tardios_ms = retraso_max_tardios_s * 1000
        self.p_duplicado_exacto = p_duplicado_exacto
        self.p_duplicado_casi = p_duplicado_casi
        self.p_malformado = p_malformado
        self.p_producto_extra = p_producto_extra
        self.p_rafaga_fraude = p_rafaga_fraude
        self.p_alerta_fraude = p_alerta_fraude
        self.p_alerta_legitima = p_alerta_legitima

        self.reloj_ms = None             # timestamp de la reseña actual (el mayor visto)
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
        """Precio del catálogo tal como viene (texto crudo, se limpia en la etapa 1).

        Si el producto no tiene precio, se usa un precio sintético estable por producto,
        a veces con símbolo de moneda o coma decimal.
        """
        precio = self.catalogo.get(asin, (None, None, None))[0]
        if precio is not None:
            self.estadisticas["precio_catalogo"] += 1
            return precio
        sintetico = 5 + (hash_estable(asin) % 9500) / 100      # entre 5.00 y 99.99
        self.estadisticas["precio_sintetico"] += 1
        u = self.rng.random()
        if u < 0.10:
            return f"${sintetico:.2f}"                        # con símbolo de moneda
        if u < 0.15:
            return f"{sintetico:.2f}".replace(".", ",")       # coma decimal
        return f"{sintetico:.2f}"

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
        """Convierte una reseña en su recorrido de compra (lista de registros limpios).

        El ancla A es el timestamp real de la reseña; el resto del recorrido ocurre en los
        30 minutos previos y la devolución, si la hay, en la hora siguiente.
        """
        A = resena["timestamp"]
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

        # Reseña: en su timestamp real
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
        """Calcula la hora de llegada del registro e inyecta ruido.

        Desorden controlado: un evento llega en su propio ts, salvo una fracción
        `porcentaje_tardios` que llega con un retraso aleatorio de hasta
        `retraso_max_tardios_s`. Con porcentaje 0 el stream sale ordenado por ts.
        Los duplicados llegan en su propio ts, justo después del original.
        """
        self.estadisticas["programados"] += 1
        llegada = registro["ts"]
        if self.porcentaje_tardios and self.rng.random() < self.porcentaje_tardios:
            llegada += self.rng.uniform(0, self.retraso_max_tardios_ms)
            self.estadisticas["tardios"] += 1

        if self.rng.random() < self.p_malformado:
            self._encolar_llegada(self._corromper(registro), llegada)
            return
        self._encolar_llegada(registro, llegada)

        u = self.rng.random()
        if u < self.p_duplicado_exacto:
            self._encolar_llegada(dict(registro), registro["ts"])
            self.estadisticas["duplicados_exactos"] += 1
        elif u < self.p_duplicado_exacto + self.p_duplicado_casi:
            copia = self._casi_duplicado(registro)
            self._encolar_llegada(copia, copia["ts"])
            self.estadisticas["duplicados_casi"] += 1

    def eventos(self, resenas):
        """Generador principal: recibe reseñas ordenadas por timestamp y entrega registros crudos
        en orden de llegada."""
        for resena in resenas:
            # Las reseñas vienen ordenadas; se usa el mayor timestamp visto por si hubiera empates o retrocesos
            self.reloj_ms = resena["timestamp"] if self.reloj_ms is None else max(self.reloj_ms, resena["timestamp"])
            for r in self._recorrido(resena):
                self._programar(r)
            if self.rng.random() < self.p_rafaga_fraude and self.recientes_por_grupo:
                for r in self._rafaga_fraude():
                    self._programar(r)

            # Ningún evento futuro puede llegar antes de (reloj - 30 min - margen); lo anterior ya se entrega
            limite = self.reloj_ms - VENTANA_RECORRIDO_MS - MARGEN_LIBERACION_MS
            while self.pendientes and self.pendientes[0][0] <= limite:
                self.estadisticas["emitidos"] += 1
                yield heapq.heappop(self.pendientes)[2]

        # Fin de las reseñas: se entrega lo que queda
        while self.pendientes:
            self.estadisticas["emitidos"] += 1
            yield heapq.heappop(self.pendientes)[2]

    def listas_senalados(self):
        """Listas de cuentas, tarjetas y dispositivos señalados (entrada de la etapa 6)."""
        return {"cuentas": self.cuentas_fraude, "tarjetas": self.tarjetas_fraude,
                "dispositivos": self.dispositivos_fraude}
