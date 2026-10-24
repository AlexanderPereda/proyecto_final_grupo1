"""Etapa 5: índice multidimensional (KD-Tree) y KNN exacto.

Cada producto con precio se representa con un vector de atributos numéricos normalizados.
- KDTree: árbol k-dimensional con hojas de hasta `tam_hoja` puntos. Cada nodo interno divide su
  región por la mediana de la dimensión de mayor dispersión. Los puntos se reordenan para que
  cada hoja ocupe un tramo contiguo de un arreglo, y la distancia a los puntos de una hoja se
  calcula de una vez con numpy.
- knn: búsqueda exacta de los k vecinos más cercanos con poda por distancia al hiperplano.
  Los k mejores candidatos se guardan en el MinHeap de la etapa 2 con la distancia negada,
  para que la raíz sea el peor candidato. Admite un filtro por etiqueta (categoría).
- rango: puntos dentro de una caja [minimos, maximos] en todas las dimensiones.
- knn_fuerza_bruta: línea base exacta. Calcula la distancia a todos los puntos en O(n·d).
- IndiceProductos: construye los vectores desde las salidas de las etapas 3 y 4 y responde
  "productos similares a este".
"""

import json
import math

import numpy as np
import pyarrow.parquet as pq

from . import config
from .etapa_2.stage2_heap import MinHeap
from .etapa_3.storage import RUTA_CATALOGO, RUTA_ESTADOS, TablaHash
from .etapa_4.bplustree import RUTA_INDICE_PRECIO, ArbolBMas

RUTA_VECTORES = config.CARPETA_INTERIM / "etapa5_vectores.npz"
RUTA_VECINOS = config.CARPETA_INTERIM / "etapa5_vecinos.parquet"
TAM_HOJA = 32
ATRIBUTOS = ["log_precio", "average_rating", "log_rating_number", "log_actividad_stream"]


# ----- KD-Tree --------------------------------------------------------------------

class KDTree:
    """KD-Tree estático sobre una matriz X de n puntos y d dimensiones.

    Construcción O(n log n): en cada nivel se elige la dimensión de mayor dispersión y se
    parte por la mediana con np.argpartition, que es O(m) para m puntos. El árbol queda
    balanceado, con altura log₂(n / tam_hoja).
    """

    def __init__(self, X, tam_hoja=TAM_HOJA, etiquetas=None):
        X = np.ascontiguousarray(X, dtype=np.float64)
        if X.ndim != 2 or len(X) == 0:
            raise ValueError("X debe ser una matriz no vacía de n × d")
        self.n, self.d = X.shape
        self.tam_hoja = tam_hoja
        self.perm = np.arange(self.n)                  # posición en el árbol -> índice original
        self.dim, self.corte, self.izq, self.der, self.lo, self.hi = [], [], [], [], [], []
        self._X0 = X
        self.raiz = self._construir(0, self.n)
        self.X = X[self.perm]                          # puntos reordenados: cada hoja es un tramo contiguo
        del self._X0
        self.etiquetas = None if etiquetas is None else np.asarray(etiquetas)[self.perm]
        self.puntos_evaluados = 0                      # puntos cuya distancia se calculó en la última consulta
        self.nodos_visitados = 0

    def _construir(self, lo, hi):
        nodo = len(self.dim)
        for lista in (self.dim, self.corte, self.izq, self.der, self.lo, self.hi):
            lista.append(-1)
        self.lo[nodo], self.hi[nodo] = lo, hi
        if hi - lo <= self.tam_hoja:
            return nodo                                # hoja: dim = -1
        indices = self.perm[lo:hi]
        puntos = self._X0[indices]
        dim = int(np.argmax(puntos.max(axis=0) - puntos.min(axis=0)))
        if puntos[:, dim].max() == puntos[:, dim].min():
            return nodo                                # todos los puntos son iguales: hoja grande
        mitad = (hi - lo) // 2
        orden = np.argpartition(puntos[:, dim], mitad)
        self.perm[lo:hi] = indices[orden]
        # La mitad izquierda queda con valores <= corte y la derecha con valores >= corte
        self.dim[nodo] = dim
        self.corte[nodo] = float(puntos[orden[mitad], dim])
        self.izq[nodo] = self._construir(lo, lo + mitad)
        self.der[nodo] = self._construir(lo + mitad, hi)
        return nodo

    @property
    def n_nodos(self):
        return len(self.dim)

    def altura(self, nodo=None):
        nodo = self.raiz if nodo is None else nodo
        if self.dim[nodo] < 0:
            return 1
        return 1 + max(self.altura(self.izq[nodo]), self.altura(self.der[nodo]))

    def knn(self, q, k, etiqueta=None):
        """Los k vecinos más cercanos a q: lista de (distancia, índice original), de menor a mayor distancia.

        Recorrido en profundidad que visita primero el lado del hiperplano donde está q. Un
        subárbol se descarta si la distancia de q a su región (cota inferior) ya no mejora al
        peor de los k candidatos. Con `etiqueta`, solo se aceptan puntos con esa etiqueta.
        """
        q = np.asarray(q, dtype=np.float64)
        qs = q.tolist()
        mejores = MinHeap()                            # (-d², -índice): la raíz es el peor candidato
        peor = math.inf
        dim, corte, izq, der, lo, hi = self.dim, self.corte, self.izq, self.der, self.lo, self.hi
        pila = [(self.raiz, 0.0)]                      # (nodo, cota inferior de d² a su región)
        evaluados = visitados = 0
        while pila:
            nodo, cota = pila.pop()
            if cota >= peor:
                continue
            visitados += 1
            dn = dim[nodo]
            if dn < 0:                                 # hoja: distancias con numpy
                a, b = lo[nodo], hi[nodo]
                d2 = ((self.X[a:b] - q) ** 2).sum(axis=1)
                evaluados += b - a
                if etiqueta is not None:
                    d2[self.etiquetas[a:b] != etiqueta] = math.inf
                for c in np.flatnonzero(d2 < peor).tolist():
                    candidato = (-float(d2[c]), -(a + c))
                    if len(mejores) < k:
                        mejores.push(candidato)
                    elif candidato > mejores.peek():   # más cercano que el peor (o empate con índice menor)
                        mejores.pop()
                        mejores.push(candidato)
                    if len(mejores) == k:
                        peor = -mejores.peek()[0]
                continue
            diferencia = qs[dn] - corte[nodo]
            cerca, lejos = (izq[nodo], der[nodo]) if diferencia < 0 else (der[nodo], izq[nodo])
            pila.append((lejos, max(cota, diferencia * diferencia)))
            pila.append((cerca, cota))                 # se visita primero el lado de q
        self.puntos_evaluados, self.nodos_visitados = evaluados, visitados
        resultado = [(-d2, -p) for d2, p in mejores.elementos()]
        resultado.sort()
        return [(math.sqrt(d2), int(self.perm[p])) for d2, p in resultado]

    def rango(self, minimos, maximos):
        """Índices originales de los puntos con minimos <= x <= maximos en todas las dimensiones."""
        minimos = np.asarray(minimos, dtype=np.float64)
        maximos = np.asarray(maximos, dtype=np.float64)
        resultado = []
        pila = [self.raiz]
        evaluados = 0
        while pila:
            nodo = pila.pop()
            dn = self.dim[nodo]
            if dn < 0:
                a, b = self.lo[nodo], self.hi[nodo]
                dentro = np.all((self.X[a:b] >= minimos) & (self.X[a:b] <= maximos), axis=1)
                evaluados += b - a
                resultado.extend(self.perm[a:b][dentro].tolist())
                continue
            if minimos[dn] <= self.corte[nodo]:
                pila.append(self.izq[nodo])
            if maximos[dn] >= self.corte[nodo]:
                pila.append(self.der[nodo])
        self.puntos_evaluados = evaluados
        return resultado


def knn_fuerza_bruta(X, q, k, etiquetas=None, etiqueta=None):
    """KNN exacto por fuerza bruta en O(n·d): lista de (distancia, índice), de menor a mayor distancia."""
    d2 = ((np.asarray(X, dtype=np.float64) - np.asarray(q, dtype=np.float64)) ** 2).sum(axis=1)
    if etiqueta is not None:
        d2[np.asarray(etiquetas) != etiqueta] = np.inf
    k = min(k, int(np.isfinite(d2).sum()))
    if k == 0:
        return []
    candidatos = np.argpartition(d2, k - 1)[:k]
    candidatos = candidatos[np.lexsort((candidatos, d2[candidatos]))]     # por distancia y luego por índice
    return [(math.sqrt(float(d2[i])), int(i)) for i in candidatos]


# ----- Vectores de productos -------------------------------------------------------

def construir_vectores(ruta_indice=RUTA_INDICE_PRECIO, ruta_catalogo=RUTA_CATALOGO, ruta_estados=RUTA_ESTADOS):
    """Atributos de cada producto del índice de precios de la etapa 4.

    Recorre el B+Tree en orden de precio, lee cada registro del catálogo con su offset y
    agrega la actividad del stream registrada por la etapa 3. Devuelve asins, la matriz de
    atributos sin normalizar, la categoría de último nivel y el título de cada producto.
    """
    estados = pq.read_table(ruta_estados, columns=["parent_asin", "vistas", "carritos", "compras"]).to_pandas()
    actividad = TablaHash(len(estados))
    for asin, v, c, p in zip(estados["parent_asin"], estados["vistas"], estados["carritos"], estados["compras"]):
        actividad.insertar(asin, int(v + c + p))

    asins, filas, categorias, titulos = [], [], [], []
    with ArbolBMas(ruta_indice) as indice, open(ruta_catalogo, "rb") as catalogo:
        for precio, asin, offset, largo in indice:
            catalogo.seek(offset)
            registro = json.loads(catalogo.read(largo))
            asins.append(asin)
            filas.append((math.log1p(precio), registro["average_rating"], math.log1p(registro["rating_number"]),
                          math.log1p(actividad.obtener(asin, 0))))
            categorias.append(registro["categoria_hoja"] or "(sin categoría)")
            titulos.append(registro["title"])
    return asins, np.array(filas, dtype=np.float64), categorias, titulos


class IndiceProductos:
    """KD-Tree sobre los vectores normalizados de los productos y consulta de productos similares.

    Cada atributo se normaliza con z-score (media 0, desviación 1) para que ninguna escala domine
    la distancia euclidiana. Además del árbol global se construye un KD-Tree por categoría. Con
    el filtro sobre el árbol global, los productos de una categoría pequeña están dispersos en
    todo el espacio y la búsqueda recorre muchas hojas; el árbol de la categoría solo contiene
    sus productos.
    """

    def __init__(self, asins, X_crudo, categorias, tam_hoja=TAM_HOJA):
        self.asins = list(asins)
        self.media = X_crudo.mean(axis=0)
        self.desv = X_crudo.std(axis=0)
        self.desv[self.desv == 0] = 1.0
        self.Z = (X_crudo - self.media) / self.desv
        self.nombres_categoria = sorted(set(categorias))
        codigo = {c: i for i, c in enumerate(self.nombres_categoria)}
        self.categoria = np.array([codigo[c] for c in categorias], dtype=np.int32)
        self.posicion = TablaHash(len(self.asins))
        for i, asin in enumerate(self.asins):
            self.posicion.insertar(asin, i)
        self.kd = KDTree(self.Z, tam_hoja=tam_hoja, etiquetas=self.categoria)
        # Un KD-Tree por categoría, con la posición global de cada uno de sus puntos
        orden = np.argsort(self.categoria, kind="stable")
        limites = np.searchsorted(self.categoria[orden], np.arange(len(self.nombres_categoria) + 1))
        self.miembros = [orden[a:b] for a, b in zip(limites[:-1], limites[1:])]
        self.kd_categoria = [KDTree(self.Z[m], tam_hoja=tam_hoja) for m in self.miembros]

    def normalizar(self, x_crudo):
        return (np.asarray(x_crudo, dtype=np.float64) - self.media) / self.desv

    def similares(self, asin, k=10, misma_categoria=True):
        """Los k productos más cercanos a `asin`, sin incluirlo: lista de (distancia, asin)."""
        i = self.posicion.obtener(asin)
        if i is None:
            return None
        if misma_categoria:
            c = int(self.categoria[i])
            vecinos = [(d, int(self.miembros[c][j])) for d, j in self.kd_categoria[c].knn(self.Z[i], k + 1)]
        else:
            vecinos = self.kd.knn(self.Z[i], k + 1)
        return [(d, self.asins[j]) for d, j in vecinos if j != i][:k]
