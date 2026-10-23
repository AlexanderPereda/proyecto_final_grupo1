"""Etapa 0: colas FIFO propias, buffer con backpressure y micro-lotes.

notebooks/etapa0_ingesta_buffer.ipynb importa este código y lo explica en los pasos 5 y 6.
"""

import threading
import time


class ColaCircular:
    """Cola FIFO sobre un arreglo de capacidad fija (buffer circular)."""

    def __init__(self, capacidad):
        if capacidad <= 0:
            raise ValueError("la capacidad debe ser positiva")
        self._datos = [None] * capacidad   # arreglo reservado una sola vez
        self._capacidad = capacidad
        self._inicio = 0                   # posición del primer elemento
        self._tam = 0                      # número de elementos guardados

    def encolar(self, elemento):
        """Agrega al final. O(1). Lanza OverflowError si la cola está llena."""
        if self._tam == self._capacidad:
            raise OverflowError("cola llena")
        fin = (self._inicio + self._tam) % self._capacidad
        self._datos[fin] = elemento
        self._tam += 1

    def desencolar(self):
        """Saca el primer elemento. O(1). Lanza IndexError si la cola está vacía."""
        if self._tam == 0:
            raise IndexError("cola vacía")
        elemento = self._datos[self._inicio]
        self._datos[self._inicio] = None   # se libera la referencia para el recolector de basura
        self._inicio = (self._inicio + 1) % self._capacidad
        self._tam -= 1
        return elemento

    def frente(self):
        """Devuelve el primer elemento sin sacarlo. O(1)."""
        if self._tam == 0:
            raise IndexError("cola vacía")
        return self._datos[self._inicio]

    def esta_vacia(self):
        return self._tam == 0

    def esta_llena(self):
        return self._tam == self._capacidad

    @property
    def capacidad(self):
        return self._capacidad

    def __len__(self):
        return self._tam


class _Nodo:
    __slots__ = ("valor", "siguiente")    # __slots__ reduce la memoria de cada nodo

    def __init__(self, valor):
        self.valor = valor
        self.siguiente = None


class ColaEnlazada:
    """Cola FIFO sobre una lista simplemente enlazada (sin límite de capacidad)."""

    def __init__(self):
        self._cabeza = None    # nodo del frente (el que sale primero)
        self._ultimo = None    # nodo del final (donde se agrega)
        self._tam = 0

    def encolar(self, elemento):
        """Agrega al final. O(1)."""
        nodo = _Nodo(elemento)
        if self._ultimo is None:          # cola vacía: el nodo es cabeza y último a la vez
            self._cabeza = nodo
        else:
            self._ultimo.siguiente = nodo
        self._ultimo = nodo
        self._tam += 1

    def desencolar(self):
        """Saca el primer elemento. O(1)."""
        if self._cabeza is None:
            raise IndexError("cola vacía")
        nodo = self._cabeza
        self._cabeza = nodo.siguiente
        if self._cabeza is None:          # la cola quedó vacía
            self._ultimo = None
        self._tam -= 1
        return nodo.valor

    def frente(self):
        if self._cabeza is None:
            raise IndexError("cola vacía")
        return self._cabeza.valor

    def esta_vacia(self):
        return self._tam == 0

    def __iter__(self):
        """Recorre los elementos del frente al final sin sacarlos."""
        nodo = self._cabeza
        while nodo is not None:
            yield nodo.valor
            nodo = nodo.siguiente

    def __len__(self):
        return self._tam


FIN = object()      # señal de que el productor terminó y el buffer está vacío
VACIO = object()    # señal de que se agotó el tiempo de espera sin recibir eventos


class BufferConBackpressure:
    """Buffer acotado y seguro entre hilos, construido sobre ColaCircular.

    Política de backpressure: si el buffer está lleno, el productor se bloquea.
    """

    def __init__(self, capacidad):
        self._cola = ColaCircular(capacidad)
        self._candado = threading.Lock()
        self._no_lleno = threading.Condition(self._candado)   # el productor espera aquí
        self._no_vacio = threading.Condition(self._candado)   # el consumidor espera aquí
        self._cerrado = False
        # Métricas
        self.bloqueos_productor = 0
        self.espera_productor_s = 0.0
        self.ocupacion_maxima = 0

    def poner(self, elemento):
        """Lo usa el productor. Si el buffer está lleno, espera (backpressure)."""
        with self._candado:
            if self._cola.esta_llena():
                self.bloqueos_productor += 1
                t0 = time.perf_counter()
                while self._cola.esta_llena():
                    self._no_lleno.wait()
                self.espera_productor_s += time.perf_counter() - t0
            self._cola.encolar(elemento)
            self.ocupacion_maxima = max(self.ocupacion_maxima, len(self._cola))
            self._no_vacio.notify()       # despierta al consumidor si estaba esperando

    def cerrar(self):
        """El productor avisa que ya no habrá más elementos."""
        with self._candado:
            self._cerrado = True
            self._no_vacio.notify_all()

    def sacar(self, timeout_s):
        """Lo usa el consumidor. Devuelve un elemento, VACIO si se agotó el tiempo o FIN."""
        with self._candado:
            limite = time.perf_counter() + timeout_s
            while self._cola.esta_vacia():
                if self._cerrado:
                    return FIN
                restante = limite - time.perf_counter()
                if restante <= 0:
                    return VACIO
                self._no_vacio.wait(restante)
            elemento = self._cola.desencolar()
            self._no_lleno.notify()       # despierta al productor si estaba bloqueado
            return elemento

    def __len__(self):
        with self._candado:
            return len(self._cola)


def micro_lotes(buffer, tam_lote, max_espera_s):
    """Generador de micro-lotes. Entrega (lote, motivo) con motivo 'tamaño', 'tiempo' o 'fin'."""
    while True:
        lote = []
        inicio = time.perf_counter()
        motivo = "tamaño"
        while len(lote) < tam_lote:
            restante = max_espera_s - (time.perf_counter() - inicio)
            elemento = buffer.sacar(max(restante, 0))
            if elemento is FIN:
                if lote:
                    yield lote, "fin"
                return
            if elemento is VACIO:
                motivo = "tiempo"
                break
            lote.append(elemento)
        if lote:
            yield lote, motivo
