"""Genera el visualizador de las etapas 3, 4 y 5 (results/visualizador/pipeline_345.html).

Uso, desde la raíz del proyecto:

    python scripts/generar_visualizador_345.py --traza results/visualizador/pipeline.html

1. Lee la traza de las etapas 0 a 2 (el HTML generado por scripts/generar_visualizador.py o su
   JSON) y toma los eventos atendidos por la etapa 2 en su orden de salida, con sus mismos #tid.
2. Lee de catalogo.jsonl (src/etapa_3/storage.py) el registro de cada producto de esos eventos.
   Con --demo, o si src/ no se puede importar, usa un catálogo sintético estable por producto y
   el visualizador lo indica en la cabecera.
3. Corre las etapas 3, 4 y 5 con las estructuras instrumentadas de src/trace_345.py.
4. Si src/ está disponible, repite el trabajo con las clases reales (CacheLRU, ArbolBMas,
   KDTree, knn_fuerza_bruta) y comprueba que den los mismos resultados. Si no coinciden, se
   detiene sin escribir el HTML.
5. Incrusta la traza como JSON en scripts/plantilla_visualizador_345.html.
"""

import argparse
import json
import math
import re
import sys
import tempfile
import zlib
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from src import trace_345 as t345  # noqa: E402

PLANTILLA = Path(__file__).with_name("plantilla_visualizador_345.html")
MARCA = '<script id="datos-traza" type="application/json"></script>'


# ---------------------------------------------------------------------
# Entrada: traza de las etapas 0 a 2
# ---------------------------------------------------------------------
def leer_traza_012(ruta):
    texto = Path(ruta).read_text(encoding="utf-8")
    if ruta.suffix.lower() in (".html", ".htm"):
        m = re.search(r'<script id="datos-traza" type="application/json">(.*?)</script>', texto, re.S)
        if not m:
            raise SystemExit(f"{ruta} no tiene la traza incrustada")
        texto = m.group(1)
    return json.loads(texto)


def eventos_atendidos(traza):
    atendidos = [e for e in traza["eventos"] if e["estado"] == "atendido"]
    atendidos.sort(key=lambda e: e["orden_salida"])
    return [(e["tid"], e["normalizado"]) for e in atendidos]


# ---------------------------------------------------------------------
# Catálogo: el real de la etapa 3 o uno sintético para la demostración
# ---------------------------------------------------------------------
def catalogo_real(asins):
    from src.etapa_3.storage import AlmacenCatalogo
    almacen = AlmacenCatalogo()
    catalogo = {}
    for a in asins:
        posicion = almacen.indice.obtener(a)
        if posicion is None:
            raise SystemExit(f"{a} no está en el catálogo")
        registro = almacen.leer(a)
        offset, largo = posicion
        catalogo[a] = {**registro, "offset": offset, "largo": largo}
    almacen.cerrar()
    return catalogo


CATEGORIAS_DEMO = ["Toner Cartridges", "Inkjet Printers", "Gel Pens", "Packing Tape", "Copy Paper",
                   "Labels", "Binders", "Office Chairs", "Desk Organizers", "Shredders"]


def catalogo_demo(eventos):
    """Registro sintético estable por producto. El precio usa el de las compras cuando existe."""
    precio_compra, ratings = {}, {}
    for _, ev in eventos:
        a = ev["parent_asin"]
        if a is None:
            continue
        if ev["event_type"] == "purchase" and ev.get("price") is not None:
            precio_compra.setdefault(a, ev["price"])
        if ev["event_type"] == "review" and ev.get("rating") is not None:
            ratings.setdefault(a, []).append(ev["rating"])
    catalogo, offset = {}, 0
    for a in sorted({ev["parent_asin"] for _, ev in eventos if ev["parent_asin"]}):
        h = zlib.crc32(a.encode())
        sin_precio = h % 10 < 4                         # ~40 % sin precio, como el catálogo real (60 %)
        if a in precio_compra:
            precio = None if sin_precio else round(precio_compra[a], 2)
        else:
            precio = None if sin_precio else round(math.exp(1.5 + (h >> 4) % 1000 / 1000 * 3.5), 2)
        rating = round(sum(ratings[a]) / len(ratings[a]), 1) if a in ratings else round(3 + (h >> 8) % 21 / 10, 1)
        largo = 300 + (h >> 12) % 400
        catalogo[a] = {"parent_asin": a, "title": None, "precio": precio, "average_rating": rating,
                       "rating_number": int(math.exp((h >> 16) % 1000 / 1000 * 9)),
                       "categoria_hoja": CATEGORIAS_DEMO[(h >> 20) % len(CATEGORIAS_DEMO)],
                       "store": None, "offset": offset, "largo": largo}
        offset += largo
    return catalogo


# ---------------------------------------------------------------------
# Comprobación contra las clases reales de src/
# ---------------------------------------------------------------------
def verificar_con_src(eventos, catalogo, r3, r4, r5, args):
    """Mismas entradas en las clases reales; devuelve una lista de (comprobación, resultado)."""
    filas = []
    from src.etapa_3.storage import CacheLRU
    cache = CacheLRU(args.capacidad_cache)
    for _, ev in eventos:
        a = ev["parent_asin"]
        if a is not None and cache.obtener(a) is None:
            cache.poner(a, catalogo[a])
    mia = r3["cache"]
    filas.append(("CacheLRU: aciertos, fallos y desalojos",
                  (cache.aciertos, cache.fallos, cache.desalojos) == (mia.aciertos, mia.fallos, mia.desalojos)))

    from src.etapa_4.bplustree import ArbolBMas, capacidad_hoja, capacidad_interno
    tam = next((p for p in range(64, 4097) if capacidad_hoja(p) == args.max_hoja
                and capacidad_interno(p) >= args.max_interno), None)
    if tam is None or capacidad_interno(tam) != args.max_interno:
        filas.append((f"ArbolBMas: no hay tamaño de página con {args.max_hoja} entradas por hoja y "
                      f"{args.max_interno} claves por nodo interno", None))
    else:
        with tempfile.TemporaryDirectory() as carpeta:
            real = ArbolBMas(Path(carpeta) / "v.bpt", tam_pagina=tam, capacidad_buffer=4, crear=True)
            real.carga_masiva(r4["carga"])
            for p, a, o, l in r4["nuevos"]:
                real.insertar(p, a, o, l)
            mio = r4["arbol"]
            filas.append(("ArbolBMas: entradas en orden de hojas", [tuple(e) for e in real] == list(mio)))
            filas.append(("ArbolBMas: altura y páginas", (real.altura, real.n_paginas) == (mio.altura, mio.n_paginas)))
            iguales = all([tuple(e) for e in real.rango(d, h)] == r for (d, h), r in zip(args.rangos, r4["rangos"]))
            filas.append(("ArbolBMas: resultados de los rangos", iguales))
            real.cerrar()

    import numpy as np
    from src.etapa_5.kdtree import KDTree, knn_fuerza_bruta
    kd = KDTree(r5["Z"])
    Z = r5["Z"]
    sin_etiqueta = np.zeros(len(Z), dtype=np.int64)
    iguales = True
    for asin, res in zip(args.consultas_usadas, r5["knn"]):
        q = Z[r5["asins"].index(asin)]
        real = [round(d, 9) for d, _ in kd.knn(q, args.k)]
        fb = [round(d, 9) for d, _ in knn_fuerza_bruta(Z, q, args.k, sin_etiqueta, None)]
        iguales &= real == fb == [round(d, 9) for d, _ in res]
    filas.append(("KDTree.knn y knn_fuerza_bruta de src/: mismas distancias", iguales))
    return filas


# ---------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------
def principal():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traza", type=Path, default=RAIZ / "results" / "visualizador" / "pipeline.html",
                    help="HTML o JSON con la traza de las etapas 0 a 2")
    ap.add_argument("--salida", type=Path, default=RAIZ / "results" / "visualizador" / "pipeline_345.html")
    ap.add_argument("--demo", action="store_true", help="usar un catálogo sintético en lugar de catalogo.jsonl")
    ap.add_argument("--capacidad-cache", type=int, default=8)
    ap.add_argument("--max-hoja", type=int, default=4)
    ap.add_argument("--max-interno", type=int, default=4)
    ap.add_argument("--llenado", type=float, default=0.9)
    ap.add_argument("--rangos", default="10-20,40-80", help="rangos de precio para la etapa 4")
    ap.add_argument("--tam-hoja-kd", type=int, default=4)
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    args.rangos = [tuple(float(x) for x in r.split("-")) for r in args.rangos.split(",")]

    traza012 = leer_traza_012(args.traza)
    eventos = eventos_atendidos(traza012)
    asins = sorted({ev["parent_asin"] for _, ev in eventos if ev["parent_asin"]})

    demo = args.demo
    stock_inicial = t345.stock_inicial_demo
    if not demo:
        try:
            catalogo = catalogo_real(asins)
            from src.etapa_3.storage import stock_inicial
        except Exception as error:                     # sin src/ o sin datos: modo demostración
            print(f"No se pudo leer el catálogo real ({error}). Se usa el catálogo de demostración.")
            demo = True
    if demo:
        catalogo = catalogo_demo(eventos)

    traza = t345.Traza()
    r3 = t345.etapa3(traza, eventos, catalogo, args.capacidad_cache, stock_inicial)
    primer_tid = {}
    for tid, ev in eventos:
        if ev["parent_asin"]:
            primer_tid.setdefault(ev["parent_asin"], tid)
    r4 = t345.etapa4(traza, r3, catalogo, args.max_hoja, args.max_interno, args.llenado, args.rangos, primer_tid)

    # Consultas KNN: los dos productos indexados con más compras en el stream
    indexados = {a for _, a, _, _ in r4["arbol"]}
    compras = Counter(ev["parent_asin"] for _, ev in eventos if ev["event_type"] == "purchase")
    args.consultas_usadas = [a for a, _ in compras.most_common() if a in indexados][:2]
    caja = {"min": [math.log1p(20), 4.5, math.log1p(1000), -1e9], "max": [math.log1p(40), 5.0, 1e9, 1e9],
            "texto": "precio entre 20 y 40 dólares, rating ≥ 4.5 y al menos 1 000 calificaciones"}
    r5 = t345.etapa5(traza, r3, r4, catalogo, args.tam_hoja_kd, args.k, args.consultas_usadas, caja)

    # Invariantes que deben cumplirse siempre
    m = r3["cache"]
    con_producto = sum(1 for _, ev in eventos if ev["parent_asin"])
    assert m.aciertos + m.fallos == con_producto
    suma_pesos = sum(t345.PESOS.get(ev["event_type"], 0) for _, ev in eventos if ev["parent_asin"])
    assert abs(sum(r3["celdas"].values()) - suma_pesos) < 1e-9
    assert all(p["detalle"]["iguales"] for p in traza.pasos if p["accion"] in ("knn_fin", "rango_kd_fin"))

    verificacion = []
    if not demo:
        verificacion = verificar_con_src(eventos, catalogo, r3, r4, r5, args)
        for texto, ok in verificacion:
            print(f"  {'OK ' if ok else ('-- ' if ok is None else 'ERROR')} {texto}")
        if any(ok is False for _, ok in verificacion):
            raise SystemExit("Las estructuras instrumentadas no coinciden con src/. No se escribe el HTML.")

    # Datos para la página
    estados = r3["estados"]
    tid_de_punto = [primer_tid.get(a) for a in r5["asins"]]
    productos = {}
    for a in asins:
        reg = catalogo[a]
        productos[a] = {
            "precio": reg["precio"], "average_rating": reg["average_rating"], "rating_number": reg["rating_number"],
            "categoria": reg.get("categoria_hoja"), "titulo": (reg.get("title") or "")[:90] or None,
            "offset": reg["offset"], "largo": reg["largo"], "primer_tid": primer_tid.get(a),
            "estado_final": estados[a].como_dict() if a in estados else None}
    datos = {
        "parametros": {
            "categoria": traza012["parametros"].get("categoria"), "semilla": traza012["parametros"].get("semilla"),
            "demo": demo, "capacidad_cache": args.capacidad_cache, "max_hoja": args.max_hoja,
            "max_interno": args.max_interno, "llenado": args.llenado, "tam_hoja_kd": args.tam_hoja_kd, "k": args.k,
            "rangos": args.rangos, "caja": caja, "pesos": t345.PESOS, "atributos": t345.ATRIBUTOS,
            "eventos_entrada": len(eventos), "eventos_traza_012": len(traza012["eventos"]),
        },
        "verificacion_src": [[texto, ok] for texto, ok in verificacion],
        "eventos": {tid: {k: ev.get(k) for k in ("event_id", "event_type", "user_id", "parent_asin", "price",
                                                   "rating", "order_type", "query", "ts")}
                    | {"orden_salida": n + 1} for n, (tid, ev) in enumerate(eventos)},
        "productos": productos,
        "matriz": {"usuarios": list(r3["usuarios"]), "productos": list(r3["productos"])},
        "kd": {**r5["kd"].estructura(), "asins": r5["asins"], "tid": tid_de_punto,
               "X": r5["X"].round(4).tolist(), "Z": r5["Z"].round(5).tolist(),
               "media": r5["media"].round(4).tolist(), "desv": r5["desv"].round(4).tolist()},
        "pasos": traza.pasos,
    }
    texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    if MARCA not in plantilla:
        raise SystemExit("La plantilla no tiene el marcador de datos")
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(plantilla.replace(MARCA, MARCA.replace("></", f">{texto}</")), encoding="utf-8")
    print(f"{args.salida}: {len(traza.pasos)} pasos, {len(texto) / 1e3:.0f} KB de datos"
          f"{' (catálogo de demostración)' if demo else ''}")


if __name__ == "__main__":
    principal()
