"""Etapa 6: filtrado probabilístico (Bloom Filter y Count-Min Sketch).

Estructuras propias de la etapa:
- BloomFilter: arreglo de m bits y k funciones hash. Responde "posiblemente observado" o
  "seguro que no observado": puede dar falsos positivos, nunca falsos negativos.

Las k posiciones de una clave se obtienen por doble hashing, g_i(x) = h1(x) + i·h2(x),
con h1 y h2 tomados de un único BLAKE2b de 128 bits (Kirsch y Mitzenmacher, 2006). Es
determinista entre ejecuciones, a diferencia del hash() de Python.

`set` se usa solo como línea base exacta en los experimentos.
"""

import hashlib
import math

# ----- Parámetros de la etapa ----------------------------------------------------

P_FP = 0.01               # tasa de falsos positivos objetivo del Bloom Filter


# ----- Hash ------------------------------------------------------------------------

def hashes_base(clave):
    """(h1, h2) de 64 bits a partir de un BLAKE2b de 128 bits. h2 es impar para que recorra
    todas las posiciones cuando el tamaño es potencia de 2."""
    d = hashlib.blake2b(clave.encode("utf-8"), digest_size=16).digest()
    return int.from_bytes(d[:8], "little"), int.from_bytes(d[8:], "little") | 1


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
        h1, h2 = hashes_base(clave)
        m = self.m
        return [(h1 + i * h2) % m for i in range(self.k)]

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
