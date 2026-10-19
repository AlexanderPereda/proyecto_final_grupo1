"""Genera visualizador/pipeline.html con la traza de las etapas 0, 1 y 2 incrustada.

El resultado es un solo archivo HTML que se abre con doble clic, sin servidor.

Uso:
    python scripts/generar_visualizador.py                  # 200 eventos, semilla de config.py
    python scripts/generar_visualizador.py --eventos 300
"""

import argparse
import json
import sys
from pathlib import Path

# Permite importar src/ al ejecutar el script desde cualquier carpeta
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src import config  # noqa: E402
from src.trace import SALTAR, generar_traza  # noqa: E402

PLANTILLA = RAIZ / "scripts" / "plantilla_visualizador.html"
MARCADOR = "__DATOS_TRAZA__"


def leer_argumentos():
    parser = argparse.ArgumentParser(description="Genera el visualizador HTML del pipeline.")
    parser.add_argument("--eventos", type=int, default=200, help="número de eventos a trazar")
    parser.add_argument("--semilla", type=int, default=config.SEED, help="semilla del generador")
    parser.add_argument("--saltar", type=int, default=SALTAR,
                        help="eventos iniciales del stream que no se trazan")
    parser.add_argument("--salida", type=Path, default=RAIZ / "visualizador" / "pipeline.html",
                        help="ruta del archivo HTML")
    return parser.parse_args()


def main():
    args = leer_argumentos()

    print(f"Generando la traza de {args.eventos} eventos (semilla {args.semilla}) ...")
    traza = generar_traza(n_eventos=args.eventos, semilla=args.semilla, saltar=args.saltar)

    # El JSON va dentro de una etiqueta <script>; "</" se escapa para que un texto como
    # "</script>" dentro de los datos no cierre la etiqueta antes de tiempo.
    datos = json.dumps(traza, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    plantilla = PLANTILLA.read_text(encoding="utf-8")
    assert plantilla.count(MARCADOR) == 1, "la plantilla debe tener un único marcador de datos"
    html = plantilla.replace(MARCADOR, datos)

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(html, encoding="utf-8")

    r = traza["resumen"]
    print(f"Pasos: {r['pasos']} · lotes: {r['lotes']} · bloqueos: {r['bloqueos_productor']} · "
          f"rechazados: {r['rechazados']} · duplicados: {r['duplicados']} · atendidos: {r['atendidos']}")
    print(f"Escrito {args.salida} ({args.salida.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
