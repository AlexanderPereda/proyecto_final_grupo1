"""Configuración central de las etapas 0, 1 y 2.

Es el único lugar del proyecto donde se escriben la categoría, las rutas de los datos y los
parámetros del experimento. Los notebooks, `src/` y los tests importan estos valores.

Los datos se generan con `notebooks/00-download-data.ipynb`, que deja en `data/raw/` las
reseñas ordenadas por `timestamp` y los metadatos de productos en formato Parquet.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# --- Datos --------------------------------------------------------------------
CATEGORIA = "Office_Products"
CARPETA_RAW = RAIZ / "data" / "raw"
RUTA_RESENAS = CARPETA_RAW / f"{CATEGORIA.lower()}.parquet"          # ordenadas por timestamp
RUTA_META = CARPETA_RAW / f"meta_{CATEGORIA.lower()}.parquet"

# Columnas que se leen de cada archivo (el resto no se carga)
COLUMNAS_RESENAS = ["user_id", "parent_asin", "rating", "verified_purchase", "timestamp"]
COLUMNAS_META = ["parent_asin", "price", "main_category", "title"]

# --- Salidas ------------------------------------------------------------------
CARPETA_INTERIM = RAIZ / "data" / "interim" / CATEGORIA              # salidas entre etapas
CARPETA_RESULTADOS = RAIZ / "results"                                 # CSV y gráficas

# --- Reproducibilidad ---------------------------------------------------------
SEED = 42

# --- Tamaño del experimento ---------------------------------------------------
# Ventana reciente: las últimas N_RESENAS reseñas del archivo, que forman un bloque continuo al
# final de las reseñas ordenadas por tiempo. El enunciado pide un stream de 10 millones de eventos;
# en esta ventana el generador produce unos 7 registros por reseña, así que 1 500 000 reseñas dan
# unos 10.5 millones de eventos.
N_RESENAS = 1_500_000

# --- Desorden controlado del stream -------------------------------------------
# Fracción de eventos que llegan tarde. Con 0 el stream sale ordenado por ts, sin llegadas tardías.
PORCENTAJE_TARDIOS = 0.05

# Retraso máximo de un evento tardío, en segundos del stream. Debe ser:
#   1. menor que la duración de un lote, para que un evento tardío caiga como mucho en el lote
#      siguiente y el desorden que deja la etapa 1 entre lotes quede acotado;
#   2. menor que la ventana de deduplicación de la etapa 1 (VENTANA_S = 600 s), para que un
#      reenvío de un evento tardío todavía se detecte entre lotes.
# El notebook de la etapa 1 comprueba la condición 1 midiendo la duración real de los lotes.
RETRASO_MAX_TARDIOS_S = 120