"""Análisis descriptivo de los campos reales del dataset (auditoría 2026-10-27).

Solo usa lo que viene de Amazon Reviews 2023 (reseñas y catálogo), no lo que inventa el generador:
concentración de la demanda (Gini de reseñas por producto, el punto de referencia natural del Gini
de exposición del recomendador), rating frente a compra verificada, precio y votos útiles, y
evolución temporal.

El notebook de la etapa 9 llama a main (paso 10). Desde la terminal:
    python -m src.etapa_9.analisis_campos_reales
Salida: results/etapa_9/analisis_campos_reales.json
"""

import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats import spearmanr

from .. import config
from .evaluacion_real import gini


def main():
    r = pq.read_table(config.RUTA_RESENAS, columns=["parent_asin", "user_id", "rating", "timestamp",
                                                    "helpful_vote", "verified_purchase"]).to_pandas()
    cat = pd.read_json(config.CARPETA_INTERIM / "etapa_3" / "catalogo.jsonl", lines=True)
    res = {}
    por_producto = r.groupby("parent_asin").size()
    res["gini_resenas_por_producto"] = gini(por_producto.to_numpy())
    orden = np.sort(por_producto.to_numpy())[::-1]
    res["fraccion_resenas_top1pct_productos"] = float(orden[: max(1, len(orden) // 100)].sum() / orden.sum())
    res["gini_resenas_por_usuario"] = gini(r.groupby("user_id").size().to_numpy())
    res["rating_medio"] = float(r["rating"].mean())
    res["distribucion_rating"] = {str(int(k)): float(v) for k, v in r["rating"].value_counts(normalize=True).sort_index().items()}
    res["rating_medio_verificada"] = float(r.loc[r.verified_purchase, "rating"].mean())
    res["rating_medio_no_verificada"] = float(r.loc[~r.verified_purchase, "rating"].mean())
    res["helpful_medio_por_rating"] = {str(int(k)): float(v) for k, v in r.groupby("rating")["helpful_vote"].mean().items()}
    anio = pd.to_datetime(r["timestamp"], unit="ms").dt.year
    res["resenas_por_anio"] = {str(k): int(v) for k, v in anio.value_counts().sort_index().items()}
    m = cat.dropna(subset=["precio"])
    m = m[m["rating_number"] >= 20]
    res["productos_con_precio_y_20_ratings"] = int(len(m))
    res["spearman_precio_rating"] = float(spearmanr(m["precio"], m["average_rating"]).correlation)
    res["spearman_precio_popularidad"] = float(spearmanr(m["precio"], m["rating_number"]).correlation)
    top = cat.groupby("categoria_hoja")["rating_number"].sum().sort_values(ascending=False)
    res["top10_categorias_por_ratings"] = {k: int(v) for k, v in top.head(10).items()}
    res["categorias_hoja"] = int(cat["categoria_hoja"].nunique())
    res["tiendas"] = int(cat["store"].nunique())
    ruta = config.RAIZ / "results" / "etapa_9" / "analisis_campos_reales.json"
    ruta.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return res


if __name__ == "__main__":
    main()
