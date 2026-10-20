"""Contrato de evento compartido por todas las etapas del pipeline."""

from dataclasses import dataclass


@dataclass(slots=True)
class Event:
    event_id: str
    event_type: str            # search | view | cart | purchase | review | return | payment_alert
    user_id: str
    parent_asin: str | None    # None en las búsquedas
    order_id: str | None
    order_type: str | None     # express | standard | scheduled
    price: float | None
    payment_id: str | None
    device_id: str | None
    ts: float                  # segundos Unix
    category: str | None = None     # categoría canónica (etapas 3 y 7)
    query: str | None = None        # texto de búsqueda (autocompletado, etapa 9)
    rating: float | None = None     # solo en reseñas
    priority: int | None = None     # lo asigna la etapa 2
    deadline: float | None = None   # lo asigna la etapa 2
