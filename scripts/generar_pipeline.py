"""Genera visualizador/pipeline_completo.html con los resultados de notebooks/demo_pipeline.ipynb.

No ejecuta el notebook: lee sus celdas de markdown y las salidas que ya tiene guardadas (textos y
tablas) y las reparte por etapa, junto con el flujo de datos entre etapas. Si el notebook se vuelve
a ejecutar, basta con correr este script otra vez para actualizar la página.

El resultado es un solo archivo HTML que se abre con doble clic, sin servidor.

Uso:
    python scripts/generar_pipeline.py
"""

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLA = RAIZ / "scripts" / "plantilla_pipeline.html"
MARCADOR = "__DATOS_PIPELINE__"

# Etapas: nombre, estructuras principales, y nombre y estructura cortos para el diagrama
ETAPAS = [
    ("Ingesta y buffer", "ColaCircular", "Ingesta", "ColaCircular"),
    ("Preprocesamiento", "Preprocesador: merge sort y búsqueda binaria", "Preproceso", "Preprocesador"),
    ("Cola con prioridades", "MinHeap con aging", "Prioridad", "MinHeap"),
    ("Catálogo, caché y estado", "TablaHash, caché LRU y EstadoProducto", "Catálogo", "TablaHash · LRU"),
    ("Índice de precios", "B+Tree en disco", "Precios", "B+Tree"),
    ("Vecinos por atributos", "KD-Tree", "Atributos", "KD-Tree"),
    ("Detección de fraude", "Bloom Filter y Count-Min Sketch", "Fraude", "Bloom · CMS"),
    ("Relacionados y similares", "MinHash + LSH y HNSW", "Similitud", "MinHash · HNSW"),
    ("Grafo de co-compra", "PageRank y Louvain", "Grafo", "PageRank · Louvain"),
    ("Servicio y salida observable", "Trie radix, índice invertido y MinHeap Top-K", "Servicio", "Top-K híbrido"),
]

# Flujo de datos: (desde, hacia, archivo o dato, etiqueta corta del diagrama, qué lleva).
# "L" es el lote de entrada de la demo y "S" la salida observable de la etapa 9.
FLUJOS = [
    ("L", "0", "registros crudos del lote", "registros crudos",
     "los registros del lote entran uno por uno a la cola"),
    ("0", "1", "etapa0_lotes.jsonl", "micro-lotes",
     "micro-lotes de hasta 16 registros que el consumidor retira de la cola"),
    ("0", "6", "listas_senalados.json", "listas de señalados",
     "cuentas, tarjetas y dispositivos señalados"),
    ("1", "2", "etapa1_lotes.jsonl", "eventos limpios",
     "eventos válidos y normalizados, sin duplicados y ordenados por ts"),
    ("2", "3", "etapa2_eventos.jsonl", "orden de servicio",
     "los eventos en el orden en que los atiende el heap"),
    ("2", "6", "etapa2_eventos.jsonl", "compras y alertas de pago",
     "las compras y las alertas de pago, en el orden de servicio"),
    ("2", "8", "etapa2_eventos.jsonl", "órdenes",
     "las órdenes de dos o más productos del stream"),
    ("3", "4", "catalogo.jsonl", "catálogo",
     "los registros del catálogo en disco; el B+Tree guarda la posición de cada uno"),
    ("3", "5", "catalogo.jsonl y estado_productos.parquet", "catálogo y actividad",
     "los atributos del catálogo y la actividad de cada producto en el stream"),
    ("4", "5", "indice_precio.bpt", "orden de precio",
     "la etapa 5 recorre los productos en orden de precio y lee cada registro con su offset"),
    ("3", "7", "matriz_usuario_producto.npz y catalogo.jsonl", "matriz usuario-producto",
     "los usuarios de cada producto, y el título, la categoría y la tienda"),
    ("6", "7", "alertas.jsonl", "usuarios bloqueados",
     "los usuarios con órdenes bloqueadas, que quedan fuera de los conjuntos de MinHash"),
    ("6", "8", "ordenes_bloqueadas.json", "órdenes bloqueadas",
     "las órdenes bloqueadas, que no entran al grafo"),
    ("3", "9", "catálogo y estado de los productos", "ficha y stock",
     "la ficha del producto y su stock"),
    ("4", "9", "indice_precio.bpt", "filtro de precio",
     "la consulta de rango para filtrar la recomendación por precio"),
    ("5", "9", "KD-Tree por categoría", "arranque en frío",
     "los vecinos por atributos para los productos sin señales de comportamiento"),
    ("6", "9", "alertas.jsonl", "alertas",
     "las alertas para el centro de monitoreo del dashboard"),
    ("7", "9", "relacionados.parquet y similares.parquet", "S_rel · S_sim",
     "las señales S_rel (MinHash + LSH) y S_sim (HNSW)"),
    ("8", "9", "grafo_cocompra.npz y pagerank.parquet", "S_co · S_pr",
     "las señales S_co (peso de la arista) y S_pr (PageRank)"),
    ("9", "S", "demo_consultas.json, metricas.json y dashboard_etapa_9_servicio.html", "consultas",
     "la ficha, el autocompletado, la recomendación y el centro de monitoreo"),
]

# Columnas del resumen del recorrido 2.1 que corresponden a cada etapa
CIFRAS_0_A_2 = {0: ["registros", "micro_lotes", "productor_en_espera"],
                1: ["rechazados", "duplicados"],
                2: ["atendidos", "ascensos_por_aging"]}


class LectorTabla(HTMLParser):
    """Lee el encabezado y las filas de una tabla HTML de pandas."""

    def __init__(self):
        super().__init__()
        self.encabezado, self.filas = [], []
        self._en_thead, self._fila, self._celda = False, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "thead":
            self._en_thead = True
        elif tag == "tbody":
            self._en_thead = False
        elif tag == "tr":
            self._fila = []
        elif tag in ("th", "td") and self._fila is not None:
            self._celda = []

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self._celda is not None:
            self._fila.append("".join(self._celda).strip())
            self._celda = None
        elif tag == "tr" and self._fila is not None:
            (self.encabezado if self._en_thead else self.filas).append(self._fila)
            self._fila = None

    def handle_data(self, data):
        if self._celda is not None:
            self._celda.append(data)


def leer_argumentos():
    parser = argparse.ArgumentParser(description="Genera el visualizador HTML del pipeline completo.")
    parser.add_argument("--notebook", type=Path, default=RAIZ / "notebooks" / "demo_pipeline.ipynb",
                        help="notebook de la demo, con sus salidas guardadas")
    parser.add_argument("--salida", type=Path, default=RAIZ / "visualizador" / "pipeline_completo.html",
                        help="ruta del archivo HTML")
    return parser.parse_args()


def fuente(celda):
    return "".join(celda["source"])


def limpiar_valor(valor, codigo):
    """Quita el '.0' que pandas agrega a los enteros de columnas con vacíos y, si pandas cortó el
    texto con '...', lo completa con el literal que aparece en el código de la celda."""
    if re.fullmatch(r"-?\d+\.0", valor):
        return valor[:-2]
    if valor.endswith("...") and len(valor) > 23:
        cola = valor[-23:-3]
        completo = re.search(re.escape(cola) + r"[^\"'{}]*", codigo)
        if completo:
            return valor[:-23] + completo.group(0)
    return valor


def leer_tabla(html, codigo):
    lector = LectorTabla()
    lector.feed(html)
    columnas = lector.encabezado[0][1:] if lector.encabezado else []
    filas = [[limpiar_valor(v, codigo) for v in fila] for fila in lector.filas]
    # El índice se muestra solo si dice algo (no es 0, 1, 2, ...)
    mostrar_indice = any(fila[0] != str(i) for i, fila in enumerate(filas))
    return {"tipo": "tabla", "columnas": columnas, "mostrar_indice": mostrar_indice,
            "filas": [{"indice": fila[0], "valores": fila[1:]} for fila in filas]}


def evento_de(texto, eventos):
    """'compra' o 'fraude' si el texto nombra uno solo de los dos eventos seguidos."""
    if not texto:
        return None
    nombrados = [nombre for nombre, datos in eventos.items()
                 if datos["id"] in texto or f"({nombre})" in texto]
    return nombrados[0] if len(nombrados) == 1 else None


def bloques_de_celda(celda, eventos):
    """Textos y tablas que dejó una celda de código, en orden. La última línea de un texto que
    precede a una tabla se usa como título de la tabla si termina en ':' o nombra un evento."""
    codigo = fuente(celda)
    salidas = celda.get("outputs", [])
    bloques, titulo = [], None
    for i, salida in enumerate(salidas):
        if salida["output_type"] == "stream":
            lineas = [linea.rstrip() for linea in "".join(salida["text"]).splitlines() if linea.strip()]
            sigue_tabla = i + 1 < len(salidas) and "text/html" in salidas[i + 1].get("data", {})
            if lineas and sigue_tabla and (lineas[-1].endswith(":") or evento_de(lineas[-1], eventos)):
                titulo = lineas.pop().rstrip(":")
            if lineas:
                bloques.append({"tipo": "texto", "lineas": lineas, "evento": evento_de(" ".join(lineas), eventos)})
        elif "text/html" in salida.get("data", {}):
            tabla = leer_tabla("".join(salida["data"]["text/html"]), codigo)
            tabla.update(titulo=titulo, evento=evento_de(titulo, eventos))
            bloques.append(tabla)
            titulo = None
        elif "text/plain" in salida.get("data", {}):
            bloques.append({"tipo": "texto", "lineas": "".join(salida["data"]["text/plain"]).splitlines(),
                            "evento": None})
    return bloques


def seccion(celda, bloques):
    """Agrupa los bloques bajo el título de la celda (su primera línea de comentario)."""
    titulo = fuente(celda).splitlines()[0].lstrip("# ").strip()
    return {"titulo": titulo, "bloques": bloques}


def tabla_markdown(texto):
    """Filas de las tablas en markdown de un texto, sin el encabezado ni el separador."""
    filas = [[c.strip() for c in linea.strip().strip("|").split("|")]
             for linea in texto.splitlines() if linea.startswith("|")]
    return [f for f in filas if not set("".join(f)) <= set("-: ")]


def etapas_de(texto):
    """'Etapas 0 a 2' -> [0, 1, 2]; 'Etapas 4 y 5' -> [4, 5]; '3 y 6' -> [3, 6]."""
    numeros = [int(n) for n in re.findall(r"\d+", texto)]
    if re.search(r"\d+ a \d+", texto):
        return list(range(numeros[0], numeros[-1] + 1))
    return numeros


def cuerpo_markdown(celda):
    """El markdown de una celda sin su encabezado."""
    return "\n".join(l for l in fuente(celda).splitlines() if not l.startswith("#")).strip()


def construir_datos(nb):
    celdas = nb["cells"]
    md = [c for c in celdas if c["cell_type"] == "markdown"]
    codigo = [c for c in celdas if c["cell_type"] == "code"]

    def celda_md(prefijo):
        return next(c for c in md if fuente(c).startswith(prefijo))

    def celda_codigo(prefijo):
        return next(c for c in codigo if fuente(c).startswith(prefijo))

    # Encabezado del notebook: título, subtítulo, eventos seguidos y modo de cada etapa
    intro = celda_md("# ")
    lineas_intro = fuente(intro).splitlines()
    titulo = lineas_intro[0].lstrip("# ").strip()
    subtitulo = next(l for l in lineas_intro[1:] if l.strip())
    tablas_intro = tabla_markdown(fuente(intro))
    eventos = {}
    for fila in tablas_intro:
        ident = fila[0].strip("`")
        if re.fullmatch(r"e\d+", ident):
            nombre = "fraude" if "fraude" in fila[1].lower() or "señalada" in fila[1] else "compra"
            eventos[nombre] = {"id": ident, "que_es": fila[1], "que_muestra": fila[2]}
    modos = {}
    for fila in tablas_intro:
        if fila[1] in ("En vivo", "Consulta"):
            for n in etapas_de(fila[0]):
                modos[n] = {"modo": fila[1], "detalle": fila[2]}

    etapas = [{"numero": n, "nombre": nombre, "estructura": estructura, "corto": corto,
               "estructura_corta": estructura_corta,
               "modo": modos.get(n, {}).get("modo"), "detalle_modo": modos.get(n, {}).get("detalle"),
               "descripcion": "", "secciones": [], "lectura": None, "evento": {}}
              for n, (nombre, estructura, corto, estructura_corta) in enumerate(ETAPAS)]

    # Paso 1: el lote de entrada
    texto_intro = "\n".join(l for l in lineas_intro[1:] if l.strip() != subtitulo).strip()
    lote = {"descripcion": texto_intro + "\n\n" + cuerpo_markdown(celda_md("## Paso 1")),
            "secciones": [{"titulo": "Configuración y lote de entrada", "bloques": bloques_de_celda(c, eventos)}
                          for c in codigo if fuente(c).startswith("import")]}

    # Paso 2: las etapas 0, 1 y 2 comparten la celda 2.1; se reparte por etapa
    paso2 = cuerpo_markdown(celda_md("## Paso 2"))
    for marca in re.finditer(r"^- \*\*Etapa (\d)\.\*\* (.*)$", paso2, re.M):
        etapas[int(marca.group(1))]["descripcion"] = marca.group(2)
    comun = [l for l in paso2.split("\n\n") if not l.startswith("- **")]
    etapas[0]["descripcion"] += "\n\n" + "\n\n".join(comun)

    c21 = celda_codigo("# 2.1")
    b21 = bloques_de_celda(c21, eventos)
    resumen = next(b for b in b21 if b["tipo"] == "tabla" and not b["titulo"])
    valores = dict(zip(resumen["columnas"], resumen["filas"][0]["valores"]))
    lineas = [l for b in b21 if b["tipo"] == "texto" for l in b["lineas"]]
    pasos = [b for b in b21 if b["tipo"] == "tabla" and b["evento"]]
    for n in (0, 1, 2):
        bloques = [{"tipo": "cifras", "titulo": "Cifras del lote",
                    "items": [[c.replace("_", " "), valores[c]] for c in CIFRAS_0_A_2[n] if c in valores]}]
        extra = {1: "Rechazos", 2: "Atendidos"}.get(n)
        if extra:
            bloques.append({"tipo": "texto", "evento": None, "lineas": [l for l in lineas if l.startswith(extra)]})
        for tabla in pasos:
            i_etapa = tabla["columnas"].index("etapa")
            filas = [f for f in tabla["filas"] if f["valores"][i_etapa] == str(n)]
            usadas = [j for j, c in enumerate(tabla["columnas"])
                      if j != i_etapa and any(f["valores"][j] for f in filas)]
            bloques.append({"tipo": "tabla", "titulo": tabla["titulo"], "evento": tabla["evento"],
                            "mostrar_indice": False, "columnas": [tabla["columnas"][j] for j in usadas],
                            "filas": [{"indice": f["indice"], "valores": [f["valores"][j] for j in usadas]}
                                      for f in filas]})
        etapas[n]["secciones"].append(seccion(c21, bloques))
    c22 = celda_codigo("# 2.2")
    etapas[2]["secciones"].append(seccion(c22, bloques_de_celda(c22, eventos)))

    # Pasos 3 a 9: una etapa por paso, con sus celdas de código "# N.x"
    for n in range(3, 10):
        etapas[n]["descripcion"] = cuerpo_markdown(celda_md(f"## Paso {n}"))
        for c in codigo:
            if re.match(rf"# {n}\.\d", fuente(c)):
                etapas[n]["secciones"].append(seccion(c, bloques_de_celda(c, eventos)))

    # Resumen de la traza: qué hizo cada etapa con los dos eventos
    c_resumen = next(c for c in codigo if fuente(c).startswith("# Qué hizo cada etapa"))
    tabla_resumen = bloques_de_celda(c_resumen, eventos)[0]
    columnas_evento = ["fraude" if c.startswith("fraude") else "compra" for c in tabla_resumen["columnas"]]
    for fila in tabla_resumen["filas"]:
        n = int(fila["indice"].split("·")[0])
        etapas[n]["evento"] = dict(zip(columnas_evento, fila["valores"]))

    # Lectura de la traza: una viñeta por grupo de etapas y la conclusión
    lectura = cuerpo_markdown(celda_md("### Lectura de la traza"))
    for marca in re.finditer(r"^- \*\*(Etapas? [^*]+?)\.\*\* (.*)$", lectura, re.M):
        for n in etapas_de(marca.group(1)):
            etapas[n]["lectura"] = {"titulo": marca.group(1), "texto": marca.group(2)}
    conclusion = "\n\n".join(p for p in lectura.split("\n\n") if not p.startswith("- **"))

    return {
        "titulo": titulo, "subtitulo": subtitulo, "eventos": eventos, "lote": lote, "etapas": etapas,
        "flujos": [{"desde": d, "hacia": h, "archivo": a, "corto": c, "texto": t} for d, h, a, c, t in FLUJOS],
        "resumen": {"tabla": tabla_resumen, "lectura": lectura, "conclusion": conclusion},
    }


def main():
    args = leer_argumentos()

    print(f"Leyendo las salidas guardadas de {args.notebook.name} ...")
    nb = json.loads(args.notebook.read_text(encoding="utf-8"))
    datos = construir_datos(nb)

    # El JSON va dentro de una etiqueta <script>; "</" se escapa para que un texto como
    # "</script>" dentro de los datos no cierre la etiqueta antes de tiempo.
    texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    plantilla = PLANTILLA.read_text(encoding="utf-8")
    assert plantilla.count(MARCADOR) == 1, "la plantilla debe tener un único marcador de datos"
    html = plantilla.replace(MARCADOR, texto)

    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(html, encoding="utf-8")

    secciones = sum(len(e["secciones"]) for e in datos["etapas"])
    tablas = sum(1 for e in datos["etapas"] for s in e["secciones"] for b in s["bloques"] if b["tipo"] == "tabla")
    print(f"Etapas: {len(datos['etapas'])} · secciones: {secciones} · tablas: {tablas} · "
          f"flujos: {len(datos['flujos'])}")
    print(f"Escrito {args.salida} ({args.salida.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
