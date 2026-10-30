"""Traza paso a paso de las etapas 3, 4 y 5 para el visualizador.

Las etapas 3 a 5 reciben los eventos que atendió la etapa 2, en su orden de salida, tal como
quedaron en la traza de las etapas 0 a 2 (los mismos números #tid). Las tres etapas corren en
secuencia, igual que en los notebooks:

    etapa 3  el stream pasa por la caché LRU, el estado de productos y la matriz DOK
    etapa 4  el índice de precios se construye con carga masiva y después se insertan,
             uno a uno, los productos que solo tienen precio observado en el stream
    etapa 5  los productos del índice de precios se convierten en vectores de 4 atributos,
             se construye el KD-Tree y se ejecutan consultas KNN y por rango

Las estructuras de este módulo siguen línea por línea el código de src/etapa_3/storage.py,
src/etapa_4/bplustree.py y src/etapa_5/kdtree.py (los métodos que muestran los notebooks), con
ganchos que registran cada operación. El B+Tree guarda las páginas en un diccionario en lugar
de un archivo para poder fotografiarlo. Los parámetros se achican para que las estructuras
quepan en pantalla (caché de 8 productos, hojas de 4 entradas, hojas del KD-Tree de 4 puntos).

`scripts/generar_visualizador_345.py` compara al final los resultados de estas copias con las
clases reales de src/ sobre los mismos datos, y se detiene si no coinciden.
"""

import heapq
import math
import zlib
from bisect import bisect_left, bisect_right

import numpy as np

# Pesos de la matriz usuario × producto (etapa 3, paso 5)
PESOS = {"view": 1.0, "cart": 2.0, "purchase": 4.0, "review": 1.0, "return": -4.0}
REPOSICION_S = 48 * 3600
ATRIBUTOS = ["log_precio", "average_rating", "log_rating_number", "log_actividad_stream"]

_BORRADO = object()          # lápida de la tabla hash


def hash_clave(clave):
    """CRC32 de la clave, igual en cualquier ejecución (etapa 3, paso 2)."""
    if isinstance(clave, int):
        clave = str(clave)
    return zlib.crc32(clave.encode("utf-8"))


def stock_inicial_demo(asin):
    """Stock sintético estable entre 5 y 50 unidades (se reemplaza por el de src/ si existe)."""
    return 5 + hash_clave("stock:" + asin) % 46


class Traza:
    """Lista de pasos. Cada paso guarda una foto de la estructura solo cuando su forma cambia."""

    def __init__(self):
        self.pasos = []
        self.contadores = {}

    def registrar(self, etapa, accion, tid=None, detalle=None, estructura=None):
        self.pasos.append({
            "paso": len(self.pasos), "etapa": etapa, "accion": accion, "tid": tid,
            "detalle": detalle or {}, "estructura": estructura,
            "contadores": dict(self.contadores),
        })


# =====================================================================
# Etapa 3. Tabla hash, caché LRU, estado de productos y matriz DOK
# =====================================================================
class TablaHash:
    """Direccionamiento abierto con sondeo lineal, lápidas y reconstrucción con carga 0.7."""

    def __init__(self, capacidad=8, carga_max=0.7):
        self.carga_max = carga_max
        cap = 1
        while cap * carga_max <= capacidad:          # 8 claves → 16 posiciones, como el notebook
            cap *= 2
        self._claves = [None] * cap
        self._valores = [None] * cap
        self._n = self._usadas = 0
        self.redimensionamientos = 0
        self.ultimo_sondeo = []                      # posiciones revisadas por la última búsqueda

    def __len__(self):
        return self._n

    @property
    def capacidad(self):
        return len(self._claves)

    def _buscar(self, clave):
        claves = self._claves
        mascara = len(claves) - 1
        i = hash_clave(clave) & mascara
        lapida = -1
        self.ultimo_sondeo = []
        while True:
            self.ultimo_sondeo.append(i)
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
        i, encontrada = self._buscar(clave)
        if encontrada:
            self._valores[i] = valor
            return False
        if self._claves[i] is None:
            self._usadas += 1
        self._claves[i] = clave
        self._valores[i] = valor
        self._n += 1
        if self._usadas > self.carga_max * len(self._claves):
            self._reconstruir()
            return True
        return False

    def eliminar(self, clave):
        i, encontrada = self._buscar(clave)
        if encontrada:
            self._claves[i] = _BORRADO
            self._valores[i] = None
            self._n -= 1
        return encontrada

    def _reconstruir(self):
        cap = len(self._claves)
        if self._n > self.carga_max * cap / 2:
            cap *= 2
        pares = list(self.items())
        self._claves = [None] * cap
        self._valores = [None] * cap
        self._n = self._usadas = 0
        sondeo = self.ultimo_sondeo
        for k, v in pares:
            self.insertar(k, v)
        self.ultimo_sondeo = sondeo
        self.redimensionamientos += 1

    def items(self):
        for k, v in zip(self._claves, self._valores):
            if k is not None and k is not _BORRADO:
                yield k, v

    def foto(self):
        """Contenido de cada posición: la clave, None (vacía) o "†" (lápida)."""
        return [None if k is None else ("†" if k is _BORRADO else k) for k in self._claves]


class _Nodo:
    __slots__ = ("clave", "valor", "ant", "sig")

    def __init__(self, clave=None, valor=None):
        self.clave, self.valor, self.ant, self.sig = clave, valor, None, None


class CacheLRU:
    """Tabla hash propia + lista doblemente enlazada con dos centinelas."""

    def __init__(self, capacidad):
        self.capacidad = capacidad
        self._tabla = TablaHash(capacidad)
        self._cabeza, self._cola = _Nodo(), _Nodo()
        self._cabeza.sig, self._cola.ant = self._cola, self._cabeza
        self.aciertos = self.fallos = self.desalojos = 0
        self.ultimo_desalojado = None
        self.hubo_reconstruccion = False
        self.al_desbordar = None                     # gancho: se llama antes de desalojar

    def _quitar(self, nodo):
        nodo.ant.sig, nodo.sig.ant = nodo.sig, nodo.ant

    def _al_frente(self, nodo):
        nodo.ant, nodo.sig = self._cabeza, self._cabeza.sig
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
        self.ultimo_desalojado = None
        self.hubo_reconstruccion = False
        nodo = self._tabla.obtener(clave)
        if nodo is not None:
            nodo.valor = valor
            self._quitar(nodo)
            self._al_frente(nodo)
            return
        nodo = _Nodo(clave, valor)
        self.hubo_reconstruccion = self._tabla.insertar(clave, nodo)
        self._al_frente(nodo)
        if len(self._tabla) > self.capacidad:
            if self.al_desbordar is not None:
                self.al_desbordar()
            ultimo = self._cola.ant
            self._quitar(ultimo)
            self._tabla.eliminar(ultimo.clave)
            self.desalojos += 1
            self.ultimo_desalojado = ultimo.clave

    def orden(self):
        """Claves de la más reciente a la menos reciente."""
        claves, nodo = [], self._cabeza.sig
        while nodo is not self._cola:
            claves.append(nodo.clave)
            nodo = nodo.sig
        return claves

    def foto(self):
        return {"lru": self.orden(), "tabla": self._tabla.foto()}


class EstadoProducto:
    CAMPOS = ("stock", "vistas", "carritos", "compras", "devoluciones", "resenas", "suma_rating",
              "agotamientos", "ventas_sin_stock", "precio_observado", "agotado_desde", "ultima_actividad")

    def __init__(self, stock):
        self.stock = stock
        self.vistas = self.carritos = self.compras = self.devoluciones = self.resenas = 0
        self.suma_rating = 0.0
        self.agotamientos = self.ventas_sin_stock = 0
        self.precio_observado = None
        self.agotado_desde = None
        self.ultima_actividad = None

    def como_dict(self):
        return {c: getattr(self, c) for c in self.CAMPOS}


def etapa3(traza, eventos, catalogo, capacidad_cache, stock_inicial):
    """Recorre los eventos en orden de servicio. Devuelve los estados, la matriz y la caché.

    `catalogo` es un dict asin → registro con offset y largo (lectura en disco simulada).
    """
    cache = CacheLRU(capacidad_cache)
    estados = {}                                      # asin → EstadoProducto (orden de aparición)
    usuarios, productos = {}, {}                      # índice de fila y de columna de la matriz
    celdas = {}                                       # DOK: fila << 32 | columna → peso
    c = traza.contadores
    c.update(consultas=0, aciertos=0, fallos=0, desalojos=0, lecturas_disco=0, productos_estado=0,
             celdas_dok=0, filas=0, columnas=0, sin_producto=0, eventos3=0)

    for tid, ev in eventos:
        c["eventos3"] += 1
        asin = ev["parent_asin"]
        if asin is None:
            c["sin_producto"] += 1
            traza.registrar(3, "sin_producto", tid, {"query": ev.get("query")})
            continue

        # 1. Consulta del producto: caché LRU → si falla, índice hash + lectura en disco
        c["consultas"] += 1
        registro = cache.obtener(asin)
        sondeo = list(cache._tabla.ultimo_sondeo)
        if registro is not None:
            c["aciertos"] += 1
            traza.registrar(3, "acierto", tid, {"asin": asin, "sondeo": sondeo}, cache.foto())
        else:
            c["fallos"] += 1
            traza.registrar(3, "fallo", tid, {"asin": asin, "sondeo": sondeo})
            registro = catalogo[asin]
            c["lecturas_disco"] += 1
            traza.registrar(3, "lectura_disco", tid, {
                "asin": asin, "offset": registro["offset"], "largo": registro["largo"],
                "registro": {k: registro.get(k) for k in ("precio", "average_rating", "rating_number",
                                                          "categoria_hoja", "store")}})
            info = {}

            def al_desbordar():
                # Foto con un producto de más: la nueva clave ya está en la tabla y al frente
                info["foto"] = cache.foto()
                info["sondeo"] = list(cache._tabla.ultimo_sondeo)

            cache.al_desbordar = al_desbordar
            cache.poner(asin, registro)
            cache.al_desbordar = None
            if cache.ultimo_desalojado is not None:
                traza.registrar(3, "poner", tid, {"asin": asin, "sondeo": info["sondeo"], "lleno": True,
                                                  "reconstruccion": cache.hubo_reconstruccion,
                                                  "capacidad_tabla": len(info["foto"]["tabla"])}, info["foto"])
                c["desalojos"] += 1
                traza.registrar(3, "desalojo", tid, {"asin": cache.ultimo_desalojado,
                                                     "sondeo": list(cache._tabla.ultimo_sondeo)}, cache.foto())
            else:
                traza.registrar(3, "poner", tid, {"asin": asin, "sondeo": list(cache._tabla.ultimo_sondeo),
                                                  "lleno": False, "reconstruccion": cache.hubo_reconstruccion,
                                                  "capacidad_tabla": cache._tabla.capacidad}, cache.foto())

        # 2. Estado dinámico del producto
        e = estados.get(asin)
        nuevo = e is None
        if nuevo:
            e = EstadoProducto(stock_inicial(asin))
            estados[asin] = e
            c["productos_estado"] += 1
        antes = e.como_dict()
        tipo, ts = ev["event_type"], ev["ts"]
        notas = []
        if e.agotado_desde is not None and ts - e.agotado_desde >= REPOSICION_S:
            e.stock = stock_inicial(asin)
            e.agotado_desde = None
            notas.append("reposicion")
        if tipo == "view":
            e.vistas += 1
        elif tipo == "cart":
            e.carritos += 1
        elif tipo == "purchase":
            e.compras += 1
            if ev.get("price") is not None:
                e.precio_observado = ev["price"]
            if e.stock > 0:
                e.stock -= 1
                if e.stock == 0:
                    e.agotado_desde = ts
                    e.agotamientos += 1
                    notas.append("agotado")
            else:
                e.ventas_sin_stock += 1
                notas.append("sin_stock")
        elif tipo == "return":
            e.devoluciones += 1
            e.stock += 1
            e.agotado_desde = None
        elif tipo == "review":
            e.resenas += 1
            e.suma_rating += ev.get("rating") or 0
        e.ultima_actividad = ts if e.ultima_actividad is None else max(e.ultima_actividad, ts)
        despues = e.como_dict()
        cambios = [k for k in despues if despues[k] != antes[k]]
        traza.registrar(3, "estado", tid, {"asin": asin, "nuevo": nuevo, "estado": despues,
                                           "cambios": cambios, "notas": notas})

        # 3. Matriz dispersa usuario × producto (DOK)
        peso = PESOS.get(tipo)
        if peso is not None:
            usuario = ev["user_id"]
            if usuario not in usuarios:
                usuarios[usuario] = len(usuarios)
            if asin not in productos:
                productos[asin] = len(productos)
            fila, col = usuarios[usuario], productos[asin]
            clave = fila << 32 | col
            celda_nueva = clave not in celdas
            celdas[clave] = celdas.get(clave, 0.0) + peso
            c.update(celdas_dok=len(celdas), filas=len(usuarios), columnas=len(productos))
            traza.registrar(3, "matriz", tid, {"usuario": usuario, "asin": asin, "fila": fila,
                                               "columna": col, "peso": peso, "valor": celdas[clave],
                                               "nueva": celda_nueva})

    # Cierre: DOK → CSR (se omiten las celdas con peso <= 0)
    positivas = sorted((k, v) for k, v in celdas.items() if v > 0)
    n_filas, n_cols = len(usuarios), len(productos)
    indptr = [0] * (n_filas + 1)
    for k, _ in positivas:
        indptr[(k >> 32) + 1] += 1
    for i in range(n_filas):
        indptr[i + 1] += indptr[i]
    nnz = len(positivas)
    traza.registrar(3, "csr", None, {"nnz": nnz, "omitidas": len(celdas) - nnz, "filas": n_filas,
                                     "columnas": n_cols, "bytes_csr": 8 * (n_filas + 1) + 12 * nnz,
                                     "bytes_densa": 8 * n_filas * n_cols, "indptr_inicio": indptr[:12]})
    return {"cache": cache, "estados": estados, "usuarios": usuarios, "productos": productos,
            "celdas": celdas, "nnz": nnz}


# =====================================================================
# Etapa 4. B+Tree con clave (precio, parent_asin)
# =====================================================================
class Hoja:
    def __init__(self, pagina, claves=None, valores=None, siguiente=-1):
        self.pagina = pagina
        self.claves = claves if claves is not None else []
        self.valores = valores if valores is not None else []
        self.siguiente = siguiente


class Interno:
    def __init__(self, pagina, claves=None, hijos=None):
        self.pagina = pagina
        self.claves = claves if claves is not None else []
        self.hijos = hijos if hijos is not None else []


def clave(precio, asin):
    return (float(precio), asin)


class ArbolBMas:
    """B+Tree con las páginas en un diccionario. Mismos algoritmos que src/etapa_4/bplustree.py."""

    def __init__(self, max_hoja, max_interno, traza=None):
        self.max_hoja, self.max_interno = max_hoja, max_interno
        self.paginas = {}
        self.raiz = None
        self.n_paginas = 1                           # la página 0 es la cabecera
        self.n_claves = 0
        self.altura = 0
        self.lecturas = self.escrituras = 0
        self.divisiones_hoja = self.divisiones_interno = 0
        self.primera_hoja = None
        self.traza = traza
        self.pendientes = []                         # nivel en construcción durante la carga masiva

    # --- páginas
    def _nueva_pagina(self):
        p = self.n_paginas
        self.n_paginas += 1
        return p

    def _escribir(self, nodo):
        self.paginas[nodo.pagina] = nodo
        self.escrituras += 1

    def _leer(self, pagina):
        self.lecturas += 1
        return self.paginas[pagina]

    def __len__(self):
        return self.n_claves

    # --- foto para el visualizador
    def foto(self, resaltar=None):
        nodos = {}
        for p, n in self.paginas.items():
            if isinstance(n, Hoja):
                nodos[p] = {"h": 1, "k": [[k[0], k[1]] for k in n.claves], "s": n.siguiente}
            else:
                nodos[p] = {"h": 0, "k": [[k[0], k[1]] for k in n.claves], "c": list(n.hijos)}
        if self.raiz is None:
            raices = [p for _, p in self.pendientes]
        else:
            # Tras una división, el nodo nuevo queda sin padre hasta que su separador sube
            hijos = {h for n in self.paginas.values() if isinstance(n, Interno) for h in n.hijos}
            raices = sorted((p for p in self.paginas if p not in hijos), key=lambda p: self.paginas[p].claves[:1])
        return {"raiz": self.raiz, "nodos": nodos, "raices": raices}

    def _contar(self):
        if self.traza is None:
            return
        self.traza.contadores.update(entradas=self.n_claves, paginas=self.n_paginas, altura=self.altura,
                                     escrituras=self.escrituras, lecturas=self.lecturas,
                                     div_hoja=self.divisiones_hoja, div_interno=self.divisiones_interno)

    def _reg(self, accion, tid=None, detalle=None, foto=True):
        if self.traza is None:
            return
        self._contar()
        self.traza.registrar(4, accion, tid, detalle, self.foto() if foto else None)

    # --- descenso
    def _hoja_de(self, k, camino=None, pasos=None):
        nodo = self._leer(self.raiz)
        while isinstance(nodo, Interno):
            i = bisect_right(nodo.claves, k)
            if camino is not None:
                camino.append((nodo, i))
            if pasos is not None:
                pasos.append([nodo.pagina, i])
            nodo = self._leer(nodo.hijos[i])
        if pasos is not None:
            pasos.append([nodo.pagina, None])
        return nodo

    # --- carga masiva
    def carga_masiva(self, entradas, llenado):
        """entradas: (precio, asin, offset, largo, tid_origen) en orden estricto de clave."""
        por_hoja = max(1, int(self.max_hoja * llenado))
        por_interno = max(2, int((self.max_interno + 1) * llenado))
        nivel, hoja, anterior = [], None, None
        for precio, asin, offset, largo in entradas:
            k = clave(precio, asin)
            if hoja is None or len(hoja.claves) == por_hoja:
                nueva = Hoja(self._nueva_pagina())
                if hoja is not None:
                    hoja.siguiente = nueva.pagina
                    self._escribir(hoja)
                    self._reg("masiva_hoja", None, {"pagina": hoja.pagina, "n": len(hoja.claves),
                                                    "por_hoja": por_hoja})
                hoja = nueva
                nivel.append((k, hoja.pagina))
                self.pendientes = nivel
                self.paginas[hoja.pagina] = hoja          # visible mientras se llena
            if anterior is not None and not anterior < k:
                raise ValueError("entradas fuera de orden")
            hoja.claves.append(k)
            hoja.valores.append((offset, largo))
            anterior = k
            self.n_claves += 1
        self._escribir(hoja)
        self._reg("masiva_hoja", None, {"pagina": hoja.pagina, "n": len(hoja.claves), "por_hoja": por_hoja,
                                        "ultima": True})
        self.primera_hoja = nivel[0][1]
        self.altura = 1
        while len(nivel) > 1:
            n_grupos = -(-len(nivel) // por_interno)
            base, extra = divmod(len(nivel), n_grupos)
            siguiente, inicio = [], 0
            for g in range(n_grupos):
                grupo = nivel[inicio:inicio + base + (g < extra)]
                inicio += len(grupo)
                nodo = Interno(self._nueva_pagina(), [k for k, _ in grupo[1:]], [p for _, p in grupo])
                self._escribir(nodo)
                siguiente.append((grupo[0][0], nodo.pagina))
                self.pendientes = siguiente + nivel[inicio:]
                self._reg("masiva_interno", None, {"pagina": nodo.pagina, "hijos": len(grupo),
                                                   "nivel": self.altura + 1, "por_interno": por_interno})
            nivel = siguiente
            self.altura += 1
        self.raiz = nivel[0][1]
        self.pendientes = []
        self._reg("masiva_fin", None, {"raiz": self.raiz, "altura": self.altura, "paginas": self.n_paginas - 1})

    # --- inserción en línea
    def insertar(self, precio, asin, offset, largo, tid=None):
        k = clave(precio, asin)
        camino, pasos = [], []
        lecturas0 = self.lecturas
        hoja = self._hoja_de(k, camino, pasos)
        self._reg("descenso", tid, {"asin": asin, "clave": [k[0], k[1]], "camino": pasos,
                                    "lecturas": self.lecturas - lecturas0, "motivo": "insertar"}, foto=False)
        i = bisect_left(hoja.claves, k)
        if i < len(hoja.claves) and hoja.claves[i] == k:
            hoja.valores[i] = (offset, largo)
            self._escribir(hoja)
            self._reg("actualizar", tid, {"asin": asin, "pagina": hoja.pagina})
            return
        hoja.claves.insert(i, k)
        hoja.valores.insert(i, (offset, largo))
        self.n_claves += 1
        if len(hoja.claves) <= self.max_hoja:
            self._escribir(hoja)
            self._reg("insertar_hoja", tid, {"asin": asin, "clave": [k[0], k[1]], "pagina": hoja.pagina,
                                             "posicion": i})
            return
        # Hoja desbordada: se muestra un momento con max_hoja + 1 entradas
        self._reg("desborde_hoja", tid, {"asin": asin, "clave": [k[0], k[1]], "pagina": hoja.pagina,
                                         "n": len(hoja.claves), "max": self.max_hoja})
        medio = len(hoja.claves) // 2
        nueva = Hoja(self._nueva_pagina(), hoja.claves[medio:], hoja.valores[medio:], hoja.siguiente)
        del hoja.claves[medio:], hoja.valores[medio:]
        hoja.siguiente = nueva.pagina
        self._escribir(hoja)
        self._escribir(nueva)
        self.divisiones_hoja += 1
        sep = nueva.claves[0]
        self._subir_separador(camino, sep, nueva.pagina, tid,
                              {"asin": asin, "izquierda": hoja.pagina, "derecha": nueva.pagina,
                               "separador": [sep[0], sep[1]]})

    def _subir_separador(self, camino, separador, pagina_derecha, tid, info_hoja):
        # Paso intermedio: la hoja ya está dividida y el separador está por subir
        self._reg("division_hoja", tid, dict(info_hoja, padre=camino[-1][0].pagina if camino else None))
        while camino:
            padre, i = camino.pop()
            padre.claves.insert(i, separador)
            padre.hijos.insert(i + 1, pagina_derecha)
            if len(padre.claves) <= self.max_interno:
                self._escribir(padre)
                self._reg("separador_padre", tid, {"pagina": padre.pagina, "separador": list(separador),
                                                   "posicion": i})
                return
            self._reg("desborde_interno", tid, {"pagina": padre.pagina, "n": len(padre.claves),
                                                "max": self.max_interno, "separador": list(separador)})
            medio = len(padre.claves) // 2
            separador = padre.claves[medio]
            nuevo = Interno(self._nueva_pagina(), padre.claves[medio + 1:], padre.hijos[medio + 1:])
            del padre.claves[medio:], padre.hijos[medio + 1:]
            self._escribir(padre)
            self._escribir(nuevo)
            self.divisiones_interno += 1
            pagina_derecha = nuevo.pagina
            self._reg("division_interno", tid, {"izquierda": padre.pagina, "derecha": nuevo.pagina,
                                                "sube": list(separador)})
        raiz = Interno(self._nueva_pagina(), [separador], [self.raiz, pagina_derecha])
        self._escribir(raiz)
        self.raiz = raiz.pagina
        self.altura += 1
        self._reg("nueva_raiz", tid, {"pagina": raiz.pagina, "separador": list(separador), "altura": self.altura})

    # --- consultas
    def buscar(self, precio, asin, registrar=True):
        k = clave(precio, asin)
        lecturas0 = self.lecturas
        pasos = []
        hoja = self._hoja_de(k, pasos=pasos)
        i = bisect_left(hoja.claves, k)
        valor = hoja.valores[i] if i < len(hoja.claves) and hoja.claves[i] == k else None
        if registrar:
            self._reg("buscar", None, {"clave": [k[0], k[1]], "camino": pasos, "encontrada": valor is not None,
                                       "valor": valor, "lecturas": self.lecturas - lecturas0}, foto=False)
        return valor

    def rango(self, desde, hasta, registrar=True):
        lecturas0 = self.lecturas
        pasos = []
        k = (float(desde), "")
        hoja = self._hoja_de(k, pasos=pasos)
        if registrar:
            self._reg("rango_inicio", None, {"desde": desde, "hasta": hasta, "camino": pasos,
                                             "lecturas": self.lecturas - lecturas0}, foto=False)
        resultado = []
        i = bisect_left(hoja.claves, k)
        while True:
            en_hoja = []
            terminado = False
            while i < len(hoja.claves):
                kk = hoja.claves[i]
                if kk[0] > hasta:
                    terminado = True
                    break
                en_hoja.append([kk[0], kk[1]])
                resultado.append((kk[0], kk[1]) + tuple(hoja.valores[i]))
                i += 1
            if registrar:
                self._reg("rango_hoja", None, {"pagina": hoja.pagina, "encontradas": en_hoja,
                                               "total": len(resultado), "corta": terminado}, foto=False)
            if terminado or hoja.siguiente < 0:
                break
            hoja = self._leer(hoja.siguiente)
            i = 0
        if registrar:
            self._reg("rango_fin", None, {"desde": desde, "hasta": hasta, "resultados": len(resultado),
                                          "lecturas": self.lecturas - lecturas0,
                                          "asins": [r[1] for r in resultado]}, foto=False)
        return resultado

    def __iter__(self):
        p = self.primera_hoja
        while p is not None and p >= 0:
            h = self.paginas[p]
            for k, v in zip(h.claves, h.valores):
                yield (k[0], k[1]) + tuple(v)
            p = h.siguiente


def etapa4(traza, r3, catalogo, max_hoja, max_interno, llenado, rangos, tid_de_asin):
    c = traza.contadores
    c.update(entradas=0, paginas=1, altura=0, escrituras=0, lecturas=0, div_hoja=0, div_interno=0)
    estados = r3["estados"]
    # Productos con actividad (los que vio la etapa 3); se indexan los que tienen precio
    con_precio = [(catalogo[a]["precio"], a, catalogo[a]["offset"], catalogo[a]["largo"])
                  for a in estados if catalogo[a]["precio"] is not None]
    sin_precio_catalogo = [a for a in estados if catalogo[a]["precio"] is None]
    nuevos = [(estados[a].precio_observado, a, catalogo[a]["offset"], catalogo[a]["largo"])
              for a in sin_precio_catalogo if estados[a].precio_observado is not None]
    traza.registrar(4, "extraer", None, {
        "con_precio": len(con_precio), "sin_precio": len(sin_precio_catalogo), "con_observado": len(nuevos),
        "sin_indice": len(sin_precio_catalogo) - len(nuevos)})
    con_precio.sort(key=lambda e: clave(e[0], e[1]))      # en src/ es el merge sort de la etapa 1
    traza.registrar(4, "ordenar", None, {"n": len(con_precio),
                                         "claves": [[p, a] for p, a, _, _ in con_precio]})

    arbol = ArbolBMas(max_hoja, max_interno, traza)
    arbol.carga_masiva(con_precio, llenado)
    for precio, asin, offset, largo in nuevos:
        arbol.insertar(precio, asin, offset, largo, tid_de_asin.get(asin))

    # Consultas: una puntual y los rangos pedidos
    if con_precio:
        p, a = con_precio[len(con_precio) // 3][:2]
        arbol.buscar(p, a)
    resultados_rango = [arbol.rango(d, h) for d, h in rangos]
    return {"arbol": arbol, "carga": con_precio, "nuevos": nuevos, "rangos": resultados_rango}


# =====================================================================
# Etapa 5. Vectores, KD-Tree y KNN exacto
# =====================================================================
class KDTree:
    """Mismo _construir, knn y rango que src/etapa_5/kdtree.py, con ganchos para la traza."""

    def __init__(self, X, tam_hoja, traza=None):
        self.traza = traza
        self._X0 = np.asarray(X, dtype=np.float64)
        self.n = len(self._X0)
        self.tam_hoja = tam_hoja
        self.perm = np.arange(self.n)
        self.dim, self.corte, self.izq, self.der, self.lo, self.hi = [], [], [], [], [], []
        self.raiz = self._construir(0, self.n, 0)
        self.X = self._X0[self.perm]

    def _construir(self, lo, hi, profundidad):
        nodo = len(self.dim)
        for lista in (self.dim, self.corte, self.izq, self.der, self.lo, self.hi):
            lista.append(-1)
        self.lo[nodo], self.hi[nodo] = lo, hi
        if hi - lo <= self.tam_hoja:
            self._reg_nodo(nodo, profundidad, "hoja")
            return nodo
        indices = self.perm[lo:hi]
        puntos = self._X0[indices]
        dispersion = puntos.max(axis=0) - puntos.min(axis=0)
        dim = int(np.argmax(dispersion))
        if puntos[:, dim].max() == puntos[:, dim].min():
            self._reg_nodo(nodo, profundidad, "hoja")
            return nodo
        mitad = (hi - lo) // 2
        orden = np.argpartition(puntos[:, dim], mitad)
        self.perm[lo:hi] = indices[orden]
        self.dim[nodo] = dim
        self.corte[nodo] = float(puntos[orden[mitad], dim])
        self._reg_nodo(nodo, profundidad, "corte", [round(float(v), 4) for v in dispersion])
        self.izq[nodo] = self._construir(lo, lo + mitad, profundidad + 1)
        self.der[nodo] = self._construir(lo + mitad, hi, profundidad + 1)
        return nodo

    def _reg_nodo(self, nodo, profundidad, tipo, dispersion=None):
        if self.traza is None:
            return
        self.traza.contadores.update(kd_nodos=nodo + 1)
        self.traza.registrar(5, "kd_" + tipo, None, {
            "nodo": nodo, "profundidad": profundidad, "lo": self.lo[nodo], "hi": self.hi[nodo],
            "dim": self.dim[nodo], "corte": self.corte[nodo], "dispersion": dispersion,
            "puntos": self.perm[self.lo[nodo]:self.hi[nodo]].tolist()})

    def estructura(self):
        return {"dim": self.dim, "corte": self.corte, "izq": self.izq, "der": self.der,
                "lo": self.lo, "hi": self.hi, "perm": self.perm.tolist(), "raiz": self.raiz}

    def altura(self):
        def h(n):
            return 1 if self.dim[n] < 0 else 1 + max(h(self.izq[n]), h(self.der[n]))
        return h(self.raiz)

    def knn(self, q, k, consulta=None):
        """Igual que KDTree.knn de src/ (sin filtro de etiqueta), con un paso por nodo."""
        q = np.asarray(q, dtype=np.float64)
        qs = q.tolist()
        mejores = []                               # heap de (-d², -p): la raíz es el peor candidato
        peor = math.inf
        dim, corte, izq, der, lo, hi = self.dim, self.corte, self.izq, self.der, self.lo, self.hi
        pila = [(self.raiz, 0.0)]
        evaluados = visitados = podados = 0
        t = self.traza

        def lista_mejores():
            return sorted([[math.sqrt(-d2), int(self.perm[-p])] for d2, p in mejores])

        while pila:
            nodo, cota = pila.pop()
            if cota >= peor:
                podados += 1
                if t:
                    t.contadores.update(kd_visitados=visitados, kd_evaluados=evaluados, kd_podados=podados)
                    t.registrar(5, "knn_poda", None, {"consulta": consulta, "nodo": nodo, "cota": math.sqrt(cota),
                                                      "peor": math.sqrt(peor), "pila": [n for n, _ in pila]})
                continue
            visitados += 1
            dn = dim[nodo]
            if dn < 0:
                a, b = lo[nodo], hi[nodo]
                d2 = ((self.X[a:b] - q) ** 2).sum(axis=1)
                evaluados += b - a
                aceptados = []
                for cc in np.flatnonzero(d2 < peor).tolist():
                    candidato = (-float(d2[cc]), -(a + cc))
                    if len(mejores) < k:
                        heapq.heappush(mejores, candidato)
                        aceptados.append(int(self.perm[a + cc]))
                    elif candidato > mejores[0]:
                        heapq.heapreplace(mejores, candidato)
                        aceptados.append(int(self.perm[a + cc]))
                    if len(mejores) == k:
                        peor = -mejores[0][0]
                if t:
                    t.contadores.update(kd_visitados=visitados, kd_evaluados=evaluados, kd_podados=podados)
                    t.registrar(5, "knn_hoja", None, {
                        "consulta": consulta, "nodo": nodo, "cota": math.sqrt(cota),
                        "puntos": self.perm[a:b].tolist(), "distancias": [math.sqrt(float(v)) for v in d2],
                        "aceptados": aceptados, "mejores": lista_mejores(),
                        "peor": math.sqrt(peor) if peor < math.inf else None, "pila": [n for n, _ in pila]})
                continue
            diferencia = qs[dn] - corte[nodo]
            cerca, lejos = (izq[nodo], der[nodo]) if diferencia < 0 else (der[nodo], izq[nodo])
            pila.append((lejos, max(cota, diferencia * diferencia)))
            pila.append((cerca, cota))
            if t:
                t.contadores.update(kd_visitados=visitados, kd_evaluados=evaluados, kd_podados=podados)
                t.registrar(5, "knn_interno", None, {
                    "consulta": consulta, "nodo": nodo, "cota": math.sqrt(cota), "dim": dn, "corte": corte[nodo],
                    "valor_q": qs[dn], "cerca": cerca, "lejos": lejos,
                    "cota_lejos": math.sqrt(max(cota, diferencia * diferencia)),
                    "peor": math.sqrt(peor) if peor < math.inf else None, "pila": [n for n, _ in pila]})
        self.puntos_evaluados, self.nodos_visitados, self.nodos_podados = evaluados, visitados, podados
        resultado = sorted((-d2, -p) for d2, p in mejores)
        return [(math.sqrt(d2), int(self.perm[p])) for d2, p in resultado]

    def rango(self, minimos, maximos, consulta=None):
        minimos = np.asarray(minimos, dtype=np.float64)
        maximos = np.asarray(maximos, dtype=np.float64)
        resultado, pila, evaluados, visitados = [], [self.raiz], 0, 0
        t = self.traza
        while pila:
            nodo = pila.pop()
            visitados += 1
            if t:
                t.contadores.update(kd_visitados=visitados)
            dn = self.dim[nodo]
            if dn < 0:
                a, b = self.lo[nodo], self.hi[nodo]
                dentro = np.all((self.X[a:b] >= minimos) & (self.X[a:b] <= maximos), axis=1)
                evaluados += b - a
                nuevos = self.perm[a:b][dentro].tolist()
                resultado.extend(nuevos)
                if t:
                    t.contadores.update(kd_evaluados=evaluados)
                    t.registrar(5, "rango_kd_hoja", None, {"consulta": consulta, "nodo": nodo,
                                                           "puntos": self.perm[a:b].tolist(), "dentro": nuevos,
                                                           "total": len(resultado), "pila": list(pila)})
                continue
            va_izq = bool(minimos[dn] <= self.corte[nodo])
            va_der = bool(maximos[dn] >= self.corte[nodo])
            if va_izq:
                pila.append(self.izq[nodo])
            if va_der:
                pila.append(self.der[nodo])
            if t:
                t.registrar(5, "rango_kd_interno", None, {"consulta": consulta, "nodo": nodo, "dim": dn,
                                                          "corte": self.corte[nodo], "va_izq": va_izq,
                                                          "va_der": va_der, "pila": list(pila)})
        self.puntos_evaluados = evaluados
        return resultado


def knn_fuerza_bruta(Z, q, k):
    """Línea base exacta: distancia a todos los puntos y desempate por índice."""
    d2 = ((Z - q) ** 2).sum(axis=1)
    orden = sorted(range(len(Z)), key=lambda i: (float(d2[i]), i))[:k]
    return [(math.sqrt(float(d2[i])), i) for i in orden]


def etapa5(traza, r3, r4, catalogo, tam_hoja, k, consultas_knn, caja):
    c = traza.contadores
    c.update(kd_puntos=0, kd_nodos=0, kd_visitados=0, kd_evaluados=0, kd_podados=0)
    # 1. Recorrido del B+Tree por sus hojas enlazadas: productos en orden de precio
    asins, X = [], []
    for precio, asin, _, _ in r4["arbol"]:
        reg, e = catalogo[asin], r3["estados"][asin]
        actividad = e.vistas + e.carritos + e.compras
        asins.append(asin)
        X.append([math.log1p(precio), float(reg["average_rating"] or 0), math.log1p(reg["rating_number"] or 0),
                  math.log1p(actividad)])
    X = np.array(X)
    media, desv = X.mean(axis=0), X.std(axis=0)
    desv[desv == 0] = 1.0
    Z = (X - media) / desv
    c["kd_puntos"] = len(asins)
    traza.registrar(5, "vectores", None, {"n": len(asins), "atributos": ATRIBUTOS,
                                          "media": media.round(4).tolist(), "desv": desv.round(4).tolist()})
    kd = KDTree(Z, tam_hoja, traza)
    traza.registrar(5, "kd_fin", None, {"nodos": len(kd.dim), "hojas": sum(1 for d in kd.dim if d < 0),
                                        "altura": kd.altura()})

    resultados = []
    posicion = {a: i for i, a in enumerate(asins)}
    for n_consulta, asin in enumerate(consultas_knn):
        i = posicion[asin]
        c.update(kd_visitados=0, kd_evaluados=0, kd_podados=0)
        traza.registrar(5, "knn_inicio", None, {"consulta": n_consulta, "asin": asin, "punto": i, "k": k,
                                                "q": Z[i].round(4).tolist()})
        res = kd.knn(Z[i], k, consulta=n_consulta)
        fb = knn_fuerza_bruta(Z, Z[i], k)
        iguales = [round(d, 9) for d, _ in res] == [round(d, 9) for d, _ in fb]
        traza.registrar(5, "knn_fin", None, {
            "consulta": n_consulta, "asin": asin, "punto": i, "resultado": [[d, p] for d, p in res],
            "fuerza_bruta": [[d, p] for d, p in fb], "iguales": iguales,
            "evaluados": kd.puntos_evaluados, "visitados": kd.nodos_visitados, "podados": kd.nodos_podados,
            "n": kd.n, "nodos": len(kd.dim)})
        resultados.append(res)

    # Consulta por rango en el espacio original de los atributos (se normaliza antes de buscar)
    minimos = (np.array(caja["min"]) - media) / desv
    maximos = (np.array(caja["max"]) - media) / desv
    c.update(kd_visitados=0, kd_evaluados=0, kd_podados=0)
    traza.registrar(5, "rango_kd_inicio", None, {"consulta": "rango", "caja": caja,
                                                 "min_z": minimos.round(4).tolist(), "max_z": maximos.round(4).tolist()})
    dentro = kd.rango(minimos, maximos, consulta="rango")
    esperado = [j for j in range(len(Z)) if np.all((Z[j] >= minimos) & (Z[j] <= maximos))]
    traza.registrar(5, "rango_kd_fin", None, {"consulta": "rango", "resultado": sorted(dentro),
                                              "esperado": esperado, "iguales": sorted(dentro) == esperado,
                                              "evaluados": kd.puntos_evaluados, "n": kd.n})
    return {"kd": kd, "asins": asins, "X": X, "Z": Z, "media": media, "desv": desv, "knn": resultados,
            "rango": sorted(dentro)}
