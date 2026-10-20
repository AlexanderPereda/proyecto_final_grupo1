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

## Integrantes y roles

Cada etapa del pipeline está a cargo de un responsable principal, encargado de su implementación
y de la redacción de la sección correspondiente en el informe final. El resto del equipo actúa
como revisor crítico de dicho trabajo.

| Integrante | Etapas | Responsabilidad principal | Rama |
| --- | --- | --- | --- |
| Cristina | 0 – 2 | Ingesta y buffer · Preprocesamiento por lotes · Cola con prioridades | `etapa-0-2-cristina` |
| Carlos | 3 – 5 | Almacenamiento, lookup y caché · Indexación B+Tree · Índice multidimensional y KNN exacto | `etapa-3-5-carlos` |
| Aris | 6 | Filtrado probabilístico (Bloom Filter + Count-Min Sketch) | `etapa-6-aris` |
| Axel | 7 | Similitud aproximada (conjuntos + ANN vectorial) | `main` |
| Jesús | 8 – 9 | Grafo de dominio (influencia + comunidades) · Servicio y visualización | `etapa-8-9-jesus` |

Axel es el mantenedor principal del repositorio.

## Estructura del repositorio

```text
data/        Datos locales (no versionados)
notebooks/   Notebooks de exploración y experimentos
docs/        Enunciado e informe final
templates/   Plantillas estándar del equipo (commits, handoffs, etc.)
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
- Ningún integrante trabaja, hace commit ni hace push en `main` ni en la rama de otro integrante.

### Integración a `main`

- El mantenedor principal revisa el trabajo de cada rama y, una vez aprobado, lo integra a `main`.
- Cuando `main` recibe cambios nuevos, cada integrante los incorpora a su rama propia.

### Archivos que no se suben al repositorio

- Los datos de `data/` y el entorno `.venv/` no se suben. El archivo `.gitignore` ya los excluye.
