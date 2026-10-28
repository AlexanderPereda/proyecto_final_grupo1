"""Tests unitarios de la etapa 8: GrafoCoCompra, PageRank, Louvain y LPA.

Verifica la equivalencia exacta con NetworkX, la conservación de propiedades probabilísticas
y topológicas, y el aislamiento de fraude de la etapa 6.

Ejecutar:
    pytest tests/etapa_8/test_graph.py -v
"""

import tempfile
from pathlib import Path

import networkx as nx
import numpy as np
import pytest

from src.etapa_8.graph import (
    GrafoCoCompra,
    componentes_conexas,
    dividir_comunidades_desconectadas,
    grado_ponderado_ranking,
    label_propagation,
    louvain,
    modularidad,
    pagerank,
    sumar_grafos,
)


def _grafo_karate():
    """Construye un GrafoCoCompra a partir del clásico Karate Club de Zachary."""
    G = nx.karate_club_graph()
    nodos = [f"p_{i:02d}" for i in range(G.number_of_nodes())]
    aristas = {}
    for u, v in G.edges():
        aristas[(f"p_{u:02d}", f"p_{v:02d}")] = 1.0
    return GrafoCoCompra.desde_aristas(nodos, aristas), G


# ----- Tests de la Estructura GrafoCoCompra ---------------------------------------

def test_grafo_cocompra_propiedades_basicas():
    asins = ["A", "B", "C", "D"]
    aristas = {
        ("A", "B"): 2.0,
        ("B", "C"): 1.0,
        ("C", "D"): 3.0,
    }
    grafo = GrafoCoCompra.desde_aristas(asins, aristas)

    assert grafo.n_nodos == 4
    assert grafo.n_aristas == 3
    assert grafo.peso_total == 6.0  # 2 + 1 + 3

    # Grados ponderados
    # A: 2 (B)
    # B: 3 (A=2, C=1)
    # C: 4 (B=1, D=3)
    # D: 3 (C=3)
    grados = grafo.grados_ponderados()
    assert np.allclose(grados, [2.0, 3.0, 4.0, 3.0])

    # Vecinos de B (índice 1)
    vecs_b, pesos_b = grafo.vecinos(1)
    assert set(vecs_b) == {0, 2}  # A y C
    assert set(pesos_b) == {2.0, 1.0}


def test_grafo_cocompra_guardar_y_cargar_npz():
    asins = ["prod_1", "prod_2", "prod_3"]
    aristas = {("prod_1", "prod_2"): 5.0, ("prod_2", "prod_3"): 10.0}
    grafo_orig = GrafoCoCompra.desde_aristas(asins, aristas)

    with tempfile.TemporaryDirectory() as tmpdir:
        ruta = Path(tmpdir) / "test_grafo.npz"
        grafo_orig.guardar_npz(ruta)
        assert ruta.exists()

        grafo_cargado = GrafoCoCompra.cargar_npz(ruta)
        assert grafo_cargado.asins == grafo_orig.asins
        assert np.array_equal(grafo_cargado.indptr, grafo_orig.indptr)
        assert np.array_equal(grafo_cargado.indices, grafo_orig.indices)
        assert np.array_equal(grafo_cargado.data, grafo_orig.data)
        assert grafo_cargado.peso_total == grafo_orig.peso_total


def test_grafo_cocompra_subgrafo_inducido():
    asins = ["A", "B", "C", "D", "E"]
    aristas = {
        ("A", "B"): 1.0,
        ("B", "C"): 2.0,
        ("C", "D"): 3.0,
        ("D", "E"): 4.0,
    }
    grafo = GrafoCoCompra.desde_aristas(asins, aristas)
    sub = grafo.subgrafo(["A", "B", "C"])

    assert sub.n_nodos == 3
    assert sub.asins == ["A", "B", "C"]
    assert sub.n_aristas == 2  # (A, B) y (B, C)
    assert sub.peso_total == 3.0


# ----- Tests de Algoritmos de Influencia: PageRank --------------------------------

def test_pagerank_coincide_con_networkx():
    grafo, G = _grafo_karate()
    pr_propio, iters, conv = pagerank(grafo, alpha=0.85, tol=1e-6)

    assert conv is True
    assert iters > 0
    assert abs(pr_propio.sum() - 1.0) < 1e-12

    # Comparar con NetworkX (unweighted para coincidir con aristas de peso 1.0)
    pr_nx_dict = nx.pagerank(G, alpha=0.85, tol=1e-6, weight=None)
    pr_nx = np.array([pr_nx_dict[i] for i in range(G.number_of_nodes())])

    # El error absoluto máximo debe ser menor a 1e-5
    error_max = np.max(np.abs(pr_propio - pr_nx))
    assert error_max < 1e-5


def test_pagerank_grafo_con_nodo_sumidero():
    # Grafo con un nodo sumidero (sin aristas)
    asins = ["A", "B", "C", "Aislado"]
    aristas = {("A", "B"): 1.0, ("B", "C"): 1.0}
    grafo = GrafoCoCompra.desde_aristas(asins, aristas)

    pr, _, conv = pagerank(grafo, alpha=0.85, tol=1e-6)
    assert conv is True
    assert abs(pr.sum() - 1.0) < 1e-12
    # El nodo aislado recibe probabilidad positiva únicamente vía teletransportación (1-alpha)/N
    assert pr[3] > 0.0


def test_grado_ponderado_ranking():
    asins = ["A", "B", "C"]
    aristas = {("A", "B"): 2.0, ("A", "C"): 8.0}
    grafo = GrafoCoCompra.desde_aristas(asins, aristas)

    scores = grado_ponderado_ranking(grafo)
    assert abs(scores.sum() - 1.0) < 1e-12
    # A tiene grado 10, B tiene grado 2, C tiene grado 8; total = 20
    assert np.allclose(scores, [10.0 / 20.0, 2.0 / 20.0, 8.0 / 20.0])


# ----- Tests de Comunidades: Modularidad, Louvain y LPA ---------------------------

def test_modularidad_coincide_con_networkx():
    grafo, G = _grafo_karate()
    # Asignar partición arbitraria de 2 comunidades
    comunidades = np.array([0 if i < 17 else 1 for i in range(G.number_of_nodes())], dtype=np.int32)

    q_propia = modularidad(grafo, comunidades)

    particion_nx = [
        set(i for i in range(17)),
        set(i for i in range(17, G.number_of_nodes()))
    ]
    q_nx = nx.community.modularity(G, particion_nx, weight=None)

    assert abs(q_propia - q_nx) < 1e-10


def test_louvain_alta_modularidad_en_karate_club():
    grafo, G = _grafo_karate()
    comm, mod, niveles = louvain(grafo, resolucion=1.0, semilla=42)

    assert niveles >= 1
    # En Karate Club Louvain debe alcanzar una modularidad alta (Q >= 0.40)
    assert mod >= 0.40

    # Verificar que es una partición válida (todas las etiquetas entre 0 y C-1)
    n_comunidades = len(np.unique(comm))
    assert 2 <= n_comunidades <= 6

    # Validar que la función modularidad coincide
    q_verif = modularidad(grafo, comm)
    assert abs(mod - q_verif) < 1e-10


def test_label_propagation_separa_componentes_desconectadas():
    # Dos cliques completos disjuntos de 4 nodos cada uno
    asins = [f"c1_{i}" for i in range(4)] + [f"c2_{i}" for i in range(4)]
    aristas = {}
    # Clique 1
    for i in range(4):
        for j in range(i + 1, 4):
            aristas[(f"c1_{i}", f"c1_{j}")] = 1.0
    # Clique 2
    for i in range(4):
        for j in range(i + 1, 4):
            aristas[(f"c2_{i}", f"c2_{j}")] = 1.0

    grafo = GrafoCoCompra.desde_aristas(asins, aristas)

    comm_lpa, iters, mod_lpa = label_propagation(grafo, semilla=42)
    assert iters >= 1
    # Deben descubrirse exactamente 2 comunidades
    assert len(np.unique(comm_lpa)) == 2
    # Todos los del clique 1 deben tener la misma etiqueta
    assert len(set(comm_lpa[:4])) == 1
    # Todos los del clique 2 deben tener la misma etiqueta
    assert len(set(comm_lpa[4:])) == 1
    # Las dos etiquetas deben ser distintas
    assert comm_lpa[0] != comm_lpa[4]
    # La modularidad en cliques disjuntos es muy alta (~0.5)
    assert mod_lpa > 0.45


def _dos_triangulos():
    """Dos triángulos disjuntos: {a0,a1,a2} y {b0,b1,b2}."""
    asins = ["a0", "a1", "a2", "b0", "b1", "b2"]
    aristas = {("a0", "a1"): 1.0, ("a1", "a2"): 1.0, ("a0", "a2"): 1.0,
               ("b0", "b1"): 1.0, ("b1", "b2"): 1.0, ("b0", "b2"): 1.0}
    return GrafoCoCompra.desde_aristas(asins, aristas)


def test_componentes_conexas_dos_triangulos():
    grafo = _dos_triangulos()
    n_comp, etiquetas = componentes_conexas(grafo)
    assert n_comp == 2
    assert len(set(etiquetas[:3])) == 1 and len(set(etiquetas[3:])) == 1
    assert etiquetas[0] != etiquetas[3]


def test_dividir_comunidad_desconectada_sube_modularidad():
    grafo = _dos_triangulos()
    # Partición defectuosa: ambos triángulos en una misma comunidad desconectada
    mala = np.zeros(6, dtype=np.int32)
    q_mala = modularidad(grafo, mala)
    buena, n_desconectadas = dividir_comunidades_desconectadas(grafo, mala)
    assert n_desconectadas == 1
    assert len(np.unique(buena)) == 2
    # Q = 0 con una sola comunidad; partida en componentes, Q = 1 - 2 * (1/2)^2 = 0.5
    assert abs(q_mala) < 1e-12
    assert abs(modularidad(grafo, buena) - 0.5) < 1e-12


def test_dividir_particion_conexa_no_cambia():
    grafo, _ = _grafo_karate()
    comm, _, _ = louvain(grafo, semilla=42, garantizar_conexion=False)
    nueva, n_desconectadas = dividir_comunidades_desconectadas(grafo, comm)
    if n_desconectadas == 0:
        assert len(np.unique(nueva)) == len(np.unique(comm))
    assert modularidad(grafo, nueva) >= modularidad(grafo, comm) - 1e-12


def test_louvain_devuelve_comunidades_conexas():
    grafo, _ = _grafo_karate()
    comm, _, _ = louvain(grafo, semilla=7)
    _, etiquetas = componentes_conexas(grafo, comm)
    # cada comunidad debe ser exactamente una componente conexa interna
    assert len(np.unique(etiquetas)) == len(np.unique(comm))


def test_sumar_grafos_une_nodos_y_suma_pesos():
    g1 = GrafoCoCompra.desde_aristas(["A", "B", "C"], {("A", "B"): 2.0, ("B", "C"): 1.0})
    g2 = GrafoCoCompra.desde_aristas(["B", "C", "D"], {("B", "C"): 3.0, ("C", "D"): 5.0})
    g = sumar_grafos(g1, g2)
    assert g.asins == ["A", "B", "C", "D"]
    assert g.n_aristas == 3
    assert g.peso_total == 11.0
    b = g.posicion.obtener("B")
    vecinos, pesos = g.vecinos(b)
    assert dict(zip([g.asins[v] for v in vecinos.tolist()], pesos.tolist())) == {"A": 2.0, "C": 4.0}


def test_extraccion_cocompras_aisla_ordenes_bloqueadas():
    import json
    from src.etapa_8.graph import extraer_cocompras

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        ruta_eventos = tmp_path / "eventos_test.jsonl"
        ruta_bloqueadas = tmp_path / "bloqueadas_test.json"

        # Ordenes bloqueadas por fraude
        ruta_bloqueadas.write_text(json.dumps(["orden_fraude_1", "orden_fraude_2"]))

        def _dummy_ev(eid, etype, oid, asin):
            return {
                "event_id": eid,
                "event_type": etype,
                "user_id": "u1",
                "parent_asin": asin,
                "order_id": oid,
                "order_type": "standard",
                "price": 10.0,
                "payment_id": "pay1",
                "device_id": "dev1",
                "ts": 1000.0,
            }

        # Eventos sinteticos
        eventos_raw = [
            # Orden legitima 1: A y B (genera arista A-B)
            _dummy_ev("e1", "purchase", "orden_legitima_1", "A"),
            _dummy_ev("e2", "purchase", "orden_legitima_1", "B"),
            # Orden legitima 2: B y C (genera arista B-C)
            _dummy_ev("e3", "purchase", "orden_legitima_2", "B"),
            _dummy_ev("e4", "purchase", "orden_legitima_2", "C"),
            # Orden legitima 3: C solo (no genera aristas)
            _dummy_ev("e5", "purchase", "orden_legitima_3", "C"),
            # Orden fraudulenta: X y Y (DEBE SER IGNORADA)
            _dummy_ev("e6", "purchase", "orden_fraude_1", "X"),
            _dummy_ev("e7", "purchase", "orden_fraude_1", "Y"),
            # Evento que no es compra (view)
            _dummy_ev("e8", "view", "orden_legitima_4", "Z"),
        ]

        # Escribir en formato ciclo jsonl
        with open(ruta_eventos, "w", encoding="utf-8") as f:
            for ev in eventos_raw:
                f.write(json.dumps({"ciclo": 0, "eventos": [ev]}) + "\n")

        grafo = extraer_cocompras(ruta_eventos=ruta_eventos, ruta_bloqueadas=ruta_bloqueadas)

        # Solo deben existir productos de ordenes legitimas con >= 2 items: A, B, C
        assert set(grafo.asins) == {"A", "B", "C"}
        assert "X" not in grafo.asins
        assert "Y" not in grafo.asins
        assert "Z" not in grafo.asins

        # Aristas esperadas: A-B y B-C
        assert grafo.n_aristas == 2
        assert grafo.peso_total == 2.0
        assert grafo.compras_bloqueadas == 2

