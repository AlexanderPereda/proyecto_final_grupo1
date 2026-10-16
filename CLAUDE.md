# CLAUDE.md

## Uso de templates

La carpeta `templates/` contiene la estructura estándar de los documentos y outputs del proyecto. Antes de realizar cualquiera de las acciones listadas, se lee el template correspondiente y se sigue de forma estricta.

| Acción | Template |
| --- | --- |
| Redactar un mensaje de commit | `templates/commit.md` |

## Estilo de redacción

- Las respuestas se redactan en lenguaje sencillo, declarativo y algorítmico.
- Las respuestas se limitan a lo solicitado por el usuario. No se explica lo que no se ha pedido. Por defecto, una explicación tiene una extensión máxima de un párrafo (5 líneas), salvo que el usuario indique otra extensión, como una línea o cuatro párrafos.
- Las sugerencias se mantienen dentro de la línea principal de la consulta. Solo se ofrecen sugerencias sobre otros temas cuando el usuario las solicita explícitamente.
- No se usan dos puntos con función explicativa. Ejemplo incorrecto: "la causa es clara: falta memoria". Ejemplo correcto: "la causa es la falta de memoria".
- No se usa lenguaje de marketing ni fórmulas enfáticas, como "no es A, es B", u otras expresiones de hype.
- No se usan incisos entre rayas. Ejemplo incorrecto: "el cambio 2 —el cual fue el más crítico— se trabajó con cuidado". Ejemplo correcto: "el cambio 2 fue el más crítico y se trabajó con cuidado".

## Registro

- El proyecto es un trabajo formal de posgrado con rigor científico. Todo texto producido mantiene un registro formal, preciso y objetivo.
