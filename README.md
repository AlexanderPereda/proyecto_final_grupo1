# Proyecto Final — Grupo 1

**Estructuras de Datos Avanzadas Orquestadas para un Pipeline Eficiente de Procesamiento de Data Streams**

UTEC — Maestría en Ciencia de Datos e Inteligencia Artificial
Estructuras de Datos y Algoritmos — 2026-2 · Prof. Heider Sánchez

## Descripción

Pipeline de procesamiento de streams de 10 etapas aplicado a un **sistema de comercio electrónico** (Escenario B):
priorización de órdenes, consulta de productos, detección de posible fraude, productos relacionados,
análisis de co-compra y servicio al usuario final.

- **Dataset base:** por confirmar
- **Enunciado:** [docs/proyecto-eda-2026-2.pdf](docs/proyecto-eda-2026-2.pdf)

## Integrantes y roles

Cada etapa del pipeline está a cargo de un responsable principal, encargado de su implementación
y de la redacción de la sección correspondiente en el informe final. El resto del equipo actúa
como revisor crítico de dicho trabajo.

| Integrante | Etapas | Responsabilidad principal |
| --- | --- | --- |
| Cristina | 0 – 2 | Ingesta y buffer · Preprocesamiento por lotes · Cola con prioridades |
| Carlos | 3 – 5 | Almacenamiento, lookup y caché · Indexación B+Tree · Índice multidimensional y KNN exacto |
| Aris | 6 | Filtrado probabilístico (Bloom Filter + Count-Min Sketch) |
| Axel | 7 | Similitud aproximada (conjuntos + ANN vectorial) |
| Jesús | 8 – 9 | Grafo de dominio (influencia + comunidades) · Servicio y visualización |

## Estructura del repositorio

    data/        Datos locales (no versionados)
    notebooks/   Notebooks de exploración y experimentos
    docs/        Enunciado e informe final
