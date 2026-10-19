"""Descarga los archivos crudos de Amazon Reviews 2023 desde Hugging Face.

Uso:
    python scripts/data_download.py               # descarga All_Beauty y Video_Games
    python scripts/data_download.py All_Beauty    # descarga solo All_Beauty

Los archivos quedan en data/raw/, conservando la ruta que tienen en el repositorio:
    data/raw/review_categories/<Categoria>.jsonl
    data/raw/meta_categories/meta_<Categoria>.jsonl
"""

import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

# --- Configuracion -----------------------------------------------------------

# Repositorio de Hugging Face (es de tipo "dataset", no "model")
REPO_ID = "McAuley-Lab/Amazon-Reviews-2023"
REPO_TYPE = "dataset"

# Categorias permitidas en el proyecto
#   All_Beauty  -> desarrollo (~700K resenas)
#   Video_Games -> experimento final (~4.6M resenas)
CATEGORIAS = ["All_Beauty", "Video_Games"]

# Carpeta data/ en la raiz del proyecto (este script esta en scripts/)
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
CARPETA_DATA = RAIZ_PROYECTO / "data"


def rutas_en_repo(categoria):
    """Devuelve las rutas de los dos archivos .jsonl de una categoria dentro del repositorio."""
    return [
        f"raw/review_categories/{categoria}.jsonl",       # resenas
        f"raw/meta_categories/meta_{categoria}.jsonl",    # metadatos de productos
    ]


# --- Descarga ----------------------------------------------------------------

def descargar_archivo(ruta_repo):
    """Descarga un archivo del repositorio y devuelve su ruta local.

    local_dir=data/ hace que el archivo se guarde en data/<ruta_repo>,
    es decir, dentro de data/raw/. Si el archivo ya existe y esta completo,
    hf_hub_download no lo vuelve a descargar.
    """
    ruta_local = hf_hub_download(
        repo_id=REPO_ID,
        filename=ruta_repo,
        repo_type=REPO_TYPE,
        local_dir=CARPETA_DATA,
    )
    return Path(ruta_local)


# --- Resumen -----------------------------------------------------------------

def contar_lineas(ruta):
    """Cuenta los registros leyendo el archivo linea por linea.

    Cada linea de un .jsonl es un registro. Iterar sobre el archivo lee una
    linea a la vez, por lo que la memoria usada no depende del tamano del archivo.
    """
    total = 0
    with open(ruta, "r", encoding="utf-8") as archivo:
        for linea in archivo:
            if linea.strip():          # se ignoran lineas vacias
                total += 1
    return total


def mostrar_resumen(rutas_locales):
    """Imprime el tamano en MB y el numero de registros de cada archivo."""
    print()
    print(f"{'Archivo':<55} {'Tamano (MB)':>12} {'Registros':>12}")
    print("-" * 81)
    for ruta in rutas_locales:
        tamano_mb = ruta.stat().st_size / (1024 * 1024)
        registros = contar_lineas(ruta)
        nombre = ruta.relative_to(RAIZ_PROYECTO).as_posix()
        print(f"{nombre:<55} {tamano_mb:>12.1f} {registros:>12,}")


# --- Programa principal ------------------------------------------------------

def leer_categorias_de_terminal():
    """Lee las categorias pasadas como argumentos. Sin argumentos se usan todas."""
    pedidas = sys.argv[1:]
    if not pedidas:
        return CATEGORIAS

    invalidas = [c for c in pedidas if c not in CATEGORIAS]
    if invalidas:
        print(f"Categoria no valida: {', '.join(invalidas)}")
        print(f"Opciones: {', '.join(CATEGORIAS)}")
        sys.exit(1)
    return pedidas


def main():
    categorias = leer_categorias_de_terminal()

    rutas_locales = []
    for categoria in categorias:
        for ruta_repo in rutas_en_repo(categoria):
            print(f"Descargando {ruta_repo} ...")
            rutas_locales.append(descargar_archivo(ruta_repo))

    mostrar_resumen(rutas_locales)


if __name__ == "__main__":
    main()
