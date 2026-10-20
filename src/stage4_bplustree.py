"""Etapa 4: indexación principal con un B+Tree en disco.

El árbol vive en un archivo dividido en páginas de tamaño fijo. Cada nodo ocupa una página:
- Página 0: cabecera (raíz, número de páginas, número de claves, altura, primera hoja).
- Hoja: entradas (clave, valor) ordenadas y el número de página de la hoja siguiente.
- Nodo interno: claves separadoras y números de página de los hijos.

La clave es compuesta, (precio, parent_asin), para que sea única aunque muchos productos
compartan precio. El valor es la posición del registro en el catálogo en disco de la etapa 3,
(offset, largo). Las páginas leídas se guardan en un buffer pool, que es la CacheLRU de la
etapa 3 con el número de página como clave.

Operaciones:
- buscar: O(log_B n) lecturas de página, donde B es el número de claves por nodo.
- rango: O(log_B n + k / B) lecturas para k resultados, recorriendo las hojas enlazadas.
- insertar: O(log_B n), con división de nodos que puede subir hasta la raíz.
- carga_masiva: O(n) a partir de entradas ordenadas, llenando las páginas de izquierda a derecha.
- eliminar: O(log_B n). Quita la entrada de su hoja sin fusionar nodos.
"""

import json
import struct

from . import config
from .stage1_preprocess import merge_sort
from .stage3_storage import RUTA_CATALOGO, CacheLRU

RUTA_INDICE_PRECIO = config.CARPETA_INTERIM / "etapa4_indice_precio.bpt"
TAM_PAGINA = 4096
CAPACIDAD_BUFFER = 256            # páginas que el buffer pool mantiene en memoria
LLENADO_CARGA_MASIVA = 0.9        # fracción de cada página que ocupa la carga masiva
LARGO_ASIN = 10

_MAGIA = b"BPTREE01"
_CABECERA = struct.Struct("<8sIqqqIq")       # magia, tam_pagina, raiz, n_paginas, n_claves, altura, primera_hoja
_NODO = struct.Struct("<BH")                  # tipo, número de claves
_SIGUIENTE = struct.Struct("<q")              # hoja siguiente (0 si no hay) o primer hijo
_ENTRADA = struct.Struct("<d10sqi")           # precio, asin, offset, largo (30 bytes)
_SEPARADOR = struct.Struct("<d10sq")          # precio, asin, hijo derecho (26 bytes)
_HOJA, _INTERNO = 0, 1
_SIN_PAGINA = 0                               # la página 0 es la cabecera, nunca un nodo


def capacidad_hoja(tam_pagina):
    return (tam_pagina - _NODO.size - _SIGUIENTE.size) // _ENTRADA.size


def capacidad_interno(tam_pagina):
    return (tam_pagina - _NODO.size - _SIGUIENTE.size) // _SEPARADOR.size


def clave(precio, asin):
    """Clave interna: (precio, asin en bytes). Las tuplas se comparan por precio y luego por asin."""
    return float(precio), asin.encode("ascii")


def bisect_izq(claves, x):
    """Primera posición i con claves[i] >= x. O(log n)."""
    lo, hi = 0, len(claves)
    while lo < hi:
        medio = (lo + hi) // 2
        if claves[medio] < x:
            lo = medio + 1
        else:
            hi = medio
    return lo


def bisect_der(claves, x):
    """Primera posición i con claves[i] > x. O(log n)."""
    lo, hi = 0, len(claves)
    while lo < hi:
        medio = (lo + hi) // 2
        if x < claves[medio]:
            hi = medio
        else:
            lo = medio + 1
    return lo


class Hoja:
    __slots__ = ("pagina", "claves", "valores", "siguiente")

    def __init__(self, pagina, claves=None, valores=None, siguiente=_SIN_PAGINA):
        self.pagina = pagina
        self.claves = claves if claves is not None else []
        self.valores = valores if valores is not None else []
        self.siguiente = siguiente


class Interno:
    """claves[i] es la menor clave del subárbol hijos[i + 1]."""

    __slots__ = ("pagina", "claves", "hijos")

    def __init__(self, pagina, claves, hijos):
        self.pagina = pagina
        self.claves = claves
        self.hijos = hijos


class ArbolBMas:
    """B+Tree en disco con páginas de tamaño fijo y buffer pool LRU."""

    def __init__(self, ruta, tam_pagina=TAM_PAGINA, capacidad_buffer=CAPACIDAD_BUFFER, crear=False):
        self.ruta = ruta
        if crear:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            self.tam_pagina = tam_pagina
            self.raiz = 1
            self.n_paginas = 2                # cabecera + hoja raíz vacía
            self.n_claves = 0
            self.altura = 1
            self.primera_hoja = 1
            self._archivo = open(ruta, "w+b")
        else:
            self._archivo = open(ruta, "r+b")
            magia, self.tam_pagina, self.raiz, self.n_paginas, self.n_claves, self.altura, self.primera_hoja = \
                _CABECERA.unpack(self._archivo.read(_CABECERA.size))
            if magia != _MAGIA:
                raise ValueError(f"{ruta} no es un índice B+Tree")
        self.max_hoja = capacidad_hoja(self.tam_pagina)
        self.max_interno = capacidad_interno(self.tam_pagina)
        self.buffer = CacheLRU(capacidad_buffer)
        self.lecturas = 0                     # páginas leídas del disco
        self.escrituras = 0                   # páginas escritas en el disco
        if crear:
            self._guardar_cabecera()
            self._escribir(Hoja(1))

    # ----- Páginas ----------------------------------------------------------------

    def _guardar_cabecera(self):
        self._archivo.seek(0)
        self._archivo.write(_CABECERA.pack(_MAGIA, self.tam_pagina, self.raiz, self.n_paginas, self.n_claves,
                                           self.altura, self.primera_hoja).ljust(self.tam_pagina, b"\0"))

    def _nueva_pagina(self):
        pagina = self.n_paginas
        self.n_paginas += 1
        return pagina

    def _codificar(self, nodo):
        if isinstance(nodo, Hoja):
            partes = [_NODO.pack(_HOJA, len(nodo.claves)), _SIGUIENTE.pack(nodo.siguiente)]
            partes += [_ENTRADA.pack(p, a, o, l) for (p, a), (o, l) in zip(nodo.claves, nodo.valores)]
        else:
            partes = [_NODO.pack(_INTERNO, len(nodo.claves)), _SIGUIENTE.pack(nodo.hijos[0])]
            partes += [_SEPARADOR.pack(p, a, h) for (p, a), h in zip(nodo.claves, nodo.hijos[1:])]
        datos = b"".join(partes)
        if len(datos) > self.tam_pagina:
            raise ValueError("el nodo no cabe en la página")
        return datos.ljust(self.tam_pagina, b"\0")

    def _decodificar(self, pagina, datos):
        tipo, n = _NODO.unpack_from(datos)
        (primero,) = _SIGUIENTE.unpack_from(datos, _NODO.size)
        inicio = _NODO.size + _SIGUIENTE.size
        if tipo == _HOJA:
            entradas = list(_ENTRADA.iter_unpack(datos[inicio:inicio + n * _ENTRADA.size]))
            return Hoja(pagina, [(p, a) for p, a, _, _ in entradas], [(o, l) for _, _, o, l in entradas], primero)
        separadores = list(_SEPARADOR.iter_unpack(datos[inicio:inicio + n * _SEPARADOR.size]))
        return Interno(pagina, [(p, a) for p, a, _ in separadores], [primero] + [h for _, _, h in separadores])

    def _leer(self, pagina):
        nodo = self.buffer.obtener(pagina)
        if nodo is None:
            self._archivo.seek(pagina * self.tam_pagina)
            nodo = self._decodificar(pagina, self._archivo.read(self.tam_pagina))
            self.lecturas += 1
            self.buffer.poner(pagina, nodo)
        return nodo

    def _escribir(self, nodo):
        """Escritura inmediata en disco (write-through) y actualización del buffer pool."""
        self._archivo.seek(nodo.pagina * self.tam_pagina)
        self._archivo.write(self._codificar(nodo))
        self.escrituras += 1
        self.buffer.poner(nodo.pagina, nodo)

    # ----- Búsqueda ---------------------------------------------------------------

    def _hoja_de(self, k, camino=None):
        """Desciende desde la raíz hasta la hoja que debe contener k. Una lectura por nivel."""
        nodo = self._leer(self.raiz)
        while isinstance(nodo, Interno):
            i = bisect_der(nodo.claves, k)
            if camino is not None:
                camino.append((nodo, i))
            nodo = self._leer(nodo.hijos[i])
        return nodo

    def buscar(self, precio, asin):
        """Valor (offset, largo) de la clave, o None si no existe."""
        k = clave(precio, asin)
        hoja = self._hoja_de(k)
        i = bisect_izq(hoja.claves, k)
        if i < len(hoja.claves) and hoja.claves[i] == k:
            return hoja.valores[i]
        return None

    def rango(self, precio_min, precio_max):
        """Entradas con precio_min <= precio <= precio_max, en orden: (precio, asin, offset, largo)."""
        desde = (float(precio_min), b"")
        hasta = float(precio_max)
        hoja = self._hoja_de(desde)
        i = bisect_izq(hoja.claves, desde)
        while True:
            claves, valores = hoja.claves, hoja.valores
            while i < len(claves):
                p, a = claves[i]
                if p > hasta:
                    return
                o, l = valores[i]
                yield p, a.decode("ascii"), o, l
                i += 1
            if hoja.siguiente == _SIN_PAGINA:
                return
            hoja = self._leer(hoja.siguiente)
            i = 0

    def __iter__(self):
        """Todas las entradas en orden, recorriendo las hojas enlazadas."""
        pagina = self.primera_hoja
        while pagina != _SIN_PAGINA:
            hoja = self._leer(pagina)
            for (p, a), (o, l) in zip(hoja.claves, hoja.valores):
                yield p, a.decode("ascii"), o, l
            pagina = hoja.siguiente

    def __len__(self):
        return self.n_claves

    # ----- Inserción --------------------------------------------------------------

    def insertar(self, precio, asin, offset, largo):
        """Inserta la entrada o actualiza su valor si la clave ya existe."""
        k = clave(precio, asin)
        camino = []
        hoja = self._hoja_de(k, camino)
        i = bisect_izq(hoja.claves, k)
        if i < len(hoja.claves) and hoja.claves[i] == k:
            hoja.valores[i] = (offset, largo)
            self._escribir(hoja)
            return
        hoja.claves.insert(i, k)
        hoja.valores.insert(i, (offset, largo))
        self.n_claves += 1
        if len(hoja.claves) <= self.max_hoja:
            self._escribir(hoja)
            return

        # División de la hoja: la mitad derecha pasa a una hoja nueva enlazada a continuación
        medio = len(hoja.claves) // 2
        nueva = Hoja(self._nueva_pagina(), hoja.claves[medio:], hoja.valores[medio:], hoja.siguiente)
        del hoja.claves[medio:], hoja.valores[medio:]
        hoja.siguiente = nueva.pagina
        self._escribir(hoja)
        self._escribir(nueva)
        self._subir_separador(camino, nueva.claves[0], nueva.pagina)

    def _subir_separador(self, camino, separador, pagina_derecha):
        """Inserta el separador en el padre y divide hacia arriba mientras haga falta."""
        while camino:
            padre, i = camino.pop()
            padre.claves.insert(i, separador)
            padre.hijos.insert(i + 1, pagina_derecha)
            if len(padre.claves) <= self.max_interno:
                self._escribir(padre)
                return
            # División del nodo interno: la clave del medio sube y no se queda en ningún hijo
            medio = len(padre.claves) // 2
            separador = padre.claves[medio]
            nuevo = Interno(self._nueva_pagina(), padre.claves[medio + 1:], padre.hijos[medio + 1:])
            del padre.claves[medio:], padre.hijos[medio + 1:]
            self._escribir(padre)
            self._escribir(nuevo)
            pagina_derecha = nuevo.pagina
        # Se dividió la raíz: el árbol crece un nivel
        raiz = Interno(self._nueva_pagina(), [separador], [self.raiz, pagina_derecha])
        self._escribir(raiz)
        self.raiz = raiz.pagina
        self.altura += 1

    # ----- Eliminación ------------------------------------------------------------

    def eliminar(self, precio, asin):
        """Quita la entrada de su hoja. Devuelve True si existía.

        No se fusionan ni redistribuyen nodos, así que una hoja puede quedar por debajo de la
        mitad o vacía. Las búsquedas y los rangos siguen siendo correctos, porque los separadores
        de los nodos internos solo guían el descenso.
        """
        k = clave(precio, asin)
        hoja = self._hoja_de(k)
        i = bisect_izq(hoja.claves, k)
        if i == len(hoja.claves) or hoja.claves[i] != k:
            return False
        del hoja.claves[i], hoja.valores[i]
        self.n_claves -= 1
        self._escribir(hoja)
        return True

    # ----- Carga masiva -----------------------------------------------------------

    def carga_masiva(self, entradas, llenado=LLENADO_CARGA_MASIVA):
        """Construye el árbol desde entradas (precio, asin, offset, largo) en orden estricto de clave.

        Las hojas se escriben de izquierda a derecha con `llenado` de su capacidad y después se
        construye cada nivel interno sobre el anterior. Cada página se escribe una vez: O(n).
        """
        if self.n_claves:
            raise ValueError("la carga masiva requiere un árbol vacío")
        por_hoja = max(1, int(self.max_hoja * llenado))
        por_interno = max(2, int((self.max_interno + 1) * llenado))      # hijos por nodo interno
        self.n_paginas = 1                                               # se reutiliza la página 1
        nivel = []                                                        # (menor clave, página)
        hoja = None
        anterior = None
        for precio, asin, offset, largo in entradas:
            k = clave(precio, asin)
            if hoja is None or len(hoja.claves) == por_hoja:
                nueva = Hoja(self._nueva_pagina())
                if hoja is not None:
                    hoja.siguiente = nueva.pagina
                    self._escribir(hoja)
                hoja = nueva
                nivel.append((k, hoja.pagina))
            if anterior is not None and not anterior < k:
                raise ValueError("las entradas de la carga masiva deben estar en orden estricto de clave")
            hoja.claves.append(k)
            hoja.valores.append((offset, largo))
            anterior = k
            self.n_claves += 1
        if hoja is None:                                                  # sin entradas: hoja raíz vacía
            hoja = Hoja(self._nueva_pagina())
            nivel.append((None, hoja.pagina))
        self._escribir(hoja)
        self.primera_hoja = nivel[0][1]

        self.altura = 1
        while len(nivel) > 1:
            n_grupos = -(-len(nivel) // por_interno)
            base, extra = divmod(len(nivel), n_grupos)                    # grupos de tamaño parejo
            siguiente_nivel = []
            inicio = 0
            for g in range(n_grupos):
                grupo = nivel[inicio:inicio + base + (g < extra)]
                inicio += len(grupo)
                nodo = Interno(self._nueva_pagina(), [k for k, _ in grupo[1:]], [p for _, p in grupo])
                self._escribir(nodo)
                siguiente_nivel.append((grupo[0][0], nodo.pagina))
            nivel = siguiente_nivel
            self.altura += 1
        self.raiz = nivel[0][1]
        self._guardar_cabecera()

    # ----- Estadísticas y cierre ---------------------------------------------------

    def ocupacion_hojas(self):
        """Fracción media de la capacidad usada en las hojas. Recorre todas las hojas."""
        hojas, entradas = 0, 0
        pagina = self.primera_hoja
        while pagina != _SIN_PAGINA:
            hoja = self._leer(pagina)
            hojas += 1
            entradas += len(hoja.claves)
            pagina = hoja.siguiente
        return entradas / (hojas * self.max_hoja) if hojas else 0.0

    def tamano_bytes(self):
        return self.n_paginas * self.tam_pagina

    def cerrar(self):
        self._guardar_cabecera()
        self._archivo.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()


# ----- Índice de precios del catálogo ---------------------------------------------

def entradas_catalogo(ruta_catalogo=RUTA_CATALOGO):
    """Entradas (precio, asin, offset, largo) de los productos con precio en el catálogo de la etapa 3."""
    offset = 0
    with open(ruta_catalogo, "rb") as archivo:
        for linea in archivo:
            registro = json.loads(linea)
            if registro["precio"] is not None:
                yield registro["precio"], registro["parent_asin"], offset, len(linea)
            offset += len(linea)


def construir_indice_precio(ruta_indice=RUTA_INDICE_PRECIO, ruta_catalogo=RUTA_CATALOGO, tam_pagina=TAM_PAGINA):
    """Ordena las entradas con el merge sort de la etapa 1 y construye el índice con carga masiva."""
    entradas = merge_sort(list(entradas_catalogo(ruta_catalogo)), clave=lambda e: (e[0], e[1]))
    arbol = ArbolBMas(ruta_indice, tam_pagina=tam_pagina, crear=True)
    arbol.carga_masiva(entradas)
    return arbol


def leer_registro(archivo_catalogo, offset, largo):
    """Registro del catálogo en la posición indicada por el índice."""
    archivo_catalogo.seek(offset)
    return json.loads(archivo_catalogo.read(largo))
