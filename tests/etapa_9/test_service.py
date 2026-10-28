"""
Pruebas unitarias para la Etapa 9: Servicio y visualización.
Verifica:
1. Trie (árbol de prefijos) y autocompletado en tiempo real.
2. TopKHeap con MinHeap acotado.
3. ScoringHibrido multicriterio.
4. Integración de ServicioEcommerce con catálogo y recomendaciones.
"""

import unittest

import numpy as np
from pathlib import Path

from src.etapa_9.service import (
    IndiceInvertido,
    NodoTrie,
    ScoringHibrido,
    ServicioEcommerce,
    TopKHeap,
    Trie,
    normalizar_texto,
    tokenizar,
)


class TestTrie(unittest.TestCase):
    def setUp(self):
        self.trie = Trie(capacidad_cache=5)

    def test_normalizar_texto(self):
        self.assertEqual(normalizar_texto("  Toner HP Laser-Jet!  "), "toner hp laser-jet")
        self.assertEqual(normalizar_texto(""), "")
        self.assertEqual(normalizar_texto("PILOT G2 (0.7 mm)"), "pilot g2 0.7 mm")

    def test_insertar_y_buscar_exacto(self):
        self.trie.insertar("toner brother", frecuencia=50.0)
        self.trie.insertar("engrapadora swingline", frecuencia=30.0)

        self.assertTrue(self.trie.buscar_exacto("toner brother"))
        self.assertTrue(self.trie.buscar_exacto("TONER BROTHER"))  # Case-insensitive
        self.assertTrue(self.trie.buscar_exacto("engrapadora swingline"))
        self.assertFalse(self.trie.buscar_exacto("toner"))  # Prefijo pero no término completo
        self.assertFalse(self.trie.buscar_exacto("cuaderno oxford"))

    def test_autocompletar_prefijo(self):
        self.trie.insertar("papel fotográfico", frecuencia=20.0)
        self.trie.insertar("papel bond", frecuencia=100.0)
        self.trie.insertar("papel carbón", frecuencia=15.0)
        self.trie.insertar("papelería escolar", frecuencia=80.0)
        self.trie.insertar("cuaderno espiral", frecuencia=50.0)

        sug = self.trie.autocompletar("pap", k=3)
        self.assertEqual(len(sug), 3)
        # El primero debe ser el de mayor frecuencia (papel bond: 100.0)
        self.assertEqual(sug[0]["termino"], "papel bond")
        self.assertEqual(sug[1]["termino"], "papelería escolar")
        self.assertEqual(sug[2]["termino"], "papel fotográfico")

    def test_autocompletar_prefijo_inexistente(self):
        self.trie.insertar("tinta canon", frecuencia=10.0)
        sug = self.trie.autocompletar("xyz", k=5)
        self.assertEqual(sug, [])

    def test_actualizar_frecuencia_y_meta(self):
        self.trie.insertar("marcador sharpie", frecuencia=10.0, meta={"asin": "ASIN1"})
        self.assertEqual(self.trie.autocompletar("mar")[0]["frecuencia"], 10.0)

        # Reinsertar con mayor frecuencia
        self.trie.insertar("marcador sharpie", frecuencia=25.0, meta={"asin": "ASIN1", "stock": True})
        top = self.trie.autocompletar("mar")[0]
        self.assertEqual(top["frecuencia"], 25.0)
        self.assertEqual(top["meta"]["stock"], True)
        self.assertEqual(len(self.trie), 1)  # No debe duplicar el término


class TestTopKHeap(unittest.TestCase):
    def test_topk_heap_orden_descendente(self):
        heap = TopKHeap(k=3)
        elementos = [
            ("artículo A", 10.5),
            ("artículo B", 45.2),
            ("artículo C", 2.1),
            ("artículo D", 99.8),
            ("artículo E", 33.0),
        ]
        for item, sc in elementos:
            heap.agregar(item, sc)

        top = heap.obtener_top()
        self.assertEqual(len(top), 3)
        # Los 3 mayores son D (99.8), B (45.2), E (33.0)
        self.assertEqual(top[0][1], "artículo D")
        self.assertEqual(top[1][1], "artículo B")
        self.assertEqual(top[2][1], "artículo E")
        self.assertAlmostEqual(top[0][0], 99.8)

    def test_topk_heap_con_menos_elementos_que_k(self):
        heap = TopKHeap(k=10)
        heap.agregar("X", 5.0)
        heap.agregar("Y", 12.0)

        top = heap.obtener_top()
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0][1], "Y")
        self.assertEqual(top[1][1], "X")

    def test_topk_heap_empates(self):
        heap = TopKHeap(k=3)
        heap.agregar("prod1", 10.0)
        heap.agregar("prod2", 10.0)
        heap.agregar("prod3", 20.0)
        heap.agregar("prod4", 10.0)

        top = heap.obtener_top()
        self.assertEqual(len(top), 3)
        self.assertEqual(top[0][1], "prod3")  # Mayor score
        scores = [sc for sc, _ in top]
        self.assertEqual(scores, [20.0, 10.0, 10.0])

    def test_topk_invalido(self):
        with self.assertRaises(ValueError):
            TopKHeap(k=0)


class TestScoringHibrido(unittest.TestCase):
    def test_pesos_y_calculo(self):
        scoring = ScoringHibrido(
            peso_similitud=0.4,
            peso_relacionados=0.3,
            peso_pagerank=0.2,
            peso_cocompra=0.1,
        )
        score = scoring.calcular_score(
            similitud_atributos=1.0,
            jaccard_comportamiento=1.0,
            pagerank_normalizado=1.0,
            afinidad_cocompra=1.0,
        )
        self.assertAlmostEqual(score, 1.0)

        score_cero = scoring.calcular_score(0.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(score_cero, 0.0)

        score_parcial = scoring.calcular_score(
            similitud_atributos=0.5,
            jaccard_comportamiento=0.0,
            pagerank_normalizado=0.5,
            afinidad_cocompra=0.0,
        )
        self.assertAlmostEqual(score_parcial, 0.4 * 0.5 + 0.2 * 0.5)

    def test_auto_normalizacion(self):
        scoring = ScoringHibrido(
            peso_similitud=2.0,
            peso_relacionados=2.0,
            peso_pagerank=2.0,
            peso_cocompra=2.0,
        )
        # La suma es 8.0, debe normalizar cada uno a 0.25
        self.assertAlmostEqual(scoring.peso_similitud, 0.25)
        self.assertAlmostEqual(scoring.peso_relacionados, 0.25)


class TestServicioEcommerce(unittest.TestCase):
    def setUp(self):
        raiz = Path.cwd()
        dir_interim = raiz / "data" / "interim"
        self.servicio = ServicioEcommerce(dir_interim, categoria="Office_Products")
        # Inyectar datos mock controlados
        self.servicio.trie.insertar("toner brother", frecuencia=500.0, meta={"asin": "ASIN_TONER"})
        self.servicio.trie.insertar("cinta scotch", frecuencia=300.0, meta={"asin": "ASIN_CINTA"})

        self.servicio._pagerank = {"ASIN_TONER": 0.001, "ASIN_CINTA": 0.0005, "ASIN_PAPEL": 0.0008}
        self.servicio._pagerank_max = 0.001
        self.servicio._similares = {
            "ASIN_TONER": [("ASIN_PAPEL", 0.3), ("ASIN_CINTA", 0.7)],
        }
        self.servicio._relacionados = {
            "ASIN_TONER": [("ASIN_PAPEL", 0.08)],
        }

    def test_autocompletar_servicio(self):
        res = self.servicio.autocompletar("ton", k=2)
        self.assertGreaterEqual(len(res), 1)
        self.assertEqual(res[0]["termino"], "toner brother")

    def test_recomendar_servicio(self):
        recs = self.servicio.recomendar("ASIN_TONER", k=2)
        self.assertGreaterEqual(len(recs), 1)
        # ASIN_PAPEL tiene similitud 1-0.3=0.7 y Jaccard 0.08, debe estar en top
        asins_recs = [r["parent_asin"] for r in recs]
        self.assertIn("ASIN_PAPEL", asins_recs)

    def test_buscar_servicio(self):
        busq = self.servicio.buscar("toner", k=5)
        self.assertGreaterEqual(len(busq), 1)
        self.assertEqual(busq[0]["parent_asin"], "ASIN_TONER")

    def test_detalle_producto_inexistente(self):
        det = self.servicio.detalle_producto("ASIN_INEXISTENTE")
        self.assertIsNone(det)


class TestTrieRadixYDifuso(unittest.TestCase):
    """Auditoría 2026-10-27: compresión radix, duplicados y búsqueda difusa OSA."""

    def test_radix_parte_aristas_y_conserva_prefijos(self):
        t = Trie(capacidad_cache=5)
        t.insertar("toner", 10.0)
        t.insertar("tonel", 5.0)
        t.insertar("to", 1.0)
        # raiz -> "to" -> {"ne" -> {"r", "l"}}: 5 nodos en vez de 7 de un caracter
        self.assertEqual(t.n_nodos, 5)
        self.assertTrue(t.buscar_exacto("to"))
        self.assertFalse(t.buscar_exacto("ton"))  # prefijo a mitad de arista
        self.assertEqual([s["termino"] for s in t.autocompletar("ton", k=5)], ["toner", "tonel"])
        self.assertEqual(t.autocompletar("tox"), [])

    def test_duplicado_con_menor_frecuencia_no_pisa_meta(self):
        t = Trie(capacidad_cache=5)
        t.insertar("pilot g2", 50.0, meta={"asin": "A"})
        t.insertar("pilot g2", 10.0, meta={"asin": "B"})
        top = t.autocompletar("pil", k=1)[0]
        self.assertEqual(top["frecuencia"], 50.0)
        self.assertEqual(top["meta"]["asin"], "A")
        self.assertEqual(len(t), 1)

    def test_difuso_transposicion_cuesta_uno(self):
        t = Trie(capacidad_cache=5)
        t.insertar("brother", 100.0)
        t.insertar("brothel", 1.0)
        res = t.autocompletar_difuso("brtoher", k=2, max_dist=1, solo_completos=True)
        self.assertEqual(res[0]["termino"], "brother")
        self.assertEqual(res[0]["distancia"], 1)

    def test_difuso_prefijo_y_fijo(self):
        t = Trie(capacidad_cache=5)
        t.insertar("stapler swingline", 10.0)
        t.insertar("tape scotch", 10.0)
        res = t.autocompletar_difuso("stapel", k=5, max_dist=1)
        self.assertEqual([r["termino"] for r in res], ["stapler swingline"])
        # el primer caracter es fijo: "xtaple" no debe casar aunque la distancia sea 1
        self.assertEqual(t.autocompletar_difuso("xtaple", k=5, max_dist=1), [])

    def test_autocompletar_difuso_complementa_exactas(self):
        t = Trie(capacidad_cache=5)
        t.insertar("sharpie marker", 10.0)
        self.assertEqual(t.autocompletar("sharpei", k=3), [])
        res = t.autocompletar("sharpei", k=3, difuso=True, max_dist=1)
        self.assertEqual(res[0]["termino"], "sharpie marker")


class TestIndiceInvertido(unittest.TestCase):
    def test_cobertura_idf(self):
        idx = IndiceInvertido()
        idx.construir(enumerate([
            "Brother TN436 Toner Cartridge",
            "Tonice acrylic sign holder",
            "HP toner cartridge black",
        ]))
        docs, cobertura = idx.puntuar(tokenizar("toner brother"))
        puntaje = dict(zip(docs.tolist(), cobertura.tolist()))
        self.assertAlmostEqual(puntaje[0], 1.0)          # contiene ambos tokens
        self.assertLess(puntaje[2], 1.0)                 # solo "toner"
        self.assertNotIn(1, puntaje)                     # "tonice" no es "toner"
        self.assertEqual(idx.df("toner"), 2)


class TestScoringAdaptativo(unittest.TestCase):
    def test_cold_start_anula_cocompra(self):
        s = ScoringHibrido(kappa_cocompra=5.0)
        w_sim, w_rel, w_pr, w_co = s.pesos_efectivos(n_cocompras=0, hay_cocompra=False)
        self.assertEqual(w_co, 0.0)
        self.assertAlmostEqual(w_sim + w_rel + w_pr, 1.0)
        # la masa de co se reparte en proporcion a sim (0.35) y rel (0.25)
        self.assertAlmostEqual(w_sim / w_rel, 0.35 / 0.25)

    def test_credibilidad_crece_con_n(self):
        s = ScoringHibrido(kappa_cocompra=5.0)
        w_co_1 = s.pesos_efectivos(n_cocompras=1)[3]
        w_co_100 = s.pesos_efectivos(n_cocompras=100)[3]
        self.assertAlmostEqual(w_co_1, 0.20 * 1 / 6)
        self.assertGreater(w_co_100, w_co_1)
        self.assertLess(w_co_100, 0.20)

    def test_kappa_cero_reproduce_pesos_estaticos(self):
        s = ScoringHibrido()
        self.assertEqual(s.pesos_efectivos(n_cocompras=1), (0.35, 0.25, 0.20, 0.20))


class TestRerankingNovedad(unittest.TestCase):
    def _ventaja_popular(self, lam):
        servicio = ServicioEcommerce(Path.cwd() / "data" / "interim", categoria="Office_Products",
                                     scoring=ScoringHibrido(lambda_novedad=lam))
        servicio._autoridad_pre = {"POPULAR": 1.0, "MEDIO": 0.5, "NICHO": 0.1}
        servicio._similares = {"ANCLA": [("POPULAR", 0.5), ("MEDIO", 0.45), ("NICHO", 0.3)]}
        cand = servicio.puntuar_candidatos("ANCLA")
        return cand["POPULAR"][0] - cand["NICHO"][0], cand

    def test_lambda_cero_conserva_orden_y_rango(self):
        ventaja, cand = self._ventaja_popular(0.0)
        self.assertGreater(ventaja, 0.0)
        _, cand = self._ventaja_popular(0.3)
        self.assertTrue(all(0.0 <= sc <= 1.0 for sc, _ in cand.values()))

    def test_lambda_alto_invierte_el_orden(self):
        ventaja, _ = self._ventaja_popular(0.6)
        self.assertLess(ventaja, 0.0)


class TestBusquedaConIndice(unittest.TestCase):
    def test_buscar_corrige_tipeo_y_rankea_por_relevancia(self):
        servicio = ServicioEcommerce(Path.cwd() / "data" / "interim", categoria="Office_Products")
        servicio._asins_docs = ["B_TONER_BROTHER", "B_TONICE", "B_TONER_HP"]
        servicio.indice.construir(enumerate([
            "Brother TN436 Toner Cartridge", "Tonice acrylic sign holder", "HP toner cartridge black",
        ]))
        servicio._autoridad_docs = np.array([0.1, 0.9, 0.5])
        for tok, df in (("brother", 3), ("toner", 5), ("cartridge", 4)):
            servicio.trie_vocabulario.insertar(tok, frecuencia=df)
        self.assertEqual(servicio.corregir_consulta("toner brtoher"), ["toner", "brother"])
        res = servicio.buscar("toner brtoher", k=3)
        self.assertEqual(res[0]["parent_asin"], "B_TONER_BROTHER")
        self.assertNotIn("B_TONICE", [r["parent_asin"] for r in res])


if __name__ == "__main__":
    unittest.main()
