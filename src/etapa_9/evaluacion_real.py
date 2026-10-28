"""Evaluación del recomendador con un objetivo real: el siguiente producto que reseña cada usuario.

Auditoría 2026-10-27. Las co-compras del stream son sintéticas (grupos crc32(asin) % 500), así que
calibrar contra ellas mide el generador, no el catálogo. Aquí el objetivo sale de las reseñas reales:
- Corte temporal en el cuantil 0.9 de los timestamps de las 12.8 M reseñas.
- Entrenamiento: grafo de co-reseñas (pares consecutivos por usuario) con las reseñas anteriores al
  corte y su PageRank.
- Prueba: para usuarios con reseñas a ambos lados del corte, ancla = último producto antes del corte,
  relevante = primer producto distinto después del corte.

Señales (todas sin futuro): S_sim (vecinos HNSW de la etapa 7, solo contenido), S_pr (PageRank del
grafo de entrenamiento en escala log), S_co (co-reseñas de entrenamiento, w / w_max del ancla) y
S_cat (1 si comparte categoria_hoja con el ancla). Candidatos: vecinos de co-reseña ∪ vecinos HNSW ∪
los 20 más populares de la categoría del ancla (generador para cold start).

Generadores de candidatos (GENERADORES): 'base' (los de arriba) y 'ampliado', que suma vecinos a
2 saltos del grafo de co-reseñas (su S_co es 0.5 · peso de camino normalizado) y los 20 más
populares de la misma tienda (la homofilia de tienda de las co-reseñas es 95× la del azar).

Fuentes del grafo de S_co, S_pr y los 2 saltos (FUENTES): 'coresena' (reseñas reales), 'sintetico'
(co-compras del generador de la etapa 0, regenerado solo con reseñas y compras anteriores al corte,
como en src/etapa_8/escala_stream.py) y 'ambos' (suma de pesos de los dos grafos). Regla de
adopción fijada antes de medir: una fuente reemplaza a 'sintetico' en el servicio si, con el
generador ampliado y el híbrido estático con λ = 0.3, mejora HitRate@10 y NDCG@10 en >= 5 % relativo
sin subir el Gini de exposición más de 0.05.

Diversidad: el Gini de exposición mide cuán concentradas están las recomendaciones (0 = todos los
productos aparecen igual; 1 = siempre los mismos). Para BAJARLO se re-rankea con
score_λ = (1 - λ) · score + λ · (1 - S_pr) (premio a la novedad) y se reporta la frontera
precisión-diversidad.

Ejecutar: .venv/bin/python -m src.etapa_9.evaluacion_real
Salida: results/etapa_9/evaluacion_real.csv y evaluacion_real_resumen.json
"""

import json
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.optimize import minimize

from .. import config
from ..etapa_8.escala_stream import grafo_desde_stream
from ..etapa_8.graph import pagerank, sumar_grafos
from ..etapa_8.grafo_resenas_reales import construir_grafo

DIR_RESULTADOS = config.RAIZ / "results" / "etapa_9"
K = 10
CUANTIL_CORTE = 0.9
N_USUARIOS = 80_000
TOP_CATEGORIA = 20
TOP_TIENDA = 20
VECINOS_2_SALTOS = 20         # vecinos directos que se expanden y vecinos de cada uno
GENERADORES = ["base", "ampliado"]
FUENTES = ["coresena", "sintetico", "ambos"]
N_RESENAS_TOTAL = 12_845_712
MEJORA_MINIMA = 0.05
SUBIDA_GINI_MAX = 0.05
LAMBDAS = [0.0, 0.05, 0.1, 0.2, 0.3, 0.5]
MODELOS_FIJOS = {
    # pesos (sim, pr, co, cat)
    "solo_similitud": (1.0, 0.0, 0.0, 0.0),
    "solo_pagerank": (0.0, 1.0, 0.0, 0.0),
    "solo_coresena": (0.0, 0.0, 1.0, 0.0),
    "solo_categoria": (0.0, 0.0, 0.0, 1.0),
    # el híbrido del servicio sin S_rel (renormalizado: 0.35, 0.20, 0.20 sobre 0.75)
    "hibrido_estatico": (0.35 / 0.75, 0.20 / 0.75, 0.20 / 0.75, 0.0),
}


def gini(conteos):
    x = np.sort(np.asarray(conteos, dtype=np.float64))
    if len(x) == 0 or x.sum() == 0:
        return 0.0
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1) @ x / (n * x.sum()))


def main():
    t0 = time.perf_counter()
    df = pq.read_table(config.RUTA_RESENAS, columns=["user_id", "parent_asin", "timestamp"]).to_pandas()
    corte = float(np.quantile(df["timestamp"].to_numpy(), CUANTIL_CORTE))
    ent = df[df["timestamp"] < corte]
    pru = df[df["timestamp"] >= corte]
    grafo_resenas = construir_grafo(ent)
    print(f"co-reseñas de entrenamiento: {grafo_resenas.n_nodos:,} nodos / {grafo_resenas.n_aristas:,} aristas "
          f"({time.perf_counter() - t0:.0f} s)", flush=True)

    # Pares (ancla, siguiente) de prueba
    ultimo = ent.sort_values("timestamp").groupby("user_id")["parent_asin"].last()
    primero = pru.sort_values("timestamp").groupby("user_id")["parent_asin"].first()
    pares = pd.concat([ultimo.rename("ancla"), primero.rename("siguiente")], axis=1, join="inner")
    pares = pares[pares["ancla"] != pares["siguiente"]]
    n_pares_total = len(pares)
    pares = pares.sample(n=min(N_USUARIOS, len(pares)), random_state=config.SEED)
    del df, ent, pru

    grafo_sint, stats_sint = grafo_desde_stream(N_RESENAS_TOTAL, ts_max=corte)
    print(f"co-compra sintética sin futuro: {grafo_sint.n_nodos:,} nodos / {grafo_sint.n_aristas:,} aristas "
          f"({time.perf_counter() - t0:.0f} s)", flush=True)
    grafos = {"coresena": grafo_resenas, "sintetico": grafo_sint}
    grafos["ambos"] = sumar_grafos(grafo_resenas, grafo_sint)

    catalogo = pd.read_json(config.CARPETA_INTERIM / "etapa_3" / "catalogo.jsonl", lines=True)
    categoria = dict(zip(catalogo["parent_asin"], catalogo["categoria_hoja"]))
    tienda = dict(zip(catalogo["parent_asin"], catalogo["store"]))
    similares = defaultdict(dict)
    sim_df = pd.read_parquet(config.CARPETA_INTERIM / "etapa_7" / "similares.parquet")
    for a, v, d in zip(sim_df["parent_asin"], sim_df["vecino"], sim_df["distancia"]):
        similares[a][v] = max(0.0, 1.0 - float(d))
    del sim_df

    lista = list(pares.itertuples())
    mitad = len(lista) // 2
    cal, prueba = lista[:mitad], lista[mitad:]
    filas = []
    resumen = {"corte_timestamp_ms": corte, "pares_disponibles": n_pares_total, "pares_evaluados": len(lista),
               "pares_calibracion": len(cal), "pares_prueba": len(prueba), "co_compra_sintetica": stats_sint}
    for fuente in FUENTES:
        grafo = grafos[fuente]
        candidatos = preparar_candidatos(grafo, categoria, tienda, similares)
        resumen[fuente] = {"nodos": grafo.n_nodos, "aristas": grafo.n_aristas}
        generadores = GENERADORES if fuente == "coresena" else ["ampliado"]
        for generador in generadores:
            filas_g, info = evaluar_generador(generador, lista, cal, prueba, candidatos, categoria,
                                              completo=(fuente == "coresena"))
            for f in filas_g:
                f["fuente"] = fuente
            filas += filas_g
            resumen[fuente][generador] = info
            pd.DataFrame(filas).to_csv(DIR_RESULTADOS / "evaluacion_real.csv", index=False)

    # Regla de adopción de la fuente de co-compra (fijada antes de medir)
    df_res = pd.DataFrame(filas)
    elegido = df_res[(df_res.generador == "ampliado") & (df_res.modelo == "hibrido_estatico")
                     & (df_res.lambda_novedad == 0.3)].set_index("fuente")
    base = elegido.loc["sintetico"]
    regla = {}
    for fuente in ("coresena", "ambos"):
        f = elegido.loc[fuente]
        regla[fuente] = {
            "mejora_hitrate": float(f["hitrate@10"] / base["hitrate@10"] - 1),
            "mejora_ndcg": float(f["ndcg@10"] / base["ndcg@10"] - 1),
            "subida_gini": float(f["gini_exposicion"] - base["gini_exposicion"]),
        }
        regla[fuente]["cumple"] = bool(regla[fuente]["mejora_hitrate"] >= MEJORA_MINIMA
                                       and regla[fuente]["mejora_ndcg"] >= MEJORA_MINIMA
                                       and regla[fuente]["subida_gini"] <= SUBIDA_GINI_MAX)
    validas = [f for f in regla if regla[f]["cumple"]]
    regla["fuente_adoptada"] = max(validas, key=lambda f: elegido.loc[f, "ndcg@10"]) if validas else "sintetico"
    resumen["regla_fuente_cocompra"] = regla
    resumen["segundos"] = time.perf_counter() - t0
    (DIR_RESULTADOS / "evaluacion_real_resumen.json").write_text(json.dumps(resumen, indent=2), encoding="utf-8")
    print(json.dumps(resumen, indent=2), flush=True)


def preparar_candidatos(grafo, categoria, tienda, similares):
    """Construye la función de candidatos y señales para un grafo de co-compra dado."""
    pr, _, _ = pagerank(grafo)
    log_pr = np.log(pr)
    s_pr = (log_pr - log_pr.min()) / (log_pr.max() - log_pr.min())
    pos = {a: i for i, a in enumerate(grafo.asins)}
    populares_cat = defaultdict(list)
    populares_tienda = defaultdict(list)
    orden_pr = np.argsort(-pr)
    for i in orden_pr:
        c = categoria.get(grafo.asins[i])
        if c is not None and len(populares_cat[c]) < TOP_CATEGORIA:
            populares_cat[c].append(grafo.asins[i])
        t_ = tienda.get(grafo.asins[i])
        if t_ and len(populares_tienda[t_]) < TOP_TIENDA:
            populares_tienda[t_].append(grafo.asins[i])

    def candidatos(a, generador="base"):
        """Devuelve (asins, X) con X = [S_sim, S_pr, S_co, S_cat] por candidato."""
        cand = {}
        idx = pos.get(a)
        if idx is not None:
            vecinos, w = grafo.vecinos(idx)
            if len(w):
                wmax = w.max()
                for j, wj in zip(vecinos.tolist(), w.tolist()):
                    cand.setdefault(grafo.asins[j], [0.0, 0.0])[1] = wj / wmax
        for v, s in similares.get(a, {}).items():
            cand.setdefault(v, [0.0, 0.0])[0] = s
        for v in populares_cat.get(categoria.get(a), ()):
            cand.setdefault(v, [0.0, 0.0])
        if generador == "ampliado":
            for v in populares_tienda.get(tienda.get(a), ()):
                cand.setdefault(v, [0.0, 0.0])
            if idx is not None:
                vecinos, w = grafo.vecinos(idx)
                camino = defaultdict(float)
                for j in np.argsort(-w)[:VECINOS_2_SALTOS]:
                    v2, w2 = grafo.vecinos(int(vecinos[j]))
                    for m in np.argsort(-w2)[:VECINOS_2_SALTOS]:
                        camino[int(v2[m])] += float(w[j] * w2[m])
                if camino:
                    cmax = max(camino.values())
                    for m, peso in camino.items():
                        c = cand.setdefault(grafo.asins[m], [0.0, 0.0])
                        c[1] = max(c[1], 0.5 * peso / cmax)
        cand.pop(a, None)
        asins = sorted(cand)
        cat_a = categoria.get(a)
        X = np.array([[cand[c][0], s_pr[pos[c]] if c in pos else 0.0, cand[c][1],
                       1.0 if cat_a is not None and categoria.get(c) == cat_a else 0.0] for c in asins])
        return asins, X.reshape(-1, 4)

    return candidatos


def _evaluar(cache, prueba, categoria, pesos, lam):
    pesos = np.asarray(pesos)
    aciertos, ndcg, cubiertos = 0.0, 0.0, 0
    exposicion = defaultdict(int)
    categorias_distintas = []
    universo = set()
    for p in prueba:
        asins, X = cache[p.ancla]
        if not asins:
            continue
        universo.update(asins)
        score = X @ pesos
        rango = score.max() - score.min()
        score = (score - score.min()) / rango if rango > 0 else np.zeros_like(score)
        score = (1 - lam) * score + lam * (1 - X[:, 1])
        top = np.lexsort((np.array(asins), -score))[:K]
        elegidos = [asins[i] for i in top]
        for c in elegidos:
            exposicion[c] += 1
        categorias_distintas.append(len({categoria.get(c) for c in elegidos}) / len(elegidos))
        cubiertos += p.siguiente in asins
        if p.siguiente in elegidos:
            r = elegidos.index(p.siguiente)
            aciertos += 1
            ndcg += 1 / np.log2(r + 2)
    n = len(prueba)
    return {"hitrate@10": aciertos / n, "ndcg@10": ndcg / n,
            "cobertura_catalogo@10": len(exposicion) / max(len(universo), 1),
            "gini_exposicion": gini(list(exposicion.values())),
            "diversidad_categorias_lista": float(np.mean(categorias_distintas)),
            "relevante_en_candidatos": cubiertos / n}


def evaluar_generador(generador, lista, cal, prueba, candidatos, categoria, completo=True):
    t0 = time.perf_counter()
    cache = {}
    for p in lista:
        if p.ancla not in cache:
            cache[p.ancla] = candidatos(p.ancla, generador)
    n_cand = np.mean([len(cache[p.ancla][0]) for p in prueba])
    print(f"[{generador}] candidatos listos ({n_cand:.0f} por ancla, {time.perf_counter() - t0:.0f} s)", flush=True)

    if not completo:
        modelos = {n: MODELOS_FIJOS[n] for n in ("solo_coresena", "hibrido_estatico")}
        filas = []
        for nombre, pesos in modelos.items():
            for lam in ((0.0, 0.3) if nombre == "hibrido_estatico" else (0.0,)):
                fila = {"generador": generador, "modelo": nombre, "lambda_novedad": lam,
                        "pesos_sim_pr_co_cat": [round(float(w), 4) for w in pesos]}
                fila.update(_evaluar(cache, prueba, categoria, pesos, lam))
                filas.append(fila)
                print(fila, flush=True)
        return filas, {"candidatos_por_ancla": float(n_cand)}

    # Regresión logística no negativa sobre calibración
    xs, ys = [], []
    for p in cal:
        asins, X = cache[p.ancla]
        xs.append(X)
        ys.append(np.array([c == p.siguiente for c in asins], dtype=np.float64))
    X = np.vstack(xs)
    Y = np.concatenate(ys)
    Xb = np.hstack([X, np.ones((len(X), 1))])

    def perdida(beta):
        z = Xb @ beta
        p_ = 1.0 / (1.0 + np.exp(-z))
        return (np.sum(np.logaddexp(0.0, z) - Y * z) + 1e-3 * beta[:4] @ beta[:4],
                Xb.T @ (p_ - Y) + np.concatenate([2e-3 * beta[:4], [0.0]]))

    libre = minimize(perdida, np.zeros(5), jac=True, method="L-BFGS-B").x
    acotada = minimize(perdida, np.zeros(5), jac=True, method="L-BFGS-B",
                       bounds=[(0, None)] * 4 + [(None, None)]).x
    modelos = dict(MODELOS_FIJOS)
    modelos["logistica_libre"] = tuple(libre[:4])
    modelos["logistica_no_negativa"] = tuple(acotada[:4] / acotada[:4].sum())

    filas = []
    for nombre, pesos in modelos.items():
        for lam in (LAMBDAS if nombre in ("hibrido_estatico", "logistica_no_negativa") else [0.0]):
            fila = {"generador": generador, "modelo": nombre, "lambda_novedad": lam,
                    "pesos_sim_pr_co_cat": [round(float(w), 4) for w in pesos]}
            fila.update(_evaluar(cache, prueba, categoria, pesos, lam))
            filas.append(fila)
            print(fila, flush=True)
    info = {"candidatos_por_ancla": float(n_cand), "pares_logistica": int(len(Y)), "tasa_positivos": float(Y.mean()),
            "coef_logistica_libre_sim_pr_co_cat": [float(b) for b in libre[:4]]}
    return filas, info


if __name__ == "__main__":
    main()
