"""
Etapa 9: Servicio y visualización del catálogo de comercio electrónico.

Implementa:
1. Trie (árbol de prefijos) con caché Top-K precalculada para autocompletado en tiempo real,
   con búsqueda difusa Damerau-Levenshtein (OSA) acotada por banda de Ukkonen.
2. Ranking Top-K multicriterio con MinHeap acotado (O(N log K)).
3. Índice invertido de títulos (postings CSR) para la búsqueda por texto.
4. Scoring híbrido con pesos adaptativos por credibilidad de la co-compra (cold start).
5. Servicio integrado de consulta, búsqueda y recomendación híbrida con las salidas de las
   etapas 3 a 8: estado de los productos (3), filtro por rango de precio con el B+Tree (4),
   arranque en frío con el KNN exacto del KD-Tree (5), alertas de fraude (6), similares y
   relacionados (7) y grafo de co-compra (8).
6. Panel de ventas y actividad con el stream de la etapa 2 y el estado de la etapa 3.
7. Exportación de métricas de servicio, consultas de demostración y dashboard.
"""

from __future__ import annotations

import heapq
import json
from array import array
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src import config
from src.etapa_3.storage import AlmacenCatalogo, stock_inicial
from src.etapa_4.bplustree import ArbolBMas
from src.etapa_5.kdtree import IndiceProductos
from src.etapa_8.graph import GrafoCoCompra, pagerank, sumar_grafos
from src.etapa_9.dashboard import generar_dashboard

# Parámetros del servicio. Se calibran con src/etapa_9/evaluacion_scoring.py (partición temporal
# 80/20 de órdenes, relacionados MinHash recalculados sin futuro) y src/etapa_9/evaluacion_real.py
# (siguiente reseña real); los resultados vigentes están en results/etapa_9/ y el paso 10 del
# notebook de la etapa 9 los discute:
# - kappa: credibilidad de la co-compra (hipótesis H1); 0 deja los pesos estáticos.
# - normalización de PageRank: 'max' deja S_pr ~ 0 salvo en los hubs; 'log' y 'percentil' la reparten.
# - pesos base 0.35/0.25/0.20/0.20: elección de diseño; se reemplazan solo si la logística no
#   negativa cumple la regla de adopción fijada antes de medir (Recall@10 y NDCG@10 +5 % relativo
#   sin perder más de 25 % de cobertura de catálogo). Decisión (2026-10-29): con S_sim corregido la
#   regla se cumple con 0/0/0.93/0.07, pero se mantienen los pesos base. El objetivo de la
#   calibración son los grupos de co-compra del generador, que no dependen de los atributos, y con
#   el objetivo real de evaluacion_real.py el híbrido con similitud supera a solo PageRank.
# - lambda_novedad: re-ranking score_λ = (1 - λ)·score + λ·(1 - S_pr) para bajar el Gini de
#   exposición, medido con el objetivo real de evaluacion_real.py.
KAPPA_COCOMPRA = 0.0              # credibilidad de la co-compra; 0 = pesos estáticos
LAMBDA_NOVEDAD = 0.3              # peso del premio a la novedad (0 = sin re-ranking)
# Fuente del grafo de S_co y S_pr: 'sintetico' (co-compras del stream, etapa 8), 'coresena'
# (reseñas reales consecutivas, src/etapa_8/grafo_resenas_reales.py) o 'ambos' (suma de pesos).
# Decisión final (2026-10-27): 'sintetico'. El enunciado define el grafo del escenario B como
# productos comprados en la misma orden, y solo esa fuente pasa por el filtro de fraude (etapa 6).
# 'coresena' y 'ambos' quedan como validación con datos reales (auditoría §7.7-7.8).
FUENTE_COCOMPRA = "sintetico"
NORMALIZACION_PAGERANK = "log"    # 'max', 'log' o 'percentil'
MAX_DIST_AUTOCOMPLETADO = 1       # distancia OSA máxima del autocompletado interactivo
MIN_DF_VOCABULARIO = 3            # df mínimo de un token para servir de corrección ortográfica
PESO_RELEVANCIA_BUSQUEDA = 0.8    # relevancia textual frente a autoridad en buscar()
STOCK_BAJO = 3                    # unidades: umbral de stock bajo del panel de ventas
RANGOS_PRECIO_EN_CACHE = 16       # rangos de precio cuyos productos se guardan entre consultas


# ============================================================================
# 1. TRIE (ÁRBOL DE PREFIJOS) PARA AUTOCOMPLETADO EN TIEMPO REAL
# ============================================================================

def normalizar_texto(texto: str) -> str:
    """Normaliza texto para indexación y búsqueda: minúsculas, sin puntuación extra."""
    if not texto:
        return ""
    texto = texto.lower().strip()
    # Reemplazar caracteres no alfanuméricos por espacios excepto guiones y barras comunes
    texto = re.sub(r"[^\w\s\-\.]", " ", texto)
    return " ".join(texto.split())


@dataclass(slots=True)
class NodoTrie:
    """Nodo del árbol de prefijos compacto (radix): la arista que entra al nodo lleva `etiqueta`."""
    hijos: dict[str, NodoTrie] = field(default_factory=dict)
    es_fin: bool = False
    termino_original: str = ""
    frecuencia: float = 0.0
    meta: dict[str, Any] | None = None
    # Caché local de las mejores sugerencias ordenadas: lista de (frecuencia, termino_original, meta)
    top_cache: list[tuple[float, str, dict[str, Any]]] = field(default_factory=list)
    etiqueta: str = ""


class Trie:
    """
    Árbol de prefijos compacto (radix / Patricia, Morrison 1968) con caché Top-K precalculada.

    Auditoría 2026-10-27: el Trie de un carácter por nodo creaba 7.2 M nodos para las 501 494
    frases del catálogo (el 90 % con un solo hijo, 3.5 GB); comprimiendo las cadenas de nodos
    unarios en una etiqueta quedan ~0.73 M nodos con la misma semántica.

    Complejidad (M = capacidad_cache, h = nodos del camino <= L):
    - Inserción de término de longitud L: O(L + h · M); cada nodo del camino revisa su caché
      de tamaño M en O(M) (inserción ordenada, sin reordenar toda la lista).
    - Consulta de prefijo de longitud L con caché: O(L + k)
    - Consulta de prefijo con k > M (recorrido del subárbol): O(L + |V_sub| log |V_sub|)
    - Consulta difusa con distancia d: O(|C_d| · d) con |C_d| los caracteres recorridos cuya fila
      de edición tiene algún valor <= d (banda de Ukkonen de ancho 2d + 1); en el peor caso
      |C_d| = O((L · Σ)^d), por eso solo se usa cuando la búsqueda exacta no llena k.
    """

    def __init__(self, capacidad_cache: int = 10):
        self.raiz = NodoTrie()
        self.capacidad_cache = capacidad_cache
        self._n_terminos = 0
        self._n_nodos = 1

    def __len__(self) -> int:
        return self._n_terminos

    @property
    def n_nodos(self) -> int:
        return self._n_nodos

    def insertar(self, termino: str, frecuencia: float = 1.0, meta: dict[str, Any] | None = None) -> None:
        """
        Inserta un término con su frecuencia y metadatos asociados.
        Actualiza la caché Top-K de cada nodo del camino; parte una arista si el término
        diverge a mitad de su etiqueta (el nodo nuevo hereda la caché del subárbol partido).
        """
        meta_dict = meta or {}
        clave = normalizar_texto(termino)
        if not clave:
            return

        nodo = self.raiz
        self._actualizar_top_cache(nodo, frecuencia, termino, meta_dict)
        i = 0
        while i < len(clave):
            hijo = nodo.hijos.get(clave[i])
            if hijo is None:
                hijo = NodoTrie(etiqueta=clave[i:])
                nodo.hijos[clave[i]] = hijo
                self._n_nodos += 1
                self._actualizar_top_cache(hijo, frecuencia, termino, meta_dict)
                nodo = hijo
                break
            etiqueta = hijo.etiqueta
            j = 0
            limite = min(len(etiqueta), len(clave) - i)
            while j < limite and etiqueta[j] == clave[i + j]:
                j += 1
            if j < len(etiqueta):
                medio = NodoTrie(etiqueta=etiqueta[:j], top_cache=list(hijo.top_cache))
                hijo.etiqueta = etiqueta[j:]
                medio.hijos[hijo.etiqueta[0]] = hijo
                nodo.hijos[clave[i]] = medio
                self._n_nodos += 1
                hijo = medio
            self._actualizar_top_cache(hijo, frecuencia, termino, meta_dict)
            nodo = hijo
            i += j

        if not nodo.es_fin:
            self._n_terminos += 1
            nodo.es_fin = True
            nodo.termino_original = termino
            nodo.frecuencia = float(frecuencia)
            nodo.meta = meta_dict
        elif float(frecuencia) >= nodo.frecuencia:
            # Auditoría 2026-10-27: antes la meta se sobrescribía aunque la frecuencia fuera menor,
            # y el término quedaba con el asin de un producto y la frecuencia de otro.
            nodo.frecuencia = float(frecuencia)
            if meta_dict:
                nodo.meta = {**(nodo.meta or {}), **meta_dict}

    def _actualizar_top_cache(
        self,
        nodo: NodoTrie,
        frecuencia: float,
        termino: str,
        meta: dict[str, Any],
    ) -> None:
        """Mantiene en el nodo los M términos de mayor frecuencia en orden descendente, en O(M).

        Un término repetido conserva su frecuencia máxima (antes se reemplazaba por la última
        insertada, aunque fuera menor, y la caché quedaba incoherente con nodo.frecuencia).
        """
        cache = nodo.top_cache
        for pos, item in enumerate(cache):
            if item[1] == termino:
                if item[0] >= frecuencia:
                    return
                del cache[pos]
                break
        else:
            if len(cache) >= self.capacidad_cache and frecuencia <= cache[-1][0]:
                return
        # Inserción ordenada estable (descendente) sin reordenar toda la lista
        pos = len(cache)
        while pos > 0 and cache[pos - 1][0] < frecuencia:
            pos -= 1
        cache.insert(pos, (frecuencia, termino, meta))
        if len(cache) > self.capacidad_cache:
            cache.pop()

    def _descender(self, clave: str) -> tuple[NodoTrie | None, bool]:
        """Nodo cuyo subárbol contiene exactamente los términos con prefijo `clave`.

        Devuelve (nodo, termina_en_nodo); termina_en_nodo es False si la clave acaba a mitad
        de la etiqueta de la arista que entra al nodo.
        """
        nodo = self.raiz
        i = 0
        while i < len(clave):
            hijo = nodo.hijos.get(clave[i])
            if hijo is None:
                return None, False
            etiqueta = hijo.etiqueta
            resto = len(clave) - i
            if resto < len(etiqueta):
                return (hijo, False) if etiqueta.startswith(clave[i:]) else (None, False)
            if not clave.startswith(etiqueta, i):
                return None, False
            nodo = hijo
            i += len(etiqueta)
        return nodo, True

    def buscar_exacto(self, termino: str) -> bool:
        """Verifica si el término exacto existe y es fin de palabra en el Trie."""
        clave = normalizar_texto(termino)
        if not clave:
            return False
        nodo, termina_en_nodo = self._descender(clave)
        return nodo is not None and termina_en_nodo and nodo.es_fin

    def autocompletar(
        self, prefijo: str, k: int = 5, difuso: bool = False, max_dist: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Devuelve hasta k sugerencias para el prefijo dado.
        Usa la caché Top-K del nodo en O(|prefijo|). Si k > capacidad_cache,
        recorre el subárbol. Con difuso=True, si la coincidencia exacta no llena k
        sugerencias, completa con autocompletar_difuso (las exactas van primero).
        """
        exactas = self._autocompletar_exacto(prefijo, k)
        if not difuso or len(exactas) >= k:
            return exactas
        vistos = {s["termino"] for s in exactas}
        for s in self.autocompletar_difuso(prefijo, k=k + len(exactas), max_dist=max_dist):
            if s["termino"] not in vistos:
                exactas.append(s)
                vistos.add(s["termino"])
                if len(exactas) >= k:
                    break
        return exactas

    def autocompletar_difuso(
        self,
        prefijo: str,
        k: int = 5,
        max_dist: int | None = None,
        prefijo_fijo: int = 1,
        solo_completos: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Sugerencias cuyo prefijo está a distancia Damerau-Levenshtein (OSA) <= max_dist.

        Recorre el Trie en profundidad llevando la fila de programación dinámica de la consulta,
        un carácter de etiqueta por fila; solo calcula la banda |i - j| <= d (Ukkonen, 1985).
        Podas exactas:
        - toda la banda supera d: ningún descendiente puede volver a <= d (las filas no bajan
          de su mínimo; la transposición tampoco, porque D[j][i] <= D[j-1][i-1] + 1);
        - con k <= M, si el mínimo de la fila ya no es menor que la distancia registrada en el
          camino, los descendientes no pueden mejorar: sus términos están en la caché ya tomada
          con distancia igual o menor y frecuencia mayor.
        Cada nodo coincidente aporta su caché Top-K, así que no se recorren subárboles.
        - max_dist None: profundización iterativa d = 1, 2 hasta llenar k, con techo 0 si L <= 2,
          1 si L <= 5 y 2 en otro caso (criterio AUTO de Lucene).
        - prefijo_fijo: primeros caracteres que deben coincidir exactamente; divide la búsqueda
          por ~|Σ| y es la hipótesis estándar (los errores de tipeo rara vez están en la 1.ª letra).
        - solo_completos: compara palabras completas (corrección ortográfica) en vez de prefijos.
        Orden: (distancia ascendente, frecuencia descendente).
        """
        clave = normalizar_texto(prefijo)
        L = len(clave)
        if L == 0:
            return []
        if max_dist is not None:
            distancias = [max_dist]
        else:
            techo = 0 if L <= 2 else 1 if L <= 5 else 2
            distancias = list(range(1, techo + 1)) or [0]
        resultado: list[dict[str, Any]] = []
        for d in distancias:
            resultado = self._difuso(clave, k, d, min(prefijo_fijo, L), solo_completos)
            if len(resultado) >= k:
                break
        return resultado

    def _difuso(self, clave: str, k: int, d: int, fijo: int, solo_completos: bool) -> list[dict[str, Any]]:
        n = len(clave)
        inf = d + 1
        mejor: dict[str, tuple[int, float, dict[str, Any]]] = {}
        podar_por_mejora = not solo_completos and k <= self.capacidad_cache

        def registrar(nodo_match: NodoTrie, dist: int) -> None:
            if solo_completos:
                previo = mejor.get(nodo_match.termino_original)
                if previo is None or dist < previo[0]:
                    mejor[nodo_match.termino_original] = (dist, nodo_match.frecuencia, nodo_match.meta or {})
                return
            for freq, term, meta in nodo_match.top_cache:
                previo = mejor.get(term)
                if previo is None or dist < previo[0]:
                    mejor[term] = (dist, freq, meta)

        fila_raiz = list(range(n + 1))
        if fijo == 0 and n <= d and not solo_completos:
            registrar(self.raiz, n)
        mejor_raiz = n if (fijo == 0 and n <= d) else inf

        # Pila de (nodo, fila previa, fila anterior a la previa, carácter previo, profundidad, mejor del camino)
        pila = [(hijo, fila_raiz, None, "", 0, mejor_raiz) for hijo in self.raiz.hijos.values()]
        while pila:
            actual, previa, ante, c_prev, j, mejor_camino = pila.pop()
            dist_nodo = inf
            podado = False
            for c in actual.etiqueta:
                j += 1
                if j <= fijo and c != clave[j - 1]:
                    podado = True
                    break
                fila = [inf] * (n + 1)
                if j <= d:
                    fila[0] = j
                minimo = fila[0]
                for i in range(max(1, j - d), min(n, j + d) + 1):
                    v = previa[i - 1] + (0 if clave[i - 1] == c else 1)
                    t = previa[i] + 1
                    if t < v:
                        v = t
                    t = fila[i - 1] + 1
                    if t < v:
                        v = t
                    if ante is not None and i > 1 and clave[i - 1] == c_prev and clave[i - 2] == c:
                        t = ante[i - 2] + 1
                        if t < v:
                            v = t
                    if v > inf:
                        v = inf
                    fila[i] = v
                    if v < minimo:
                        minimo = v
                if minimo > d:
                    podado = True
                    break
                if fila[n] < dist_nodo and j > fijo - 1:
                    dist_nodo = fila[n]
                ante, previa, c_prev = previa, fila, c
            if podado:
                # la coincidencia hallada antes de la poda dentro de la misma etiqueta sigue valiendo
                if not solo_completos and dist_nodo <= d:
                    registrar(actual, dist_nodo)
                continue
            if solo_completos:
                if actual.es_fin and previa[n] <= d:
                    registrar(actual, previa[n])
            elif dist_nodo <= d:
                registrar(actual, dist_nodo)
            mejor_camino = min(mejor_camino, dist_nodo)
            if podar_por_mejora and min(previa) >= mejor_camino:
                continue
            for hijo in actual.hijos.values():
                pila.append((hijo, previa, ante, c_prev, j, mejor_camino))

        orden = sorted(mejor.items(), key=lambda x: (x[1][0], -x[1][1], x[0]))
        return [
            {"termino": term, "frecuencia": freq, "meta": meta, "distancia": dist}
            for term, (dist, freq, meta) in orden[:k]
        ]

    def _autocompletar_exacto(self, prefijo: str, k: int = 5) -> list[dict[str, Any]]:
        """Coincidencia exacta de prefijo: O(L + k) con la caché, recorrido si k > M."""
        clave = normalizar_texto(prefijo)
        nodo, _ = self._descender(clave)
        if nodo is None:
            return []

        # Si el caché del nodo tiene suficientes sugerencias, devolver directamente
        if len(nodo.top_cache) >= k or not nodo.hijos:
            return [
                {"termino": term, "frecuencia": freq, "meta": meta}
                for freq, term, meta in nodo.top_cache[:k]
            ]

        # Búsqueda completa si se solicita un k mayor al caché
        candidatos = []
        pila = [nodo]
        while pila:
            actual = pila.pop()
            if actual.es_fin:
                candidatos.append((actual.frecuencia, actual.termino_original, actual.meta or {}))
            for h in actual.hijos.values():
                pila.append(h)

        # Ordenar y tomar los top k
        candidatos.sort(key=lambda x: x[0], reverse=True)
        return [
            {"termino": term, "frecuencia": freq, "meta": meta}
            for freq, term, meta in candidatos[:k]
        ]


# ============================================================================
# 2. TOP-K RANKING MULTICRITERIO CON MINHEAP ACOTADO
# ============================================================================

class TopKHeap:
    """
    Estructura para retener los K elementos de mayor puntuación en O(N log K).
    Utiliza un MinHeap de tamaño a lo sumo K donde la raíz es el elemento con
    la menor puntuación de los K mejores.
    """

    def __init__(self, k: int):
        if k <= 0:
            raise ValueError("K debe ser mayor que cero")
        self.k = k
        self._heap: list[tuple[float, int, Any]] = []
        self._contador = 0  # Desempate determinista sin comparar objetos

    def __len__(self) -> int:
        return len(self._heap)

    def agregar(self, item: Any, score: float) -> None:
        """
        Inserta un elemento con su score.
        Si el heap tiene K elementos y score <= raíz, se descarta en O(1).
        Si score > raíz, se reemplaza en O(log K).
        """
        self._contador += 1
        entrada = (float(score), self._contador, item)

        if len(self._heap) < self.k:
            heapq.heappush(self._heap, entrada)
        else:
            # La raíz tiene el mínimo score del Top-K actual
            if score > self._heap[0][0]:
                heapq.heappushpop(self._heap, entrada)

    def agregar_todos(self, items: Iterable[tuple[Any, float]]) -> None:
        """Agrega múltiples pares (item, score)."""
        for item, score in items:
            self.agregar(item, score)

    def obtener_top(self) -> list[tuple[float, Any]]:
        """
        Devuelve los K elementos ordenados de mayor a menor puntuación.
        Complejidad: O(K log K).
        """
        # Extraer en orden ascendente y luego invertir
        copia = list(self._heap)
        copia.sort(key=lambda x: x[0], reverse=True)
        return [(score, item) for score, _, item in copia]


# ============================================================================
# 3. FUNCIÓN DE SCORING HÍBRIDO MULTICRITERIO
# ============================================================================

def similitud_desde_distancia(distancia: float) -> float:
    """S_sim en [0, 1] a partir de la distancia de HNSW (etapa 7).

    HNSW devuelve la distancia euclidiana entre vectores de norma 1, d = sqrt(2 - 2·cos), en [0, 2],
    así que cos = 1 - d² / 2. S_sim es ese coseno, con los valores negativos llevados a 0.
    Revisión técnica 2026-10-28 (punto 1.5): antes se usaba 1 - d, que vale 0 para todo coseno <= 0.5.
    """
    d = float(distancia)
    return max(0.0, 1.0 - d * d / 2.0)


@dataclass
class ScoringHibrido:
    """
    Ponderación multicriterio para el ranking unificado de recomendación y búsqueda.
    Score = w_sim * S_sim + w_rel * S_rel + w_pr * S_pr + w_co * S_co

    Pesos adaptativos (auditoría 2026-10-27), en pesos_efectivos:
    1. Credibilidad de la co-compra (estimador de Bühlmann / shrinkage bayesiano): con n_q las
       co-compras observadas del ancla, w_co(q) = w_co · n_q / (n_q + kappa). Con n_q = 0
       (cold start) w_co = 0; con n_q >> kappa vuelve al peso base. La masa retirada se reparte
       entre sim y rel en proporción a sus pesos base.
    2. Disponibilidad: una señal que no existe para el ancla (sin vecinos HNSW, sin firma MinHash,
       fuera del grafo) se anula y los pesos se renormalizan a suma 1.
    Nota: para un ancla fija, anular una señal que vale 0 en todos los candidatos y renormalizar
    es una transformación monótona del score, así que no cambia el orden; lo que sí lo cambia es
    la fracción n_q / (n_q + kappa) en anclas con pocas co-compras, donde S_co = w / w_max vale
    1.0 con una sola co-compra.
    """
    peso_similitud: float = 0.35      # HNSW atributos
    peso_relacionados: float = 0.25   # MinHash + LSH Jaccard comportamiento
    peso_pagerank: float = 0.20       # Centralidad espectral de co-compra
    peso_cocompra: float = 0.20       # Peso directo de aristas en el grafo CSR
    kappa_cocompra: float = 0.0       # 0 desactiva el shrinkage (pesos estáticos originales)
    lambda_novedad: float = 0.0       # 0 desactiva el re-ranking por novedad

    def __post_init__(self):
        total = self.peso_similitud + self.peso_relacionados + self.peso_pagerank + self.peso_cocompra
        if abs(total - 1.0) > 1e-4:
            # Auto-normalizar si difiere ligeramente
            self.peso_similitud /= total
            self.peso_relacionados /= total
            self.peso_pagerank /= total
            self.peso_cocompra /= total

    def pesos_efectivos(
        self,
        n_cocompras: float | None = None,
        hay_similitud: bool = True,
        hay_relacionados: bool = True,
        hay_cocompra: bool = True,
    ) -> tuple[float, float, float, float]:
        """Devuelve (w_sim, w_rel, w_pr, w_co) para un ancla, normalizados a suma 1."""
        w_sim = self.peso_similitud if hay_similitud else 0.0
        w_rel = self.peso_relacionados if hay_relacionados else 0.0
        w_pr = self.peso_pagerank
        w_co = self.peso_cocompra if hay_cocompra else 0.0

        if w_co > 0 and self.kappa_cocompra > 0 and n_cocompras is not None:
            credibilidad = n_cocompras / (n_cocompras + self.kappa_cocompra)
            retirado = w_co * (1.0 - credibilidad)
            w_co -= retirado
            receptores = w_sim + w_rel
            if receptores > 0:
                w_sim += retirado * w_sim / receptores
                w_rel += retirado * w_rel / receptores

        total = w_sim + w_rel + w_pr + w_co
        if total <= 0:
            return 0.0, 0.0, 1.0, 0.0
        return w_sim / total, w_rel / total, w_pr / total, w_co / total

    def calcular_score(
        self,
        similitud_atributos: float = 0.0,
        jaccard_comportamiento: float = 0.0,
        pagerank_normalizado: float = 0.0,
        afinidad_cocompra: float = 0.0,
        pesos: tuple[float, float, float, float] | None = None,
    ) -> float:
        """Calcula el score híbrido normalizado en [0, 1] (pesos base o los efectivos dados)."""
        w_sim, w_rel, w_pr, w_co = pesos or (
            self.peso_similitud, self.peso_relacionados, self.peso_pagerank, self.peso_cocompra
        )
        s = (
            w_sim * min(max(similitud_atributos, 0.0), 1.0)
            + w_rel * min(max(jaccard_comportamiento, 0.0), 1.0)
            + w_pr * min(max(pagerank_normalizado, 0.0), 1.0)
            + w_co * min(max(afinidad_cocompra, 0.0), 1.0)
        )
        return float(min(max(s, 0.0), 1.0))


# ============================================================================
# 3b. ÍNDICE INVERTIDO DE TÍTULOS PARA LA BÚSQUEDA POR TEXTO
# ============================================================================

def tokenizar(texto: str) -> list[str]:
    """Tokens de búsqueda: texto normalizado, sin puntuación de borde, longitud >= 2."""
    return [t for t in (w.strip("-.") for w in normalizar_texto(texto).split()) if len(t) >= 2]


class IndiceInvertido:
    """
    Índice invertido token -> documentos, con postings contiguos en formato CSR (NumPy).

    - Construcción: O(T log T) con T = total de pares (token, documento) (un argsort).
    - Memoria: 4 bytes por posting + 8 por token (indptr) + el diccionario del vocabulario.
    - Consulta de q tokens: O(sum_t df_t) con np.bincount sobre los postings concatenados;
      el score de cada documento es la fracción de IDF de la consulta que cubre (en [0, 1]).
    El Trie resuelve prefijos (autocompletado); la búsqueda por varias palabras en cualquier
    posición del título necesita este índice: con el Trie, "toner brother" solo podía casar
    títulos que empezaran literalmente así.
    """

    def __init__(self):
        self.vocabulario: dict[str, int] = {}
        self.indptr = np.zeros(1, dtype=np.int64)
        self.postings = np.empty(0, dtype=np.int32)
        self.idf = np.empty(0, dtype=np.float64)
        self.n_docs = 0

    def construir(self, documentos: Iterable[tuple[int, str]]) -> None:
        tokens_ids = array("i")
        docs_ids = array("i")
        vocab = self.vocabulario
        n_docs = 0
        for doc_id, texto in documentos:
            n_docs = max(n_docs, doc_id + 1)
            for tok in set(tokenizar(texto)):
                tid = vocab.get(tok)
                if tid is None:
                    tid = len(vocab)
                    vocab[tok] = tid
                tokens_ids.append(tid)
                docs_ids.append(doc_id)
        t = np.frombuffer(tokens_ids, dtype=np.int32)
        dcs = np.frombuffer(docs_ids, dtype=np.int32)
        orden = np.argsort(t, kind="stable")
        self.postings = dcs[orden]
        df = np.bincount(t, minlength=len(vocab))
        self.indptr = np.concatenate(([0], np.cumsum(df))).astype(np.int64)
        self.n_docs = n_docs
        self.idf = np.log((n_docs + 1) / (df + 0.5))

    def df(self, token: str) -> int:
        tid = self.vocabulario.get(token)
        return 0 if tid is None else int(self.indptr[tid + 1] - self.indptr[tid])

    def puntuar(self, tokens: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """Devuelve (doc_ids candidatos, cobertura IDF en [0, 1]) para los tokens dados."""
        tids = [self.vocabulario[t] for t in dict.fromkeys(tokens) if t in self.vocabulario]
        if not tids:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float64)
        total_idf = float(sum(self.idf[t] for t in tids))
        partes = [self.postings[self.indptr[t]:self.indptr[t + 1]] for t in tids]
        pesos = [np.full(len(p), self.idf[t]) for p, t in zip(partes, tids)]
        docs = np.concatenate(partes)
        acumulado = np.bincount(docs, weights=np.concatenate(pesos), minlength=self.n_docs)
        candidatos = np.nonzero(acumulado)[0].astype(np.int32)
        return candidatos, acumulado[candidatos] / total_idf


# ============================================================================
# 4. SERVICIO INTEGRADO DE CONSULTA Y VISUALIZACIÓN
# ============================================================================

class ServicioEcommerce:
    """
    Fachada de servicio integral que orquesta y consulta los índices y modelos de las
    etapas 3 a 8: el catálogo y el estado de la etapa 3, el B+Tree en disco de la etapa 4,
    el KD-Tree de la etapa 5, las alertas de la etapa 6, los vecinos de la etapa 7 y el
    grafo de la etapa 8.
    """

    def __init__(
        self,
        dir_interim: Path,
        categoria: str = "Office_Products",
        scoring: ScoringHibrido | None = None,
        normalizacion_pagerank: str = NORMALIZACION_PAGERANK,
        fuente_cocompra: str = FUENTE_COCOMPRA,
    ):
        self.dir_interim = dir_interim / categoria
        self.categoria = categoria

        # Módulos y artefactos
        self.almacen_catalogo: AlmacenCatalogo | None = None
        self.grafo_cocompra: GrafoCoCompra | None = None
        self.trie = Trie(capacidad_cache=10)
        self.scoring = scoring or ScoringHibrido(kappa_cocompra=KAPPA_COCOMPRA, lambda_novedad=LAMBDA_NOVEDAD)
        self.normalizacion_pagerank = normalizacion_pagerank
        self.fuente_cocompra = fuente_cocompra

        # Búsqueda por texto: índice invertido de títulos y Trie de vocabulario para corregir tokens
        self.indice = IndiceInvertido()
        self.trie_vocabulario = Trie(capacidad_cache=1)
        self._asins_docs: list[str] = []
        self._autoridad_docs = np.empty(0, dtype=np.float64)

        # Diccionarios de consulta rápida en memoria
        self._pagerank: dict[str, float] = {}
        self._pagerank_max: float = 1.0
        self._autoridad_pre: dict[str, float] = {}
        self._grados_pond: dict[str, float] = {}
        self._comunidades_louvain: dict[str, int] = {}
        self._comunidades_lpa: dict[str, int] = {}
        self._similares: dict[str, list[tuple[str, float]]] = {}
        self._relacionados: dict[str, list[tuple[str, float]]] = {}
        self._alertas_resumen: dict[str, Any] = {}
        self._alertas_muestra: list[dict[str, Any]] = []

        # Índices de las etapas 3, 4 y 5
        self._estado: dict[str, tuple[int, bool]] = {}          # etapa 3: stock y disponibilidad
        self.indice_precio: ArbolBMas | None = None             # etapa 4: filtro por rango de precio
        self.indice_atributos: IndiceProductos | None = None    # etapa 5: arranque en frío
        self._rangos_precio: dict[tuple[float, float], set[str]] = {}

    def cargar_datos(
        self, limite_terminos_trie: int | None = None, indexar_texto: bool = True, indices_auxiliares: bool = True
    ) -> None:
        """Carga en memoria todos los artefactos de las etapas previas.

        limite_terminos_trie None indexa el catálogo completo (auditoría 2026-10-27: el límite
        anterior de 50 000 inserciones cubría solo los primeros ~3 % de los 710 503 productos).
        indices_auxiliares carga el estado de la etapa 3 y los índices de las etapas 4 y 5; la
        calibración del scoring no los usa.
        """
        t0 = time.perf_counter()

        # 1. Catálogo (Etapa 3)
        ruta_cat = self.dir_interim / "etapa_3" / "catalogo.jsonl"
        if ruta_cat.exists():
            self.almacen_catalogo = AlmacenCatalogo(ruta_cat)

        # 1b. Estado de los productos (Etapa 3), índice de precios (Etapa 4) y de atributos (Etapa 5)
        if indices_auxiliares:
            self._cargar_indices_auxiliares()

        # 2. PageRank y Comunidades (Etapa 8)
        ruta_pr = self.dir_interim / "etapa_8" / "pagerank.parquet"
        if ruta_pr.exists():
            df_pr = pd.read_parquet(ruta_pr)
            pr_col = "pagerank_score" if "pagerank_score" in df_pr.columns else "pagerank"
            self._pagerank = dict(zip(df_pr["parent_asin"], df_pr[pr_col]))
            self._grados_pond = dict(zip(df_pr["parent_asin"], df_pr["grado_ponderado"]))
            self._pagerank_max = max(self._pagerank.values()) if self._pagerank else 1.0
            self._preparar_autoridad(df_pr[pr_col].to_numpy(), df_pr["parent_asin"].tolist())

        ruta_com = self.dir_interim / "etapa_8" / "comunidades.parquet"
        if ruta_com.exists():
            df_com = pd.read_parquet(ruta_com)
            self._comunidades_louvain = dict(zip(df_com["parent_asin"], df_com["comunidad_louvain"]))
            self._comunidades_lpa = dict(zip(df_com["parent_asin"], df_com["comunidad_lpa"]))

        # 3. Grafo de co-compra (Etapa 8)
        ruta_grafo = self.dir_interim / "etapa_8" / "grafo_cocompra.npz"
        if ruta_grafo.exists():
            self.grafo_cocompra = GrafoCoCompra.cargar_npz(ruta_grafo)
        self._usar_fuente_cocompra()

        # 4. Similares y Relacionados (Etapa 7)
        ruta_sim = self.dir_interim / "etapa_7" / "similares.parquet"
        if ruta_sim.exists():
            df_sim = pd.read_parquet(ruta_sim)
            # Agrupar por parent_asin
            for asin, grupo in df_sim.groupby("parent_asin"):
                self._similares[str(asin)] = list(zip(grupo["vecino"], grupo["distancia"]))

        ruta_rel = self.dir_interim / "etapa_7" / "relacionados.parquet"
        if ruta_rel.exists():
            df_rel = pd.read_parquet(ruta_rel)
            for asin, grupo in df_rel.groupby("parent_asin"):
                self._relacionados[str(asin)] = list(zip(grupo["vecino"], grupo["jaccard"]))

        # 5. Alertas de Fraude (Etapa 6)
        ruta_alertas = self.dir_interim / "etapa_6" / "alertas.jsonl"
        if ruta_alertas.exists():
            self._cargar_resumen_alertas(ruta_alertas)

        # 6. Indexación en Trie de autocompletado e índice invertido de búsqueda
        if indexar_texto:
            self._indexar_trie_desde_catalogo(limite_terminos_trie)

        print(f"ServicioEcommerce inicializado en {time.perf_counter() - t0:.2f} s")

    def _cargar_indices_auxiliares(self) -> None:
        """Estado de la etapa 3, B+Tree de precios de la etapa 4 y KD-Tree de atributos de la etapa 5.

        El KD-Tree se reconstruye con los vectores de vectores.npz, que cubren todos los productos
        con precio, también los que no aparecen en el stream; por eso sirve para el arranque en frío.
        """
        ruta_estados = self.dir_interim / "etapa_3" / "estado_productos.parquet"
        if ruta_estados.exists():
            est = pd.read_parquet(ruta_estados, columns=["parent_asin", "stock", "disponible"])
            self._estado = {a: (int(s), bool(d)) for a, s, d in zip(est["parent_asin"], est["stock"], est["disponible"])}
        ruta_bpt = self.dir_interim / "etapa_4" / "indice_precio.bpt"
        if ruta_bpt.exists():
            self.indice_precio = ArbolBMas(ruta_bpt)
        ruta_vec = self.dir_interim / "etapa_5" / "vectores.npz"
        if ruta_vec.exists():
            z = np.load(ruta_vec)
            categorias = [str(c) for c in z["nombres_categoria"][z["categoria"]]]
            self.indice_atributos = IndiceProductos([str(a) for a in z["asins"]], z["Z"] * z["desv"] + z["media"],
                                                    categorias)

    def _cargar_resumen_alertas(self, ruta_alertas: Path) -> None:
        """Lee y sintetiza las alertas de fraude de la etapa 6."""
        total = 0
        bloqueadas = 0
        en_revision = 0
        aprobadas = 0
        motivos_conteo: dict[str, int] = {}
        muestra = []
        bloqueos_fraude = 0

        with open(ruta_alertas, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                total += 1
                reg = json.loads(line)
                dec = reg.get("decision")
                if dec == "bloquear":
                    bloqueadas += 1
                elif dec in ("revisar", "retener"):
                    # la etapa 6 emite "revisar"; antes solo se contaba "retener" y daba 0
                    en_revision += 1
                else:
                    aprobadas += 1

                for m in reg.get("motivos", []):
                    motivos_conteo[m] = motivos_conteo.get(m, 0) + 1

                if dec == "bloquear" and reg.get("fraude_real"):
                    bloqueos_fraude += 1

                if len(muestra) < 20:
                    muestra.append({
                        "event_id": reg.get("event_id"),
                        "order_id": reg.get("order_id"),
                        "user_id": reg.get("user_id"),
                        "decision": dec,
                        "motivos": reg.get("motivos", []),
                        "fraude_real": reg.get("fraude_real"),
                        "estimado_cms": reg.get("estimado_cms"),
                    })

        self._alertas_resumen = {
            "total_alertas": total,
            "bloqueadas": bloqueadas,
            "en_revision": en_revision,
            "aprobadas": aprobadas,
            # Auditoría 2026-10-27: antes era la constante 1.0; ahora se mide con fraude_real
            "precision_bloqueos": bloqueos_fraude / bloqueadas if bloqueadas else None,
            "motivos": motivos_conteo,
        }
        self._alertas_muestra = muestra

    def _usar_fuente_cocompra(self) -> None:
        """Reemplaza el grafo de co-compra y su PageRank según fuente_cocompra."""
        if self.fuente_cocompra == "sintetico":
            return
        ruta = self.dir_interim / "etapa_8" / "grafo_resenas.npz"
        if not ruta.exists():
            raise FileNotFoundError(f"{ruta} no existe: correr src.etapa_8.grafo_resenas_reales")
        resenas = GrafoCoCompra.cargar_npz(ruta)
        if self.fuente_cocompra == "ambos" and self.grafo_cocompra is not None:
            self.grafo_cocompra = sumar_grafos(resenas, self.grafo_cocompra)
        else:
            self.grafo_cocompra = resenas
        pr, _, _ = pagerank(self.grafo_cocompra)
        self._pagerank = dict(zip(self.grafo_cocompra.asins, pr.tolist()))
        self._pagerank_max = float(pr.max())
        self._grados_pond = dict(zip(self.grafo_cocompra.asins, self.grafo_cocompra.grados_ponderados().tolist()))
        self._preparar_autoridad(pr, self.grafo_cocompra.asins)

    def _preparar_autoridad(self, scores: np.ndarray, asins: list[str]) -> None:
        """Precalcula S_pr según normalizacion_pagerank ('max', 'log' o 'percentil').

        Con 'max' la mediana de pr / pr_max es 0.0084 (cola pesada): el término w_pr · S_pr
        aporta ~0 salvo en los hubs. 'log' y 'percentil' reparten S_pr en [0, 1].
        """
        scores = np.asarray(scores, dtype=np.float64)
        if len(scores) == 0:
            return
        if self.normalizacion_pagerank == "log":
            log_s = np.log(np.maximum(scores, 1e-300))
            rango = log_s.max() - log_s.min()
            norm = (log_s - log_s.min()) / rango if rango > 0 else np.ones_like(log_s)
        elif self.normalizacion_pagerank == "percentil":
            norm = (np.argsort(np.argsort(scores)) + 1) / len(scores)
        else:
            norm = scores / scores.max()
        self._autoridad_pre = dict(zip(asins, norm.tolist()))

    def _autoridad(self, asin: str) -> float:
        """S_pr del producto en [0, 1]; 0 si no está en el grafo de co-compra."""
        if self._autoridad_pre:
            return self._autoridad_pre.get(asin, 0.0)
        return self._pagerank.get(asin, 0.0) / self._pagerank_max

    def _indexar_trie_desde_catalogo(self, limite: int | None) -> None:
        """Puebla el Trie (marcas, categorías, títulos) y el índice invertido de títulos."""
        if not self.almacen_catalogo:
            return

        ruta_cat = self.dir_interim / "etapa_3" / "catalogo.jsonl"
        insertados = 0

        # Primero indexar marcas y categorías (máxima prioridad)
        marcas_vistas: set[str] = set()
        categorias_vistas: set[str] = set()
        documentos: list[str] = []

        with open(ruta_cat, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                prod = json.loads(line)
                asin = prod.get("parent_asin", "")
                pr_score = self._pagerank.get(asin, 0.00001)

                store = prod.get("store")
                if store and store not in marcas_vistas:
                    marcas_vistas.add(store)
                    self.trie.insertar(store, frecuencia=500.0, meta={"tipo": "marca", "asin": asin})
                    insertados += 1

                cat_hoja = prod.get("categoria_hoja")
                if cat_hoja and cat_hoja not in categorias_vistas:
                    categorias_vistas.add(cat_hoja)
                    self.trie.insertar(cat_hoja, frecuencia=300.0, meta={"tipo": "categoria", "asin": asin})
                    insertados += 1

                title = prod.get("title")
                if title:
                    # Indexar título y frases cortas (primeras 4 palabras)
                    palabras = title.split()
                    frase_corta = " ".join(palabras[:4])
                    peso = 100.0 * (pr_score / self._pagerank_max) + 1.0
                    self.trie.insertar(frase_corta, frecuencia=peso, meta={"tipo": "producto", "asin": asin, "titulo": title})
                    insertados += 1
                    self._asins_docs.append(asin)
                    documentos.append(f"{title} {store or ''} {cat_hoja or ''}")

                if limite is not None and insertados >= limite:
                    break

        self.indice.construir(enumerate(documentos))
        self._autoridad_docs = np.array([self._autoridad(a) for a in self._asins_docs], dtype=np.float64)

        # Vocabulario para corrección ortográfica: tokens alfabéticos con df >= MIN_DF_VOCABULARIO
        df = np.diff(self.indice.indptr)
        for tok, tid in self.indice.vocabulario.items():
            if df[tid] >= MIN_DF_VOCABULARIO and tok.isalpha():
                self.trie_vocabulario.insertar(tok, frecuencia=float(df[tid]))

    # ------------------------------------------------------------------------
    # MÉTODOS DE CONSULTA Y SERVICIO
    # ------------------------------------------------------------------------

    def autocompletar(self, prefijo: str, k: int = 5, difuso: bool = True) -> list[dict[str, Any]]:
        """Devuelve hasta k sugerencias: exactas en O(L) y, si faltan, difusas.

        La distancia del autocompletado interactivo se limita a MAX_DIST_AUTOCOMPLETADO = 1:
        con d = 1 la p50 medida es 0.6-0.9 ms sobre el catálogo completo; con d = 2 una consulta
        larga sin coincidencias cuesta ~5 ms. d = 2 se reserva para corregir palabras sueltas.
        """
        if len(normalizar_texto(prefijo)) < 3:
            difuso = False
        return self.trie.autocompletar(prefijo, k=k, difuso=difuso, max_dist=MAX_DIST_AUTOCOMPLETADO)

    def detalle_producto(self, parent_asin: str) -> dict[str, Any] | None:
        """Obtiene la ficha técnica y atributos de un producto."""
        if not self.almacen_catalogo:
            return None
        reg = self.almacen_catalogo.leer(parent_asin)
        if not reg:
            return None

        # Enriquecer con métricas de grafo (etapa 8) y con el stock de la etapa 3
        reg["pagerank"] = self._pagerank.get(parent_asin, 0.0)
        reg["grado_ponderado"] = self._grados_pond.get(parent_asin, 0.0)
        reg["comunidad_louvain"] = self._comunidades_louvain.get(parent_asin, -1)
        reg["comunidad_lpa"] = self._comunidades_lpa.get(parent_asin, -1)
        reg["stock"], reg["disponible"] = self._stock(parent_asin)
        return reg

    def _stock(self, asin: str) -> tuple[int | None, bool | None]:
        """Stock y disponibilidad según la etapa 3; un producto sin actividad conserva su stock inicial."""
        if not self._estado:
            return None, None
        return self._estado.get(asin) or (stock_inicial(asin), True)

    def productos_en_rango_precio(self, precio_min: float | None, precio_max: float | None) -> set[str]:
        """Productos con precio en [precio_min, precio_max] según el B+Tree de la etapa 4.

        La consulta de rango cuesta O(log_B n + r / B) lecturas de página para r resultados. Los
        últimos RANGOS_PRECIO_EN_CACHE rangos se guardan para no recorrer las hojas en cada consulta.
        Los productos sin precio en el catálogo no están en el índice, así que nunca pasan el filtro.
        """
        if self.indice_precio is None:
            raise RuntimeError("Falta el índice de precios de la etapa 4 (etapa_4/indice_precio.bpt)")
        clave = (float(precio_min or 0.0), float("inf") if precio_max is None else float(precio_max))
        if clave not in self._rangos_precio:
            if len(self._rangos_precio) >= RANGOS_PRECIO_EN_CACHE:
                self._rangos_precio.pop(next(iter(self._rangos_precio)))
            self._rangos_precio[clave] = {asin for _, asin, _, _ in self.indice_precio.rango(*clave)}
        return self._rangos_precio[clave]

    def ancla_arranque_frio(self) -> str | None:
        """Ejemplo de arranque en frío: el producto sin ninguna señal de comportamiento (fuera del stream)
        con más calificaciones en el catálogo, según el índice de atributos de la etapa 5."""
        indice = self.indice_atributos
        if indice is None:
            return None
        activos = set(self._similares) | set(self._relacionados) | set(self._pagerank)
        candidatos = [i for i, a in enumerate(indice.asins) if a not in activos]
        if not candidatos:
            return None
        columna = 2                       # log_rating_number en ATRIBUTOS de la etapa 5
        return indice.asins[max(candidatos, key=lambda i: indice.Z[i, columna])]

    def puntuar_candidatos(self, parent_asin: str) -> dict[str, tuple[float, dict[str, float]]]:
        """
        Genera los candidatos del ancla (HNSW, MinHash/LSH, vecinos de co-compra) y su score
        híbrido con los pesos efectivos del ancla. Devuelve {asin: (score, fuentes)}.
        """
        candidatos_scores: dict[str, dict[str, float]] = {}

        # 1. Similares por atributos (HNSW Etapa 7); distancia euclidiana entre vectores de norma 1
        similares = self._similares.get(parent_asin, [])
        for vecino, dist in similares:
            if vecino == parent_asin:
                continue
            sim = similitud_desde_distancia(dist)
            if vecino not in candidatos_scores:
                candidatos_scores[vecino] = {"sim": 0.0, "rel": 0.0, "co": 0.0}
            candidatos_scores[vecino]["sim"] = sim

        # 2. Relacionados por comportamiento (MinHash + LSH Etapa 7)
        relacionados = self._relacionados.get(parent_asin, [])
        for vecino, jacc in relacionados:
            if vecino == parent_asin:
                continue
            if vecino not in candidatos_scores:
                candidatos_scores[vecino] = {"sim": 0.0, "rel": 0.0, "co": 0.0}
            candidatos_scores[vecino]["rel"] = float(jacc)

        # 3. Co-compra directa en el grafo CSR (Etapa 8)
        n_cocompras = 0.0
        if self.grafo_cocompra and self.grafo_cocompra.posicion:
            idx = self.grafo_cocompra.posicion.obtener(parent_asin)
            if idx is not None:
                vecinos_idx, pesos = self.grafo_cocompra.vecinos(idx)
                n_cocompras = float(pesos.sum())
                if len(pesos) > 0:
                    max_peso = float(np.max(pesos)) if np.max(pesos) > 0 else 1.0
                    for v_idx, w in zip(vecinos_idx, pesos):
                        v_asin = self.grafo_cocompra.asins[v_idx]
                        if v_asin == parent_asin:
                            continue
                        if v_asin not in candidatos_scores:
                            candidatos_scores[v_asin] = {"sim": 0.0, "rel": 0.0, "co": 0.0}
                        candidatos_scores[v_asin]["co"] = float(w) / max_peso

        pesos_ancla = self.scoring.pesos_efectivos(
            n_cocompras=n_cocompras,
            hay_similitud=bool(similares),
            hay_relacionados=bool(relacionados),
            hay_cocompra=n_cocompras > 0,
        )
        resultado = {}
        for cand_asin, fuentes in candidatos_scores.items():
            score = self.scoring.calcular_score(
                similitud_atributos=fuentes["sim"],
                jaccard_comportamiento=fuentes["rel"],
                pagerank_normalizado=self._autoridad(cand_asin),
                afinidad_cocompra=fuentes["co"],
                pesos=pesos_ancla,
            )
            resultado[cand_asin] = (score, fuentes)
        return self._rerankear_novedad(resultado)

    def _rerankear_novedad(self, resultado: dict[str, tuple[float, dict[str, float]]]):
        """score_λ = (1 - λ)·score_norm + λ·(1 - S_pr), con score_norm min-max dentro del ancla.

        Baja el Gini de exposición: a igual score base, gana el producto menos popular.
        """
        lam = self.scoring.lambda_novedad
        if lam <= 0 or not resultado:
            return resultado
        scores = [sc for sc, _ in resultado.values()]
        bajo, alto = min(scores), max(scores)
        rango = alto - bajo
        return {
            c: ((1 - lam) * ((sc - bajo) / rango if rango > 0 else 0.0) + lam * (1 - self._autoridad(c)), f)
            for c, (sc, f) in resultado.items()
        }

    def recomendar(
        self, parent_asin: str, k: int = 10, precio_min: float | None = None, precio_max: float | None = None
    ) -> list[dict[str, Any]]:
        """
        Produce el ranking Top-K de recomendaciones híbridas para un producto dado,
        combinando similitud vectorial HNSW, Jaccard LSH, autoridad PageRank y co-compra.
        Los pesos se adaptan por ancla (ScoringHibrido.pesos_efectivos).
        - Con precio_min o precio_max solo entran los candidatos de ese rango (B+Tree de la etapa 4).
        - Arranque en frío: si el ancla no tiene ninguna señal (sin vecinos HNSW, sin firma MinHash
          y fuera del grafo, como un producto que nunca apareció en el stream), la lista sale del
          KNN exacto por atributos dentro de su categoría con el KD-Tree de la etapa 5, ordenada por
          distancia. Los candidatos de las anclas con señales no cambian.
        """
        candidatos = self.puntuar_candidatos(parent_asin)
        fuente = "hibrido"
        if not candidatos and self.indice_atributos is not None:
            vecinos = self.indice_atributos.similares(parent_asin, k=max(5 * k, 50)) or []
            candidatos = {asin: (1.0 / (1.0 + d), {"atr": d}) for d, asin in vecinos}
            fuente = "arranque_frio"
        if precio_min is not None or precio_max is not None:
            en_rango = self.productos_en_rango_precio(precio_min, precio_max)
            candidatos = {a: c for a, c in candidatos.items() if a in en_rango}

        # TopKHeap para selección eficiente O(N log K)
        topk = TopKHeap(k=k)
        for cand_asin, (score_final, _) in candidatos.items():
            topk.agregar(cand_asin, score_final)

        # Construir resultados enriquecidos
        resultados = []
        for rank, (score, cand_asin) in enumerate(topk.obtener_top(), 1):
            info = self.detalle_producto(cand_asin) or {}
            fuentes = candidatos[cand_asin][1]
            stock, disponible = self._stock(cand_asin)
            resultados.append({
                "rango": rank,
                "parent_asin": cand_asin,
                "fuente": fuente,
                "score_hibrido": round(score, 4) if fuente == "hibrido" else None,
                "similitud_atributos": round(fuentes.get("sim", 0.0), 4),
                "jaccard_comportamiento": round(fuentes.get("rel", 0.0), 4),
                "afinidad_cocompra": round(fuentes.get("co", 0.0), 4),
                "distancia_atributos_etapa5": round(fuentes["atr"], 4) if "atr" in fuentes else None,
                "pagerank": round(self._pagerank.get(cand_asin, 0.0), 6),
                "titulo": info.get("title", "N/D"),
                "categoria": info.get("categoria_hoja", "N/D"),
                "precio": info.get("precio"),
                "tienda": info.get("store", "N/D"),
                "stock": stock,
                "disponible": disponible,
            })
        return resultados

    def corregir_consulta(self, query: str) -> list[str]:
        """Tokeniza la consulta y corrige cada token fuera del vocabulario (OSA <= 2)."""
        corregidos = []
        for tok in tokenizar(query):
            if tok in self.indice.vocabulario or not tok.isalpha() or len(self.trie_vocabulario) == 0:
                corregidos.append(tok)
                continue
            sug = self.trie_vocabulario.autocompletar_difuso(tok, k=1, solo_completos=True)
            corregidos.append(sug[0]["termino"] if sug else tok)
        return corregidos

    def buscar(
        self, query: str, k: int = 10, precio_min: float | None = None, precio_max: float | None = None
    ) -> list[dict[str, Any]]:
        """
        Búsqueda por texto con ranking multicriterio:
        score = 0.8 · cobertura IDF de la consulta en el título + 0.2 · autoridad (S_pr).
        Con el índice invertido construido la consulta cuesta O(sum_t df_t) y la selección
        O(C) con np.argpartition; sin índice (pruebas) usa los candidatos del Trie.
        Con precio_min o precio_max solo quedan los productos de ese rango (B+Tree de la etapa 4).

        Auditoría 2026-10-27: antes el score era 0.5 + 0.5 · S_pr (la relevancia era constante)
        y, si faltaban candidatos, se completaba con el prefijo query[:3]: "toner brother"
        devolvía portacarteles de la marca TONICE.
        """
        alfa = PESO_RELEVANCIA_BUSQUEDA
        tokens = self.corregir_consulta(query)
        if not tokens:
            return []
        en_rango = None
        if precio_min is not None or precio_max is not None:
            en_rango = self.productos_en_rango_precio(precio_min, precio_max)

        if self.indice.n_docs > 0:
            docs, cobertura = self.indice.puntuar(tokens)
            if en_rango is not None and len(docs) > 0:
                dentro = np.array([self._asins_docs[d] in en_rango for d in docs.tolist()], dtype=bool)
                docs, cobertura = docs[dentro], cobertura[dentro]
            if len(docs) == 0:
                return []
            score = alfa * cobertura + (1.0 - alfa) * self._autoridad_docs[docs]
            m = min(k, len(docs))
            top = np.argpartition(-score, m - 1)[:m]
            top = top[np.argsort(-score[top], kind="stable")]
            pares = [(float(score[i]), self._asins_docs[docs[i]]) for i in top]
        else:
            topk = TopKHeap(k=k)
            consulta = set(tokens)
            for s in self.autocompletar(" ".join(tokens), k=max(k, 10)):
                meta = s.get("meta", {})
                if not meta.get("asin") or (en_rango is not None and meta["asin"] not in en_rango):
                    continue
                texto = set(tokenizar(f"{s['termino']} {meta.get('titulo', '')}"))
                relevancia = len(consulta & texto) / len(consulta)
                topk.agregar(meta["asin"], alfa * relevancia + (1.0 - alfa) * self._autoridad(meta["asin"]))
            pares = topk.obtener_top()

        resultados = []
        for rank, (score, asin) in enumerate(pares, 1):
            info = self.detalle_producto(asin) or {}
            resultados.append({
                "rango": rank,
                "parent_asin": asin,
                "score": round(score, 4),
                "titulo": info.get("title", "N/D"),
                "categoria": info.get("categoria_hoja", "N/D"),
                "precio": info.get("precio"),
                "tienda": info.get("store", "N/D"),
                "pagerank": round(info.get("pagerank", 0.0), 6),
            })
        return resultados

    def resumen_fraude(self) -> dict[str, Any]:
        """Devuelve estadísticas y muestra de alertas de la etapa 6."""
        return {
            "resumen": self._alertas_resumen,
            "muestra": self._alertas_muestra,
        }

    def panel_ventas_actividad(self, k: int = 10) -> dict[str, Any]:
        """Panel de ventas, actividad y stock que pide el escenario B.

        - Ventas y actividad por día: una pasada por el stream de la etapa 2. Las compras de las
          órdenes bloqueadas por la etapa 6 se cuentan aparte y no suman ventas ni ingresos.
        - Stock, disponibilidad, agotamientos y ventas sin stock: estado de los productos de la
          etapa 3. Esos contadores incluyen las compras fraudulentas, porque la etapa 3 se actualiza
          antes del filtro de la etapa 6 (limitación declarada en el informe de las etapas 3 a 5).
        """
        ruta_bloqueadas = self.dir_interim / "etapa_6" / "ordenes_bloqueadas.json"
        bloqueadas = set(json.loads(ruta_bloqueadas.read_text(encoding="utf-8"))) if ruta_bloqueadas.exists() else set()
        por_dia, unidades, ingresos = agregar_actividad(
            eventos_del_stream(self.dir_interim / "etapa2_eventos.jsonl"), bloqueadas)
        estado = pd.read_parquet(self.dir_interim / "etapa_3" / "estado_productos.parquet").set_index("parent_asin")

        fichas: dict[str, tuple[str, str]] = {}

        def ficha(asin: str) -> tuple[str, str]:
            if asin not in fichas:
                reg = (self.almacen_catalogo.leer(asin) if self.almacen_catalogo else None) or {}
                fichas[asin] = (reg.get("title") or "N/D", reg.get("categoria_hoja") or "(sin categoría)")
            return fichas[asin]

        total: Counter = Counter()
        for conteo in por_dia.values():
            total.update(conteo)
        serie = [{
            "fecha": time.strftime("%Y-%m-%d", time.gmtime(dia * 86_400)),
            "compras": por_dia[dia]["purchase"], "compras_bloqueadas": por_dia[dia]["purchase_bloqueada"],
            "vistas": por_dia[dia]["view"], "carritos": por_dia[dia]["cart"], "devoluciones": por_dia[dia]["return"],
            "resenas": por_dia[dia]["review"], "busquedas": por_dia[dia]["search"],
            "alertas_pago": por_dia[dia]["payment_alert"],
        } for dia in sorted(por_dia)]

        resumen = {
            "dias": len(serie),
            "unidades_vendidas": int(sum(unidades.values())),
            "ingresos": round(float(sum(ingresos.values())), 2),
            "compras_bloqueadas": int(total["purchase_bloqueada"]),
            "vistas": int(total["view"]),
            "carritos": int(total["cart"]),
            "devoluciones": int(total["return"]),
            "tasa_devolucion": total["return"] / total["purchase"] if total["purchase"] else None,
            "conversion_vista_compra": total["purchase"] / total["view"] if total["view"] else None,
            "productos_con_actividad": int(len(estado)),
            "productos_agotados": int((~estado["disponible"]).sum()),
            "productos_stock_bajo": int(((estado["stock"] > 0) & (estado["stock"] <= STOCK_BAJO)).sum()),
            "agotamientos": int(estado["agotamientos"].sum()),
            "ventas_sin_stock": int(estado["ventas_sin_stock"].sum()),
            "umbral_stock_bajo": STOCK_BAJO,
        }

        top_productos = []
        for asin, n in unidades.most_common(k):
            titulo, categoria = ficha(asin)
            con_estado = asin in estado.index
            top_productos.append({
                "parent_asin": asin, "titulo": titulo, "categoria": categoria, "unidades": int(n),
                "ingresos": round(ingresos.get(asin, 0.0), 2),
                "stock": int(estado.at[asin, "stock"]) if con_estado else None,
                "disponible": bool(estado.at[asin, "disponible"]) if con_estado else None,
            })

        unidades_categoria: Counter = Counter()
        ingresos_categoria: dict[str, float] = defaultdict(float)
        for asin, n in unidades.items():
            categoria = ficha(asin)[1]
            unidades_categoria[categoria] += n
            ingresos_categoria[categoria] += ingresos.get(asin, 0.0)
        por_categoria = [{"categoria": c, "unidades": int(n), "ingresos": round(ingresos_categoria[c], 2)}
                         for c, n in unidades_categoria.most_common(k)]

        agotados = []
        sin_stock = estado[~estado["disponible"]].sort_values(["ventas_sin_stock", "agotamientos"], ascending=False)
        for asin, fila in sin_stock.head(k).iterrows():
            titulo, categoria = ficha(asin)
            agotados.append({"parent_asin": asin, "titulo": titulo, "categoria": categoria,
                             "ventas_sin_stock": int(fila["ventas_sin_stock"]),
                             "agotamientos": int(fila["agotamientos"]), "compras": int(fila["compras"])})

        return {"resumen": resumen, "por_dia": serie, "top_productos": top_productos,
                "por_categoria": por_categoria, "agotados": agotados}

    def metricas_sistema(self) -> dict[str, Any]:
        """Devuelve un resumen consolidado de las etapas del pipeline."""
        return {
            "categoria": self.categoria,
            "trie_terminos": len(self.trie),
            "productos_catalogo_indexados": self.indice.n_docs,
            "productos_pagerank": len(self._pagerank),
            "productos_similares": len(self._similares),
            "productos_relacionados": len(self._relacionados),
            "productos_con_estado": len(self._estado),
            "productos_indice_precio": len(self.indice_precio) if self.indice_precio is not None else 0,
            "productos_indice_atributos": len(self.indice_atributos.asins) if self.indice_atributos is not None else 0,
            "alertas_fraude": self._alertas_resumen.get("total_alertas", 0),
            "algoritmos_activos": [
                "Trie radix (Prefijos con Top-K Caché + difuso Damerau-OSA)",
                "Índice invertido de títulos (postings CSR, cobertura IDF)",
                "MinHeap (Ranking Top-K Acotado)",
                "Scoring Híbrido Multicriterio",
                "TablaHash + estado de productos (stock y disponibilidad, etapa 3)",
                "B+Tree en disco (filtro por rango de precio, etapa 4)",
                "KD-Tree (arranque en frío por atributos, etapa 5)",
                "HNSW (Similitud Vectorial de Atributos)",
                "MinHash + LSH (Comportamiento Jaccard)",
                "PageRank CSR (Autoridad Espectral)",
                "Louvain (Comunidades Multinivel)",
                "Bloom Filter + CMS (Detección de Fraude)",
            ]
        }

    def cerrar(self) -> None:
        """Libera el archivo del catálogo y el del índice de precios."""
        if self.almacen_catalogo:
            self.almacen_catalogo.cerrar()
            self.almacen_catalogo = None
        if self.indice_precio is not None:
            self.indice_precio.cerrar()
            self.indice_precio = None


# ============================================================================
# 4b. PANEL DE VENTAS Y ACTIVIDAD
# ============================================================================

def eventos_del_stream(ruta: Path) -> Iterable[dict[str, Any]]:
    """Eventos de la salida de la etapa 2 (un ciclo de servicio por línea) como diccionarios."""
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            yield from json.loads(linea)["eventos"]


def agregar_actividad(eventos: Iterable[dict[str, Any]], bloqueadas: set[str]):
    """Una pasada por el stream: eventos por día y tipo, y unidades e ingresos legítimos por producto.

    Las compras de las órdenes bloqueadas por la etapa 6 se cuentan como 'purchase_bloqueada' y no
    suman ventas. Devuelve (por_dia {día Unix: Counter por tipo}, unidades Counter, ingresos dict).
    """
    por_dia: dict[int, Counter] = defaultdict(Counter)
    unidades: Counter = Counter()
    ingresos: dict[str, float] = defaultdict(float)
    for ev in eventos:
        ts = ev.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        dia = int(ts // 86_400)
        tipo = ev.get("event_type")
        if tipo == "purchase" and ev.get("order_id") in bloqueadas:
            por_dia[dia]["purchase_bloqueada"] += 1
            continue
        por_dia[dia][tipo] += 1
        asin = ev.get("parent_asin")
        if tipo == "purchase" and asin:
            unidades[asin] += 1
            if ev.get("price") is not None:
                ingresos[asin] += float(ev["price"])
    return por_dia, unidades, ingresos


# ============================================================================
# 5. ORQUESTACIÓN DE LA ETAPA 9
# ============================================================================

ANCLAS_DEMO = ["B0BF98LWMZ", "B0BN478RWJ", "B087S2JRXY", "B0B3MX7LVG", "B0BZ67T58M"]


def _latencias_us(funcion, argumentos, repeticiones: int = 20) -> list[float]:
    """Latencias en microsegundos de funcion(arg) para cada argumento, con repeticiones."""
    salida = []
    for arg in argumentos:
        for _ in range(repeticiones):
            t = time.perf_counter()
            funcion(arg)
            salida.append((time.perf_counter() - t) * 1e6)
    return salida


def ejecutar_etapa(
    categoria: str = config.CATEGORIA,
    raiz: Path | None = None,
    limite_trie: int | None = None,
) -> dict[str, Any]:
    """
    Ejecuta el pipeline de la etapa 9:
    1. Carga de índices, Trie radix de autocompletado e índice invertido (catálogo completo).
    2. Consultas de prueba: autocompletado exacto y difuso, búsqueda con corrección ortográfica,
       recomendación Top-K con pesos adaptativos, filtro por rango de precio (etapa 4) y arranque
       en frío (etapa 5).
    3. Panel de ventas, actividad y stock (etapas 2, 3 y 6).
    4. Persistencia de métricas y demostraciones en disco y generación del dashboard.
    """
    raiz_path = raiz or config.RAIZ
    dir_interim = raiz_path / "data" / "interim"
    dir_etapa9 = dir_interim / categoria / "etapa_9"
    dir_etapa9.mkdir(parents=True, exist_ok=True)

    print(f"--- Iniciando Etapa 9: Servicio y Visualización ({categoria}) ---")
    t0 = time.perf_counter()

    servicio = ServicioEcommerce(dir_interim, categoria)
    servicio.cargar_datos(limite_terminos_trie=limite_trie)
    t_carga = time.perf_counter() - t0

    # Pruebas de autocompletado exacto (O(L) con la caché)
    consultas_trie = ["ton", "bro", "stap", "pen", "note", "pilo", "scot", "hp"]
    demo_autocompletado = {pref: servicio.autocompletar(pref, k=5) for pref in consultas_trie}
    lat_exacto = _latencias_us(lambda q: servicio.trie.autocompletar(q, k=5), consultas_trie)

    # Pruebas de autocompletado difuso (prefijos con un error de tipeo, d = 1)
    consultas_difusas = ["tonr", "stapel", "brotehr", "pilto g2", "scoth tape", "sharpei", "swinglien"]
    demo_difuso = {q: servicio.autocompletar(q, k=5) for q in consultas_difusas}
    lat_difuso = _latencias_us(
        lambda q: servicio.trie.autocompletar_difuso(q, k=5, max_dist=MAX_DIST_AUTOCOMPLETADO),
        consultas_difusas, repeticiones=5,
    )

    # Recomendación híbrida Top-K: anclas de mayor PageRank (antes estaban fijadas a mano)
    top_pr = sorted(servicio._pagerank, key=servicio._pagerank.get, reverse=True)[:5] or ANCLAS_DEMO
    top_pr_asin = top_pr[0]
    recs = servicio.recomendar(top_pr_asin, k=10)
    lat_rec = _latencias_us(lambda a: servicio.recomendar(a, k=10), top_pr)

    # Filtro por rango de precio (B+Tree de la etapa 4): alternativas que no cuestan más que el ancla
    rango_demo = None
    if servicio.indice_precio is not None:
        precio_ancla = (servicio.detalle_producto(top_pr_asin) or {}).get("precio")
        precio_min, precio_max = (0.0, float(precio_ancla)) if precio_ancla else (0.0, 20.0)
        servicio._rangos_precio.clear()
        t_sub0 = time.perf_counter()
        en_rango = servicio.productos_en_rango_precio(precio_min, precio_max)
        ms_rango = (time.perf_counter() - t_sub0) * 1000.0
        rango_demo = {
            "asin_origen": top_pr_asin, "precio_min": precio_min, "precio_max": precio_max,
            "productos_en_rango": len(en_rango), "ms_consulta_rango": round(ms_rango, 3),
            "recomendaciones": servicio.recomendar(top_pr_asin, k=8, precio_min=precio_min, precio_max=precio_max),
        }

    # Arranque en frío (KD-Tree de la etapa 5) con un producto que nunca apareció en el stream
    ancla_frio = servicio.ancla_arranque_frio()

    # Búsqueda Top-K con índice invertido y corrección de tokens
    consultas_busqueda = ["toner brother", "toner brtoher", "stapler swinglien", "pilot g2 pens"]
    demo_busqueda = {
        q: {"consulta_corregida": " ".join(servicio.corregir_consulta(q)), "resultados": servicio.buscar(q, k=10)}
        for q in consultas_busqueda
    }
    lat_busq = _latencias_us(lambda q: servicio.buscar(q, k=10), consultas_busqueda, repeticiones=5)

    # Fichas y recomendaciones de los productos que el dashboard permite seleccionar
    anclas = [(asin, f"top {i} de PageRank") for i, asin in enumerate(top_pr, 1)]
    if ancla_frio is not None:
        anclas.append((ancla_frio, "arranque en frío, sin actividad en el stream"))
    catalogo_demo = {}
    for asin, etiqueta in anclas:
        detalle = servicio.detalle_producto(asin)
        if detalle is not None:
            catalogo_demo[asin] = {"etiqueta": etiqueta, "detalle": detalle,
                                   "recomendaciones": servicio.recomendar(asin, k=8)}

    # Panel de ventas, actividad y stock
    t_sub0 = time.perf_counter()
    panel = servicio.panel_ventas_actividad()
    t_panel = time.perf_counter() - t_sub0

    # Consolidar métricas
    metricas = {
        "categoria": categoria,
        "tiempo_inicializacion_s": round(t_carga, 3),
        "terminos_trie_indexados": len(servicio.trie),
        "nodos_trie_radix": servicio.trie.n_nodos,
        "productos_indexados_busqueda": servicio.indice.n_docs,
        "vocabulario_busqueda": len(servicio.indice.vocabulario),
        "postings_busqueda": int(len(servicio.indice.postings)),
        "vocabulario_correccion": len(servicio.trie_vocabulario),
        "latencia_autocompletado_p50_ms": round(float(np.median(lat_exacto)) / 1000.0, 4),
        "latencia_autocompletado_p95_ms": round(float(np.percentile(lat_exacto, 95)) / 1000.0, 4),
        "latencia_autocompletado_difuso_p50_ms": round(float(np.median(lat_difuso)) / 1000.0, 4),
        "latencia_autocompletado_difuso_p95_ms": round(float(np.percentile(lat_difuso, 95)) / 1000.0, 4),
        "latencia_recomendacion_topk_ms": round(float(np.median(lat_rec)) / 1000.0, 3),
        "latencia_recomendacion_topk_p95_ms": round(float(np.percentile(lat_rec, 95)) / 1000.0, 3),
        "latencia_busqueda_topk_p50_ms": round(float(np.median(lat_busq)) / 1000.0, 3),
        "latencia_busqueda_topk_p95_ms": round(float(np.percentile(lat_busq, 95)) / 1000.0, 3),
        "latencia_rango_precio_ms": rango_demo["ms_consulta_rango"] if rango_demo else None,
        "segundos_panel_ventas": round(t_panel, 1),
        "scoring_pesos": {
            "similitud": servicio.scoring.peso_similitud,
            "relacionados": servicio.scoring.peso_relacionados,
            "pagerank": servicio.scoring.peso_pagerank,
            "cocompra": servicio.scoring.peso_cocompra,
            "kappa_cocompra": servicio.scoring.kappa_cocompra,
            "lambda_novedad": servicio.scoring.lambda_novedad,
            "normalizacion_pagerank": servicio.normalizacion_pagerank,
            "fuente_cocompra": servicio.fuente_cocompra,
        },
        "resumen_sistema": servicio.metricas_sistema(),
    }

    # Guardar métricas y consultas de prueba
    ruta_metricas = dir_etapa9 / "metricas.json"
    ruta_metricas.write_text(json.dumps(metricas, indent=2, ensure_ascii=False), encoding="utf-8")

    demo_salida = {
        "autocompletado": demo_autocompletado,
        "autocompletado_difuso": demo_difuso,
        "recomendacion_ejemplo": {
            "asin_origen": top_pr_asin,
            "detalle_origen": servicio.detalle_producto(top_pr_asin),
            "recomendaciones": recs,
        },
        "busqueda_ejemplo": {
            "query": "toner brother",
            "resultados": demo_busqueda["toner brother"]["resultados"],
        },
        "busquedas": demo_busqueda,
        "recomendacion_rango_precio": rango_demo,
        "arranque_frio": {"asin_origen": ancla_frio,
                          "recomendaciones": catalogo_demo.get(ancla_frio, {}).get("recomendaciones", [])},
        "fraude": servicio.resumen_fraude(),
        "panel_ventas": panel,
        "catalogo_demo": catalogo_demo,
    }
    ruta_demo = dir_etapa9 / "demo_consultas.json"
    ruta_demo.write_text(json.dumps(demo_salida, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    servicio.cerrar()
    ruta_dashboard = generar_dashboard(ruta_demo, ruta_metricas)

    print(f"✓ Métricas guardadas en: {ruta_metricas}")
    print(f"✓ Demos guardadas en: {ruta_demo}")
    print(f"✓ Dashboard generado en: {ruta_dashboard}")
    print(f"Etapa 9 completada en {time.perf_counter() - t0:.2f} s")
    return metricas


if __name__ == "__main__":
    ejecutar_etapa()
