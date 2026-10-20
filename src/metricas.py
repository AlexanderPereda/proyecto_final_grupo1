"""Métricas de desorden temporal de un stream dividido en lotes.

Un evento está fuera de orden si su `ts` es menor que el de algún evento que llegó antes.
Se separa en dos tipos según dónde está ese evento anterior:

- **entre lotes**: su ts es menor que el mayor ts de los lotes anteriores. Ordenar su propio
  lote no lo corrige, porque el evento con el que se invierte ya salió en otro lote.
- **dentro del lote**: no es menor que nada de los lotes anteriores, pero sí que algún evento
  previo de su mismo lote. Ordenar el lote por ts sí lo corrige.

Al entrar a la etapa 1 se miden los registros crudos con ts válido; al salir, los eventos limpios.
"""


def contar_fuera_de_orden(lotes_ts):
    """Recibe una secuencia de lotes, cada uno una lista de ts en orden de llegada.

    Devuelve {"eventos", "fuera_de_orden", "dentro_del_lote", "entre_lotes", "porcentaje"}.
    Los ts que no son números (registros malformados) se ignoran.
    """
    eventos = dentro = entre = 0
    maximo_lotes_previos = float("-inf")
    for lote in lotes_ts:
        maximo_lote = float("-inf")
        for ts in lote:
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                continue
            eventos += 1
            if ts < maximo_lotes_previos:
                entre += 1
            elif ts < maximo_lote:
                dentro += 1
            maximo_lote = max(maximo_lote, ts)
        maximo_lotes_previos = max(maximo_lotes_previos, maximo_lote)
    total = dentro + entre
    return {
        "eventos": eventos,
        "fuera_de_orden": total,
        "dentro_del_lote": dentro,
        "entre_lotes": entre,
        "porcentaje": 100 * total / eventos if eventos else 0.0,
    }
