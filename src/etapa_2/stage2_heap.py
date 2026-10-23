"""Etapa 2: min-heap propio y cola de prioridades con aging.

Código copiado de notebooks/etapa2_cola_prioridades.ipynb (pasos 2 a 4).
"""

CLASE_ALERTA, CLASE_EXPRESS, CLASE_ESTANDAR, CLASE_PROGRAMADA, CLASE_NAVEGACION = 1, 2, 3, 4, 5
NOMBRE_CLASE = {1: "1 alerta", 2: "2 express", 3: "3 estándar", 4: "4 programada", 5: "5 navegación"}
PLAZO_S = {1: 5 * 60, 2: 2 * 3600, 3: 24 * 3600, 4: 72 * 3600, 5: 30 * 60}
AGING_S = 15 * 60          # cada 15 minutos de espera el evento sube una clase
CLASE_MAXIMA_AGING = 3     # el aging nunca supera la clase 3 (no adelanta alertas ni express)


def clase_de(evento):
    """Clase de prioridad base de un evento."""
    if evento.event_type == "payment_alert":
        return CLASE_ALERTA
    if evento.event_type == "purchase":
        return {"express": CLASE_EXPRESS, "standard": CLASE_ESTANDAR,
                "scheduled": CLASE_PROGRAMADA}[evento.order_type]
    if evento.event_type == "return":
        return CLASE_ESTANDAR
    return CLASE_NAVEGACION          # search, view, cart, review


class MinHeap:
    """Min-heap binario sobre un arreglo. Los elementos deben ser comparables con <."""

    def __init__(self, elementos=None):
        self._a = list(elementos) if elementos else []
        if self._a:
            self.heapify()

    def __len__(self):
        return len(self._a)

    def peek(self):
        """Devuelve el mínimo sin sacarlo. O(1)."""
        if not self._a:
            raise IndexError("heap vacío")
        return self._a[0]

    def push(self, x):
        """Inserta x. O(log n)."""
        self._a.append(x)
        self._subir(len(self._a) - 1)

    def pop(self):
        """Saca y devuelve el mínimo. O(log n)."""
        a = self._a
        if not a:
            raise IndexError("heap vacío")
        ultimo = a.pop()
        if not a:                       # el heap tenía un solo elemento
            return ultimo
        minimo = a[0]
        a[0] = ultimo                   # el último pasa a la raíz y baja
        self._bajar(0)
        return minimo

    def heapify(self):
        """Convierte el arreglo en heap (Floyd). O(n)."""
        for i in range(len(self._a) // 2 - 1, -1, -1):   # nodos internos, de abajo hacia arriba
            self._bajar(i)

    def reemplazar_todo(self, elementos):
        """Sustituye el contenido y lo reordena con heapify. O(n)."""
        self._a = list(elementos)
        self.heapify()

    def elementos(self):
        """Arreglo interno (en orden de heap, no ordenado)."""
        return self._a

    def _subir(self, i):
        a = self._a
        x = a[i]
        while i > 0:
            padre = (i - 1) // 2
            if x < a[padre]:
                a[i] = a[padre]         # el padre baja al hueco
                i = padre
            else:
                break
        a[i] = x

    def _bajar(self, i):
        a = self._a
        n = len(a)
        x = a[i]
        while True:
            hijo = 2 * i + 1
            if hijo >= n:
                break
            if hijo + 1 < n and a[hijo + 1] < a[hijo]:
                hijo += 1               # se elige el hijo menor
            if a[hijo] < x:
                a[i] = a[hijo]          # el hijo sube al hueco
                i = hijo
            else:
                break
        a[i] = x


def violaciones_heap(a):
    """Devuelve los pares (padre, hijo) de posiciones del arreglo donde el hijo es menor que su padre.

    En un min-heap válido la lista está vacía. Recorre cada hijo h y lo compara con su
    padre (h - 1) // 2, así que revisa los n - 1 pares del árbol en O(n).
    """
    return [((h - 1) // 2, h) for h in range(1, len(a)) if a[h] < a[(h - 1) // 2]]


def es_heap(a):
    """Comprueba la propiedad de heap: cada padre es <= que sus hijos."""
    return all(not (a[h] < a[(h - 1) // 2]) for h in range(1, len(a)))


class ColaPrioridadEventos:
    """Cola de prioridades de eventos sobre MinHeap, con aging opcional."""

    def __init__(self, aging_s=AGING_S):
        self._heap = MinHeap()
        self._seq = 0
        self.aging_s = aging_s            # None desactiva el aging
        self.ascensos = 0                 # ascensos de clase (un evento puede subir varias veces)

    def push(self, evento, ahora):
        clase = clase_de(evento)
        deadline = evento.ts + PLAZO_S[clase]
        evento.priority = clase
        evento.deadline = deadline
        self._heap.push((clase, deadline, self._seq, clase, ahora, evento))
        self._seq += 1

    def pop(self):
        """Devuelve (evento, clase con la que salió, hora de entrada)."""
        clase_efectiva, _, _, _, t_entrada, evento = self._heap.pop()
        return evento, clase_efectiva, t_entrada

    def envejecer(self, ahora):
        """Recalcula la clase efectiva de cada entrada y reconstruye el heap. O(n)."""
        if self.aging_s is None:
            return
        nuevas = []
        for clase_actual, deadline, seq, clase_base, t_entrada, evento in self._heap.elementos():
            clase = clase_base
            if clase_base > CLASE_MAXIMA_AGING:
                niveles = int((ahora - t_entrada) // self.aging_s)
                clase = max(CLASE_MAXIMA_AGING, clase_base - niveles)
            if clase < clase_actual:
                self.ascensos += clase_actual - clase
            nuevas.append((clase, deadline, seq, clase_base, t_entrada, evento))
        self._heap.reemplazar_todo(nuevas)

    def __len__(self):
        return len(self._heap)
