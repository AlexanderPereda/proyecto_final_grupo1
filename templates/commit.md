# Template: Commit

## Estructura

    TITULO DEL COMMIT

    - accion atomica 1
    - accion atomica 2
    - accion atomica n

## Título

- Frase declarativa que nombra la tarea realizada.
- Es general, pero delimita con claridad el alcance de la tarea.
- No incluye explicaciones, motivos ni contexto.
- Se escribe en MAYÚSCULAS.
- Va separado de la descripción por una línea en blanco.

## Descripción

- Registra exclusivamente cambios realizados en el código del repositorio; no describe ningún otro tipo de avance o cambio del proyecto.
- Es una lista en viñetas, sin límite de cantidad. Cada viñeta registra una única acción concreta realizada sobre el código del repositorio: creación, modificación, eliminación, movimiento o renombrado de archivos, carpetas, funciones, clases, configuraciones, etc.
- Las viñetas se redactan en forma declarativa, atómica e impersonal: "se creo", "se modifico", "se elimino".
- Cuando aplique, se indica la ruta del archivo o carpeta afectada.
- No son explicaciones metodológicas: no incluyen el porqué, quién lo pidió ni los beneficios del cambio.
- Se escriben en minúsculas.

## Formato

- No se usan tildes ni la letra ñ, ni en el título ni en la descripción.
- Ni el título ni las viñetas se cortan con saltos de línea por longitud.
- No se agrega la línea `Co-Authored-By` ni ningún otro trailer.

## Ejemplos correctos

### Ejemplo correcto 1

    IMPLEMENTACION DEL BLOOM FILTER

    - se creo el archivo etapa_6/bloom_filter.py
    - se agrego la clase BloomFilter en etapa_6/bloom_filter.py
    - se agrego el metodo insertar a la clase BloomFilter
    - se agrego el metodo consultar a la clase BloomFilter
    - se agrego la funcion calcular_parametros_optimos en etapa_6/bloom_filter.py

### Ejemplo correcto 2

    CORRECCION DE LA DEDUPLICACION EN EL PREPROCESAMIENTO POR LOTES

    - se modifico la funcion busqueda_binaria en etapa_1/deduplicacion.py
    - se reemplazo el ordenamiento por insercion por merge sort en etapa_1/ordenamiento.py
    - se elimino la funcion eliminar_duplicados_lineal de etapa_1/deduplicacion.py

### Ejemplo correcto 3

    REORGANIZACION DE LOS NOTEBOOKS DE LA ETAPA 6

    - se creo la carpeta notebooks/etapa_6/
    - se movio notebooks/bloom.ipynb a notebooks/etapa_6/bloom.ipynb
    - se renombro notebooks/prueba1.ipynb a notebooks/etapa_6/benchmark_count_min.ipynb
    - se elimino el archivo notebooks/borrador.ipynb

## Ejemplos incorrectos

### Ejemplo incorrecto 1

    cambio en los scripts para poder trabajar mejor el modelado de usuarios pues el usuario solicito un sistema normalizado

    - se normalizaron los elementos que antes no estaban porque de esa manera se corrige mejor los campos trabajados pues la columna X y la columna Y no comparten foreign key y por lo tanto no pueden efectuar join
    - el usuario solicito modificacion de un codigo, se realizo y se puso un nuevo codigo en su lugar con mejoras importantes

Errores:

- El título está en minúsculas y explica el motivo del cambio.
- La primera viñeta justifica el cambio en lugar de registrar la acción.
- La segunda viñeta menciona quién lo pidió, agrupa varias acciones y valora el resultado ("mejoras importantes").
- Ninguna viñeta indica el archivo afectado.

### Ejemplo incorrecto 2

    CAMBIOS VARIOS

    - Agregué el count-min sketch y arreglé unos bugs del heap
    - se reviso con el equipo la eleccion entre HNSW e IVF
    - se actualizo la funcion de hash para reducir los falsos positivos y asi
      cumplir con el umbral definido en el informe

Errores:

- El título es genérico y no delimita la tarea.
- La primera viñeta empieza con mayúscula, está en primera persona, usa tildes y agrupa dos acciones.
- La segunda viñeta registra una reunión, no un cambio de código.
- La tercera viñeta explica el motivo y está cortada con un salto de línea.

### Ejemplo incorrecto 3

    IMPLEMENTACIÓN DEL GRAFO DE CO-COMPRA PORQUE SE NECESITA PARA LAS RECOMENDACIONES
    - se creó el archivo etapa_8/grafo.py
    - se implemento pagerank
    - se descargo el dataset de kaggle

    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

Errores:

- El título usa tildes y explica el motivo ("porque...").
- Falta la línea en blanco entre el título y la descripción.
- La primera viñeta usa tilde.
- La segunda viñeta no indica el archivo afectado.
- La tercera viñeta registra una descarga de datos, no un cambio de código.
- Incluye el trailer Co-Authored-By.
