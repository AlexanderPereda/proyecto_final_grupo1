"""Calibración offline del scoring híbrido de la etapa 9 (auditoría 2026-10-27).

Protocolo temporal (sin mirar el futuro):
1. Las órdenes legítimas (sin las bloqueadas por la etapa 6) se ordenan por su primer timestamp;
   el 80 % más antiguo construye el grafo de co-compra de entrenamiento y su PageRank.
2. Objetivo: para cada producto ancla con co-compras en el 20 % final, recuperar en el Top-10
   a los productos con los que se co-compró en ese periodo (relevancia binaria).
3. Candidatos = vecinos HNSW (contenido, sin fuga) ∪ relacionados MinHash ∪ vecinos de co-compra
   del grafo de entrenamiento. Tres variantes de la señal de relacionados:
   - fuga: los de la etapa 7, calculados con todas las interacciones (filtran futuro);
   - temporal: MinHash + LSH de la etapa 7 recalculados solo con los eventos anteriores al corte
     (mismos parámetros: 256 hashes, 256 bandas de 1 fila, >= 5 usuarios, k = 10);
   - sin: sin la señal.
4. Los anclas se parten al azar en calibración y prueba (50/50); los pesos se ajustan en
   calibración y se informan en prueba.

Hipótesis fijadas antes de la primera medición:
- H1: kappa > 0 mejora Recall@10 en anclas con pocas co-compras (n_q <= 2).
- H2: la normalización de PageRank importa ('max' deja S_pr ~ 0).
- H3: en cold start los pesos adaptativos dan el mismo orden que los estáticos.
Añadido tras ver que las líneas base de una sola señal superaban al híbrido: pesos por regresión
logística puntual (learning-to-rank pointwise) con coeficientes >= 0, representables en
ScoringHibrido. Regla de adopción fijada antes de medir la variante temporal: se adoptan los pesos
aprendidos en la variante 'temporal' solo si en prueba mejoran Recall@10 y NDCG@10 en >= 5 %
relativo frente al híbrido estático y la cobertura de catálogo del Top-10 no cae más de 25 %
relativo; si no, se conservan los pesos estáticos.

Ejecutar: .venv/bin/python -m src.etapa_9.evaluacion_scoring
Salida: results/etapa_9/calibracion_scoring.csv, calibracion_lineas_base.csv y
        calibracion_scoring_resumen.json
"""

import json
import time
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .. import config
from ..etapa_3.storage import PESOS, RUTA_EVENTOS_ETAPA2, leer_eventos
from ..etapa_7.similarity import (
    BANDAS, FILAS, LSH, MIN_USUARIOS, N_HASHES, ConjuntosProductos, MinHash, relacionados_lsh,
    usuarios_bloqueados,
)
from ..etapa_8.graph import GrafoCoCompra, ordenes_bloqueadas, pagerank
from .service import ScoringHibrido, ServicioEcommerce

DIR_RESULTADOS = config.RAIZ / "results" / "etapa_9"
FRACCION_ENTRENAMIENTO = 0.8
K = 10
KAPPAS = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
NORMALIZACIONES = ["max", "log", "percentil"]
PESOS_ESTATICOS = (0.35, 0.25, 0.20, 0.20)
# Líneas base de una sola señal (pesos sim, rel, pr, co), con normalización 'log' de PageRank
LINEAS_BASE = {
    "solo_similitud": (1.0, 0.0, 0.0, 0.0),
    "solo_relacionados": (0.0, 1.0, 0.0, 0.0),
    "solo_pagerank": (0.0, 0.0, 1.0, 0.0),
    "solo_cocompra": (0.0, 0.0, 0.0, 1.0),
}
MEJORA_MINIMA = 0.05          # regla de adopción: mejora relativa mínima en Recall@10 y NDCG@10
CAIDA_COBERTURA_MAX = 0.25    # regla de adopción: caída relativa máxima de cobertura de catálogo


def particion_temporal(fraccion=FRACCION_ENTRENAMIENTO):
    """Devuelve (aristas_entrenamiento, pares_prueba, ts_corte) a partir del stream de eventos."""
    bloqueadas = ordenes_bloqueadas()
    productos = defaultdict(set)
    inicio = {}
    for ev in leer_eventos(RUTA_EVENTOS_ETAPA2):
        if ev.event_type != "purchase" or not ev.order_id or not ev.parent_asin:
            continue
        if ev.order_id in bloqueadas:
            continue
        productos[ev.order_id].add(ev.parent_asin)
        if ev.order_id not in inicio or ev.ts < inicio[ev.order_id]:
            inicio[ev.order_id] = ev.ts
    ordenes = [o for o, p in productos.items() if len(p) >= 2]
    ordenes.sort(key=lambda o: inicio[o])
    corte = int(len(ordenes) * fraccion)

    def pares(lista):
        conteo = defaultdict(int)
        for o in lista:
            prods = sorted(productos[o])
            for i in range(len(prods)):
                for j in range(i + 1, len(prods)):
                    conteo[(prods[i], prods[j])] += 1
        return conteo

    return pares(ordenes[:corte]), pares(ordenes[corte:]), inicio[ordenes[corte]]


def relacionados_temporales(ts_corte, min_usuarios=MIN_USUARIOS):
    """Relacionados MinHash + LSH de la etapa 7 con solo los eventos anteriores a ts_corte.

    Reproduce la matriz usuario × producto de la etapa 3 (mismos PESOS por tipo de evento) y el
    filtro de usuarios bloqueados de la etapa 7. Devuelve {asin: [(vecino, jaccard), ...]}.
    """
    bloqueados = usuarios_bloqueados()
    acumulado = defaultdict(float)
    for ev in leer_eventos(RUTA_EVENTOS_ETAPA2):
        if ev.ts >= ts_corte or ev.parent_asin is None or ev.user_id in bloqueados:
            continue
        peso = PESOS.get(ev.event_type)
        if peso is not None:
            acumulado[(ev.parent_asin, ev.user_id)] += peso

    usuarios_por_producto = defaultdict(list)
    id_usuario = {}
    for (asin, usuario), valor in acumulado.items():
        if valor != 0:
            usuarios_por_producto[asin].append(id_usuario.setdefault(usuario, len(id_usuario)))
    asins = sorted(a for a, us in usuarios_por_producto.items() if len(us) >= min_usuarios)
    listas = [np.sort(np.array(usuarios_por_producto[a], dtype=np.int32)) for a in asins]
    indptr = np.concatenate(([0], np.cumsum([len(x) for x in listas])))
    conjuntos = ConjuntosProductos(asins, indptr, np.concatenate(listas), len(id_usuario))

    firmas = MinHash(N_HASHES, config.SEED).firmas(conjuntos.indptr, conjuntos.indices)
    lsh = LSH(firmas, BANDAS, FILAS)
    salida = {}
    for i, asin in enumerate(asins):
        salida[asin] = [(asins[j], float(jac)) for jac, j in relacionados_lsh(conjuntos, lsh, i, K)]
    return salida


def metricas_ranking(ranking, relevantes):
    aciertos = [1.0 if a in relevantes else 0.0 for a in ranking[:K]]
    recall = sum(aciertos) / len(relevantes)
    dcg = sum(h / np.log2(i + 2) for i, h in enumerate(aciertos))
    idcg = sum(1.0 / np.log2(i + 2) for i in range(min(len(relevantes), K)))
    return recall, dcg / idcg


def gini(conteos):
    x = np.sort(np.asarray(conteos, dtype=np.float64))
    if len(x) == 0 or x.sum() == 0:
        return 0.0
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1) @ x / (n * x.sum()))


def main():
    t0 = time.perf_counter()
    DIR_RESULTADOS.mkdir(parents=True, exist_ok=True)
    aristas_ent, pares_prueba, ts_corte = particion_temporal()
    asins_ent = sorted({a for par in aristas_ent for a in par})
    grafo_ent = GrafoCoCompra.desde_aristas(asins_ent, aristas_ent)
    pr_ent, _, _ = pagerank(grafo_ent)
    print(f"entrenamiento: {grafo_ent.n_nodos:,} nodos, {grafo_ent.n_aristas:,} aristas; "
          f"pares de prueba: {len(pares_prueba):,} ({time.perf_counter() - t0:.1f} s)")

    rel_temporal = relacionados_temporales(ts_corte)
    print(f"relacionados temporales: {len(rel_temporal):,} productos ({time.perf_counter() - t0:.1f} s)")

    servicio = ServicioEcommerce(config.RAIZ / "data" / "interim", config.CATEGORIA)
    servicio.cargar_datos(indexar_texto=False)
    servicio.grafo_cocompra = grafo_ent
    servicio._pagerank = dict(zip(grafo_ent.asins, pr_ent.tolist()))
    servicio._pagerank_max = float(pr_ent.max())
    variantes = {"fuga": servicio._relacionados, "temporal": rel_temporal, "sin": {}}

    relevantes = defaultdict(set)
    for (a, b) in pares_prueba:
        relevantes[a].add(b)
        relevantes[b].add(a)
    grados_ent = dict(zip(grafo_ent.asins, grafo_ent.grados_ponderados().tolist()))

    # Anclas evaluables: con vecinos HNSW (la única señal sin fuga presente en cold start)
    anclas = sorted(a for a in relevantes if a in servicio._similares)
    rng = np.random.default_rng(config.SEED)
    rng.shuffle(anclas)
    mitad = len(anclas) // 2
    conjunto = {a: ("calibracion" if i < mitad else "prueba") for i, a in enumerate(anclas)}
    anclas_prueba = [a for a in anclas if conjunto[a] == "prueba"]
    anclas_cal = [a for a in anclas if conjunto[a] == "calibracion"]

    def grupo(a):
        n = grados_ent.get(a, 0.0)
        if n == 0:
            return "cold_start"
        return "n<=2" if n <= 2 else ("3-9" if n < 10 else ">=10")

    # ----- Grilla kappa × normalización por variante de relacionados --------------
    filas = []
    for variante, rel in variantes.items():
        servicio._relacionados = rel
        for norm in NORMALIZACIONES:
            servicio.normalizacion_pagerank = norm
            servicio._preparar_autoridad(pr_ent, grafo_ent.asins)
            for kappa in KAPPAS:
                servicio.scoring = ScoringHibrido(kappa_cocompra=kappa)
                for a in anclas:
                    cand = servicio.puntuar_candidatos(a)
                    ranking = [c for c, _ in sorted(cand.items(), key=lambda x: (-x[1][0], x[0]))]
                    rec, ndcg = metricas_ranking(ranking, relevantes[a])
                    filas.append({
                        "relacionados": variante, "normalizacion_pr": norm, "kappa": kappa,
                        "conjunto": conjunto[a], "grupo": grupo(a), "ancla": a,
                        "recall@10": rec, "ndcg@10": ndcg,
                        "relevante_en_candidatos": any(c in relevantes[a] for c in cand),
                    })
            print(f"  grilla {variante}/{norm} lista ({time.perf_counter() - t0:.0f} s)")

    # ----- Líneas base, logística no negativa y cobertura (S_pr en 'log') ----------
    servicio.normalizacion_pagerank = "log"
    servicio._preparar_autoridad(pr_ent, grafo_ent.asins)
    servicio.scoring = ScoringHibrido()

    def senales(a):
        """Matriz (candidatos x 4) con S_sim, S_rel, S_pr(log), S_co y la etiqueta de relevancia."""
        cand = servicio.puntuar_candidatos(a)
        asins = sorted(cand)
        x = np.array([[cand[c][1]["sim"], cand[c][1]["rel"], servicio._autoridad(c), cand[c][1]["co"]]
                      for c in asins]).reshape(-1, 4)
        y = np.array([c in relevantes[a] for c in asins], dtype=np.float64)
        return asins, x, y

    def evaluar_pesos(pesos, lista):
        rec_total, ndcg_total = 0.0, 0.0
        recomendados = defaultdict(int)
        universo = set()
        for a in lista:
            asins, x, _ = senales(a)
            universo.update(asins)
            orden = np.lexsort((np.array(asins), -(x @ pesos))) if len(asins) else []
            ranking = [asins[i] for i in orden]
            for c in ranking[:K]:
                recomendados[c] += 1
            rec, ndcg = metricas_ranking(ranking, relevantes[a])
            rec_total += rec
            ndcg_total += ndcg
        return {"recall@10": rec_total / len(lista), "ndcg@10": ndcg_total / len(lista),
                "cobertura_catalogo@10": len(recomendados) / max(len(universo), 1),
                "gini_recomendaciones": gini(list(recomendados.values()))}

    def ajustar_logistica(lista):
        xs, ys = [], []
        for a in lista:
            _, x, y = senales(a)
            xs.append(x)
            ys.append(y)
        X = np.vstack(xs)
        Y = np.concatenate(ys)
        Xb = np.hstack([X, np.ones((len(X), 1))])

        def perdida(beta):
            z = Xb @ beta
            p = 1.0 / (1.0 + np.exp(-z))
            ll = np.sum(np.logaddexp(0.0, z) - Y * z) + 1e-3 * beta[:4] @ beta[:4]
            grad = Xb.T @ (p - Y) + np.concatenate([2e-3 * beta[:4], [0.0]])
            return ll, grad

        libre = minimize(perdida, np.zeros(5), jac=True, method="L-BFGS-B").x
        acotada = minimize(perdida, np.zeros(5), jac=True, method="L-BFGS-B",
                           bounds=[(0, None)] * 4 + [(None, None)]).x
        return libre, acotada, len(Y), float(Y.mean())

    lineas_base = []
    for variante, rel in variantes.items():
        servicio._relacionados = rel
        modelos = {n: np.array(p) for n, p in LINEAS_BASE.items() if rel or n != "solo_relacionados"}
        modelos["hibrido_estatico_log"] = np.array(PESOS_ESTATICOS)
        libre, acotada, n_pares, tasa = ajustar_logistica(anclas_cal[:15_000])
        modelos["logistica_libre"] = libre[:4]
        normalizada = acotada[:4] / acotada[:4].sum() if acotada[:4].sum() > 0 else acotada[:4]
        modelos["logistica_no_negativa"] = normalizada
        for nombre, pesos in modelos.items():
            fila = {"relacionados": variante, "modelo": nombre,
                    "pesos": [round(float(w), 4) for w in pesos]}
            fila.update(evaluar_pesos(pesos, anclas_prueba))
            if nombre.startswith("logistica"):
                fila.update({"pares_entrenamiento": n_pares, "tasa_positivos": tasa})
            lineas_base.append(fila)
        print(f"  lineas base {variante} listas ({time.perf_counter() - t0:.0f} s)")
    df_base = pd.DataFrame(lineas_base)
    df_base.to_csv(DIR_RESULTADOS / "calibracion_lineas_base.csv", index=False)

    df = pd.DataFrame(filas)
    agregado = (df.groupby(["relacionados", "normalizacion_pr", "kappa", "conjunto", "grupo"])
                  [["recall@10", "ndcg@10"]].mean().reset_index())
    n_anclas = df[(df.kappa == 0) & (df.normalizacion_pr == "max") & (df.relacionados == "fuga")].groupby(
        ["conjunto", "grupo"]).size().rename("anclas").reset_index()
    agregado = agregado.merge(n_anclas, on=["conjunto", "grupo"])
    agregado.to_csv(DIR_RESULTADOS / "calibracion_scoring.csv", index=False)

    global_ = (df.groupby(["relacionados", "normalizacion_pr", "kappa", "conjunto"])
                 [["recall@10", "ndcg@10"]].mean().reset_index())
    resumen = {}
    for variante in variantes:
        cal = global_[(global_.conjunto == "calibracion") & (global_.relacionados == variante)]
        mejor = cal.sort_values("ndcg@10", ascending=False).iloc[0]
        resumen[variante] = {"mejor_kappa": float(mejor.kappa), "mejor_normalizacion": mejor.normalizacion_pr}

    # Regla de adopción (fijada antes de medir) sobre la variante temporal
    tmp = df_base[df_base.relacionados == "temporal"].set_index("modelo")
    base, cand = tmp.loc["hibrido_estatico_log"], tmp.loc["logistica_no_negativa"]
    mejora_rec = cand["recall@10"] / base["recall@10"] - 1
    mejora_ndcg = cand["ndcg@10"] / base["ndcg@10"] - 1
    caida_cob = 1 - cand["cobertura_catalogo@10"] / base["cobertura_catalogo@10"]
    resumen["regla_adopcion"] = {
        "pesos_candidatos": cand["pesos"], "mejora_recall": float(mejora_rec),
        "mejora_ndcg": float(mejora_ndcg), "caida_cobertura": float(caida_cob),
        "adoptar": bool(mejora_rec >= MEJORA_MINIMA and mejora_ndcg >= MEJORA_MINIMA
                        and caida_cob <= CAIDA_COBERTURA_MAX),
    }
    cold = df[df.grupo == "cold_start"]
    pivote = cold.pivot_table(index=["relacionados", "normalizacion_pr", "ancla"], columns="kappa", values="recall@10")
    resumen["h3_cold_start_identico"] = bool(np.allclose(pivote.values, pivote.values[:, [0]]))
    resumen["cobertura_candidatos"] = float(df[(df.kappa == 0) & (df.normalizacion_pr == "max")
                                               & (df.relacionados == "fuga")]["relevante_en_candidatos"].mean())
    resumen["productos_relacionados_temporales"] = len(rel_temporal)
    resumen["anclas"] = len(anclas)
    resumen["segundos"] = time.perf_counter() - t0
    (DIR_RESULTADOS / "calibracion_scoring_resumen.json").write_text(json.dumps(resumen, indent=2), encoding="utf-8")
    print(json.dumps(resumen, indent=2))
    servicio.cerrar()


if __name__ == "__main__":
    main()
