# Plan de cambios en cascada

Proyecto EDA 2026-2 · Grupo 1 · Escenario B · Plan del 29 de octubre de 2026

**Origen.** Los cambios salen de la [revisión técnica](revision_tecnica.md). Los números entre paréntesis, como 1.3 o 1.10, son sus puntos.

**Cómo se ordena.** Es una cascada: cada paso se apoya en el anterior y aporta menos que él. Se puede parar después de cualquier paso sabiendo que lo pendiente vale menos que lo ya hecho.

## Estado

| Paso | Cambio | Qué resuelve | Ejecución | Estado |
| --- | --- | --- | --- | --- |
| 1 | Notebooks 8 y 9 y su código | 1.3, 1.4, 1.5 y 1.9; flujo entre etapas (1.6 y 1.10); seis criterios parciales de la rúbrica | 1.5 a 2 h (real: 6 h) | Hecho el 29 de octubre: notebooks 8 y 9 ejecutados; pesos base mantenidos por decisión declarada |
| 2 | Demo de un evento a través de las diez etapas | "No cumple" del componente 2 | Minutos | Hecho el 29 de octubre: `notebooks/demo_pipeline.ipynb` ejecutado, con `src/demo_pipeline.py` |
| 3 | Informes 8 y 9 desde los CSV nuevos | 1.2, postura de 1.1 y documentación profunda de la etapa 8 | Ninguna | Hecho el 29 de octubre: informes 8 y 9 reescritos y postura de 1.1 en el informe 7 |
| 4 | README | "No cumple" del README y tabla de roles | Ninguna | Hecho el 29 de octubre: diagrama, orden de ejecución con comandos, estructura, aporte de cada integrante y `.mailmap` |
| 5 | Textos de las etapas 6 y 7 | 1.7 y 1.8 | Ninguna; medir es opcional | Hecho el 29 de octubre: textos de los informes 6 y 7, y mediciones de recall, fuerza bruta, índice invertido y construcción con 100 repeticiones |
| 6 | Coherencia de las etapas 2 a 5 con el flujo nuevo | Restos de 1.10 y puntos bajos del informe 3-4-5 | Ninguna | Hecho el 29 de octubre: tabla de arquitectura y frases del informe 3-4-5, y etiquetas "uso previsto" de los notebooks 2 a 7 y del informe 7 |
| 7 | Limpieza de baja severidad | Resto de la severidad baja | Ninguna | Hecho el 29 de octubre: referencias a la ejecución de Aris en el notebook 6, respaldos citados por la auditoría y docstring de `src/__init__.py` |

Al cerrar un paso se cambia su estado en esta tabla. El punto de corte recomendado está al final.

## Paso 1. Notebooks 8 y 9 y su código

**Errores de método.**

- 1.3: filtrar el ASIN vacío con `if not asin` en [escala_stream.py:68](../src/etapa_8/escala_stream.py#L68) y confirmar que `""` ya no está en `grafo.asins`.
- 1.4: medir el tiempo en una pasada sin `tracemalloc` y la memoria en otra, en [benchmark.py:41](../src/etapa_8/benchmark.py#L41). Se vuelven a medir `escala_comunidades.csv` y `referencia_networkx.csv`.
- 1.5: convertir la distancia de HNSW con S_sim = 1 − d²/2 en [service.py:915](../src/etapa_9/service.py#L915) y volver a calibrar el scoring.

**Protocolo de medición.**

- 100 repeticiones con media y desviación estándar en las etapas 8 y 9, o una reducción justificada como en las etapas 0 a 7.
- Gráficos log-log con rectas de referencia de la complejidad esperada.
- Estabilidad de las comunidades con varias semillas (NMI o ARI) y la distribución completa de tamaños.
- `escala_stream` dentro del notebook 8, para cubrir el rango de 1K a 10M.

**Flujo entre etapas (1.6 y 1.10).**

- 4 → 9: filtro por rango de precio con el B+Tree.
- 5 → 9: `vecinos.parquet` como candidatos o para el arranque en frío.
- 3 → 9: panel de ventas y actividad con el stock y la disponibilidad.
- 6 → 8: grafo con y sin la exclusión de fraude, con el efecto real que resulte.

**Textos de los notebooks.**

- 1.1: en la celda 17 del notebook 8, declarar la limitación del generador y mostrar la validación con co-reseñas, que ya está en `results/etapa_8/grafo_resenas_reales.json`. Esa validación da además una comparación no trivial entre Louvain y LPA (modularidad 0.32 frente a 0.008).
- 1.9: el texto de "Ponderación calibrada" del notebook 9 se decide después de volver a calibrar con S_sim corregido.

**Reproducibilidad y detalles menores.**

- Los CSV y el dashboard salen de celdas o de comandos documentados, no de `scratch/`, y los notebooks importan `src/config.py`.
- Tabla resumen del notebook 9 ("ColaCircular con drop", "B+Tree en memoria y disco"), "Paso 9" repetido y términos en inglés del notebook 8, y docstring de [benchmark.py:127](../src/etapa_9/benchmark.py#L127).

**Por qué va primero.** Corrige los errores que invalidan cifras y avanza seis criterios parciales de la rúbrica, y todos los pasos siguientes citan sus resultados. El flujo y los paneles entran aquí porque tocan el notebook 9: dejarlos para después obligaría a reejecutarlo y a rehacer el diagrama.

## Paso 2. Demo de un evento a través de las diez etapas

- Un notebook o script que siga un `event_id` o un lote pequeño de la etapa 0 a la 9 y muestre la prioridad, la alerta, los relacionados, el PageRank y la recomendación.
- **Se apoya en el paso 1:** recién entonces las etapas 4 y 5 alimentan la 9 y S_sim es correcto, así que la traza recorre de verdad las diez etapas.
- Cierra el "No cumple" del componente 2. Se ejecuta en minutos.

## Paso 3. Informes 8 y 9 desde los CSV nuevos

- 1.2: reescribir las cifras desde los CSV y las métricas nuevas y quitar las erratas del encabezado.
- 1.1: la misma postura en los informes 7, 8 y 9. El informe 8 incluye las cifras de la validación con co-reseñas, que hoy solo menciona en una nota.
- 1.6: describir el efecto real de 6 → 8 y retirar la afirmación sobre "graph poisoning".
- Documentación profunda de la etapa 8, como pide la rúbrica: paper original, implementación de referencia, casos de uso, límites y compromisos de cada familia. Agregar que en un grafo no dirigido PageRank ≈ grado.
- El informe 9 lleva el diagrama con el flujo real y la traza del paso 2, usa `revisar` en lugar de `retener` y cita Cormen 4.ª ed.
- **Se apoya en los pasos 1 y 2.** No requiere ejecución.

## Paso 4. README

- Diagrama del pipeline, el mismo del informe 9.
- Orden de ejecución de los notebooks, los scripts, los tests y la demo, con sus comandos.
- Estructura completa del repositorio, incluidos `src/`, `tests/`, `results/`, `scripts/`, `visualizador/` y `scratch/`.
- Tabla de roles con el aporte concreto de cada integrante, coherente con el historial, y un `.mailmap` que unifique las dos identidades de autor de Axel.
- Cierra el segundo "No cumple". **Se apoya en los pasos 1 a 3** porque documenta la arquitectura final. No requiere ejecución.

## Paso 5. Textos de las etapas 6 y 7

- Informe 6 (1.7): la precisión 1.000 de los bloqueos se cumple por construcción y el recall 0.87 es la fracción esperada de 7/8; `_compras` queda fuera de `memoria_bytes`; el Bloom Filter y la `TablaHash` guardan las mismas 140 claves.
- Informe 7 (1.8): el recall 0.946 se mide frente al espacio proyectado, que conserva el 56.8 % de los vecinos; el coseno disperso exacto tarda 7.4 ms; la conversión a float64 infla el factor de 588×; falta discutir el índice invertido como alternativa a LSH 256 × 1.
- Es solo texto y prepara las preguntas probables de la sustentación. Medirlo (recall frente a TF-IDF exacto y 100 repeticiones de construcción) es opcional y obliga a reejecutar parte de la etapa 7.

## Paso 6. Coherencia de las etapas 2 a 5 con el flujo nuevo

- Alinear la tabla de arquitectura del informe 3-4-5 con el flujo del paso 1.
- Quitar las etiquetas "uso previsto" de los notebooks 2 a 7. Es solo markdown y no requiere reejecutarlos.
- Corregir en el informe 3-4-5 lo de "100 repeticiones de 1 000 000 consultas" y las frases de versiones anteriores.

## Paso 7. Limpieza de baja severidad

- "La ejecución de Aris" en el notebook 6, los respaldos fuera del repositorio que cita la auditoría y el docstring de `src/__init__.py`.

## Fuera de la cascada: corrección de fondo del generador

- Co-compras agrupadas por `categoria_hoja` (1.1 completo), ráfagas de fraude con órdenes de varios productos (1.6 completo) y la etapa 3 después del filtro de fraude (1.11).
- Obliga a reejecutar las etapas 0 a 9 (4 a 6 h) y a reescribir todos los informes.
- No se apoya en los pasos anteriores: los rehace. Si se decide hacerlo, va antes del paso 1.

## Dónde cortar

Al terminar el paso 4 quedan resueltos los cinco puntos de severidad alta (1.1 como postura declarada), los dos "No cumple" y casi todos los criterios parciales de la rúbrica. Se recomienda llegar al paso 5, que es solo texto. Del paso 6 en adelante el beneficio es marginal.
