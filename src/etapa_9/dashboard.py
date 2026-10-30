"""Genera visualizador/dashboard_etapa_9_servicio.html con los datos de la etapa 9 incrustados.

El resultado es un solo archivo HTML que se abre con doble clic, sin servidor, como el visualizador
de las etapas 0 a 2 (scripts/generar_visualizador.py). La plantilla está en
src/etapa_9/plantilla_dashboard.html y los datos salen de demo_consultas.json y metricas.json, que
escribe ejecutar_etapa en data/interim/<CATEGORIA>/etapa_9/. ejecutar_etapa llama a este módulo al
terminar; también se puede generar solo el HTML con `python -m src.etapa_9.dashboard`.
"""

import json
from pathlib import Path

from .. import config

CARPETA_ETAPA9 = config.CARPETA_INTERIM / "etapa_9"
RUTA_DEMO = CARPETA_ETAPA9 / "demo_consultas.json"
RUTA_METRICAS = CARPETA_ETAPA9 / "metricas.json"
RUTA_DASHBOARD = config.RAIZ / "visualizador" / "dashboard_etapa_9_servicio.html"
PLANTILLA = Path(__file__).with_name("plantilla_dashboard.html")
MARCADOR = "__DATOS_DASHBOARD__"


def generar_dashboard(ruta_demo=RUTA_DEMO, ruta_metricas=RUTA_METRICAS, destino=RUTA_DASHBOARD):
    """Escribe el dashboard con los datos incrustados y devuelve su ruta."""
    datos = {"demo": json.loads(Path(ruta_demo).read_text(encoding="utf-8")),
             "metricas": json.loads(Path(ruta_metricas).read_text(encoding="utf-8"))}
    # Los datos van como literal de JavaScript dentro de una etiqueta <script>, que admite NaN;
    # "</" se escapa para que un texto como "</script>" en un título no cierre la etiqueta.
    texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    assert plantilla.count(MARCADOR) == 1, "la plantilla debe tener un único marcador de datos"
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(plantilla.replace(MARCADOR, texto), encoding="utf-8")
    return destino


if __name__ == "__main__":
    print(f"Dashboard generado en {generar_dashboard()}")
