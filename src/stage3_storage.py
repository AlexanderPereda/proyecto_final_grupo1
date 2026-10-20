"""Etapa 3: almacenamiento, lookup y caché.

Estructuras propias de la etapa:
- TablaHash: direccionamiento abierto con sondeo lineal, lápidas y redimensionamiento.
- AlmacenCatalogo: archivo de registros del catálogo en disco. Una TablaHash guarda
  parent_asin -> (offset, largo) y cada lectura hace un seek al registro.
- CacheLRU: TablaHash + lista doblemente enlazada. Consulta, inserción y desalojo en O(1).
- MatrizDispersa: matriz usuario × producto. Durante el stream se guarda como diccionario de
  claves (DOK) sobre una TablaHash; al cerrar se convierte a CSR (MatrizCSR).
- EtapaAlmacenamiento: recibe los eventos en el orden de servicio de la etapa 2, consulta cada
  producto a través de la caché, actualiza el estado dinámico del producto y la matriz.

`dict`, `OrderedDict` y la búsqueda lineal se usan solo como líneas base en los experimentos.
"""

import json
import zlib
from array import array
from collections import Counter

import pyarrow.parquet as pq

from . import config
from .events import Event
from .stage1_preprocess import normalizar_precio

# ----- Parámetros de la etapa ----------------------------------------------------

RUTA_EVENTOS_ETAPA2 = config.CARPETA_INTERIM / "etapa2_eventos.jsonl"    # entrada: salida de la etapa 2
RUTA_CATALOGO = config.CARPETA_INTERIM / "etapa3_catalogo.jsonl"          # registros en disco
COLUMNAS_CATALOGO = ["parent_asin", "title", "main_category", "categories", "price",
                     "average_rating", "rating_number", "store"]
CAPACIDAD_CACHE = 50_000          # productos que la LRU mantiene en memoria

# Peso de cada tipo de evento en la matriz usuario × producto (retroalimentación implícita).
# La búsqueda no tiene producto y la alerta de pago no es una interacción con el producto.
PESOS = {"view": 1.0, "cart": 2.0, "purchase": 4.0, "review": 1.0, "return": -4.0}

STOCK_MIN, STOCK_MAX = 5, 50      # stock inicial sintético por producto
REPOSICION_S = 48 * 3600          # un producto agotado se repone 48 h después de agotarse


# ----- Entrada --------------------------------------------------------------------

def leer_eventos(ruta=RUTA_EVENTOS_ETAPA2):
    """Entrega los eventos de la etapa 2 uno a uno, en orden de servicio.

    El archivo tiene un ciclo de servicio por línea y se lee línea por línea, así que la
    memoria usada depende del tamaño de un ciclo y no del número de eventos.
    """
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            for datos in json.loads(linea)["eventos"]:
                yield Event(**datos)


# ----- Función hash ---------------------------------------------------------------

def hash_clave(clave):
    """Hash determinista de 32 bits para textos y enteros no negativos.

    Se usa CRC32 porque el hash() de Python cambia en cada ejecución para los textos y
    los experimentos deben ser reproducibles (mismo criterio que hash_estable en la etapa 0).
    """
    if isinstance(clave, str):
        return zlib.crc32(clave.encode("utf-8"))
    if isinstance(clave, int):
        return zlib.crc32(clave.to_bytes(8, "little"))
    raise TypeError(f"clave no soportada: {type(clave).__name__}")


# ----- Tabla hash -----------------------------------------------------------------

_BORRADO = object()               # lápida: la posición estuvo ocupada y se eliminó
_AUSENTE = object()               # valor por defecto interno de obtener


class TablaHash:
    """Tabla hash con direccionamiento abierto y sondeo lineal.

    Dos arreglos paralelos guardan claves y valores. La capacidad es potencia de 2, así que
    la posición inicial es hash & (capacidad - 1). Al eliminar se deja una lápida para no
    cortar las secuencias de sondeo. Cuando las posiciones usadas (claves + lápidas) superan
    `carga_max` de la capacidad, se reconstruye la tabla: se duplica la capacidad si las claves
    vivas lo justifican; si no, se conserva y solo se limpian las lápidas.

    Costo esperado O(1) por operación con factor de carga acotado; O(n) en el peor caso.
    """

    def __init__(self, capacidad=8, carga_max=0.7):
        cap = 8
        while cap * carga_max < capacidad:
            cap *= 2
        self.carga_max = carga_max
        self._claves = [None] * cap
        self._valores = [None] * cap
        self._n = 0                 # claves vivas
        self._usadas = 0            # claves vivas + lápidas
        self.redimensionamientos = 0

    def _buscar(self, clave):
        """Devuelve (posición, encontrada). Si no la encuentra, la posición es donde insertarla."""
        claves = self._claves
        mascara = len(claves) - 1
        i = hash_clave(clave) & mascara
        lapida = -1
        while True:
            k = claves[i]
            if k is None:
                return (lapida if lapida >= 0 else i), False
            if k is _BORRADO:
                if lapida < 0:
                    lapida = i
            elif k == clave:
                return i, True
            i = (i + 1) & mascara

    def obtener(self, clave, defecto=None):
        i, encontrada = self._buscar(clave)
        return self._valores[i] if encontrada else defecto

    def insertar(self, clave, valor):
        """Inserta o actualiza la clave. Las claves no pueden ser None."""
        i, encontrada = self._buscar(clave)
        if encontrada:
            self._valores[i] = valor
            return
        if self._claves[i] is None:          # se reutiliza una lápida sin aumentar las usadas
            self._usadas += 1
        self._claves[i] = clave
        self._valores[i] = valor
        self._n += 1
        if self._usadas > self.carga_max * len(self._claves):
            self._reconstruir()

    def eliminar(self, clave):
        """Elimina la clave. Devuelve True si existía."""
        i, encontrada = self._buscar(clave)
        if not encontrada:
            return False
        self._claves[i] = _BORRADO
        self._valores[i] = None
        self._n -= 1
        return True

    def _reconstruir(self):
        cap = len(self._claves)
        if self._n > self.carga_max * cap / 2:
            cap *= 2
        pares = list(self.items())
        self._claves = [None] * cap
        self._valores = [None] * cap
        self._n = self._usadas = 0
        for k, v in pares:
            self.insertar(k, v)
        self.redimensionamientos += 1

    def items(self):
        for k, v in zip(self._claves, self._valores):
            if k is not None and k is not _BORRADO:
                yield k, v

    def claves(self):
        for k, _ in self.items():
            yield k

    def __contains__(self, clave):
        return self._buscar(clave)[1]

    def __len__(self):
        return self._n

    @property
    def capacidad(self):
        return len(self._claves)

    def factor_carga(self):
        return self._n / len(self._claves)

    def sondeo_medio(self):
        """Número medio de posiciones revisadas en una búsqueda exitosa. O(n)."""
        mascara = len(self._claves) - 1
        total = 0
        for i, k in enumerate(self._claves):
            if k is not None and k is not _BORRADO:
                total += ((i - (hash_clave(k) & mascara)) & mascara) + 1
        return total / self._n if self._n else 0.0


# ----- Catálogo en disco ----------------------------------------------------------

def _registro_producto(fila):
    """Registro del catálogo con el precio convertido a número, la ruta de categorías y su último nivel."""
    try:
        precio = normalizar_precio(fila["price"])
    except ValueError:
        precio = None
    if precio is not None and precio <= 0:
        precio = None
    categorias = fila["categories"] or []
    return {
        "parent_asin": fila["parent_asin"].strip().upper(),
        "title": fila["title"],
        "main_category": fila["main_category"],
        "categorias": categorias,
        "categoria_hoja": categorias[-1] if categorias else None,
        "precio": precio,
        "average_rating": fila["average_rating"],
        "rating_number": fila["rating_number"],
        "store": fila["store"],
    }


def construir_archivo_catalogo(ruta_meta=config.RUTA_META, ruta_salida=RUTA_CATALOGO, tam_bloque=50_000):
    """Escribe el catálogo como archivo de registros (un JSON por línea) y devuelve cuántos escribió.

    Se lee el Parquet por bloques para no cargar el catálogo completo en memoria.
    """
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta_salida.with_name(ruta_salida.name + ".part")
    n = 0
    with open(temporal, "wb") as salida:
        for bloque in pq.ParquetFile(ruta_meta).iter_batches(batch_size=tam_bloque, columns=COLUMNAS_CATALOGO):
            for fila in bloque.to_pylist():
                salida.write(json.dumps(_registro_producto(fila), ensure_ascii=False).encode("utf-8") + b"\n")
                n += 1
    temporal.replace(ruta_salida)
    return n


class AlmacenCatalogo:
    """Catálogo en disco con índice hash en memoria: parent_asin -> (offset, largo).

    La memoria del índice crece con el número de productos, pero los registros completos
    quedan en disco. Cada consulta cuesta O(1) en el índice más un seek y una lectura.
    """

    def __init__(self, ruta=RUTA_CATALOGO):
        self.ruta = ruta
        self.indice = TablaHash()
        self.lecturas = 0
        offset = 0
        with open(ruta, "rb") as archivo:           # una pasada para construir el índice
            for linea in archivo:
                inicio = linea.index(b'"parent_asin": "') + 16
                asin = linea[inicio:linea.index(b'"', inicio)].decode("utf-8")
                self.indice.insertar(asin, (offset, len(linea)))
                offset += len(linea)
        self._archivo = open(ruta, "rb")

    def leer(self, asin):
        """Devuelve el registro del producto o None si no está en el catálogo."""
        posicion = self.indice.obtener(asin)
        if posicion is None:
            return None
        offset, largo = posicion
        self._archivo.seek(offset)
        self.lecturas += 1
        return json.loads(self._archivo.read(largo))

    def cerrar(self):
        self._archivo.close()

    def __len__(self):
        return len(self.indice)

    def __contains__(self, asin):
        return asin in self.indice


# ----- Caché LRU ------------------------------------------------------------------

class _Nodo:
    __slots__ = ("clave", "valor", "ant", "sig")

    def __init__(self, clave=None, valor=None):
        self.clave = clave
        self.valor = valor
        self.ant = self.sig = None


class CacheLRU:
    """Caché de capacidad fija que desaloja el elemento usado hace más tiempo.

    La TablaHash da acceso O(1) al nodo de cada clave. La lista doblemente enlazada mantiene
    el orden de uso: el más reciente queda junto a la cabeza y el menos reciente junto a la
    cola. Mover un nodo al frente y desalojar el último cuestan O(1).
    """

    def __init__(self, capacidad):
        if capacidad < 1:
            raise ValueError("la capacidad debe ser al menos 1")
        self.capacidad = capacidad
        self._tabla = TablaHash(capacidad)
        self._cabeza = _Nodo()                       # centinelas: evitan casos borde
        self._cola = _Nodo()
        self._cabeza.sig = self._cola
        self._cola.ant = self._cabeza
        self.aciertos = 0
        self.fallos = 0
        self.desalojos = 0

    def _quitar(self, nodo):
        nodo.ant.sig = nodo.sig
        nodo.sig.ant = nodo.ant

    def _al_frente(self, nodo):
        nodo.ant = self._cabeza
        nodo.sig = self._cabeza.sig
        self._cabeza.sig.ant = nodo
        self._cabeza.sig = nodo

    def obtener(self, clave, defecto=None):
        nodo = self._tabla.obtener(clave)
        if nodo is None:
            self.fallos += 1
            return defecto
        self.aciertos += 1
        self._quitar(nodo)
        self._al_frente(nodo)
        return nodo.valor

    def poner(self, clave, valor):
        nodo = self._tabla.obtener(clave)
        if nodo is not None:
            nodo.valor = valor
            self._quitar(nodo)
            self._al_frente(nodo)
            return
        nodo = _Nodo(clave, valor)
        self._tabla.insertar(clave, nodo)
        self._al_frente(nodo)
        if len(self._tabla) > self.capacidad:
            ultimo = self._cola.ant
            self._quitar(ultimo)
            self._tabla.eliminar(ultimo.clave)
            self.desalojos += 1

    def claves_por_uso(self):
        """Claves del más reciente al menos reciente."""
        nodo = self._cabeza.sig
        while nodo is not self._cola:
            yield nodo.clave
            nodo = nodo.sig

    def tasa_aciertos(self):
        total = self.aciertos + self.fallos
        return self.aciertos / total if total else 0.0

    def __contains__(self, clave):
        return clave in self._tabla

    def __len__(self):
        return len(self._tabla)


# ----- Matriz dispersa ------------------------------------------------------------

class MatrizCSR:
    """Matriz dispersa en formato CSR (compressed sparse row).

    indptr[i]:indptr[i+1] delimita en `indices` y `datos` las columnas y valores de la fila i.
    Memoria O(filas + nnz). Acceso a una fila en O(1) más su número de elementos.
    """

    def __init__(self, indptr, indices, datos, n_columnas):
        self.indptr = indptr
        self.indices = indices
        self.datos = datos
        self.n_filas = len(indptr) - 1
        self.n_columnas = n_columnas

    @property
    def nnz(self):
        return len(self.datos)

    def fila(self, i):
        a, b = self.indptr[i], self.indptr[i + 1]
        return self.indices[a:b], self.datos[a:b]

    def valor(self, i, j):
        """Valor de la celda (i, j) con búsqueda binaria dentro de la fila. O(log k)."""
        lo, hi = self.indptr[i], self.indptr[i + 1]
        while lo < hi:
            medio = (lo + hi) // 2
            if self.indices[medio] < j:
                lo = medio + 1
            else:
                hi = medio
        return self.datos[lo] if lo < self.indptr[i + 1] and self.indices[lo] == j else 0.0

    def transpuesta(self):
        """CSR de la transpuesta con conteo por columnas. O(filas + columnas + nnz)."""
        conteo = array("q", [0]) * (self.n_columnas + 1)
        for j in self.indices:
            conteo[j + 1] += 1
        for j in range(self.n_columnas):
            conteo[j + 1] += conteo[j]
        indptr = array("q", conteo)
        siguiente = array("q", conteo)
        indices = array("i", [0]) * self.nnz
        datos = array("d", [0.0]) * self.nnz
        for i in range(self.n_filas):
            for k in range(self.indptr[i], self.indptr[i + 1]):
                j = self.indices[k]
                destino = siguiente[j]
                indices[destino] = i
                datos[destino] = self.datos[k]
                siguiente[j] = destino + 1
        return MatrizCSR(indptr, indices, datos, self.n_filas)

    def bytes(self):
        return sum(a.itemsize * len(a) for a in (self.indptr, self.indices, self.datos))

    def a_densa(self):
        """Lista de listas. Solo para matrices pequeñas (tests)."""
        densa = [[0.0] * self.n_columnas for _ in range(self.n_filas)]
        for i in range(self.n_filas):
            for j, v in zip(*self.fila(i)):
                densa[i][j] = v
        return densa


class MatrizDispersa:
    """Matriz usuario × producto que se actualiza durante el stream.

    Las filas y columnas se crean al aparecer un usuario o un producto nuevo. Cada celda no
    nula se guarda en una TablaHash con clave entera fila << 32 | columna (DOK). Sumar un peso
    cuesta O(1) esperado. `a_csr` ordena las celdas y produce la versión compacta.
    """

    def __init__(self):
        self.fila_de = TablaHash()        # user_id -> fila
        self.columna_de = TablaHash()     # parent_asin -> columna
        self.usuarios = []                # fila -> user_id
        self.productos = []               # columna -> parent_asin
        self.celdas = TablaHash()

    def _indice(self, tabla, lista, clave):
        i = tabla.obtener(clave)
        if i is None:
            i = len(lista)
            tabla.insertar(clave, i)
            lista.append(clave)
        return i

    def sumar(self, usuario, asin, peso):
        fila = self._indice(self.fila_de, self.usuarios, usuario)
        columna = self._indice(self.columna_de, self.productos, asin)
        clave = fila << 32 | columna
        self.celdas.insertar(clave, self.celdas.obtener(clave, 0.0) + peso)

    def valor(self, usuario, asin):
        fila, columna = self.fila_de.obtener(usuario), self.columna_de.obtener(asin)
        if fila is None or columna is None:
            return 0.0
        return self.celdas.obtener(fila << 32 | columna, 0.0)

    @property
    def forma(self):
        return len(self.usuarios), len(self.productos)

    def a_csr(self):
        """Convierte a CSR. Las celdas con peso <= 0 (por ejemplo, compra devuelta) se omiten.

        El ordenamiento por clave entera ordena por (fila, columna). O(nnz log nnz).
        """
        n_filas, n_columnas = self.forma
        celdas = sorted((k, v) for k, v in self.celdas.items() if v > 0)
        indptr = array("q", [0]) * (n_filas + 1)
        indices = array("i")
        datos = array("d")
        for clave, v in celdas:
            indptr[(clave >> 32) + 1] += 1
            indices.append(clave & 0xFFFFFFFF)
            datos.append(v)
        for i in range(n_filas):
            indptr[i + 1] += indptr[i]
        return MatrizCSR(indptr, indices, datos, n_columnas)


# ----- Estado dinámico del producto ------------------------------------------------

class EstadoProducto:
    """Contadores y disponibilidad de un producto. Solo existe para productos con actividad."""

    __slots__ = ("vistas", "carritos", "compras", "devoluciones", "resenas", "suma_rating",
                 "stock", "agotado_desde", "agotamientos", "ventas_sin_stock", "precio_observado",
                 "ultima_actividad")

    def __init__(self, stock):
        self.vistas = self.carritos = self.compras = self.devoluciones = self.resenas = 0
        self.suma_rating = 0.0
        self.stock = stock
        self.agotado_desde = None
        self.agotamientos = 0
        self.ventas_sin_stock = 0
        self.precio_observado = None
        self.ultima_actividad = None

    @property
    def disponible(self):
        return self.stock > 0


def stock_inicial(asin):
    """Stock sintético estable por producto, entre STOCK_MIN y STOCK_MAX."""
    return STOCK_MIN + zlib.crc32((asin + "|stock").encode("utf-8")) % (STOCK_MAX - STOCK_MIN + 1)


# ----- Etapa completa -------------------------------------------------------------

class EtapaAlmacenamiento:
    """Etapa 3: lookup de productos con caché, estado dinámico y matriz de interacciones."""

    def __init__(self, almacen, capacidad_cache=CAPACIDAD_CACHE):
        self.almacen = almacen
        self.cache = CacheLRU(capacidad_cache)
        self.estados = TablaHash()                  # parent_asin -> EstadoProducto
        self.matriz = MatrizDispersa()
        self.metricas = Counter()

    def consultar_producto(self, asin):
        """Registro del catálogo: primero la caché, después el archivo en disco."""
        registro = self.cache.obtener(asin)
        if registro is None:
            registro = self.almacen.leer(asin)
            if registro is None:
                return None
            self.cache.poner(asin, registro)
        return registro

    def estado(self, asin):
        return self.estados.obtener(asin)

    def ficha(self, asin):
        """Datos de catálogo y estado dinámico juntos (consulta de producto del escenario B)."""
        registro = self.consultar_producto(asin)
        if registro is None:
            return None
        e = self.estados.obtener(asin)
        ficha = dict(registro)
        ficha["disponible"] = True if e is None else e.disponible
        ficha["stock"] = stock_inicial(asin) if e is None else e.stock
        if ficha["precio"] is None and e is not None:
            ficha["precio"] = e.precio_observado
        return ficha

    def procesar(self, evento):
        """Actualiza la etapa con un evento limpio que sale de la etapa 2."""
        m = self.metricas
        m["eventos"] += 1
        asin = evento.parent_asin
        if asin is None:                              # búsquedas
            m["sin_producto"] += 1
            return
        if self.consultar_producto(asin) is None:
            m["fuera_de_catalogo"] += 1

        e = self.estados.obtener(asin)
        if e is None:
            e = EstadoProducto(stock_inicial(asin))
            self.estados.insertar(asin, e)
        tipo = evento.event_type
        ts = evento.ts
        if e.agotado_desde is not None and ts - e.agotado_desde >= REPOSICION_S:
            e.stock = stock_inicial(asin)              # reposición
            e.agotado_desde = None
            m["reposiciones"] += 1

        if tipo == "view":
            e.vistas += 1
        elif tipo == "cart":
            e.carritos += 1
        elif tipo == "purchase":
            e.compras += 1
            if evento.price is not None:
                e.precio_observado = evento.price
            if e.stock > 0:
                e.stock -= 1
                if e.stock == 0:
                    e.agotado_desde = ts
                    e.agotamientos += 1
            else:
                e.ventas_sin_stock += 1
        elif tipo == "return":
            e.devoluciones += 1
            e.stock += 1
            e.agotado_desde = None
        elif tipo == "review":
            e.resenas += 1
            e.suma_rating += evento.rating
        e.ultima_actividad = ts if e.ultima_actividad is None else max(e.ultima_actividad, ts)

        peso = PESOS.get(tipo)
        if peso is not None:
            self.matriz.sumar(evento.user_id, asin, peso)
        m[f"tipo_{tipo}"] += 1

    def tabla_estados(self):
        """Filas (dict) con el estado de cada producto con actividad, para guardar en Parquet."""
        filas = []
        for asin, e in self.estados.items():
            filas.append({
                "parent_asin": asin, "vistas": e.vistas, "carritos": e.carritos, "compras": e.compras,
                "devoluciones": e.devoluciones, "resenas": e.resenas,
                "rating_medio_stream": e.suma_rating / e.resenas if e.resenas else None,
                "stock": e.stock, "disponible": e.disponible, "agotamientos": e.agotamientos,
                "ventas_sin_stock": e.ventas_sin_stock, "precio_observado": e.precio_observado,
                "ultima_actividad": e.ultima_actividad,
            })
        return filas


# ----- Persistencia de la matriz -------------------------------------------------

def guardar_matriz(ruta, csr, usuarios, productos):
    """Guarda la CSR y los identificadores de filas y columnas en un .npz."""
    import numpy as np

    np.savez_compressed(ruta, indptr=np.frombuffer(csr.indptr, dtype=np.int64),
                        indices=np.frombuffer(csr.indices, dtype=np.int32),
                        datos=np.frombuffer(csr.datos, dtype=np.float64),
                        usuarios=np.array(usuarios), productos=np.array(productos))


def cargar_matriz(ruta):
    """Devuelve (MatrizCSR, usuarios, productos) desde el .npz de guardar_matriz."""
    import numpy as np

    z = np.load(ruta)
    csr = MatrizCSR(array("q", z["indptr"].tobytes()), array("i", z["indices"].tobytes()),
                    array("d", z["datos"].tobytes()), len(z["productos"]))
    return csr, z["usuarios"].tolist(), z["productos"].tolist()
