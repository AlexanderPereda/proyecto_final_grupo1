"""Configuración de pytest: agrega la raíz del proyecto al sys.path para importar src/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
