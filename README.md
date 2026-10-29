# Proyecto Final — Grupo 1

**Estructuras de Datos Avanzadas Orquestadas para un Pipeline Eficiente de Procesamiento de Data Streams**

UTEC — Maestría en Ciencia de Datos e Inteligencia Artificial
Estructuras de Datos y Algoritmos — 2026-2 · Prof. Heider Sánchez

## Descripción

Pipeline de procesamiento de streams de 10 etapas aplicado a un **sistema de comercio electrónico** (Escenario B):
priorización de órdenes, consulta de productos, detección de posible fraude, productos relacionados,
análisis de co-compra y servicio al usuario final.

- **Dataset base:** [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/) (McAuley-Lab), categoría `Office_Products`. Se descarga y se convierte a Parquet con [notebooks/00-download-data.ipynb](notebooks/00-download-data.ipynb).
- **Enunciado:** [docs/proyecto-eda-2026-2.pdf](docs/proyecto-eda-2026-2.pdf)

## Pipeline

Las etapas se conectan en lote por archivos: cada una lee la salida completa de las anteriores en `data/interim/<CATEGORIA>/` y escribe la suya. La categoría y la semilla (42) están en [src/config.py](src/config.py).

```text
Etapa 0  Generador y ColaCircular con backpressure, sin descarte ──► etapa0_lotes.jsonl
   │
Etapa 1  Preprocesador: validación, normalización y deduplicación ──► etapa1_lotes.jsonl
   │
Etapa 2  ColaPrioridadEventos (MinHeap con aging) ──► etapa2_eventos.jsonl
   │
   ├──► Etapa 3  TablaHash, AlmacenCatalogo, caché LRU, estado y matriz usuario-producto
   │       ├──► Etapa 4  B+Tree de precios sobre el catálogo
   │       │       └──► Etapa 5  vectores de atributos y KD-Tree (catálogo, estado y B+Tree)
   │       └──► Etapa 7  MinHash + LSH sobre la matriz y HNSW sobre el catálogo
   ├──► Etapa 6  Bloom Filter y Count-Min Sketch ──► alertas y órdenes bloqueadas
   │       ├──► Etapa 7  sin los usuarios bloqueados
   │       └──► Etapa 8  sin las órdenes bloqueadas
   └──► Etapa 8  grafo de co-compra, PageRank y Louvain

Etapa 9  ServicioEcommerce ◄── etapa 2: ventas y actividad del panel
                           ◄── etapa 3: fichas, stock y disponibilidad
                           ◄── etapa 4: filtro por rango de precio
                           ◄── etapa 5: arranque en frío
                           ◄── etapa 6: alertas y compras bloqueadas
                           ◄── etapa 7: candidatos y señales S_sim y S_rel
                           ◄── etapa 8: candidatos, señales S_co y S_pr y comunidad
   └──► Trie radix, índice invertido, TopKHeap y scoring híbrido ──► dashboard
```

| Etapa | Estructuras | Código | Notebook | Informe |
| --- | --- | --- | --- | --- |
| 0. Ingesta y buffer | Generador del stream y ColaCircular con backpressure | [src/etapa_0/](src/etapa_0/) | [etapa0_ingesta_buffer](notebooks/etapa0_ingesta_buffer.ipynb) | [Etapas 0 a 2](docs/informe_etapa_0_1_2.md) |
| 1. Preprocesamiento | Validación, normalización, merge sort, deduplicación y búsqueda binaria | [src/etapa_1/](src/etapa_1/) | [etapa1_preprocesamiento](notebooks/etapa1_preprocesamiento.ipynb) | [Etapas 0 a 2](docs/informe_etapa_0_1_2.md) |
| 2. Cola con prioridades | MinHeap con aging | [src/etapa_2/](src/etapa_2/) | [etapa2_cola_prioridades](notebooks/etapa2_cola_prioridades.ipynb) | [Etapas 0 a 2](docs/informe_etapa_0_1_2.md) |
| 3. Almacenamiento y caché | TablaHash, catálogo en disco, caché LRU y matriz usuario × producto | [src/etapa_3/](src/etapa_3/) | [etapa3_almacenamiento_cache](notebooks/etapa3_almacenamiento_cache.ipynb) | [Etapas 3 a 5](docs/informe_etapa_3_4_5.md) |
| 4. Indexación | B+Tree de precios en disco | [src/etapa_4/](src/etapa_4/) | [etapa4_indexacion_bplustree](notebooks/etapa4_indexacion_bplustree.ipynb) | [Etapas 3 a 5](docs/informe_etapa_3_4_5.md) |
| 5. Índice multidimensional | KD-Tree y KNN exacto con filtro por categoría | [src/etapa_5/](src/etapa_5/) | [etapa5_kdtree_knn](notebooks/etapa5_kdtree_knn.ipynb) | [Etapas 3 a 5](docs/informe_etapa_3_4_5.md) |
| 6. Filtrado probabilístico | Bloom Filter y Count-Min Sketch por ventanas | [src/etapa_6/](src/etapa_6/) | [etapa6_filtrado_probabilistico](notebooks/etapa6_filtrado_probabilistico.ipynb) | [Etapa 6](docs/informe_etapa_6.md) |
| 7. Similitud aproximada | MinHash + LSH y HNSW | [src/etapa_7/](src/etapa_7/) | [etapa7_similitud_aproximada](notebooks/etapa7_similitud_aproximada.ipynb) | [Etapa 7](docs/informe_etapa_7.md) |
| 8. Grafo de dominio | Grafo CSR de co-compra, PageRank, Louvain y Label Propagation | [src/etapa_8/](src/etapa_8/) | [etapa8_grafo_dominio](notebooks/etapa8_grafo_dominio.ipynb) | [Etapa 8](docs/informe_etapa_8.md) |
| 9. Servicio y visualización | Trie radix, índice invertido, TopKHeap y scoring híbrido | [src/etapa_9/](src/etapa_9/) | [etapa9_servicio_visualizacion](notebooks/etapa9_servicio_visualizacion.ipynb) | [Etapa 9](docs/informe_etapa_9.md) |

La traza de un lote por las diez etapas está en [notebooks/demo_pipeline.ipynb](notebooks/demo_pipeline.ipynb).

## Ejecución

### Entorno

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; en Linux o macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Los notebooks se ejecutan con el kernel del entorno `.venv` (Python 3.12), desde Jupyter o VS Code, o desde la terminal:

```bash
jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=-1 notebooks/<notebook>.ipynb
```

### Orden de los notebooks

Cada notebook lee las salidas de los anteriores, así que se ejecutan en este orden. Las salidas quedan en `data/interim/Office_Products/`.

| Orden | Notebook | Salida |
| --- | --- | --- |
| 1 | `00-download-data.ipynb` | `data/raw/`: reseñas y metadatos en JSONL y Parquet |
| 2 | `etapa0_ingesta_buffer.ipynb` | `etapa0_lotes.jsonl`, `etapa0_inyecciones.json` y `listas_senalados.json` |
| 3 | `etapa1_preprocesamiento.ipynb` | `etapa1_lotes.jsonl`, `etapa1_rechazados.jsonl` y `etapa1_metricas.json` |
| 4 | `etapa2_cola_prioridades.ipynb` | `etapa2_eventos.jsonl` |
| 5 | `etapa3_almacenamiento_cache.ipynb` | `etapa_3/`: catálogo en disco, estado de los productos y matriz usuario-producto |
| 6 | `etapa4_indexacion_bplustree.ipynb` | `etapa_4/indice_precio.bpt` |
| 7 | `etapa5_kdtree_knn.ipynb` | `etapa_5/`: vectores de atributos y vecinos |
| 8 | `etapa6_filtrado_probabilistico.ipynb` | `etapa_6/`: alertas, compras y órdenes bloqueadas |
| 9 | `etapa7_similitud_aproximada.ipynb` | `etapa_7/`: relacionados y similares |
| 10 | `etapa8_grafo_dominio.ipynb` | `etapa_8/`: grafos de co-compra y co-reseñas, PageRank y comunidades |
| 11 | `etapa9_servicio_visualizacion.ipynb` | `etapa_9/` y `visualizador/dashboard_servicio.html` |
| 12 | `demo_pipeline.ipynb` | Ninguna: sigue un lote de 200 registros y dos eventos por las diez etapas |

Los experimentos guardan sus tablas y gráficos en `results/etapa_<n>/`. Desde la etapa 1, cada notebook reutiliza las tablas que ya existen; con `REEJECUTAR_EXPERIMENTOS = True` las vuelve a medir.

### Experimentos desde la terminal

Los notebooks 8 y 9 llaman a sus módulos, que también se ejecutan solos desde la raíz del repositorio. Las mediciones de la etapa 7 que pidió la [revisión técnica](docs/revision_tecnica.md) solo se ejecutan desde la terminal. Todos necesitan las salidas de los notebooks anteriores.

| Comando | Salida |
| --- | --- |
| `python -m src.etapa_7.mediciones_revision [recall] [fuerza_bruta] [indice_invertido] [construccion]` | `results/etapa_7/`: recall de HNSW frente al coseno exacto en TF-IDF, fuerza bruta en float32, índice invertido exacto y construcción con 100 repeticiones |
| `python -m src.etapa_8.benchmark` | `results/etapa_8/`: grillas de α y γ, escala de PageRank y de comunidades, referencia NetworkX, estabilidad y exclusión de la etapa 6 |
| `python -m src.etapa_8.escala_stream [N ...]` | `results/etapa_8/escala_stream.csv`: grafo construido desde el stream, de 150 000 a 12.8 M reseñas |
| `python -m src.etapa_8.grafo_resenas_reales [--grilla]` | `results/etapa_8/grafo_resenas_reales.json`: validación con el grafo de co-reseñas reales |
| `python -m src.etapa_9.benchmark` | `results/etapa_9/`: escala del Trie, autocompletado difuso y Top-K frente al ordenamiento |
| `python -m src.etapa_9.evaluacion_scoring` | `results/etapa_9/calibracion_*`: calibración de los pesos del scoring |
| `python -m src.etapa_9.evaluacion_real` | `results/etapa_9/evaluacion_real*`: evaluación con un objetivo real |
| `python -m src.etapa_9.analisis_campos_reales` | `results/etapa_9/analisis_campos_reales.json` |
| `python -m src.etapa_9.service` | La etapa 9 completa: `data/interim/Office_Products/etapa_9/` y el dashboard |
| `python -m src.etapa_9.dashboard` | Solo vuelve a generar `visualizador/dashboard_servicio.html` |

### Demo del pipeline

[notebooks/demo_pipeline.ipynb](notebooks/demo_pipeline.ipynb), con las funciones de [src/demo_pipeline.py](src/demo_pipeline.py), toma 200 registros del stream de la etapa 0 y sigue una compra legítima y una de fraude por las diez etapas: prioridad, alerta, relacionados, PageRank y recomendación. Necesita las salidas de todos los notebooks anteriores y se ejecuta en unos minutos.

### Visualizadores

Son archivos HTML con los datos incrustados, que se abren con doble clic, sin servidor.

| Archivo | Contenido | Cómo se genera |
| --- | --- | --- |
| `visualizador/pipeline.html` | Traza paso a paso de las etapas 0 a 2 | `python scripts/generar_visualizador.py`, con la traza de `src/trace.py` |
| `visualizador/dashboard_servicio.html` | Recomendación, autocompletado, ventas y actividad, alertas de fraude y arquitectura | Al final de la etapa 9, o con `python -m src.etapa_9.dashboard` |
| `visualizador/mapa_escenario_b.html` | Mapa del escenario B por etapa: entrada, tratamiento, hallazgos y salida | Archivo estático |

### Tests

```bash
python -m pytest tests
```

Hay un módulo de pruebas por etapa en `tests/etapa_<n>/`. [tests/conftest.py](tests/conftest.py) agrega la raíz del repositorio al `sys.path` para importar `src/`.

## Integrantes y roles

Cada etapa del pipeline está a cargo de un responsable principal, encargado de su implementación
y de la redacción de la sección correspondiente en el informe final. El resto del equipo actúa
como revisor crítico de dicho trabajo.

| Integrante | Etapas | Responsabilidad principal | Aporte concreto | Rama |
| --- | --- | --- | --- | --- |
| Cristina | 0 – 2 | Ingesta y buffer · Preprocesamiento por lotes · Cola con prioridades | Notebook de descarga del dataset; generador del stream, ColaCircular, preprocesador y MinHeap con aging en `src/`, con sus tests; configuración central y métricas de desorden temporal; traza y visualizador de las etapas 0 a 2; notebooks e informe de las etapas 0 a 2 | `etapa-0-2-cristina` |
| Carlos | 3 – 5 | Almacenamiento, lookup y caché · Indexación B+Tree · Índice multidimensional y KNN exacto | TablaHash, catálogo en disco, caché LRU y matriz usuario-producto; B+Tree en disco; KD-Tree por categoría y KNN exacto; sus tests y notebooks; adaptación de las etapas 3 y 4 al stream de 10 millones de eventos; informe de las etapas 3 a 5 | `etapa-3-5-carlos` |
| Aris | 6 | Filtrado probabilístico (Bloom Filter + Count-Min Sketch) | Bloom Filter, Count-Min Sketch por ventanas y detector de fraude, con sus tests; notebook e informe de la etapa 6 | `etapa-6-aris` |
| Axel | 7 | Similitud aproximada (conjuntos + ANN vectorial) | Estructura del repositorio, plantillas, preparación del dataset y README; etapa 7 completa: módulo, tests, notebook, grillas e informe; reorganización por etapa de `src/`, `tests/` y `results/`, y corrección y ejecución de los notebooks 0 a 6 sobre el flujo integrado; lista conocida y ejecución sobre el stream en la etapa 6; integración de las ramas en `main`; revisión técnica, corrección y ejecución de las etapas 8 y 9, y demo del pipeline | `etapa-7-axel` |
| Jesús | 8 – 9 | Grafo de dominio (influencia + comunidades) · Servicio y visualización | Grafo CSR de co-compra con PageRank, Louvain y Label Propagation; servicio con Trie radix, índice invertido, TopKHeap, scoring híbrido y dashboard; tests, notebooks, experimentos, informes y auditoría de las etapas 8 y 9; mapa del escenario B | `etapa-8-9-jesus` |

Axel es el mantenedor principal del repositorio.

**Lectura del historial.**

- Axel aparece con dos identidades de autor, `Axel Pereda` y `Axel Pereda Escalante`, con el mismo correo. [.mailmap](.mailmap) las une, así que `git shortlog -sn` las cuenta como una sola.
- Los commits de Axel en las etapas 0 a 6 son de integración: la reorganización por etapa y la corrección y ejecución de los notebooks sobre el flujo integrado. La implementación de cada etapa está en los commits de su responsable.
- Las etapas 8 y 9 entraron en un solo commit de Jesús, el 27 de octubre. Las correcciones posteriores, que salen de la [revisión técnica](docs/revision_tecnica.md), son de Axel.

## Estructura del repositorio

```text
.
├── README.md
├── requirements.txt          Dependencias con versión fija
├── .mailmap                  Une las identidades de autor en git shortlog
├── data/                     Datos locales, no versionados
│   ├── raw/                  Dataset descargado, en JSONL y Parquet
│   └── interim/<CATEGORIA>/  Salidas de cada etapa, que leen las siguientes
├── docs/
│   ├── proyecto-eda-2026-2.pdf   Enunciado
│   ├── informe_etapa_*.md        Informes de las etapas 0-1-2, 3-4-5, 6, 7, 8 y 9
│   ├── auditoria_etapas_8_9.md   Auditoría de las etapas 8 y 9
│   ├── revision_tecnica.md       Revisión del proyecto frente a la rúbrica
│   ├── plan_cascada.md           Plan de cambios que sale de la revisión, con su estado
│   └── img/                      Figuras del informe de las etapas 0 a 2
├── notebooks/                Descarga del dataset, un notebook por etapa y la demo del pipeline
├── results/etapa_0 … etapa_9/    Tablas CSV y gráficos de los experimentos
├── scripts/                  Generador del visualizador de las etapas 0 a 2 y su plantilla
├── src/
│   ├── config.py             Categoría, semilla y rutas compartidas
│   ├── events.py             Contrato de evento común a todas las etapas
│   ├── metricas.py           Métricas de desorden temporal del stream
│   ├── trace.py              Traza de las etapas 0 a 2 para el visualizador
│   ├── demo_pipeline.py      Funciones de la demo del pipeline
│   └── etapa_0 … etapa_9/    Implementación de cada etapa
├── templates/                Plantillas del equipo, como el formato de los commits
├── tests/etapa_0 … etapa_9/      Pruebas con pytest
└── visualizador/             Visualizador de las etapas 0 a 2, dashboard del servicio y mapa del escenario B
```

## Indicaciones para los integrantes

### Configuración inicial

- Cada integrante clona el repositorio en su equipo.
- Git se configura con el nombre del integrante y con el mismo correo de su cuenta de GitHub, porque el historial de contribuciones forma parte de la nota individual.
- El entorno virtual se llama `.venv`, se crea en la raíz del repositorio y usa Python 3.12.
- Los paquetes se instalan con pip a partir de `requirements.txt`.
- Los notebooks se ejecutan con el kernel del entorno `.venv`.
- Los datos no se versionan. Cada integrante los genera ejecutando el notebook [notebooks/00-download-data.ipynb](notebooks/00-download-data.ipynb), que deja los archivos Parquet en `data/raw/`.

### Trabajo con ramas

- Cada integrante trabaja únicamente en la rama con su propio nombre, indicada en la tabla de [Integrantes y roles](#integrantes-y-roles).
- Antes de empezar cada sesión de trabajo, el integrante comprueba que está en su rama y trae los últimos cambios de esa rama desde GitHub.
- Todo commit se realiza en la rama propia y sigue el formato de [templates/commit.md](templates/commit.md).
- Todo push se realiza a la rama propia.
- Ningún integrante trabaja, hace commit ni hace push en la rama de otro integrante.
- En `main` solo se trabaja cuando el mantenedor principal lo indica, y solo sobre el informe propio en `docs/`. Antes de cada push a `main` se ejecuta `git pull origin main`; si aparece un conflicto, se detiene el trabajo y se avisa al mantenedor.

### Integración a `main`

- El mantenedor principal revisa el trabajo de cada rama y, una vez aprobado, lo integra a `main`.
- Cuando `main` recibe cambios nuevos, cada integrante los incorpora a su rama propia.

### Archivos que no se suben al repositorio

- Los datos de `data/` y el entorno `.venv/` no se suben. El archivo `.gitignore` ya los excluye.
