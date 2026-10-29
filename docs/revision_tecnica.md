# Revisión técnica del proyecto frente a la rúbrica

Proyecto EDA 2026-2 · Grupo 1 · Escenario B · Revisión del 28 de octubre de 2026

**Alcance.** Se revisó la rúbrica (`docs/proyecto-eda-2026-2.pdf`) y, por etapa, el notebook, sus resultados en `results/`, su código en `src/` y `tests/` y su informe en `docs/`. No se ejecutó ningún notebook, script ni test: todo sale de leer el código y las salidas guardadas. Quedan fuera la plataforma de ejecución y la longitud del informe final.

**Lectura general.** Las etapas 0 a 7 son consistentes entre notebook, CSV e informe. Tienen verificación contra una verdad de referencia, un protocolo de medición uniforme y limitaciones declaradas. Los problemas de fondo están en las etapas 8 y 9, y en la integración del pipeline completo. Los tres más graves son estos:

- la estructura de co-compra que analizan las etapas 7 a 9 la fija el generador sintético;
- los informes 8 y 9 conservan cifras que no salen de `results/`;
- algunas mediciones y conversiones de esas etapas tienen errores de método.

## 1. Inconsistencias en la solución

### Severidad alta

**1.1 Las co-compras son grupos de hash y los textos las presentan como comportamiento real.** El generador agrega productos a una orden solo desde el mismo grupo `hash(asin) % 500` ([generator.py:22](../src/etapa_0/generator.py#L22), `_productos_extra`). Eso explica tres resultados:

- las 841 comunidades de Louvain son exactamente las 841 componentes conexas para todo γ ∈ [0.5, 20], con pureza de categoría 0.067, igual al azar;
- la etapa 9 recomienda un carrito plegable y una tarjeta de cumpleaños para el tóner Brother;
- en la etapa 7, los relacionados por comportamiento mezclan productos sin relación, por ejemplo una alfombrilla de juego con una tarjeta de cumpleaños.

La auditoría (§7.1 y §7.9) lo reconoce, pero varios textos siguen diciendo lo contrario:

- el markdown de la celda 17 del notebook 8 promete comunidades de "cartuchos de tóner, papelería escolar, embalaje";
- el informe 8 habla de "841 comunidades temáticas puras" y de "clústeres temáticos nítidos";
- el informe 9 dice "productos complementarios… cross-selling";
- el informe 7 llama "complementarios" a los relacionados de la mina Pentel.

Sí hay evidencia con datos reales, aunque casi no se usa. El grafo de co-reseñas sobre las 12.8 M reseñas reales ([grafo_resenas_reales.py](../src/etapa_8/grafo_resenas_reales.py), `results/etapa_8/grafo_resenas_reales.json`) da comunidades con pureza de categoría 0.106 frente a 0.059 al azar, y una modularidad de 0.32 con Louvain frente a 0.008 con LPA. La decisión de mantener la fuente sintética está documentada en [service.py:45-50](../src/etapa_9/service.py#L45-L50) y en la nota "Decisión sobre el grafo" del informe 8: el enunciado define el grafo del escenario B como productos de la misma orden, y solo esa fuente pasa por el filtro de fraude. La auditoría desarrolla esa validación, pero el notebook 8 no la muestra y el informe 8 solo la menciona en esa nota, sin cifras. El problema, entonces, es la redacción del resultado, no la falta de evidencia.

El equipo debe fijar una sola postura. O regenera las órdenes agrupando por `categoria_hoja`, lo que obliga a reejecutar las etapas 0 a 9, o declara el resultado como una limitación del generador en todos los textos y presenta la validación con co-reseñas como respaldo.

**1.2 Los informes 8 y 9 tienen cifras que no se reproducen con `results/`.** Las "fe de erratas" del encabezado no corrigen el cuerpo. Ejemplos:

| Informe | Afirmación en el cuerpo | Lo que dicen notebook y CSV |
| --- | --- | --- |
| 8 | Top-10 de PageRank: Swingline, Scotch, Oxford, Sharpie… | Solo coincide el top-1 (B0BF98LWMZ); ninguno de los otros 9 ASIN está en el top-10 del notebook |
| 8 | Grado ponderado máximo de 469 | 1 606 |
| 8 | "Comunidad A agrupa más de 12 000 productos" | La comunidad mayor tiene 356 |
| 8 | 15.2 % de nodos colgantes de grado 0 | Imposible: el grafo solo contiene productos con al menos una co-compra |
| 8 | Grilla γ ∈ {0.5…1.5} con 8 488 comunidades; tablas de escala con n = 1 000 y 1 643 aristas; extrapolación de LPA con pendiente 1.41 | `grilla_resolucion.csv` (841 comunidades), `escala_*.csv` (n = 1 235 y 7 059 aristas) y pendientes 0.95, 1.14 y 1.13 en el notebook |
| 9 | Recomendaciones del tóner con S_sim de 0.451, 0.382… y la HP DeskJet en el puesto 4 | Notebook 9, celda 11: S_sim = 0.0 en los 8 resultados y otros productos y precios |
| 9 | Trie de 46 818 términos y 0.75 µs; TopK 3.13× más rápido; tabla de escala del Trie con 539 MB | 585 983 términos y 1.2 µs; 2.90×; `escala_trie.csv` con 155 MB |
| 9 | Decisiones `bloquear / retener / aprobar` | La etapa 6 emite `revisar` |

Hay que reescribir los dos informes desde los CSV y las métricas actuales y quitar las erratas del encabezado.

**1.3 Error en `escala_stream.py`: el producto vacío `""` funciona como un nodo central.** La corrupción `asin_vacio` del generador deja `parent_asin = ""`, y el filtro `isinstance(asin, str)` lo acepta ([escala_stream.py:68](../src/etapa_8/escala_stream.py#L68)). Ese nodo une órdenes de todos los grupos de hash. La evidencia está en `escala_stream.csv`:

- con 1.5 M reseñas hay 366 componentes y la mayor tiene 141 434 nodos, frente a 841 componentes de 356 nodos como máximo en el grafo oficial;
- `louvain_igual_componentes` sale `False` en todos los tamaños;
- hay 1.6 % más de aristas.

La validación de la auditoría (§7.2) solo comparó conteos de nodos y aristas, así que no lo detectó. El diagnóstico sale del código y de los CSV, sin ejecutar nada, y se puede confirmar buscando `""` en `grafo.asins`. Si se confirma, quedan invalidadas las columnas de componentes y la modularidad por componentes. La corrección es filtrar `if not asin` y volver a medir.

**1.4 La etapa 8 mide el tiempo con `tracemalloc` activo.** `medir_memoria_y_tiempo` cronometra dentro del rastreo ([benchmark.py:41](../src/etapa_8/benchmark.py#L41)). Las etapas 0 a 7 separan las dos pasadas por esta razón. Sobre el mismo grafo de 150 534 nodos:

| Algoritmo | Tiempo en `escala_comunidades.csv` | Tiempo en la ejecución del notebook | Factor |
| --- | --- | --- | --- |
| Louvain | 87.9 s | 4.0 s | ≈ 22× |
| LPA | 41.3 s | 3.2 s | ≈ 12× |
| PageRank (numpy) | 207 ms | 204 ms | sin cambio apreciable |

NetworkX se midió sin rastreo (líneas 204 a 211 del mismo archivo). Por eso la conclusión de que "el Louvain de NetworkX es 15-18× más rápido" y las extrapolaciones a 10M, como las 3 h de Louvain, son artefactos de la medición.

**1.5 La etapa 9 convierte mal la distancia de la etapa 7.** HNSW devuelve la distancia euclidiana entre vectores de norma 1, d = √(2 − 2·cos), en el rango [0, 2] ([similarity.py:586](../src/etapa_7/similarity.py#L586)). La etapa 9 la trata como una "distancia coseno" y calcula S_sim = max(0, 1 − d) ([service.py:915](../src/etapa_9/service.py#L915)). El resultado es S_sim = 0 siempre que cos ≤ 0.5. Con la mediana d = 0.963, S_sim vale 0.04, cuando el coseno real es ≈ 0.54. Esto sesga el scoring y las conclusiones de la calibración: "solo similitud" da 0.0099 y la similitud tiene coeficiente negativo. La conversión correcta es S_sim = 1 − d²/2.

### Severidad media

**1.6 La exclusión de fraude de la etapa 6 no cambia el grafo de la etapa 8.** Cada compra de una ráfaga es una orden de un solo producto ([generator.py:310](../src/etapa_0/generator.py#L310)), así que las 31 994 órdenes bloqueadas no generan ninguna arista. La afirmación de que "se inyectarían decenas de miles de aristas espurias (graph poisoning)" no tiene base. La integración se ve en el código, pero no tiene efecto medible. En la etapa 7 sí lo tiene: excluir a los 30 usuarios cambia el 93.9 % de las listas.

**1.7 Etapa 6: la memoria del detector y la evaluación están infladas a su favor.**

- El detector guarda la lista exacta de órdenes de cada una de las 288 ventanas (`_compras`, [probabilistic.py:315](../src/etapa_6/probabilistic.py#L315)), pero `memoria_bytes` la excluye. La ventaja en memoria del CMS dentro del pipeline no está demostrada.
- El Bloom Filter y la `TablaHash` guardan las mismas 140 claves, las dos en RAM.
- La precisión 1.000 de los bloqueos se cumple por construcción: solo se bloquea lo que está en la lista y la lista define el fraude. El recall de 0.87 es la fracción esperada de 7/8. El informe debe decirlo así.

**1.8 Etapa 7: el recall de HNSW se mide frente a una referencia reducida.**

- El 0.946 compara contra el KNN exacto en los vectores proyectados a 128 dimensiones, y esa proyección solo conserva el 56.8 % de los vecinos del coseno exacto en TF-IDF (grilla 6.2). La calidad final frente a TF-IDF no se mide.
- El coseno exacto disperso con listas por columna tarda 7.4 ms por consulta y es exacto, así que la conclusión "sin HNSW costaría lo mismo que la fuerza bruta" lo omite.
- La fuerza bruta convierte los vectores a float64 en cada consulta, con un pico de 2 GB con 1M, lo que infla el factor de 588×.
- LSH con 256 × 1 es un OR puro. La alternativa exacta natural es un índice invertido usuario → productos, que ya existe en la CSR de la etapa 3 y tiene 1.98 productos por usuario. Daría recall 1.0, y no se discute.

**1.9 La ponderación del scoring no está calibrada.** El notebook 9 (celda 7) dice "Ponderación calibrada: 0.35/0.25/0.20/0.20". La calibración temporal no la adoptó, y en ella el híbrido (0.0942) no supera a PageRank solo (0.0975).

**1.10 Hay salidas sin consumidor posterior.** Ninguna etapa usa `vectores.npz` ni `vecinos.parquet` de la etapa 5; la etapa 7 solo importa código de ella. El índice B+Tree de la etapa 4 solo lo usa la etapa 5. La etapa 9 no usa la disponibilidad ni el stock de la etapa 3. Aun así, el informe 3-4-5 (tabla de arquitectura) y el diagrama del informe 9 muestran a las etapas 4 y 5 alimentando la 9. Quedan etiquetas "uso previsto" en los notebooks 2 a 7.

**1.11 La etapa 3 se actualiza antes del filtro de fraude.** Las 36 667 compras fraudulentas descuentan stock, suman en la matriz y llegan al precio observado (etapa 4) y a la actividad (etapa 5). La limitación está declarada en el informe 3-4-5, pero sigue abierta.

### Severidad baja

- El informe 3-4-5 dice "100 repeticiones de 1 000 000 consultas" para la caché; el notebook hace 1M de calentamiento y 100 bloques de 10 000. También conserva frases de una versión anterior, como "el tiempo pasa a 202 s… se mantiene".
- La tabla resumen del notebook 9 dice "ColaCircular con drop", pero la etapa 0 usa backpressure sin descarte, y dice "B+Tree en memoria y disco". El notebook 8 repite el número de paso ("Paso 9" dos veces) y mezcla inglés ("converged", "computed").
- Los notebooks 8 y 9 no importan `src/config.py`: escriben a mano la categoría y la semilla. Tampoco generan sus CSV, que salen de `benchmark.py`, `escala_stream.py` y `evaluacion_*.py` ejecutados fuera del notebook.
- El notebook 6 cita "la ejecución de Aris" como referencia, una corrida que no está en el repositorio. La auditoría cita respaldos fuera del repositorio.
- [benchmark.py:127](../src/etapa_9/benchmark.py#L127) dice "con repeticiones" pero mide una sola vez. El docstring de `src/__init__.py` sigue hablando de "etapas 0, 1 y 2".
- El informe 9 cita Cormen 3.ª ed. (2009); el resto cita la 4.ª (2022).

## 2. Inconsistencias con la rúbrica

| Criterio de la rúbrica | Estado | Evidencia y acción |
| --- | --- | --- |
| Un evento atraviesa las diez etapas y produce una salida observable (componente 2) | **No cumple** | No hay orquestador ni traza de un evento; las etapas se conectan en lote por archivos, como reconoce el informe 3-4-5. Falta un script o notebook que siga un `event_id` o un lote pequeño de 0 a 9 y muestre la prioridad, la alerta, los relacionados, el PageRank y la recomendación |
| La salida de cada etapa alimenta una posterior de forma observable | Parcial | Ver 1.10 (etapas 4 y 5) y 1.6 (efecto nulo de 6 → 8) |
| Servicio del escenario B: "panel de ventas, actividad y alertas" | Parcial | El dashboard tiene recomendación, autocompletado, fraude y arquitectura. No tiene panel de ventas ni de actividad, aunque la etapa 3 ya tiene esos datos |
| Datasets de 1K a 10M en las etapas [FIJA] | Parcial | Etapa 6 completa. Etapa 7 llega a 1M y extrapola 10M, con justificación. Etapa 8 usa subgrafos de hasta 150K nodos; `escala_stream` cubre de 1M a 92M eventos, pero tiene el error de 1.3 y no aparece en el notebook ni en el informe |
| 100 repeticiones, promedio y desviación estándar | Parcial | Las etapas 0 a 6 cumplen, con reducciones justificadas. La etapa 7 cumple en las consultas; en la construcción usa un presupuesto propio de 120 s (5 repeticiones con 100K, cuando 100 tardarían 37 min, que es viable). **Las etapas 8 y 9 miden una sola vez, sin desviación** |
| Escalabilidad en log-log contrastada con la complejidad esperada | Parcial | Las etapas 0 a 7 cumplen. La etapa 8 grafica sin rectas de referencia y solo ajusta pendientes para extrapolar. La etapa 9 grafica con el eje x logarítmico y sin pendientes |
| Comunidades comparadas por modularidad, tiempo, número, tamaños y **estabilidad** | Parcial | No se mide la estabilidad (varias semillas, NMI o ARI). Los tamaños se reportan solo como máximo y mediana. La comparación con LPA es trivial porque las comunidades son las componentes (1.1) |
| Ranking de influencia frente a uno simple por grado | Cumple | Spearman 0.956 y 84 de 100 en común. Conviene decir que en un grafo no dirigido PageRank ≈ grado, como se espera en teoría |
| Probabilísticas frente a exactas: tiempo, memoria, falsos positivos y error de frecuencia | Cumple | Etapa 6 completa, con `pyprobables` como referencia |
| Conjuntos frente a fuerza bruta; ANN frente a KNN exacto, con tiempo, recall@K y memoria | Cumple | Etapa 7, con `datasketch` como referencia. Matices en 1.8 |
| Memoria actual y pico con `tracemalloc` | Cumple | Etapa 8 con el sesgo de 1.4 |
| Documentación profunda de las etapas 6 a 8: paper original, implementación de referencia y benchmarks | Parcial | Las etapas 6 y 7 cumplen. La etapa 8 no desarrolla, para cada familia, casos de uso, límites ni compromisos; algo aparece solo en la auditoría |
| README con diagrama del pipeline | No cumple | No tiene diagrama ni orden de ejecución de los notebooks ni comandos de tests y scripts. La estructura del repositorio que describe omite `src/`, `tests/`, `results/`, `scripts/`, `visualizador/` y `scratch/` |
| Tabla de roles con el aporte concreto de cada integrante, consistente con el historial | Parcial | La tabla no tiene la columna de aporte concreto. El historial muestra a Axel con 58 commits, muchos en notebooks ajenos ("corrección y ejecución" de las etapas 1 a 6), y a Jesús con un solo commit para las etapas 8 y 9 completas. Axel tiene dos identidades de autor. Es un riesgo para la "contribución técnica verificable" individual |

## 3. Otras observaciones

**Fortalezas que conviene conservar.**

- La etapa 1 se verifica contra el ruido inyectado: los rechazos coinciden exactamente y el exceso de duplicados está explicado.
- La etapa 6 tiene una lista conocida al 50 %, que evita que el resultado se cumpla por construcción en la regla de ráfaga.
- Las etapas 3 a 7 usan grillas completas y verificación del protocolo de repeticiones.
- Varias etapas documentan las decisiones que se corrigieron después de medir.
- La auditoría de las etapas 8 y 9 es autocrítica y bien fundada. El problema es que sus conclusiones no se trasladaron al notebook 8 ni a los informes 8 y 9.

**Reproducibilidad.** Los notebooks 8 y 9 y el dashboard se generan con scripts de `scratch/`, una carpeta versionada. Para el informe final convendría que cada cifra citada salga de una celda ejecutada o de un comando documentado en el README.

**Preguntas probables en la sustentación.** Responden a los puntos débiles de la sección 1:

- ¿Por qué un Bloom Filter si la tabla exacta de 140 claves también está en RAM?
- ¿Por qué LSH 256 × 1 y no un índice invertido exacto?
- ¿Qué significa un recall de 0.946 si la proyección pierde el 43 % de los vecinos?
- ¿Por qué Louvain devuelve las componentes conexas?
- ¿Qué aporta excluir el fraude del grafo si las órdenes fraudulentas tienen un solo producto?
- ¿Por qué la precisión de los bloqueos es exactamente 1?

**Plan de cambios.** Las prioridades quedaron ordenadas como una cascada en [plan_cascada.md](plan_cascada.md), que lleva el estado de cada paso.
