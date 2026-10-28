"""
Generador del notebook notebooks/etapa9_servicio_visualizacion.ipynb
Sigue el estándar y formato de notebooks/etapa8_grafo_dominio.ipynb.
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def crear_notebook():
    cells = []

    def md(text):
        cells.append({
            "cell_type": "markdown",
            "metadata": {},
            "source": [line + "\n" for line in text.strip().split("\n")]
        })

    def code(text):
        cells.append({
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [line + "\n" for line in text.strip().split("\n")]
        })

    # PORTADA
    md("""
# Etapa 9. Servicio y Visualización (Trie, Top-K y Dashboard Interactivo)

Proyecto EDA 2026-2 · Grupo 1 · Escenario B (comercio electrónico)

Esta etapa materializa la capa de servicio de cara al usuario final y unifica de manera observable las diez etapas del pipeline. Implementa:

1. **Autocompletado de prefijos en tiempo real:** Estructura de árbol de prefijos (**Trie**) con caché local Top-K precomputada en cada nodo, garantizando latencias constantes $O(L)$ de nivel sub-milisegundo.
2. **Ranking Top-K multicriterio con MinHeap acotado:** Algoritmo de selección asintóticamente óptimo en $O(N \\log K)$ frente al ordenamiento completo $O(N \\log N)$, combinando similitud vectorial de atributos (Etapa 7 HNSW), similitud de comportamiento (Etapa 7 MinHash + LSH), autoridad espectral (Etapa 8 PageRank) y afinidad de co-compra (Etapa 8 CSR).
3. **Monitoreo de seguridad y fraude:** Integración del panel de alertas probabilísticas de la Etapa 6 (Bloom Filter + Count-Min Sketch).
4. **Dashboard y visualización interactiva:** Interfaz web completa disponible en `visualizador/dashboard_servicio.html`.
""")

    # PASO 1
    md("""
## Paso 1. Configuración y entrada

Se configuran las rutas del proyecto, la semilla determinista (`SEED = 42`) y la detección de plataforma.
""")

    code("""
import gc
import json
import os
import platform
import sys
import time
from pathlib import Path

from IPython.display import display
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Raíz del proyecto
RAIZ = Path.cwd()
if RAIZ.name == "notebooks":
    RAIZ = RAIZ.parent
    os.chdir(RAIZ)
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from src.etapa_9.service import (
    IndiceInvertido,
    NodoTrie,
    ScoringHibrido,
    ServicioEcommerce,
    TopKHeap,
    Trie,
    ejecutar_etapa,
    normalizar_texto,
    tokenizar,
)

CATEGORIA = "Office_Products"
DIR_INTERIM = RAIZ / "data" / "interim" / CATEGORIA
DIR_ETAPA9 = DIR_INTERIM / "etapa_9"
DIR_RESULTADOS = RAIZ / "results" / "etapa_9"

SEED = 42
PLATAFORMA = {
    "procesador": platform.processor() or platform.machine(),
    "python": platform.python_version(),
    "sistema": platform.system(),
}
print("Entorno configurado:", PLATAFORMA)
""")

    # PASO 2
    md("""
## Paso 2. Estructura de Datos Trie (Árbol de Prefijos)

El **Trie** (Fredkin, 1960) organiza el vocabulario del catálogo estructurado como un árbol donde cada arista representa un carácter. 

**Optimización de latencia con Top-K Caché Local:**
En un Trie estándar, resolver un prefijo de longitud $L$ requiere descender hasta el nodo del prefijo en $O(L)$ y luego recorrer exhaustivamente el subárbol descendente para encontrar las mejores palabras ($O(L + |\\text{subárbol}| \\log K)$). Para garantizar respuesta instantánea (*search-as-you-type*), cada nodo mantiene una caché local con las $M$ sugerencias de mayor frecuencia que transitan por él, reduciendo la complejidad de consulta estrictamente a $O(L)$, independiente del tamaño del vocabulario.
""")

    code("""
# 2.1 Demostración de Trie con Top-K Caché
trie_demo = Trie(capacidad_cache=5)

# Insertar términos con frecuencias de popularidad
terminos_muestra = [
    ("toner brother tn436", 500.0, {"asin": "B0BF98LWMZ", "cat": "Toner"}),
    ("toner hp laserjet", 420.0, {"asin": "B01F3M964Y", "cat": "Toner"}),
    ("toner canon imageclass", 310.0, {"asin": "B00DY8P67W", "cat": "Toner"}),
    ("tinta epson ecotank", 290.0, {"asin": "B07KPL8Q91", "cat": "Tinta"}),
    ("tijeras de oficina scotch", 180.0, {"asin": "B00006IE5D", "cat": "Papelería"}),
    ("papel bond 500 hojas", 400.0, {"asin": "B001PMHZZ6", "cat": "Papel"}),
    ("papel fotográfico glossy", 150.0, {"asin": "B09MHP71BG", "cat": "Papel"}),
]

for term, freq, meta in terminos_muestra:
    trie_demo.insertar(term, frecuencia=freq, meta=meta)

print(f"Términos indexados en Trie demo: {len(trie_demo)}")

# Consultar prefijo 'ton'
sugerencias_ton = trie_demo.autocompletar("ton", k=3)
print("\\nSugerencias de autocompletado para prefijo 'ton':")
display(pd.DataFrame(sugerencias_ton))

# Consultar prefijo 'pap'
sugerencias_pap = trie_demo.autocompletar("pap", k=3)
print("\\nSugerencias de autocompletado para prefijo 'pap':")
display(pd.DataFrame(sugerencias_pap))
""")

    # PASO 3
    md("""
## Paso 3. Estructura de Datos Top-K con MinHeap

Para clasificar los $K$ mejores productos entre $N$ candidatos:
- Ordenar la lista completa con `sorted()` cuesta $O(N \\log N)$ tiempo y $O(N)$ espacio.
- La estructura **TopKHeap** mantiene un `MinHeap` de tamaño acotado $K$. Al evaluar cada candidato con score $s$:
  - Si el heap tiene menos de $K$ elementos, se inserta en $O(\\log K)$.
  - Si ya contiene $K$ elementos y $s > \\text{raíz.score}$, se reemplaza la raíz en $O(\\log K)$.
  - De lo contrario, se descarta en $O(1)$.
- Complejidad total: **$O(N \\log K)$** en tiempo y **$O(K)$** en espacio auxiliar. Cuando $N = 10^6$ y $K = 10$, $N \\log K \\approx 3.3 \\times 10^6$ operaciones frente a $N \\log N \\approx 20 \\times 10^6$ de un ordenamiento completo, logrando una aceleración de más de 3×.
""")

    code("""
# 3.1 Demostración de TopKHeap
topk_demo = TopKHeap(k=3)
candidatos_demo = [
    ("Producto Alfa", 0.42),
    ("Producto Beta", 0.89),
    ("Producto Gamma", 0.15),
    ("Producto Delta", 0.96),
    ("Producto Epsilon", 0.73),
]

for item, score in candidatos_demo:
    topk_demo.agregar(item, score)

print("Top 3 elementos seleccionados:")
for score, item in topk_demo.obtener_top():
    print(f"  Score: {score:.2f} -> {item}")
""")

    # PASO 4
    md("""
## Paso 4. Función de Scoring Híbrido Multicriterio

La relevancia de un producto complementario $p$ respecto a una consulta o producto base $q$ se calcula mediante una combinación lineal convexa de cuatro dimensiones del pipeline:

$$S(p \\mid q) = w_{\\text{sim}} \\cdot S_{\\text{sim}}(p, q) + w_{\\text{rel}} \\cdot S_{\\text{rel}}(p, q) + w_{\\text{pr}} \\cdot \\widetilde{S}_{\\text{pr}}(p) + w_{\\text{co}} \\cdot S_{\\text{co}}(p, q)$$

donde:
- $S_{\\text{sim}}(p, q) = \\max(0, 1 - d_{\\text{HNSW}}(p, q))$: Similitud de atributos textuales y categoría (Etapa 7).
- $S_{\\text{rel}}(p, q) = J(p, q)$: Co-ocurrencia de usuarios estimada por MinHash + LSH (Etapa 7).
- $\\widetilde{S}_{\\text{pr}}(p) = \\frac{\\text{PageRank}(p)}{\\max \\text{PageRank}}$: Autoridad espectral global en el grafo (Etapa 8).
- $S_{\\text{co}}(p, q) = \\frac{w_{pq}}{\\max_k w_{pk}}$: Afinidad directa de co-compra en la matriz CSR (Etapa 8).
- Ponderación calibrada: $w_{\\text{sim}} = 0.35, w_{\\text{rel}} = 0.25, w_{\\text{pr}} = 0.20, w_{\\text{co}} = 0.20$.
""")

    code("""
# 4.1 Evaluación de Scoring Híbrido
scoring = ScoringHibrido(
    peso_similitud=0.35,
    peso_relacionados=0.25,
    peso_pagerank=0.20,
    peso_cocompra=0.20,
)

s1 = scoring.calcular_score(similitud_atributos=0.85, jaccard_comportamiento=0.12, pagerank_normalizado=0.90, afinidad_cocompra=0.75)
s2 = scoring.calcular_score(similitud_atributos=0.30, jaccard_comportamiento=0.01, pagerank_normalizado=0.10, afinidad_cocompra=0.00)

print(f"Score Producto Relevante y Conectado: {s1:.4f}")
print(f"Score Producto Poco Relacionado:      {s2:.4f}")
""")

    # PASO 5
    md("""
## Paso 5. Orquestación del Servicio de Comercio Electrónico

La clase `ServicioEcommerce` integra los artefactos generados en las etapas 3, 6, 7 y 8, proporcionando una API unificada en memoria principal.
""")

    code("""
# 5.1 Carga del Servicio
servicio = ServicioEcommerce(RAIZ / "data" / "interim", categoria=CATEGORIA)
servicio.cargar_datos()  # catálogo completo (antes 50 000 inserciones, ~3 % del catálogo)

print(f"Términos en Trie: {len(servicio.trie):,}")
print(f"Productos con PageRank: {len(servicio._pagerank):,}")
print(f"Productos con Similares HNSW: {len(servicio._similares):,}")
print(f"Productos con Relacionados LSH: {len(servicio._relacionados):,}")
""")

    code("""
# 5.2 Recomendación Híbrida Top-K para el Producto Ancla Top-1 (B0BF98LWMZ)
recs_top1 = servicio.recomendar("B0BF98LWMZ", k=8)
df_recs = pd.DataFrame(recs_top1)[[
    "rango", "parent_asin", "score_hibrido", "similitud_atributos",
    "jaccard_comportamiento", "afinidad_cocompra", "pagerank", "titulo", "precio"
]]
print("Top-K Recomendaciones Híbridas para Brother TN436:")
display(df_recs)
""")

    # PASO 6
    md("""
## Paso 6. Centro de Monitoreo de Fraude (Etapa 6)

El servicio consulta las decisiones emitidas por el detector probabilístico de la etapa 6 (Count-Min Sketch + Bloom Filter), clasificando las operaciones en bloqueos automáticos, revisiones manuales y transacciones aprobadas.
""")

    code("""
resumen_f = servicio.resumen_fraude()
print("Resumen de Seguridad:")
print(f"  Total Alertas: {resumen_f['resumen'].get('total_alertas', 0):,}")
print(f"  Bloqueadas:    {resumen_f['resumen'].get('bloqueadas', 0):,} "
      f"(precisión medida con fraude_real: {resumen_f['resumen'].get('precision_bloqueos', 0):.4f})")
print(f"  Retenidas:     {resumen_f['resumen'].get('retenidas', 0):,}")
print(f"  Aprobadas:     {resumen_f['resumen'].get('aprobadas', 0):,}")

print("\\nMuestra de Alertas Procesadas:")
display(pd.DataFrame(resumen_f["muestra"]).head(6))
""")

    # PASO 7
    md("""
## Paso 7. Salidas de la Etapa 9 y Visualizador Interactivo

Se verifican los artefactos exportados por la etapa 9:
1. `metricas.json`: Latencias p50 y p95, throughput y tamaños de índices.
2. `demo_consultas.json`: Consultas precomputadas para la demo.
3. `visualizador/dashboard_servicio.html`: Dashboard web interactivo para usuario final.
""")

    code("""
ruta_metricas = DIR_ETAPA9 / "metricas.json"
ruta_demo = DIR_ETAPA9 / "demo_consultas.json"
ruta_dash = RAIZ / "visualizador" / "dashboard_servicio.html"

for r in (ruta_metricas, ruta_demo, ruta_dash):
    tam_kb = r.stat().st_size / 1024
    print(f"✓ {r.name:30s} | Tamaño: {tam_kb:7.1f} KB")

metricas_etapa9 = json.loads(ruta_metricas.read_text(encoding="utf-8"))
print("\\nMétricas registradas:")
print(json.dumps(metricas_etapa9, indent=2))
""")

    # PASO 8
    md("""
## Paso 8. Experimentos de Escalabilidad

Se analizan los resultados de benchmarking obtenidos en `results/etapa_9/`:
1. **Escalabilidad del Trie:** Tiempo de construcción, memoria y latencia por consulta de prefijo frente al número de términos.
2. **TopKHeap frente a Sorting Completo:** Comparación asintótica $O(N \\log K)$ vs $O(N \\log N)$ hasta $N = 10^6$.
""")

    code("""
# 8.1 Carga de tablas de benchmarking
df_trie = pd.read_csv(DIR_RESULTADOS / "escala_trie.csv")
df_comp = pd.read_csv(DIR_RESULTADOS / "comparacion_sorting.csv")

print("--- ESCALA TRIE ---")
display(df_trie)

print("\\n--- TOP-K HEAP vs SORTING COMPLETO (K=10) ---")
display(df_comp)
""")

    code("""
# 8.2 Gráficas comparativas
fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))

# Trie Latencia vs N
axes[0].plot(df_trie["n_terminos"], df_trie["latencia_p50_us"], "o-", color="#2ca02c", label="p50 (Mediana)")
axes[0].plot(df_trie["n_terminos"], df_trie["latencia_p95_us"], "s--", color="#d62728", label="p95")
axes[0].set_xscale("log")
axes[0].set_title("Trie: Latencia de Autocompletado vs N Términos", fontsize=11)
axes[0].set_xlabel("Número de términos ($N$)", fontsize=10)
axes[0].set_ylabel("Latencia (µs)", fontsize=10)
axes[0].grid(True, which="both", linestyle="--", alpha=0.5)
axes[0].legend(fontsize=9)

# TopK Speedup vs N
axes[1].plot(df_comp["n"], df_comp["speedup_vs_sorted"], "o-", color="#9467bd", linewidth=2)
axes[1].set_xscale("log")
axes[1].set_title("Speedup de TopKHeap frente a sorted() Completo", fontsize=11)
axes[1].set_xlabel("Número de candidatos ($N$)", fontsize=10)
axes[1].set_ylabel("Aceleración relativa (×)", fontsize=10)
axes[1].grid(True, which="both", linestyle="--", alpha=0.5)

fig.suptitle("Etapa 9. Rendimiento Asintótico de Autocompletado y Ranking", fontsize=12)
fig.tight_layout()
plt.show()
""")

    # PASO 8b: AUDITORIA
    md("""
## Paso 8b. Auditoría técnica (2026-10-27)

Cambios verificados en esta sección (detalle en `docs/auditoria_etapas_8_9.md`):
1. **Trie radix.** El Trie de un carácter por nodo creaba 7.2 M nodos para el catálogo completo (90 % unarios, 3.5 GB); la compresión radix deja ~0.72 M con la misma semántica.
2. **Autocompletado difuso.** Damerau-Levenshtein (OSA) sobre el Trie con banda de Ukkonen y poda exacta; solo se activa si la búsqueda exacta no llena $k$.
3. **Búsqueda por índice invertido.** La búsqueda anterior puntuaba $0.5 + 0.5 \\cdot S_{pr}$ (relevancia constante) y completaba con el prefijo `query[:3]`.
4. **Calibración del scoring.** Partición temporal 80/20 de órdenes; ver `results/etapa_9/calibracion_scoring*.csv`.
""")

    code("""
# 8b.1 Tamaño del Trie radix y del índice invertido sobre el catálogo completo
print(f"Términos en el Trie:        {len(servicio.trie):,}")
print(f"Nodos del Trie radix:       {servicio.trie.n_nodos:,}")
print(f"Productos en el índice:     {servicio.indice.n_docs:,}")
print(f"Vocabulario / postings:     {len(servicio.indice.vocabulario):,} / {len(servicio.indice.postings):,}")

# 8b.2 Autocompletado exacto frente a difuso (d = 1)
for consulta in ["stapel", "brotehr", "pilto g2", "scoth tape"]:
    exactas = servicio.trie.autocompletar(consulta, k=3)
    difusas = servicio.autocompletar(consulta, k=3)
    print(f"{consulta!r:14s} exactas={len(exactas)} difusas={[(s['termino'], s.get('distancia', 0)) for s in difusas]}")

display(pd.read_csv(DIR_RESULTADOS / "difuso_trie.csv"))
""")

    code("""
# 8b.3 Búsqueda con corrección ortográfica por token
for consulta in ["toner brother", "toner brtoher", "stapler swinglien"]:
    print(f"{consulta!r} -> {' '.join(servicio.corregir_consulta(consulta))!r}")
    display(pd.DataFrame(servicio.buscar(consulta, k=5))[["rango", "score", "titulo", "tienda"]])
""")

    code("""
# 8b.4 Calibración temporal del scoring híbrido (prueba, Recall@10 por grupo de co-compras del ancla)
cal = pd.read_csv(DIR_RESULTADOS / "calibracion_scoring.csv")
prueba = cal[cal["conjunto"] == "prueba"]
display(prueba.pivot_table(index=["relacionados", "normalizacion_pr", "kappa"],
                           columns="grupo", values="recall@10").round(4))
display(pd.read_csv(DIR_RESULTADOS / "calibracion_lineas_base.csv"))
print(json.dumps(json.loads((DIR_RESULTADOS / "calibracion_scoring_resumen.json").read_text()), indent=2))

servicio.cerrar()
""")

    # PASO 9
    md("""
## Resumen del Pipeline Punta a Punta (Etapas 0 a 9)

| Etapa | Responsabilidad | Estructura Principal | Complejidad | Salida Principal hacia Etapa 9 |
| --- | --- | --- | --- | --- |
| **0** | Ingesta & Buffer | ColaCircular con drop | $O(1)$ | Buffer de eventos de transacciones |
| **1** | Preprocesamiento | Merge Sort + Búsqueda Binaria | $O(N \\log N)$ | Normalización y validación de tipos |
| **2** | Priorización | MinHeap con envejecimiento (aging) | $O(\\log N)$ | Ordenamiento por urgencia y alertas |
| **3** | Almacenamiento | TablaHash + AlmacenCatalogo + CSR | $O(1)$ / $O(nnz)$ | Catálogo en disco y matriz usuario-producto |
| **4** | Indexación | B+Tree en memoria y disco | $O(\\log_B N)$ | Búsquedas por rango de precios |
| **5** | Índice Espacial | KD-Tree & KNN exacto | $O(\\log N)$ / $O(N d)$ | Línea base exacta de vecinos por atributos |
| **6** | Filtrado Probabilístico | Bloom Filter + Count-Min Sketch | $O(k)$ / $O(d)$ | 47 710 alertas y 31 994 órdenes bloqueadas |
| **7** | Similitud Aproximada | MinHash + LSH & HNSW | $O(b+c)$ / $O(\\log N)$ | Similares (atributos) y Relacionados (Jaccard) |
| **8** | Grafo de Dominio | PageRank (Power Iteration) & Louvain | $O(k (\|V\| + \|E\|))$ / $O(N \\log N)$ | Autoridad global (PageRank) y comunidades conexas |
| **9** | Servicio & Visualización | Trie radix con Top-K Caché (+ difuso OSA), índice invertido & TopKHeap | $O(L + k)$ consulta, $O(L + hM)$ inserción / $O(N \\log K)$ | Autocompletado, búsqueda, ranking Top-K y dashboard web |

## Referencias

- **Cormen, T. H., Leiserson, C. E., Rivest, R. L., & Stein, C. (2009).** *Introduction to Algorithms* (3rd ed.). MIT Press. Cap. 6 (Heapsort y colas de prioridad).
- **Fredkin, E. (1960).** Trie memory. *Communications of the ACM*, 3(9), 490-499. https://doi.org/10.1145/367390.367400
- **Knuth, D. E. (1998).** *The Art of Computer Programming, Volume 3: Sorting and Searching* (2nd ed.). Addison-Wesley. Cap. 6.3 (Digital Searching).
- **Manning, C. D., Raghavan, P., & Schütze, H. (2008).** *Introduction to Information Retrieval*. Cambridge University Press. Cap. 3 (Dictionaries and tolerant retrieval).
""")

    nb = {
        "cells": cells,
        "metadata": {
            "language_info": {
                "name": "python",
                "version": "3.12.13"
            },
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3"
            }
        },
        "nbformat": 4,
        "nbformat_minor": 4
    }

    destino = RAIZ / "notebooks" / "etapa9_servicio_visualizacion.ipynb"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=2, ensure_ascii=False)
    print(f"Notebook creado con éxito en: {destino}")


if __name__ == "__main__":
    crear_notebook()
