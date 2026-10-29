# Auditoría técnica de las Etapas 8 y 9 (2026-10-27)

Responde a las seis preguntas abiertas sobre las etapas 8 y 9 y documenta las correcciones
aplicadas. Toda cifra viene de una medición reproducible del repositorio (script y CSV indicados);
lo que es razonamiento sin medición se marca como tal.

Los respaldos previos a los cambios (código, documentos, notebooks, resultados y artefactos de
`data/interim` de las etapas 8 y 9) se guardaron fuera del repositorio y no forman parte de la entrega.
Las cifras vigentes de las etapas 8 y 9 están en `results/` y en sus informes.

---

## 0. Hallazgos que cambian la lectura del proyecto

| # | Afirmación previa | Medición | Evidencia |
|---|---|---|---|
| H1 | «841 comunidades temáticas puras» (Q = 0.9979) | Las 841 comunidades de Louvain son **exactamente las 841 componentes conexas** del grafo, para todo γ ∈ [0.5, 20]. Pureza de `categoria_hoja`: **0.0672**, frente a **0.0673-0.0674** con etiquetas permutadas al azar (según la semilla). | `metricas.json` (`louvain_igual_componentes: true`), `results/etapa_8/grilla_resolucion.csv`, notebook 8 §9 |
| H2 | «Speedup 60× de PageRank frente a NetworkX» | El 60× salió de n = 1 000 (NetworkX tardó 85 ms en su primera llamada: calentamiento). Con muestras comparables: **1.8× (n = 1 235) y 2.6× (n = 10 222)**. Louvain propio es **15-18× más lento** que el de NetworkX. | `results/etapa_8/referencia_networkx.csv` |
| H3 | «PageRank premia prestigio propagado, distinto del grado» | Spearman(PR, grado ponderado) = **0.956**; Pearson con la aproximación cerrada (1-α)/n + α·k_i/2m = **0.978**; 84 de los Top-100 coinciden. | notebook 8 §9.3 |
| H4 | «Grilla de resolución» y curvas de escala | La grilla usaba `asins[:10_000]` (orden alfabético): un subgrafo casi sin aristas, con 8 488 comunidades de mediana 1 para todo γ. Las muestras de escala eran un BFS desde un hub (~280 nodos) más nodos aleatorios sueltos: 10 000 nodos y solo 5 131 aristas. | `src/etapa_8/benchmark.py` (antes y después) |
| H5 | «46 818 términos indexados» en el Trie | El límite de 50 000 inserciones cortaba la lectura del catálogo: se indexaba **~3 % de los 710 503 productos**. | `src/etapa_9/service.py` |
| H6 | Búsqueda «toner brother» | Devolvía portacarteles TONICE y sobres TONESPAC: el score era 0.5 + 0.5·S_pr (relevancia constante) y se completaba con el prefijo `query[:3]` = "ton". | `demo_consultas.json` anterior a la corrección, fuera del repositorio |
| H7 | «Precisión 100 %» de bloqueos | Era la constante `1.0` escrita en el código; además `retenidas` daba 0 porque la etapa 6 emite `"revisar"`, no `"retener"`. Medida ahora con `fraude_real`: **1.0** (41 532 / 41 532), retenidas 6 178. | `_cargar_resumen_alertas` |
| H8 | «Speedup 3.13× del MinHeap» | Correcto frente a `sorted()`, pero `np.argpartition` (ya medido en el CSV) resolvía N = 10⁶ en **7.3 ms frente a 87.3 ms** del heap: 12× más rápido. | `results/etapa_9/comparacion_sorting.csv` |
| H9 | Scoring híbrido 0.35/0.25/0.20/0.20 | En una evaluación temporal honesta la popularidad sola casi iguala al híbrido (Recall@10 0.0975 frente a 0.0942). Los relacionados MinHash de la etapa 7 tenían fuga: solos daban 0.261 y, recalculados sin futuro, 0.031. Detalle en la Auditoría 1. | `results/etapa_9/calibracion_*.csv` |

**Lectura de fondo.** El grafo de co-compra de este stream sintético es una unión disjunta de 841
componentes (500 de ~280-320 nodos y 341 de menos de 20). Dentro de cada componente casi no hay
estructura: Louvain por componente da Q mediana 0.267, frente a 0.219 del mismo componente recableado
con igual secuencia de grados (solo el 17 % supera al nulo por más de 0.05). Las co-compras no siguen
la categoría del producto. Por eso el tóner Brother recomienda un carrito plegable y una tarjeta de
cumpleaños: el recomendador reproduce fielmente lo que el generador de órdenes puso en los datos.

---

## Auditoría 1. Calibración del scoring híbrido y cold start

### 1.1 Defectos de escala (medidos)

El score suma cuatro señales con distribuciones incomparables:

| Señal | Normalización original | Distribución medida |
|---|---|---|
| S_sim = 1 − d_coseno (HNSW) | ninguna | mediana 0.04 (d mediana 0.963) |
| S_rel = Jaccard MinHash | ninguna | mediana 0.033 → aporta 0.25 · 0.033 ≈ 0.008 |
| S_pr | pr / pr_max | mediana **0.0084**, p99 0.14: el término 0.20·S_pr es ~0 salvo en los hubs |
| S_co | w / w_max del ancla | vale **1.0** para el vecino más fuerte aunque haya una sola co-compra |

Los pesos nominales no son los efectivos. La contribución real depende de la escala de cada señal, y S_co, normalizada por ancla, domina. En el ejemplo del tóner, los cuatro primeros tienen S_sim = 0.

### 1.2 Formulación propuesta e implementada

`ScoringHibrido.pesos_efectivos(n_q, disponibilidad)` (`src/etapa_9/service.py`):

1. **Credibilidad (Bühlmann / shrinkage bayesiano):** w_co(q) = w_co · n_q / (n_q + κ), con n_q las
   co-compras observadas del ancla. La masa retirada pasa a sim y rel en proporción a sus pesos base.
2. **Disponibilidad:** una señal ausente para el ancla (sin vecinos HNSW, sin firma, fuera del grafo)
   se anula y los pesos se renormalizan a suma 1.
3. **Normalización de S_pr** configurable: `max` (original), `log` o `percentil`.

**Resultado teórico (verificado).** Para un ancla fija, anular una señal que vale 0 en *todos* los
candidatos y renormalizar es una transformación monótona del score, así que **el orden no cambia**. La
transferencia de peso en cold start puro no mejora el ranking: solo reescala el score. Sirve para
umbrales y para mezclar listas, no para ordenar. Verificado sobre 9 397 anclas cold start de prueba
(`h3_cold_start_identico: true`). En cold start lo que importa es la *generación de candidatos*, no
los pesos.

### 1.3 Evaluación temporal (`src/etapa_9/evaluacion_scoring.py`)

Protocolo: el grafo y el PageRank se construyen con el 80 % más antiguo de las órdenes legítimas
(131 854 nodos, 658 701 aristas). Se predicen las co-compras del 20 % final (186 285 pares), con
59 869 anclas repartidas 50/50 entre calibración y prueba. Hipótesis fijadas antes de medir: H1
(κ > 0 ayuda con n_q ≤ 2), H2 (la normalización de S_pr importa) y H3 (cold start invariante).

La señal de relacionados MinHash se evalúa en tres variantes:
- **fuga:** los relacionados de la etapa 7, calculados con todo el stream.
- **temporal:** MinHash + LSH de la etapa 7 recalculados solo con los eventos anteriores al corte,
  con los mismos parámetros (256 hashes, 256 bandas, ≥ 5 usuarios); cubre 65 248 productos.
- **sin:** sin la señal.

La variante **temporal** es la única honesta con esa señal.

Recall@10 en prueba, κ = 0, híbrido estático:

| Variante | max (original) | log | percentil |
|---|---|---|---|
| fuga | 0.1457 | 0.1583 | 0.1659 |
| **temporal** | — | **0.0942** | — |
| sin | 0.0897 | 0.0944 | — |

(Tabla completa por variante, grupo de n_q, normalización y κ: `results/etapa_9/calibracion_scoring.csv`.)

- **H1 rechazada:** κ > 0 no mejoró Recall@10 en ningún grupo ni en ninguna variante. En este stream
  una sola co-compra ya predice la siguiente, porque las componentes persisten en el tiempo. Se deja
  κ = 0 y el mecanismo disponible.
- **H2 confirmada:** `log` es la mejor normalización en las variantes temporal y sin, y se adopta.
- **H3 confirmada** (ver 1.2).

### 1.4 Pesos aprendidos y fuga de la señal MinHash

Prueba (29 935 anclas), S_pr en `log`. Regresión logística L2 sobre los pares (ancla, candidato) de
15 000 anclas de calibración, libre y con coeficientes ≥ 0 (representables en `ScoringHibrido`).
También se reporta la cobertura de catálogo del Top-10 (ítems distintos recomendados / candidatos
distintos) y el Gini de las recomendaciones (más alto = más concentrado en pocos productos).

| Modelo (variante temporal) | Pesos (sim, rel, pr, co) | Recall@10 | NDCG@10 | Cobertura | Gini |
|---|---|---|---|---|---|
| solo similitud | (1, 0, 0, 0) | 0.0099 | 0.0068 | 0.755 | 0.389 |
| solo relacionados | (0, 1, 0, 0) | 0.0308 | 0.0373 | 0.716 | 0.380 |
| solo co-compra | (0, 0, 0, 1) | 0.0801 | 0.0973 | 0.725 | 0.439 |
| solo PageRank | (0, 0, 1, 0) | 0.0975 | 0.1247 | 0.598 | 0.533 |
| **híbrido estático** | (0.35, 0.25, 0.20, 0.20) | **0.0942** | **0.1159** | **0.647** | **0.498** |
| logística libre | (−93.0, −8.9, +6.40, −0.06) | 0.1003 | 0.1301 | 0.620 | 0.516 |
| logística no negativa | (0, 0, 0.927, 0.073) | 0.0986 | 0.1277 | 0.602 | 0.531 |

Lectura:
1. **La fuerza de los relacionados MinHash era casi toda fuga.** Solos dan Recall@10 = **0.261**
   con los datos de la etapa 7 y **0.031** recalculados sin futuro (8.5× menos). Cualquier
   evaluación que use `relacionados.parquet` sobreestima esa señal.
2. **La similitud de contenido tiene coeficiente fuertemente negativo** en todas las variantes:
   en este stream los productos parecidos no se co-compran después. Es coherente con la pureza de
   categoría igual al azar.
3. El mejor modelo representable (coeficientes ≥ 0) es casi «solo popularidad» (pr 0.93, co 0.07).

### 1.5 Decisión: se conservan los pesos estáticos (regla fijada antes de medir)

Regla, escrita en el script antes de correr la variante temporal: se adoptan los pesos aprendidos
solo si en prueba mejoran Recall@10 **y** NDCG@10 en ≥ 5 % relativo y la cobertura de catálogo no
cae más de 25 %. Resultado de la logística no negativa frente al estático (variante temporal):

| Criterio | Resultado | Cumple |
|---|---|---|
| Recall@10 ≥ +5 % | +4.6 % (0.0942 → 0.0986) | **no** |
| NDCG@10 ≥ +5 % | +10.1 % (0.1159 → 0.1277) | sí |
| Caída de cobertura ≤ 25 % | −7.1 % (0.647 → 0.602) | sí |

**No se adoptan: el servicio mantiene 0.35/0.25/0.20/0.20 con S_pr en `log` y κ = 0.** Razones
adicionales al criterio: la alternativa concentra más las recomendaciones (Gini 0.498 → 0.531,
Auditoría 5) y es un recomendador de populares que en estos datos sintéticos gana por cómo se
generaron las órdenes, no por relevancia de producto. La logística libre, que sí supera la regla en
NDCG y casi en Recall, necesita pesos negativos que `ScoringHibrido` no admite y que penalizarían la
similitud, algo indefendible para un catálogo real.

Recomendación general: calibrar los pesos por *learning-to-rank* sobre una partición temporal, con
todas las señales recalculadas sin futuro y una regla de adopción fijada de antemano, no a mano.
Para un cold start real hay que añadir candidatos que no dependan de la co-compra: vecinos HNSW,
populares de la misma categoría o caminatas de 2 saltos.

---

## Auditoría 2. Louvain frente a Leiden y comunidades desconectadas

**Riesgo en este grafo: nulo en la práctica, medido.** Louvain dejó **0** comunidades
desconectadas. LPA dejó **6** (27 nodos). La causa es estructural: cada comunidad de Louvain es una
componente conexa completa, conexa por definición.

**Corrección implementada (garantía, no heurística).** `dividir_comunidades_desconectadas` parte cada
comunidad en sus componentes conexas internas con un BFS sobre las aristas intra-comunidad (scipy
`connected_components`), en O(|V| + |E|). Se aplica por defecto a Louvain y a LPA.
**Q nunca baja:** L_c se conserva porque no hay aristas entre las partes, y
(k_a + k_b)² > k_a² + k_b², así que la penalización solo disminuye. Medido en LPA: Q pasa de
0.99585580507 a 0.99585580514 y las comunidades de 1 706 a 1 712. Tests:
`test_dividir_comunidad_desconectada_sube_modularidad` (Q de 0 a 0.5 con dos triángulos).

**Qué agrega Leiden además** (Traag, Waltman y van Eck, 2019): la fase de refinamiento mezcla nodos
solo dentro de cada comunidad, empezando por singletons, y solo hacia subcomunidades bien conectadas
(γ-conexas). Eso garantiza comunidades γ-separadas y, al converger, subconjuntos óptimos; el split
posterior solo garantiza conexión. Implementación compacta sobre el código actual: entre la Fase 1 y
la Fase 2 de `louvain()`, por cada comunidad C se corre un Louvain restringido a C desde singletons,
aceptando un movimiento solo si el nodo y la subcomunidad destino cumplen
k_in ≥ γ · k_v · (K_C − k_v) / 2m. El grafo agregado se construye sobre esas subcomunidades, pero la
partición inicial del nivel siguiente sigue siendo la de Louvain. Costo: O(|E|) adicional por nivel,
el mismo orden que la Fase 1. **No se implementó**, porque en estos datos no cambiaría el resultado
(0 desconexiones).

**El problema real es el límite de resolución** (Fortunato y Barthélemy, 2007). Con √(2m) = 1 461 y un
peso medio por componente de 1 269, ninguna subestructura interna es resoluble con γ = 1. La grilla
nueva lo confirma: con γ = 20 siguen siendo las 841 componentes. Para resolver dentro de cada
componente hace falta Louvain por componente (γ local) o CPM (Traag et al., 2011), que no tiene
límite de resolución. Aun así, el contraste con el modelo nulo indica que dentro de las componentes
casi no hay estructura que encontrar.

**Recomendación de costo.** Descomponer primero en componentes (O(|V| + |E|)) y correr Louvain por
componente, en paralelo, reduce el trabajo, y una componente pequeña converge en una pasada. El
Louvain actual con diccionarios Python usa 185 MB de pico (tracemalloc), 9× el CSR de 20.5 MB, y
anula el argumento de memoria del CSR.

---

## Auditoría 3. Autocompletado difuso sub-milisegundo

**Elección: autómata de Levenshtein simulado sobre el propio Trie** (fila de programación dinámica por
nodo, Damerau-OSA, banda de Ukkonen |i − j| ≤ d). Se descartan:
- **BK-Tree:** indexa palabras completas, no prefijos. Además, con d = 2 y |Σ| ≈ 40 visita una
  fracción grande del árbol (la desigualdad triangular poda poco con distancias pequeñas).
- **N-gramas + índice invertido:** sirve como filtro de candidatos, pero necesita una verificación
  posterior y, para prefijos cortos, los q-gramas tienen poca selectividad.
- **FST/autómata de Schulz-Mihov precompilado:** es la opción óptima, O(L) por estado y usada por
  Lucene, pero en Python puro su ventaja constante se pierde. Recorrer el Trie con la fila DP explora
  el mismo producto autómata × Trie.

**Integración con la caché Top-K:** cada nodo cuya última celda de la fila es ≤ d aporta su
`top_cache` en O(M), sin recorrer el subárbol. Podas exactas, demostradas en el docstring y
verificadas con 7 200 casos aleatorios contra fuerza bruta OSA:
1. Si toda la banda supera d, se poda la rama: las filas no bajan de su mínimo, ni siquiera con
   transposición, porque D[j][i] ≤ D[j−1][i−1] + 1.
2. Con k ≤ M, si el mínimo de la fila ya no es menor que la distancia registrada en el camino, se
   poda: los descendientes están en la caché ya tomada, con frecuencia mayor o igual.
3. Profundización iterativa d = 1 → 2 (techo AUTO de Lucene: 0 si L ≤ 2, 1 si L ≤ 5, 2 si no).
4. Primer carácter fijo: divide la búsqueda por ~|Σ|.

**Trie radix (necesario para escalar).** Al indexar el catálogo completo, el Trie de un carácter por
nodo creaba **7.2 M nodos (90 % unarios), 3.47 GB**. Con compresión Patricia/radix quedan
**723 761 nodos, 417 MB** (8.3× menos), con la misma semántica.

**Latencias medidas** (catálogo completo, 585 983 términos; M1, Python 3.12):

| Operación | p50 | p95 | Fuente |
|---|---|---|---|
| Prefijo exacto (caché Top-K) | 1.2 µs | 1.2 µs | `metricas.json` |
| Difuso d = 1, demos del servicio | 0.74 ms | 0.88 ms | `metricas.json` |
| Difuso d = 1, 300 tipeos sintéticos (292 300 términos) | 0.43 ms | 0.68 ms (p99 0.79) | `difuso_trie.csv` |
| Difuso d = 2, mismos tipeos | 1.93 ms | 3.02 ms (p99 3.96) | `difuso_trie.csv` |
| Búsqueda con índice invertido y corrección | 1.83 ms | 2.02 ms | `metricas.json` |

Acierto (el prefijo original aparece en el Top-5): **89.7 %** con d = 1 y **94.7 %** con d = 2. Sin
difuso, el 94 % de esas consultas no devolvía nada. Benchmark de escala del Trie radix (250 000
términos): 155 MB y 11 µs por inserción, frente a 565 MB y 25 µs del Trie original. La consulta
exacta sube de 0.75 µs a 1.08 µs por la comparación de etiquetas.

**Decisión:** el autocompletado interactivo usa d ≤ 1 (sub-milisegundo). d = 2 se reserva para
corregir palabras sueltas contra el vocabulario de la búsqueda (53 341 palabras con df ≥ 3), donde es
barato. Así se resuelve el caso del enunciado: «toner brtoher» → «toner brother», porque la
transposición cuesta 1 en OSA y 2 en Levenshtein.

---

## Auditoría 4. Out-of-core para 10⁸ nodos y 10¹⁰ aristas

**Corrección de la cuenta previa.** El CSR simétrico guarda cada arista dos veces: 2·10¹⁰
entradas × (4 B de `int32` + 8 B de `float64`) = **240 GB**, no 160 GB. Los 160 GB solo salen con pesos
`float32`. Como los pesos son conteos enteros, `uint16` con saturación (o la cuantización de los
pesos altos) da 2·10¹⁰ × 6 B = 120 GB, y si se guarda solo el triángulo superior, con SpMV simétrico
(y = Uᵀx + Ux), **60 GB**. A eso se suma `indptr` (10⁸ × 8 B = 0.8 GB) y por cada vector de PageRank
0.8 GB (`float64`) o 0.4 GB (`float32`).

**PageRank (semi-externo, una máquina):** los vectores (0.4-0.8 GB) caben en RAM y la matriz se lee en
streaming por bloques de filas desde `np.memmap` en cada iteración. Es el modelo semi-externo:
O(|V|) de memoria y E/S de scan(|E|) por iteración, sin accesos aleatorios a disco. Con ~25
iteraciones y 60-120 GB a ~2 GB/s de SSD NVMe, sale ~1-1.5 h. Mejoras: (a) Gauss-Seidel o
extrapolación de Aitken para bajar iteraciones; (b) particionar los nodos en P intervalos (shards de
GraphChi, Kyrola et al., 2012), para que cada bloque toque un rango contiguo de x y se aproveche la
caché. Nota: en un grafo no dirigido, PR ≈ (1−α)/n + α·k_i/2m (Pearson 0.978 aquí). Para muchos usos
el grado ponderado, que es un solo scan, es un sustituto casi gratuito.

**Louvain (semi-externo):** la Fase 1 necesita en RAM el vector de comunidades y el Σ_tot de cada
comunidad (O(|V|) ≈ 1.2 GB) y recorre las listas de adyacencia en streaming. Cada pasada es un scan
secuencial, porque el orden aleatorio de nodos se sustituye por un orden por bloques. La Fase 2
(contracción) es una agregación externa: ordenar las aristas por (c_u, c_v) con *external sort* y
sumar; el grafo contraído suele caber en RAM tras el primer nivel. Alternativa sin Louvain: componentes
conexas semi-externas con union-find O(|V|), un scan, más LPA en streaming. En estos datos eso habría
bastado, porque las comunidades son las componentes.

---

## Auditoría 5. Sesgo de popularidad de PageRank

**Diagnóstico medido:** en el grafo no dirigido, PageRank es casi una función del grado (Spearman
0.956), así que el «rich-get-richer» de PR aquí es simplemente popularidad. La evaluación temporal
muestra que la popularidad sola ya es una línea base fuerte: solo-PageRank, Recall@10 = 0.0975, casi
iguala al híbrido (0.0942) y concentra más las recomendaciones (Gini 0.533 frente a 0.498). Ese es el patrón conocido de los recomendadores evaluados con
interacciones futuras: las métricas de acierto premian la popularidad.

**Recomendación:**
1. **Personalized PageRank (PPR)** con reinicio en el ancla, en lugar de PR global, para S_co/S_pr:
   mide la proximidad al ancla, no la popularidad global. Con *push* local (Andersen, Chung y Lang,
   2006) cuesta O(1/(ε(1−α))), independiente de n, y es apto para servir en línea.
2. Si se mantiene PR global como prior, se usa en escala `log` (implementado) y con un peso aprendido.
   Una normalización por entropía no tiene una justificación estadística clara aquí.
3. Medir la **cobertura de catálogo** y el Gini de las recomendaciones, no solo el Recall, antes de
   aumentar el peso de la popularidad. Ya se miden en `calibracion_lineas_base.csv` y forman parte
   de la regla de adopción de §1.5.

---

## Auditoría 6. Coherencia arquitectónica y despliegue

| Punto | Estado |
|---|---|
| Fraude (E6) → grafo (E8) | Correcto: se excluyen las 31 878 compras de las 31 994 órdenes bloqueadas antes de las aristas. |
| Fraude (E6) → servicio (E9) | Corregido: la precisión se mide y `retenidas` cuenta `"revisar"`. |
| E7 → E9 | La distancia HNSW es coseno en [0, 1.21]; 1 − d es válido. Los relacionados MinHash usan todas las interacciones; la evaluación los recalcula hasta el corte (`relacionados_temporales`) y mostró que su fuerza aparente era fuga (0.261 → 0.031). |
| `catalogo_demo` del dashboard | Ningún script del repositorio lo generaba; ahora lo produce `ejecutar_etapa`. |
| Reproducción de la etapa 8 | `ejecutar_etapa('Office_Products')` en la etapa 8 pasaba la categoría como `ruta_eventos`. Se documentó la llamada correcta, `ejecutar_etapa()`. |
| Dependencias | `requirements.txt` no declaraba `scipy`, `networkx` ni `nbconvert`, aunque se usan. Corregido. |
| Memoria del servicio | RSS de 2.6 GB con el catálogo completo, frente a 1.6 GB antes con el 3 %. El grueso viene de los diccionarios de similares/relacionados (1.95 M tuplas) y del Trie. Para un contenedor FastAPI conviene pasar similares/relacionados a arreglos CSR (`indptr`, `vecinos`, `pesos`) y cargar con `np.load(mmap_mode="r")`, compartido entre *workers*. |
| Arranque | 32-45 s de construcción del Trie y del índice en cada arranque: hay que serializarlos (pickle/NPZ) o construirlos en el build de la imagen. |
| Concurrencia | Las estructuras son de solo lectura tras la carga, así que son seguras con varios *workers* si se comparten por fork (copy-on-write). Ojo: el conteo de referencias de Python ensucia las páginas; los arreglos NumPy con mmap no tienen ese problema. |

---

## Auditoría 7. Datos reales, escala medida y diversidad (segunda fase, 2026-10-27)

### 7.1 Qué es real y qué es sintético en el dataset

| Dato | Origen | Real |
|---|---|---|
| 12 845 712 reseñas (usuario, producto, fecha, rating, compra verificada, votos útiles, texto), 1998-2023 | Amazon Reviews 2023 | sí |
| 710 503 productos (título, tienda, categorías, precio, rating medio, n.º de ratings) | Amazon | sí |
| `bought_together` | Amazon | vacío en los 710 503 productos |
| Órdenes multi-producto, vistas, carritos, búsquedas, devoluciones, fraude | `src/etapa_0/generator.py` | no |

Mecanismo, leído en el código: con probabilidad 0.25 una orden suma de 1 a 3 productos vistos hace
poco en el mismo grupo `crc32(asin) % 500`, un hash del código del producto. Eso explica por qué las
comunidades son esos grupos de hash fragmentados (pureza igual al azar), por qué la similitud de
contenido predice en contra y por qué la popularidad gana. **Las conclusiones sobre co-compras
describen al generador, no a Amazon, y esto no se corrige con algoritmos.** La señal de
comportamiento real disponible son las co-reseñas.

### 7.2 Escala medida sin extrapolar (`src/etapa_8/escala_stream.py`)

Se regeneró el stream de la etapa 0 con ventanas reales crecientes, hasta el dataset completo, y se
corrió la etapa 8 entera en un subproceso por tamaño. El filtro de fraude usa la verdad de
referencia del generador. Validación: con 1.5 M reseñas da 151 042 nodos y 818 423 aristas, frente
a 150 534 y 805 587 del pipeline oficial con el detector de la etapa 6 (+0.3 % y +1.6 %).
`results/etapa_8/escala_stream.csv`:

| Reseñas | Eventos | Nodos | Aristas | PageRank | Louvain | LPA | Total | RSS pico |
|---|---|---|---|---|---|---|---|---|
| 375 000 | 2.6 M | 68 499 | 232 973 | 0.09 s | 2.5 s | 1.8 s | 14 s | 0.90 GB |
| 1 500 000 | 10.5 M | 151 042 | 818 423 | 0.29 s | 5.3 s | 4.1 s | 48 s | 1.47 GB |
| 6 000 000 | 43.3 M | 342 343 | 2 815 463 | 1.11 s | 13.0 s | 9.5 s | 180 s | 3.82 GB |
| **12 845 712 (todo)** | **92.4 M** | **583 653** | **5 407 743** | **1.99 s** | **33.7 s** | **32.0 s** | **407 s** | **6.99 GB** |

Pendientes log-log medidas (de 375 mil a 12.8 M): stream 1.02 frente a eventos; construcción
del CSR 1.04, PageRank 0.99 y memoria CSR 0.98 frente a aristas (lineales, como predice la teoría);
Louvain 0.78 y LPA 0.86 (sublineales en este rango); RSS pico 0.66. **El dataset completo usa el
7 % de los 96 GB**, así que no hace falta extrapolar con los datos reales: ya es el límite de lo que
existe. Una réplica sintética de 10× cabría en memoria, aunque solo mediría costo, no estructura
nueva. Hallazgo adicional: Q ≈ 0.995 con 767-991 comunidades en **todos** los tamaños (86× más
reseñas), la firma de los ~500 grupos de hash del generador.

### 7.3 El mismo pipeline sobre la señal real: grafo de co-reseñas (`src/etapa_8/grafo_resenas_reales.py`)

Arista u–v con peso igual al número de usuarios que reseñaron v justo después de u (pares
consecutivos por usuario: O(reseñas)). `results/etapa_8/grafo_resenas_reales.json`:

| Métrica | Co-compra sintética | Co-reseña real |
|---|---|---|
| Nodos / aristas | 150 534 / 805 587 | 579 547 / 4 733 125 |
| Componente gigante | ninguna (841 componentes de ~280) | **98.7 %** de los nodos |
| Spearman(PageRank, grado) | 0.956 | 0.895 |
| Louvain: Q / comunidades | 0.998 / 841 = componentes | **0.322** / 4 272 (81 con ≥ 10 nodos; la mayor tiene 139 900) |
| Comunidades de Louvain desconectadas | 0 | **12** (se partieron; el problema que Leiden evita aparece con datos reales) |
| LPA | Q 0.996 | **Q 0.008**, 710 desconectadas: colapsa en una etiqueta gigante |
| Pureza de categoría / azar | 0.067 / 0.067 | **0.106 / 0.059** |
| Homofilia de aristas: misma categoría / azar | — | **11.1 % / 1.5 %** (7.7×) |
| Homofilia de aristas: misma tienda / azar | — | **6.1 % / 0.064 %** (95×) |

Límite de resolución con datos reales (`grilla_resolucion_resenas.csv`): al subir γ de 1 a 20, la
mayor comunidad baja de 139 900 a 5 591 nodos y la pureza de categoría sube de 0.106 a 0.163 (2.5×
el azar), y la de tienda de 0.028 a 0.063. La Q con γ = 1 baja de 0.322 a 0.204. El γ = 1 por defecto
fusiona grupos temáticos reales en una comunidad gigante.

### 7.4 Recomendador con un objetivo real (`src/etapa_9/evaluacion_real.py`)

Objetivo: el siguiente producto que reseña cada usuario. Corte en el cuantil 0.9 del tiempo
(agosto de 2022), 299 548 pares disponibles y 40 000 de prueba. Señales sin futuro: S_sim (HNSW),
S_pr (log, grafo de entrenamiento), S_co (co-reseñas de entrenamiento) y S_cat (misma categoría).

| Modelo (generador ampliado) | HitRate@10 | NDCG@10 | Gini de exposición | Cobertura |
|---|---|---|---|---|
| solo similitud | 0.0053 | 0.0033 | 0.652 | 0.176 |
| solo PageRank | 0.0046 | 0.0029 | 0.967 | 0.008 |
| solo co-reseña | 0.0069 | 0.0041 | 0.703 | 0.183 |
| **híbrido estático** | **0.0081** | **0.0046** | 0.846 | 0.087 |
| logística no negativa | 0.0077 | 0.0045 | 0.871 | 0.049 |
| **híbrido + novedad λ = 0.3** | **0.0082** | **0.0047** | **0.793** | **0.114** |
| híbrido + novedad λ = 0.5 | 0.0057 | 0.0035 | 0.453 | 0.386 |

1. **Con datos reales el híbrido estático gana**: supera a cada señal sola y a la logística
   aprendida. La similitud de contenido tiene coeficiente **positivo** (+4.1; en el sintético,
   −102). Esto respalda haber conservado los pesos.
2. HitRate@10 ≈ 0.8 % parece bajo, pero el azar entre 710 mil productos da 0.0014 %: es ~570× el azar.
   El techo lo pone la **generación de candidatos**: el siguiente producto solo está entre ellos en
   el 3.4 % de los casos (generador base) o el 5.4 % (ampliado, con 2 saltos y populares de la misma
   tienda), lo que sube el HitRate un 7 %. Es la palanca con más retorno.

### 7.5 Cómo bajar el Gini (más diversidad)

El Gini de exposición mide la concentración de las recomendaciones (0 = equitativo, 1 = siempre los
mismos); hay que **bajarlo**. Referencia natural: la demanda real ya tiene Gini 0.838 (el 1 % de los
productos concentra el 41.8 % de las reseñas, `analisis_campos_reales.json`), así que un recomendador
con Gini cercano a 0.84 solo reproduce esa concentración.

Mecanismo implementado: `score_λ = (1 − λ)·score_norm + λ·(1 − S_pr)` (premio a la novedad;
`ScoringHibrido.lambda_novedad`, `LAMBDA_NOVEDAD = 0.3` en el servicio). Frontera medida (tabla de
7.4): con λ = 0.3 el Gini baja de 0.846 a 0.793 y la cobertura sube de 0.087 a 0.114 **sin perder
precisión** (HitRate de 0.0081 a 0.0082). Es una mejora de Pareto, repetida en los dos generadores
de candidatos y en los dos modelos. Con λ = 0.5 el Gini cae a 0.45, pero el HitRate pierde ~30 %.
Otras palancas: candidatos de cola larga (2 saltos, misma tienda) y topes de exposición por producto.

### 7.6 Otros campos reales con conclusiones válidas (`src/etapa_9/analisis_campos_reales.py`)

- Ratings en forma de J: 67.8 % de 5 estrellas y 10.5 % de 1; media 4.21. Las compras verificadas
  puntúan más (4.22 frente a 4.02).
- Las reseñas negativas reciben más votos útiles (1.60 con 1 estrella frente a 0.68 con 5): la
  utilidad percibida está sesgada a lo negativo.
- Precio frente a rating medio: Spearman −0.16 (157 309 productos con ≥ 20 ratings); precio frente a
  popularidad: −0.06.
- Volumen: crece hasta 2020-2021 (1.9 M reseñas/año) y cae después; 2023 es parcial.
- Demanda: Gini 0.838 por producto y 0.339 por usuario. Las categorías con más ratings son
  cartuchos de tinta (6.4 M), tarjetas de felicitación, agendas y bolígrafos.

### 7.7 Co-reseñas reales como fuente de co-compra del servicio

`ServicioEcommerce(fuente_cocompra=...)` acepta tres fuentes para S_co, S_pr y los 2 saltos:
`sintetico` (grafo de la etapa 8), `coresena` (`grafo_resenas.npz`) y `ambos` (suma de pesos,
`sumar_grafos` en `src/etapa_8/graph.py`). Comparación con objetivo real, generador ampliado y
λ = 0.3. La co-compra sintética se regeneró sin futuro (11.5 M reseñas anteriores al corte: 531 518
nodos y 4.85 M aristas), así que ninguna fuente ve el periodo de prueba.

| Fuente | HitRate@10 | NDCG@10 | Gini | Siguiente producto entre los candidatos |
|---|---|---|---|---|
| sintético | 0.0032 | 0.0015 | 0.725 | 1.4 % |
| **co-reseñas** | **0.0082 (+158 %)** | **0.0047 (+213 %)** | 0.793 (+0.069) | 5.4 % |
| ambos | 0.0041 (+30 %) | 0.0022 (+46 %) | 0.735 (+0.010) | 3.9 % |

Regla fijada antes de medir: se reemplaza `sintetico` si HitRate@10 y NDCG@10 mejoran ≥ 5 % y el
Gini no sube más de 0.05. Co-reseñas no la cumple por el Gini (+0.069); `ambos` sí.

Observaciones posteriores a la medición (no cambian la decisión, que es del equipo):
- La suma cruda mezcla dos escalas (usuarios consecutivos y órdenes sintéticas). Donde el grafo
  sintético tiene un hub, domina: el tóner `B0BF98LWMZ` sigue recibiendo carritos y tarjetas con
  `ambos`, y con `coresena` sola recibe tóneres y cartuchos compatibles (5 de 6 de la misma familia).
- El Gini de las fuentes no es del todo comparable: `sintetico` tiene menos candidatos (249 por
  ancla, frente a 681), lo que limita mecánicamente la concentración. Con `coresena` y λ = 0.5 el Gini
  baja a 0.45 y el HitRate (0.0057) sigue 78 % por encima del sintético.
- Latencia de `puntuar_candidatos` con `ambos`: p50 0.01 ms, p95 0.11 ms; recomendar el tóner, 3.1 ms.

### 7.8 Decisión final: el servicio se queda con la co-compra sintética

**Justificación oficial de la decisión** (texto acordado por el equipo para informes y presentación):

> El enunciado define el grafo del escenario B como productos comprados en la misma orden, y así lo
> implementamos. Como validación probamos fuera del pipeline una señal alternativa construida con
> reseñas reales. Mejora la precisión, pero no cumple esa definición, no pasa por el filtro de fraude
> y la métrica con que se evaluó la favorece por construcción. Por eso no la integramos y el servicio
> usa el grafo de co-compra.

Desarrollo de cada argumento:

Se cierra con `FUENTE_COCOMPRA = "sintetico"`, más S_pr en `log` y `LAMBDA_NOVEDAD = 0.3`. Por qué no
se adopta `ambos` ni `coresena`, aunque `ambos` haya pasado la regla de §7.7:
1. **El enunciado define el grafo del escenario B**: «una relación ponderada conecta productos
   comprados dentro de la misma orden» (`docs/proyecto-eda-2026-2.pdf`). Una co-reseña no es una
   orden. Servir recomendaciones desde otro grafo desconecta la Etapa 9 de la Etapa 8 entregada.
2. **Solo el stream pasa por el filtro de fraude** de la etapa 6. Las co-reseñas no atraviesan la
   cadena ingesta → fraude → grafo que el proyecto pretende demostrar.
3. **La evaluación real favorece a las co-reseñas por construcción.** El objetivo es «siguiente
   reseña» y las aristas de co-reseña son «reseñas consecutivas»: se mide casi la misma relación
   que se usó para construir la señal. No es evidencia neutral de mejor co-compra.
4. `ambos` suma escalas distintas (usuarios frente a órdenes), sin calibrar entre sí. En los hubs
   sintéticos domina el ruido (el tóner sigue recibiendo carritos y tarjetas).

Lo que sí se conserva, porque se sostiene también con la fuente sintética (tabla §7.7, fila
sintético): λ = 0.3 sube el HitRate@10 de 0.0026 a 0.0032 y baja el Gini de 0.762 a 0.725. El código
de fuentes alternativas queda disponible y documentado como validación con datos reales.

### 7.9 Causa raíz de las co-compras sin sentido

No es un error de programación ni un input mal leído por una etapa posterior. Es una **decisión de
modelado de la Etapa 0** cuyas consecuencias nadie validó después:
- El dataset no trae co-compras (`bought_together` vacío; lo anota `docs/informe_etapa_0_1_2.md`) y
  el enunciado recomienda generar el stream de forma sintética. Simular órdenes era necesario.
- El generador agrega a una orden productos vistos hace poco en el mismo grupo `crc32(asin) % 500`.
  Usar un hash garantiza reproducibilidad, pero el grupo no tiene relación con la categoría, la tienda
  ni el comportamiento: las «co-compras» quedan semánticamente al azar.
- La falla está en la interpretación posterior: el informe de la Etapa 8 llamó «temáticas puras» a
  las comunidades sin medir su pureza, y nadie contrastó el grafo con la señal real.
- Corrección posible (no aplicada, porque obliga a regenerar las etapas 0 a 9): agrupar por
  `categoria_hoja` o por vecinos de co-reseña en `_productos_extra`. Así el grafo sintético
  heredaría estructura real y seguiría cumpliendo «misma orden».

---

## Complejidades Big-O corregidas

| Componente | Reportado | Correcto |
|---|---|---|
| PageRank | O(k·\|E\|) «por iteración» | O(\|V\| + \|E\|) por iteración; O(k(\|V\| + \|E\|)) total, k = O(log(1/τ)/log(1/α)) |
| Extracción de co-compras | — | O(Σ_o s_o²) por expansión en cliques: cuadrática en el tamaño de la orden |
| Trie: inserción | O(L) | O(L · M) en el Trie original (orden de la caché en cada nodo); O(L + h·M) en el radix |
| Trie: consulta | O(L) | O(L + k) |
| Trie: memoria | O(Σ\|t\|·Σ) | O(nodos · M) referencias; 505 B por nodo medidos; 7.2 M → 0.72 M nodos con radix |
| TopKHeap | O(N log K) | Peor caso O(N log K). Con orden aleatorio, el número esperado de reemplazos es K·(H_N − H_K) ≈ K ln(N/K) (≈ 115 para N = 10⁶, K = 10), así que el costo es O(N + K log K · ln(N/K)): domina el recorrido O(N) en Python, no el log K. `np.argpartition` es O(N) en C. |
| Louvain | O(\|V\| log \|V\|) empírico | O(pasadas · \|E\|) por nivel; memoria O(\|V\| + \|E\|) en diccionarios (~9× el CSR) |
| Búsqueda (nueva) | — | O(Σ_t df_t) con `np.bincount` + O(C) de selección con `argpartition` |
| Difuso (nuevo) | — | O(\|C_d\| · d); peor caso \|C_d\| = O((L·Σ)^d) |

---

## Archivos modificados

- `src/etapa_8/graph.py`: `componentes_conexas`, `dividir_comunidades_desconectadas`,
  `garantizar_conexion` en Louvain/LPA, métricas de componentes y docstrings de complejidad.
- `src/etapa_8/benchmark.py`: muestreo por componentes completas y grilla γ ∈ {0.5…20} sobre el grafo
  completo.
- `src/etapa_9/service.py`: Trie radix, caché O(M) con semántica de máximo, difuso OSA, índice
  invertido, corrección de consulta, `buscar` reescrito, `ScoringHibrido.pesos_efectivos`,
  normalización de S_pr, catálogo completo, resumen de fraude medido y `ejecutar_etapa` con
  `catalogo_demo`.
- `src/etapa_9/benchmark.py`: líneas base `heapq.nlargest` y `argpartition`, conteo de reemplazos del
  heap y benchmark difuso.
- `src/etapa_9/evaluacion_scoring.py` (nuevo): calibración temporal.
- `src/etapa_8/escala_stream.py`, `src/etapa_8/grafo_resenas_reales.py`, `src/etapa_9/evaluacion_real.py`,
  `src/etapa_9/analisis_campos_reales.py` (nuevos, segunda fase): escala medida, señal real, objetivo
  real y campos reales. `ScoringHibrido.lambda_novedad` y `LAMBDA_NOVEDAD = 0.3` en el servicio.
- `tests/etapa_8/test_graph.py` (+4) y `tests/etapa_9/test_service.py` (+10).
- `scratch/generar_notebook8.py`, `scratch/generar_notebook9.py`, `scratch/generar_dashboard.py`:
  secciones de auditoría y KPIs leídos de las métricas.
- `requirements.txt`.

## Referencias

- Andersen, R., Chung, F., y Lang, K. (2006). Local graph partitioning using PageRank vectors. *FOCS*.
- Bühlmann, H. (1967). Experience rating and credibility. *ASTIN Bulletin*, 4(3).
- Damerau, F. J. (1964). A technique for computer detection and correction of spelling errors. *CACM*, 7(3).
- Fortunato, S., y Barthélemy, M. (2007). Resolution limit in community detection. *PNAS*, 104(1).
- Kyrola, A., Blelloch, G., y Guestrin, C. (2012). GraphChi: Large-scale graph computation on just a PC. *OSDI*.
- Morrison, D. R. (1968). PATRICIA. *JACM*, 15(4).
- Schulz, K. U., y Mihov, S. (2002). Fast string correction with Levenshtein automata. *IJDAR*, 5(1).
- Traag, V. A., Van Dooren, P., y Nesterov, Y. (2011). Narrow scope for resolution-limit-free community detection. *Phys. Rev. E*, 84.
- Traag, V. A., Waltman, L., y van Eck, N. J. (2019). From Louvain to Leiden. *Scientific Reports*, 9.
- Ukkonen, E. (1985). Algorithms for approximate string matching. *Information and Control*, 64.
