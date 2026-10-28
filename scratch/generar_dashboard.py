"""
Script para generar el dashboard interactivo de servicio y visualización:
visualizador/dashboard_servicio.html
"""

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
RUTA_DEMO = RAIZ / "data" / "interim" / "Office_Products" / "etapa_9" / "demo_consultas.json"
RUTA_METRICAS = RAIZ / "data" / "interim" / "Office_Products" / "etapa_9" / "metricas.json"

with open(RUTA_DEMO, "r", encoding="utf-8") as f:
    datos_demo = json.load(f)

with open(RUTA_METRICAS, "r", encoding="utf-8") as f:
    datos_metricas = json.load(f)

json_demo_str = json.dumps(datos_demo, ensure_ascii=False)
json_metricas_str = json.dumps(datos_metricas, ensure_ascii=False)

# KPIs leídos de las métricas de la etapa 9 (antes estaban escritos a mano en el HTML)
_resumen_fraude = datos_demo.get("fraude", {}).get("resumen", {})
kpi_total_alertas = f"{_resumen_fraude.get('total_alertas', 0):,}"
kpi_bloqueadas = f"{_resumen_fraude.get('bloqueadas', 0):,}"
kpi_retenidas = f"{_resumen_fraude.get('retenidas', 0):,}"
_precision = _resumen_fraude.get("precision_bloqueos")
kpi_precision = f"{100 * _precision:.1f}%" if _precision is not None else "N/D"
kpi_lat_trie = f"{1000 * datos_metricas.get('latencia_autocompletado_p50_ms', 0):.1f} µs"
kpi_lat_difuso = f"{datos_metricas.get('latencia_autocompletado_difuso_p50_ms', 0):.2f} ms"
kpi_lat_rec = f"{datos_metricas.get('latencia_recomendacion_topk_ms', 0):.2f} ms"
kpi_terminos = f"{datos_metricas.get('terminos_trie_indexados', 0):,}"
kpi_nodos = f"{datos_metricas.get('nodos_trie_radix', 0):,}"

html_content = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard de Servicio y Visualización · Etapa 9</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --bg-app: #0c0d12;
  --bg-surface: #14161f;
  --bg-surface-elevated: #1b1e2b;
  --bg-card: rgba(27, 30, 43, 0.7);
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-accent: rgba(99, 102, 241, 0.3);
  --text-main: #f3f4f6;
  --text-secondary: #9ca3af;
  --text-muted: #6b7280;
  --accent-primary: #6366f1;
  --accent-gradient: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
  --accent-cyan: #06b6d4;
  --accent-emerald: #10b981;
  --accent-rose: #f43f5e;
  --accent-amber: #f59e0b;
  --font-main: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  background-color: var(--bg-app);
  color: var(--text-main);
  font-family: var(--font-main);
  min-height: 100vh;
  line-height: 1.5;
  background-image: radial-gradient(circle at 15% 15%, rgba(99, 102, 241, 0.12) 0%, transparent 40%),
                    radial-gradient(circle at 85% 85%, rgba(168, 85, 247, 0.08) 0%, transparent 40%);
}}

.layout-container {{
  max-width: 1440px;
  margin: 0 auto;
  padding: 24px 32px 48px;
}}

/* Header */
header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border-subtle);
  margin-bottom: 28px;
}}

.brand-badge {{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-surface-elevated);
  padding: 4px 12px;
  border-radius: 99px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-cyan);
  border: 1px solid rgba(6, 182, 212, 0.3);
  margin-bottom: 6px;
}}

h1.app-title {{
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  background: var(--accent-gradient);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}

.sub-header {{
  font-size: 13px;
  color: var(--text-secondary);
}}

.header-status {{
  display: flex;
  align-items: center;
  gap: 16px;
}}

.status-pill {{
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  background: rgba(16, 185, 129, 0.1);
  color: var(--accent-emerald);
  padding: 6px 14px;
  border-radius: 20px;
  border: 1px solid rgba(16, 185, 129, 0.2);
}}

.status-dot {{
  width: 8px;
  height: 8px;
  background: var(--accent-emerald);
  border-radius: 50%;
  box-shadow: 0 0 8px var(--accent-emerald);
}}

/* Navigation Tabs */
.nav-tabs {{
  display: flex;
  gap: 10px;
  margin-bottom: 28px;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 12px;
}}

.tab-btn {{
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-secondary);
  font-family: var(--font-main);
  font-size: 14px;
  font-weight: 500;
  padding: 8px 18px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  gap: 8px;
}}

.tab-btn:hover {{
  color: var(--text-main);
  background: var(--bg-surface);
}}

.tab-btn.active {{
  color: #fff;
  background: var(--accent-primary);
  border-color: var(--accent-primary);
  box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35);
}}

/* Panels */
.tab-panel {{
  display: none;
  animation: fadeIn 0.25s ease forwards;
}}

.tab-panel.active {{
  display: block;
}}

@keyframes fadeIn {{
  from {{ opacity: 0; transform: translateY(6px); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}

/* Grid & Cards */
.grid-2col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}}

.grid-3col {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}}

.card {{
  background: var(--bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--border-subtle);
  border-radius: 14px;
  padding: 20px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
}}

.card-title {{
  font-size: 15px;
  font-weight: 600;
  color: var(--text-main);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}}

/* Search bar with live autocomplete */
.search-box-container {{
  position: relative;
  margin-bottom: 24px;
}}

.search-input-wrapper {{
  position: relative;
  display: flex;
  align-items: center;
}}

.search-icon {{
  position: absolute;
  left: 18px;
  color: var(--text-muted);
  width: 20px;
  height: 20px;
}}

input.search-input {{
  width: 100%;
  padding: 16px 20px 16px 50px;
  background: var(--bg-surface-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: 12px;
  color: #fff;
  font-size: 15px;
  font-family: var(--font-main);
  outline: none;
  transition: all 0.2s;
}}

input.search-input:focus {{
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.25);
}}

.quick-chips {{
  display: flex;
  gap: 8px;
  margin-top: 10px;
  align-items: center;
}}

.chip-label {{
  font-size: 12px;
  color: var(--text-muted);
}}

.chip {{
  font-size: 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}}

.chip:hover {{
  color: var(--accent-cyan);
  border-color: var(--accent-cyan);
}}

.autocomplete-dropdown {{
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  background: var(--bg-surface-elevated);
  border: 1px solid var(--border-accent);
  border-radius: 10px;
  padding: 8px;
  z-index: 100;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.45);
  display: none;
}}

.sug-item {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}}

.sug-item:hover {{
  background: rgba(99, 102, 241, 0.15);
}}

.sug-text {{
  font-size: 14px;
  font-weight: 500;
  color: #fff;
}}

.sug-tag {{
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 600;
  text-transform: uppercase;
}}

.tag-marca {{ background: rgba(6, 182, 212, 0.15); color: var(--accent-cyan); }}
.tag-categoria {{ background: rgba(168, 85, 247, 0.15); color: #c084fc; }}
.tag-producto {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); }}

/* Product Card Highlight */
.product-hero {{
  display: flex;
  gap: 20px;
  background: linear-gradient(135deg, rgba(31, 35, 53, 0.9) 0%, rgba(20, 22, 31, 0.9) 100%);
  border: 1px solid var(--border-accent);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 24px;
}}

.product-avatar {{
  width: 80px;
  height: 80px;
  border-radius: 10px;
  background: var(--bg-surface);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  border: 1px solid var(--border-subtle);
}}

.product-info {{
  flex: 1;
}}

.product-asin {{
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent-cyan);
}}

.product-name {{
  font-size: 18px;
  font-weight: 600;
  color: #fff;
  margin: 4px 0 8px;
}}

.product-meta-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: var(--text-secondary);
}}

.product-meta-row strong {{
  color: var(--text-main);
}}

/* Table styling */
table.data-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}}

table.data-table th {{
  text-align: left;
  padding: 10px 12px;
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-weight: 600;
  border-bottom: 1px solid var(--border-subtle);
}}

table.data-table td {{
  padding: 12px;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-main);
}}

table.data-table tr:hover td {{
  background: rgba(255, 255, 255, 0.02);
}}

.badge {{
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 6px;
  font-weight: 600;
  display: inline-block;
}}

.badge-danger {{ background: rgba(244, 63, 94, 0.15); color: var(--accent-rose); border: 1px solid rgba(244, 63, 94, 0.3); }}
.badge-warning {{ background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.3); }}
.badge-success {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); border: 1px solid rgba(16, 185, 129, 0.3); }}
.badge-neutral {{ background: var(--bg-surface); color: var(--text-secondary); }}

.score-bar-wrapper {{
  display: flex;
  align-items: center;
  gap: 8px;
}}

.score-bar {{
  flex: 1;
  height: 6px;
  background: var(--bg-surface);
  border-radius: 3px;
  overflow: hidden;
}}

.score-bar-fill {{
  height: 100%;
  background: var(--accent-gradient);
  border-radius: 3px;
}}

/* Metrics KPI Cards */
.kpi-card {{
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 12px;
  padding: 18px;
}}

.kpi-title {{
  font-size: 12px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
  margin-bottom: 6px;
}}

.kpi-value {{
  font-size: 26px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: #fff;
}}

.kpi-subtext {{
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}}
</style>
</head>
<body>

<div class="layout-container">
  <!-- Header -->
  <header>
    <div>
      <div class="brand-badge">PROYECTO EDA 2026-2 · GRUPO 1</div>
      <h1 class="app-title">Servicio y Visualización de Comercio Electrónico</h1>
      <p class="sub-header">Amazon Reviews 2023 (`Office_Products`) · Autocompletado Trie, Ranking Top-K Multicriterio y Panel de Fraude</p>
    </div>
    <div class="header-status">
      <div class="status-pill">
        <span class="status-dot"></span>
        <span>Pipeline Activo · 10 Etapas Conectadas</span>
      </div>
    </div>
  </header>

  <!-- Navigation Tabs -->
  <nav class="nav-tabs">
    <button class="tab-btn active" onclick="cambiarTab('tab-recomendacion')">
      <span>🎯</span> Recomendación Híbrida (Top-K)
    </button>
    <button class="tab-btn" onclick="cambiarTab('tab-autocompletado')">
      <span>⚡</span> Autocompletado en Vivo (Trie)
    </button>
    <button class="tab-btn" onclick="cambiarTab('tab-fraude')">
      <span>🛡️</span> Monitoreo de Fraude (Etapa 6)
    </button>
    <button class="tab-btn" onclick="cambiarTab('tab-metricas')">
      <span>📊</span> Arquitectura & Métricas del Pipeline
    </button>
  </nav>

  <!-- TAB 1: RECOMENDACIÓN TOP-K -->
  <div id="tab-recomendacion" class="tab-panel active">
    <!-- Selector de Producto Ancla -->
    <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 20px;">
      <span style="font-size: 13px; color: var(--text-secondary); font-weight: 500;">Seleccionar Producto Ancla:</span>
      <select id="sel-producto" onchange="actualizarProductoSeleccionado()" style="background: var(--bg-surface-elevated); color: #fff; border: 1px solid var(--border-subtle); padding: 8px 14px; border-radius: 8px; font-family: var(--font-main); font-size: 13px; cursor: pointer;">
        <option value="B0BF98LWMZ">B0BF98LWMZ · Toner Compatible Brother TN436 (Top-1 PageRank)</option>
        <option value="B0BN478RWJ">B0BN478RWJ · Impresora Todo-en-Uno HP DeskJet (Top-2 PageRank)</option>
        <option value="B087S2JRXY">B087S2JRXY · Trituradora de Papel Amazon Basics (Top-3 PageRank)</option>
        <option value="B0B3MX7LVG">B0B3MX7LVG · Cinta Doble Faz Nano EZlifego (Top-4 PageRank)</option>
        <option value="B0BZ67T58M">B0BZ67T58M · Ruedas de Silla de Oficina LIFELONG (Top-5 PageRank)</option>
      </select>
    </div>

    <!-- Ficha del Producto Seleccionado -->
    <div class="product-hero" id="hero-producto">
      <!-- Se inyecta dinámicamente -->
    </div>

    <!-- Tabla Top-K Recomendaciones Híbridas -->
    <div class="card">
      <div class="card-title">
        <span>Top-K Recomendaciones Complementarias (MinHeap O(N log K))</span>
        <span style="font-size: 12px; color: var(--text-muted); font-weight: 400;">Ponderación: 35% Atributos (HNSW) · 25% Comportamiento (LSH) · 20% PageRank · 20% Co-compra</span>
      </div>
      <table class="data-table" id="tabla-recomendaciones">
        <thead>
          <tr>
            <th style="width: 50px;">#</th>
            <th>Producto Recomendado</th>
            <th>Categoría</th>
            <th>Precio</th>
            <th style="width: 140px;">Score Híbrido</th>
            <th>Atributos</th>
            <th>Jaccard</th>
            <th>Co-compra</th>
            <th>PageRank</th>
          </tr>
        </thead>
        <tbody id="tbody-recs">
          <!-- Se inyecta dinámicamente -->
        </tbody>
      </table>
    </div>
  </div>

  <!-- TAB 2: AUTOCOMPLETADO (TRIE) -->
  <div id="tab-autocompletado" class="tab-panel">
    <div class="card" style="margin-bottom: 24px;">
      <div class="card-title">Autocompletado de Prefijo en Tiempo Real (Trie radix O(L + k), difuso OSA d = 1)</div>
      <div class="search-box-container">
        <div class="search-input-wrapper">
          <svg class="search-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
          <input type="text" id="input-busqueda" class="search-input" placeholder="Escribe un prefijo (ej. 'ton', 'bro', 'sta', 'pen', 'not', 'pilo', 'scot')..." oninput="manejarInputTrie(this.value)">
        </div>
        <div class="quick-chips">
          <span class="chip-label">Sugerencias rápidas:</span>
          <button class="chip" onclick="probarPrefijo('ton')">ton</button>
          <button class="chip" onclick="probarPrefijo('bro')">bro</button>
          <button class="chip" onclick="probarPrefijo('stap')">stap</button>
          <button class="chip" onclick="probarPrefijo('pen')">pen</button>
          <button class="chip" onclick="probarPrefijo('note')">note</button>
          <button class="chip" onclick="probarPrefijo('pilo')">pilo</button>
          <button class="chip" onclick="probarPrefijo('scot')">scot</button>
        </div>
        <div id="dropdown-trie" class="autocomplete-dropdown">
          <!-- Opciones dinámicas -->
        </div>
      </div>
    </div>

    <!-- Resultados de Búsqueda de Ejemplo -->
    <div class="card">
      <div class="card-title">Resultados de Búsqueda para "Toner Brother" (Ranking Híbrido Top-K)</div>
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 50px;">Rango</th>
            <th>ASIN</th>
            <th>Título del Producto</th>
            <th>Categoría</th>
            <th>Tienda</th>
            <th>Precio</th>
            <th>PageRank</th>
          </tr>
        </thead>
        <tbody id="tbody-busqueda">
          <!-- Inyectado dinámicamente -->
        </tbody>
      </table>
    </div>
  </div>

  <!-- TAB 3: MONITOREO DE FRAUDE (ETAPA 6) -->
  <div id="tab-fraude" class="tab-panel">
    <div class="grid-3col" style="margin-bottom: 24px;">
      <div class="kpi-card">
        <div class="kpi-title">Total Alertas Detectadas</div>
        <div class="kpi-value" id="kpi-total-alertas">{kpi_total_alertas}</div>
        <div class="kpi-subtext">Count-Min Sketch + Bloom Filter</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Órdenes Bloqueadas</div>
        <div class="kpi-value" style="color: var(--accent-rose);" id="kpi-bloqueadas">{kpi_bloqueadas}</div>
        <div class="kpi-subtext">Precisión medida con fraude_real: {kpi_precision}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Órdenes Retenidas para Revisión</div>
        <div class="kpi-value" style="color: var(--accent-amber);" id="kpi-retenidas">{kpi_retenidas}</div>
        <div class="kpi-subtext">Ráfaga de compras sospechosas</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Muestra de Alertas Procesadas en Tiempo Real (Etapa 6 -> Etapa 9)</div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Event ID</th>
            <th>Order ID</th>
            <th>User ID</th>
            <th>Motivos de Sospecha</th>
            <th>Estimado CMS</th>
            <th>Decisión Tomada</th>
            <th>Fraude Real</th>
          </tr>
        </thead>
        <tbody id="tbody-alertas">
          <!-- Inyectado dinámicamente -->
        </tbody>
      </table>
    </div>
  </div>

  <!-- TAB 4: MÉTRICAS DEL PIPELINE -->
  <div id="tab-metricas" class="tab-panel">
    <div class="grid-3col" style="margin-bottom: 24px;">
      <div class="kpi-card">
        <div class="kpi-title">Latencia Trie Autocompletado</div>
        <div class="kpi-value" style="color: var(--accent-cyan);">{kpi_lat_trie}</div>
        <div class="kpi-subtext">p50 exacto con Top-K caché · difuso (d = 1): {kpi_lat_difuso}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Latencia Top-K Recomendación</div>
        <div class="kpi-value" style="color: var(--accent-emerald);">{kpi_lat_rec}</div>
        <div class="kpi-subtext">Scoring híbrido 4 fuentes + MinHeap</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-title">Términos en Trie</div>
        <div class="kpi-value">{kpi_terminos}</div>
        <div class="kpi-subtext">Catálogo completo en {kpi_nodos} nodos radix</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Resumen de Algoritmos y Complejidades del Pipeline Completo</div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Etapa</th>
            <th>Módulo</th>
            <th>Estructura / Algoritmo Principal</th>
            <th>Complejidad Temporal</th>
            <th>Complejidad Espacial</th>
            <th>Rol en el Servicio (Etapa 9)</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Etapa 0</strong></td>
            <td>Ingesta & Buffer</td>
            <td>ColaCircular con drop de desborde</td>
            <td>O(1)</td>
            <td>O(capacidad)</td>
            <td>Buffer de entrada de eventos</td>
          </tr>
          <tr>
            <td><strong>Etapa 1</strong></td>
            <td>Preprocesamiento</td>
            <td>Merge sort + Búsqueda binaria</td>
            <td>O(N log N)</td>
            <td>O(N)</td>
            <td>Normalización de eventos</td>
          </tr>
          <tr>
            <td><strong>Etapa 2</strong></td>
            <td>Priorización</td>
            <td>MinHeap con envejecimiento (aging)</td>
            <td>O(log N)</td>
            <td>O(N)</td>
            <td>Estructura base para TopKHeap</td>
          </tr>
          <tr>
            <td><strong>Etapa 3</strong></td>
            <td>Almacenamiento</td>
            <td>TablaHash + AlmacenCatalogo + CSR</td>
            <td>O(1) / O(nnz)</td>
            <td>O(V + E)</td>
            <td>Consulta de metadatos de productos</td>
          </tr>
          <tr>
            <td><strong>Etapa 4</strong></td>
            <td>Indexación</td>
            <td>B+Tree en memoria y disco</td>
            <td>O(log B N)</td>
            <td>O(N)</td>
            <td>Búsquedas por rango de precio</td>
          </tr>
          <tr>
            <td><strong>Etapa 5</strong></td>
            <td>Índice Multidimensional</td>
            <td>KD-Tree y KNN exacto</td>
            <td>O(log N) / O(N d)</td>
            <td>O(N)</td>
            <td>Línea base exacta de vecinos</td>
          </tr>
          <tr>
            <td><strong>Etapa 6</strong></td>
            <td>Filtrado Probabilístico</td>
            <td>Bloom Filter + Count-Min Sketch</td>
            <td>O(k) / O(d)</td>
            <td>O(m + w·d)</td>
            <td>Centro de monitoreo de fraude</td>
          </tr>
          <tr>
            <td><strong>Etapa 7</strong></td>
            <td>Similitud Aproximada</td>
            <td>MinHash + LSH & HNSW</td>
            <td>O(b + c) / O(log N)</td>
            <td>O(N · n_hashes)</td>
            <td>Similares (atributos) y Relacionados</td>
          </tr>
          <tr>
            <td><strong>Etapa 8</strong></td>
            <td>Grafo de Dominio</td>
            <td>PageRank (Power Iteration) & Louvain</td>
            <td>O(k |E|) / O(N log N)</td>
            <td>O(V + E) CSR</td>
            <td>Autoridad global y afinidad co-compra</td>
          </tr>
          <tr>
            <td><strong>Etapa 9</strong></td>
            <td>Servicio & Visualización</td>
            <td>Trie radix con Top-K Caché, índice invertido & TopKHeap</td>
            <td>O(L + k) / O(Σ df) / O(N log K)</td>
            <td>O(N)</td>
            <td>Motor unificado de cara al usuario final</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
// Datos embebidos directamente del pipeline
const DATOS_DEMO = {json_demo_str};
const DATOS_METRICAS = {json_metricas_str};

function cambiarTab(tabId) {{
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));

  event.currentTarget.classList.add('active');
  document.getElementById(tabId).classList.add('active');
}}

// Manejo de Producto Seleccionado
function actualizarProductoSeleccionado() {{
  const sel = document.getElementById('sel-producto');
  const asin = sel.value;
  const prodData = DATOS_DEMO.catalogo_demo[asin];
  if (!prodData) return;

  const det = prodData.detalle || {{}};
  const recs = prodData.recomendaciones || [];

  // Hero Card
  const heroHtml = `
    <div class="product-avatar">📦</div>
    <div class="product-info">
      <div class="product-asin">ASIN: ${{det.parent_asin || asin}} · TIENDA: ${{det.store || 'N/D'}}</div>
      <div class="product-name">${{det.title || 'Producto sin título'}}</div>
      <div class="product-meta-row">
        <div>Categoría: <strong>${{det.categoria_hoja || 'N/D'}}</strong></div>
        <div>Precio: <strong>$${{det.precio ? Number(det.precio).toFixed(2) : 'N/D'}}</strong></div>
        <div>PageRank: <strong>${{det.pagerank ? Number(det.pagerank).toFixed(6) : '0.000000'}}</strong></div>
        <div>Comunidad Louvain: <span class="badge badge-neutral">Clúster #${{det.comunidad_louvain ?? 'N/D'}}</span></div>
      </div>
    </div>
  `;
  document.getElementById('hero-producto').innerHTML = heroHtml;

  // Tabla Recomendaciones
  const tbody = document.getElementById('tbody-recs');
  if (recs.length === 0) {{
    tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">No hay recomendaciones disponibles para este producto.</td></tr>';
    return;
  }}

  let recsHtml = '';
  recs.forEach(r => {{
    const pct = Math.round(r.score_hibrido * 100);
    recsHtml += `
      <tr>
        <td><strong>#${{r.rango}}</strong></td>
        <td>
          <div style="font-weight: 500; color: #fff;">${{r.titulo}}</div>
          <div style="font-size: 11px; font-family: var(--font-mono); color: var(--text-muted);">${{r.parent_asin}} · ${{r.tienda}}</div>
        </td>
        <td><span class="badge badge-neutral">${{r.categoria}}</span></td>
        <td style="font-weight: 600;">${{r.precio ? '$' + Number(r.precio).toFixed(2) : 'N/D'}}</td>
        <td>
          <div class="score-bar-wrapper">
            <span style="font-weight: 600; width: 36px;">${{r.score_hibrido.toFixed(2)}}</span>
            <div class="score-bar"><div class="score-bar-fill" style="width: ${{pct}}%;"></div></div>
          </div>
        </td>
        <td>${{r.similitud_atributos.toFixed(2)}}</td>
        <td>${{r.jaccard_comportamiento.toFixed(3)}}</td>
        <td>${{r.afinidad_cocompra.toFixed(2)}}</td>
        <td style="font-family: var(--font-mono); font-size: 11px;">${{r.pagerank.toFixed(6)}}</td>
      </tr>
    `;
  }});
  tbody.innerHTML = recsHtml;
}}

// Autocompletado Trie
function manejarInputTrie(val) {{
  const dropdown = document.getElementById('dropdown-trie');
  const q = val.trim().toLowerCase();
  if (q.length < 2) {{
    dropdown.style.display = 'none';
    return;
  }}

  // Buscar en las demos de autocompletado
  let resultados = [];
  const demos = Object.assign({{}}, DATOS_DEMO.autocompletado || {{}}, DATOS_DEMO.autocompletado_difuso || {{}});
  for (const [pref, sugs] of Object.entries(demos)) {{
    if (q.startsWith(pref) || pref.startsWith(q)) {{
      resultados = sugs;
      break;
    }}
  }}

  if (resultados.length === 0) {{
    // Simular búsqueda de coincidencia
    dropdown.innerHTML = '<div style="padding: 10px; color: var(--text-muted); font-size: 13px;">No se encontraron términos para "' + q + '"</div>';
    dropdown.style.display = 'block';
    return;
  }}

  let html = '';
  resultados.slice(0, 6).forEach(s => {{
    const tipo = (s.meta && s.meta.tipo) ? s.meta.tipo : 'producto';
    html += `
      <div class="sug-item" onclick="seleccionarSugerencia('${{s.termino}}')">
        <span class="sug-text">${{s.termino}}${{s.distancia ? ' <small style="color: var(--text-muted);">(OSA ' + s.distancia + ')</small>' : ''}}</span>
        <span class="sug-tag tag-${{tipo}}">${{tipo}}</span>
      </div>
    `;
  }});
  dropdown.innerHTML = html;
  dropdown.style.display = 'block';
}}

function probarPrefijo(pref) {{
  const input = document.getElementById('input-busqueda');
  input.value = pref;
  manejarInputTrie(pref);
}}

function seleccionarSugerencia(termino) {{
  document.getElementById('input-busqueda').value = termino;
  document.getElementById('dropdown-trie').style.display = 'none';
}}

// Cargar Búsqueda de Ejemplo
function cargarBusquedaDemo() {{
  const tbody = document.getElementById('tbody-busqueda');
  const items = (DATOS_DEMO.busqueda_ejemplo && DATOS_DEMO.busqueda_ejemplo.resultados) || [];
  let html = '';
  items.forEach(it => {{
    html += `
      <tr>
        <td><strong>#${{it.rango}}</strong></td>
        <td style="font-family: var(--font-mono); font-size: 12px; color: var(--accent-cyan);">${{it.parent_asin}}</td>
        <td style="font-weight: 500;">${{it.titulo}}</td>
        <td><span class="badge badge-neutral">${{it.categoria}}</span></td>
        <td>${{it.tienda}}</td>
        <td style="font-weight: 600;">${{it.precio ? '$' + Number(it.precio).toFixed(2) : 'N/D'}}</td>
        <td style="font-family: var(--font-mono);">${{it.pagerank.toFixed(6)}}</td>
      </tr>
    `;
  }});
  tbody.innerHTML = html;
}}

// Cargar Alertas de Fraude
function cargarAlertasDemo() {{
  const tbody = document.getElementById('tbody-alertas');
  const items = (DATOS_DEMO.fraude && DATOS_DEMO.fraude.muestra) || [];
  let html = '';
  items.forEach(al => {{
    const decClass = al.decision === 'bloquear' ? 'badge-danger' : (al.decision === 'retener' ? 'badge-warning' : 'badge-success');
    const fraudeReal = al.fraude_real ? '<span style="color: var(--accent-rose); font-weight: 600;">SÍ</span>' : '<span style="color: var(--text-muted);">NO</span>';
    html += `
      <tr>
        <td style="font-family: var(--font-mono); font-size: 12px;">${{al.event_id}}</td>
        <td style="font-family: var(--font-mono); font-size: 12px;">${{al.order_id}}</td>
        <td style="font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary);">${{al.user_id}}</td>
        <td>${{al.motivos.map(m => '<span class="badge badge-neutral">' + m + '</span>').join(' ')}}</td>
        <td style="font-family: var(--font-mono);">${{al.estimado_cms !== null ? al.estimado_cms : '—'}}</td>
        <td><span class="badge ${{decClass}}">${{al.decision.toUpperCase()}}</span></td>
        <td>${{fraudeReal}}</td>
      </tr>
    `;
  }});
  tbody.innerHTML = html;
}}

// Inicializar al cargar
window.addEventListener('DOMContentLoaded', () => {{
  actualizarProductoSeleccionado();
  cargarBusquedaDemo();
  cargarAlertasDemo();
}});
</script>
</body>
</html>
"""

destino = RAIZ / "visualizador" / "dashboard_servicio.html"
with open(destino, "w", encoding="utf-8") as f:
    f.write(html_content)

print(f"Dashboard interactivo generado en: {destino}")
