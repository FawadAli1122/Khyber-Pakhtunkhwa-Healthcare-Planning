# Interactive Dashboard (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the static `report/KP_Healthcare_Plan.html` into a self-contained interactive dashboard — sortable/searchable/filterable district table, a clickable choropleth map, and a new "International GIS Standards" methodology section — with zero new runtime dependencies and no build toolchain.

**Architecture:** `scripts/14_build_html_report.py` (the existing report generator) gains two new pure-Python helper modules — `scripts/lib/dashboard_data.py` (metrics/geometry → JSON payload for the choropleth) and `scripts/lib/dashboard_assets.py` (CSS/JS string constants) — and its `build()` function is extended to embed that JSON/CSS/JS into the generated HTML and add `data-*` attributes to the district table. All interactivity is vanilla JS running client-side against the embedded data; nothing is fetched at runtime.

**Tech Stack:** Python 3.12, `shapely` (already a dependency, used for `.simplify()`), stdlib `csv`/`json`, vanilla JS/CSS (no npm, no CDN), `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-15-interactive-dashboard-phase1-design.md`

## Global Constraints

- No new Python dependencies — reuse `shapely`, already imported in `scripts/14_build_html_report.py`.
- No CDN links, no `<script src="...">` to any external URL, no separate `.json`/`.js` files fetched at runtime — everything inlines into the one generated HTML file so it still opens via `file://`.
- Follow the existing `scripts/lib/` module + `from scripts.lib.<module> import <name>` import convention (see `scripts/11_suggest_new_sites.py:17-18`).
- Follow the existing `tests/verify_*.py` convention (plain `assert`-based script with `if __name__ == "__main__": main()`) for end-to-end checks, and `tests/lib/test_*.py` (pytest style) for unit tests on pure functions.
- Reuse `TIER_COLORS` (`scripts/14_build_html_report.py:34-39`) verbatim for any tier-based coloring — do not redefine the four hex values elsewhere.
- Preserve every existing section/figure/table in the report exactly as-is unless a task explicitly says to replace it (the population-density and gap-score maps are the only two removed; the facility-distribution and DEM maps stay unchanged).

---

### Task 1: `scripts/lib/dashboard_data.py` — geometry/metrics → dashboard JSON payload

**Files:**
- Create: `scripts/lib/dashboard_data.py`
- Test: `tests/lib/test_dashboard_data.py`

**Interfaces:**
- Produces: `dashboard_data.LON_MIN`, `LON_MAX`, `LAT_MIN`, `LAT_MAX`, `SVG_WIDTH`, `SVG_HEIGHT` (module-level constants); `dashboard_data.project(lon, lat) -> (float, float)`; `dashboard_data.polygon_to_svg_path(geom) -> str` (geom is a shapely `Polygon`/`MultiPolygon`); `dashboard_data.build_dashboard_payload(boundaries, metrics) -> dict` (`boundaries` is the parsed `data/processed/boundaries.json` dict, `metrics` is a list of `dict` rows as returned by `csv.DictReader` on `data/processed/district_metrics.csv`) — returns `{"districts": [{"district": str, "gap_score": float, "pop_density": float, "need_tier": str, "path": str}, ...]}`, JSON-serializable via `json.dumps`.
- Consumes: nothing from other tasks (pure data transform, only depends on `shapely.geometry.shape`, already used the same way in `scripts/14_build_html_report.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/test_dashboard_data.py`:

```python
import json

import pytest
from shapely.geometry import Polygon

from scripts.lib import dashboard_data


def test_project_maps_corners_to_svg_bounds():
    x0, y0 = dashboard_data.project(dashboard_data.LON_MIN, dashboard_data.LAT_MAX)
    x1, y1 = dashboard_data.project(dashboard_data.LON_MAX, dashboard_data.LAT_MIN)
    assert x0 == pytest.approx(0.0)
    assert y0 == pytest.approx(0.0)
    assert x1 == pytest.approx(dashboard_data.SVG_WIDTH)
    assert y1 == pytest.approx(dashboard_data.SVG_HEIGHT)


def test_polygon_to_svg_path_triangle_keeps_all_vertices_no_duplicate_close():
    triangle = Polygon([(71.0, 34.0), (72.0, 34.0), (71.5, 35.0)])
    path = dashboard_data.polygon_to_svg_path(triangle)
    assert path.startswith("M")
    assert path.endswith("Z")
    # 3 unique vertices, closing duplicate dropped (SVG "Z" re-closes it) -> 1 "M" + 2 "L"
    assert path.count("L") == 2


def test_build_dashboard_payload_matches_metrics_and_skips_unmatched_boundary():
    square = [[71.0, 34.0], [72.0, 34.0], [71.5, 35.0], [71.0, 34.0]]
    boundaries = {
        "districts": [
            {"district": "Testabad", "geometry": {"type": "Polygon", "coordinates": [square]}},
            {"district": "NoMetricsDistrict", "geometry": {"type": "Polygon", "coordinates": [square]}},
        ]
    }
    metrics = [
        {"district": "Testabad", "gap_score": "42.34", "pop_density": "100.06", "need_tier": "High"},
    ]

    payload = dashboard_data.build_dashboard_payload(boundaries, metrics)

    assert len(payload["districts"]) == 1
    rec = payload["districts"][0]
    assert rec["district"] == "Testabad"
    assert rec["gap_score"] == pytest.approx(42.3)
    assert rec["pop_density"] == pytest.approx(100.1)
    assert rec["need_tier"] == "High"
    assert rec["path"].startswith("M")
    json.dumps(payload)  # must be JSON-serializable
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_dashboard_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.lib.dashboard_data'`

- [ ] **Step 3: Implement `scripts/lib/dashboard_data.py`**

```python
"""Build the JSON payload embedded in the interactive dashboard: one
compact record per district combining its gap score/population density
(for the choropleth fill) with a simplified SVG path for its boundary
geometry. Pure data transform - no HTML/JS/matplotlib here, so it's
testable without rendering anything.
"""
from shapely.geometry import shape

# KP's districts span roughly this lon/lat box (data/processed/boundaries.json).
# A fixed box - rather than deriving min/max per render - keeps the choropleth's
# scale stable across rebuilds even if a future district edit shifts the data's
# bounding box slightly.
LON_MIN, LON_MAX = 69.3, 74.2
LAT_MIN, LAT_MAX = 31.5, 36.9
SVG_WIDTH = 620
SVG_HEIGHT = 740

# Degrees, not meters - district polygons don't need survey precision for a
# ~600px map, and this keeps the embedded JSON payload small.
SIMPLIFY_TOLERANCE_DEG = 0.005


def project(lon, lat):
    """Linear lon/lat -> SVG pixel coords. Y is flipped: SVG grows downward,
    latitude grows upward."""
    x = (lon - LON_MIN) / (LON_MAX - LON_MIN) * SVG_WIDTH
    y = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * SVG_HEIGHT
    return x, y


def polygon_to_svg_path(geom):
    """Convert a shapely Polygon/MultiPolygon into one SVG <path> `d` string
    (one subpath per polygon part; interior rings/holes are not drawn - none
    of KP's district polygons have holes worth rendering at dashboard scale).
    """
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    parts = []
    for poly in polys:
        simplified = poly.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
        coords = list(simplified.exterior.coords)
        if coords and coords[0] == coords[-1]:
            coords = coords[:-1]  # drop the duplicate closing point; "Z" re-closes it
        if len(coords) < 3:
            continue
        points = [project(lon, lat) for lon, lat in coords]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points) + " Z"
        parts.append(d)
    return " ".join(parts)


def build_dashboard_payload(boundaries, metrics):
    """boundaries: parsed data/processed/boundaries.json
    ({"districts": [{"district": ..., "geometry": <geojson>}, ...]}).
    metrics: list of dict rows from data/processed/district_metrics.csv
    (csv.DictReader output).
    Returns a JSON-serializable dict for the "#dashboard-data" script block.
    """
    metrics_by_district = {m["district"]: m for m in metrics}
    records = []
    for d in boundaries["districts"]:
        m = metrics_by_district.get(d["district"])
        if m is None:
            continue
        records.append(
            {
                "district": d["district"],
                "gap_score": round(float(m["gap_score"]), 1),
                "pop_density": round(float(m["pop_density"]), 1),
                "need_tier": m["need_tier"],
                "path": polygon_to_svg_path(shape(d["geometry"])),
            }
        )
    return {"districts": records}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_dashboard_data.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/dashboard_data.py tests/lib/test_dashboard_data.py
git commit -m "feat: add dashboard_data module for choropleth JSON payload

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `scripts/lib/dashboard_assets.py` — CSS/JS for the interactive dashboard

**Files:**
- Create: `scripts/lib/dashboard_assets.py`
- Test: `tests/lib/test_dashboard_assets.py`

**Interfaces:**
- Produces: `dashboard_assets.DASHBOARD_CSS` (str), `dashboard_assets.DASHBOARD_JS` (str) — both plain (non-f) strings so their `{`/`}` braces need no escaping when spliced into `scripts/14_build_html_report.py`'s report-building f-string later (Task 4).
- Consumes: nothing (no Python dependency on Task 1 — the JS reads `#dashboard-data` and the table's `data-*` attributes purely at runtime in the browser).

**Contract the JS depends on** (established here, consumed by markup added in Tasks 4-5 — record it so those tasks match exactly):
- `<script type="application/json" id="dashboard-data">` holds the JSON from `dashboard_data.build_dashboard_payload(...)`.
- `<svg id="choropleth-svg">` is the mount point for one `<path data-district="...">` per district.
- `<div id="choropleth-legend">` is the mount point for the legend swatches.
- `.metric-toggle button[data-metric="gap_score"|"pop_density"]` switch the choropleth's fill metric.
- `#district-table` is a `<table>` whose `<tbody>` rows each carry `data-district`, `data-population`, `data-area`, `data-density`, `data-institutions`, `data-beds`, `data-doctors`, `data-terrain`, `data-gap-score`, `data-tier`.
- `#district-search` is the free-text search `<input>`.
- `.tier-filter-chip[data-tier="Critical"|"High"|"Moderate"|"Low"]` are the tier filter buttons.
- `th[data-sort-key="district"|"population"|"area"|"density"|"institutions"|"beds"|"doctors"|"terrain"|"gap-score"|"tier"]` make a column header sortable.

- [ ] **Step 1: Write the failing tests**

Create `tests/lib/test_dashboard_assets.py`:

```python
from scripts.lib import dashboard_assets


def test_dashboard_css_contains_key_selectors():
    for selector in ["#choropleth-svg", ".tier-filter-chip", ".dashboard-table", ".metric-toggle", "#district-search"]:
        assert selector in dashboard_assets.DASHBOARD_CSS, f"missing selector: {selector}"


def test_dashboard_js_contains_key_hooks():
    for token in [
        "DOMContentLoaded",
        "dashboard-data",
        "choropleth-svg",
        "choropleth-legend",
        "district-table",
        "district-search",
        "tier-filter-chip",
        "data-sort-key",
        "metric-toggle",
    ]:
        assert token in dashboard_assets.DASHBOARD_JS, f"missing JS hook: {token}"


def test_dashboard_js_braces_and_parens_balance():
    js = dashboard_assets.DASHBOARD_JS
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
    assert js.count("[") == js.count("]")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/lib/test_dashboard_assets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.lib.dashboard_assets'`

- [ ] **Step 3: Implement `scripts/lib/dashboard_assets.py`**

```python
"""CSS and JS string constants for the interactive dashboard, kept as plain
(non-f) strings so scripts/14_build_html_report.py can splice them into its
report-building f-string via {DASHBOARD_CSS}/{DASHBOARD_JS} without needing
to escape every brace in this file. See dashboard_assets's module docstring
in the implementation plan (Task 2) for the DOM contract these depend on.
"""

DASHBOARD_CSS = r"""
/* --- Interactive dashboard (Phase 1) --- */
.dashboard-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
  margin: 0.75rem 0 1rem;
}
.dashboard-controls input[type="search"] {
  flex: 1 1 220px;
  padding: 0.45rem 0.7rem;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
  font-size: 0.95rem;
}
#district-search {
  min-width: 180px;
}
.tier-filter-chip, .metric-toggle button {
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--ink-soft);
  border-radius: 999px;
  padding: 0.35rem 0.85rem;
  font-size: 0.85rem;
  cursor: pointer;
}
.tier-filter-chip[aria-pressed="true"] {
  background: var(--tier-color, var(--accent));
  color: #fff;
  border-color: transparent;
}
.metric-toggle {
  display: flex;
  gap: 0.4rem;
}
.metric-toggle button[aria-pressed="true"] {
  background: var(--accent);
  color: #fff;
  border-color: transparent;
}
table.dashboard-table th[data-sort-key] {
  cursor: pointer;
  user-select: none;
}
table.dashboard-table th.sorted-asc:after { content: " \25B2"; opacity: 1; }
table.dashboard-table th.sorted-desc:after { content: " \25BC"; opacity: 1; }
table.dashboard-table tbody tr.is-hidden { display: none; }
table.dashboard-table tbody tr.is-dimmed { opacity: 0.35; }
table.dashboard-table tbody tr.is-active { outline: 2px solid var(--accent); outline-offset: -2px; }
#choropleth-wrap {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  align-items: center;
}
#choropleth-svg {
  width: 100%;
  max-width: 420px;
  height: auto;
}
#choropleth-svg path {
  stroke: var(--paper);
  stroke-width: 0.6;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
#choropleth-svg path.is-dimmed { opacity: 0.25; }
#choropleth-svg path.is-active { stroke: var(--accent); stroke-width: 2; }
.choropleth-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--ink-soft);
}
.choropleth-legend span.swatch {
  display: inline-block;
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 2px;
  margin-right: 0.3rem;
  vertical-align: middle;
}
"""

DASHBOARD_JS = r"""
(function () {
  "use strict";

  var TIER_COLORS = { "Critical": "#d03b3b", "High": "#ec835a", "Moderate": "#fab219", "Low": "#0ca30c" };
  var TIER_RANK = { "Critical": 3, "High": 2, "Moderate": 1, "Low": 0 };

  function byId(id) { return document.getElementById(id); }

  function cssEscape(value) {
    if (window.CSS && window.CSS.escape) return window.CSS.escape(value);
    return value.replace(/["\\]/g, "\\$&");
  }

  function densityColor(value, min, max) {
    var t = max > min ? (value - min) / (max - min) : 0;
    t = Math.max(0, Math.min(1, t));
    var lo = [253, 237, 216], hi = [168, 90, 23]; // light -> dark ochre, matches the report's accent hue
    var r = Math.round(lo[0] + (hi[0] - lo[0]) * t);
    var g = Math.round(lo[1] + (hi[1] - lo[1]) * t);
    var b = Math.round(lo[2] + (hi[2] - lo[2]) * t);
    return "rgb(" + r + "," + g + "," + b + ")";
  }

  function loadDashboardData() {
    var el = byId("dashboard-data");
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function initChoropleth() {
    var data = loadDashboardData();
    var svg = byId("choropleth-svg");
    var legend = byId("choropleth-legend");
    if (!svg || !data || !data.districts || !data.districts.length) return null;

    var districts = data.districts;
    var densities = districts.map(function (d) { return d.pop_density; });
    var densityMin = Math.min.apply(null, densities);
    var densityMax = Math.max.apply(null, densities);
    var metric = "gap_score";
    var paths = {};

    districts.forEach(function (d) {
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d.path);
      path.setAttribute("data-district", d.district);
      path.setAttribute("role", "button");
      path.setAttribute("tabindex", "0");
      var title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = d.district;
      path.appendChild(title);
      svg.appendChild(path);
      paths[d.district] = path;
      path.addEventListener("click", function () { setActiveDistrict(d.district); });
      path.addEventListener("keydown", function (evt) {
        if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); setActiveDistrict(d.district); }
      });
    });

    function renderLegend() {
      if (!legend) return;
      legend.innerHTML = "";
      var entries = metric === "gap_score"
        ? [["Critical", TIER_COLORS.Critical], ["High", TIER_COLORS.High], ["Moderate", TIER_COLORS.Moderate], ["Low", TIER_COLORS.Low]]
        : [["Lower density", densityColor(densityMin, densityMin, densityMax)], ["Higher density", densityColor(densityMax, densityMin, densityMax)]];
      entries.forEach(function (entry) {
        var item = document.createElement("span");
        var swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.background = entry[1];
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(entry[0]));
        legend.appendChild(item);
      });
    }

    function paint() {
      districts.forEach(function (d) {
        var color = metric === "gap_score"
          ? (TIER_COLORS[d.need_tier] || "#8a8f8c")
          : densityColor(d.pop_density, densityMin, densityMax);
        paths[d.district].setAttribute("fill", color);
      });
      renderLegend();
    }

    function setMetric(next) {
      metric = next;
      paint();
    }

    function setDimmed(activeTier) {
      districts.forEach(function (d) {
        paths[d.district].classList.toggle("is-dimmed", !!(activeTier && d.need_tier !== activeTier));
      });
    }

    function setActiveDistrict(name) {
      Object.keys(paths).forEach(function (key) {
        paths[key].classList.toggle("is-active", key === name);
      });
      var row = document.querySelector('tr[data-district="' + cssEscape(name) + '"]');
      if (row) {
        row.classList.add("is-active");
        row.scrollIntoView({ block: "nearest", behavior: "smooth" });
        setTimeout(function () { row.classList.remove("is-active"); }, 1600);
      }
    }

    paint();
    return { setMetric: setMetric, setDimmed: setDimmed, setActiveDistrict: setActiveDistrict };
  }

  function initTable(choropleth) {
    var table = byId("district-table");
    if (!table) return;
    var tbody = table.tBodies[0];
    var rows = Array.prototype.slice.call(tbody.rows);
    var search = byId("district-search");
    var chips = Array.prototype.slice.call(document.querySelectorAll(".tier-filter-chip"));
    var activeTier = null;

    function applyFilters() {
      var term = (search && search.value || "").trim().toLowerCase();
      rows.forEach(function (row) {
        var matchesTier = !activeTier || row.getAttribute("data-tier") === activeTier;
        var matchesSearch = !term || row.getAttribute("data-district").toLowerCase().indexOf(term) !== -1;
        row.classList.toggle("is-hidden", !(matchesTier && matchesSearch));
      });
      if (choropleth) choropleth.setDimmed(activeTier);
    }

    if (search) search.addEventListener("input", applyFilters);

    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        var tier = chip.getAttribute("data-tier");
        activeTier = activeTier === tier ? null : tier;
        chips.forEach(function (c) { c.setAttribute("aria-pressed", c === chip && activeTier ? "true" : "false"); });
        applyFilters();
      });
    });

    rows.forEach(function (row) {
      row.addEventListener("click", function () {
        if (choropleth) choropleth.setActiveDistrict(row.getAttribute("data-district"));
      });
    });

    function compareRows(a, b, key) {
      if (key === "tier") {
        return (TIER_RANK[a.getAttribute("data-tier")] || 0) - (TIER_RANK[b.getAttribute("data-tier")] || 0);
      }
      var av = a.getAttribute("data-" + key);
      var bv = b.getAttribute("data-" + key);
      var an = parseFloat(av);
      var bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return an - bn;
      return String(av).localeCompare(String(bv));
    }

    var headers = Array.prototype.slice.call(table.querySelectorAll("th[data-sort-key]"));
    var sortState = { key: null, dir: 1 };
    headers.forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute("data-sort-key");
        sortState.dir = sortState.key === key ? -sortState.dir : 1;
        sortState.key = key;
        headers.forEach(function (h) { h.classList.remove("sorted-asc", "sorted-desc"); });
        th.classList.add(sortState.dir === 1 ? "sorted-asc" : "sorted-desc");
        var sorted = rows.slice().sort(function (a, b) { return compareRows(a, b, key) * sortState.dir; });
        sorted.forEach(function (row) { tbody.appendChild(row); });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var choropleth = initChoropleth();
    initTable(choropleth);
    var toggleButtons = Array.prototype.slice.call(document.querySelectorAll(".metric-toggle button"));
    toggleButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        toggleButtons.forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
        if (choropleth) choropleth.setMetric(btn.getAttribute("data-metric"));
      });
    });
  });
})();
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/lib/test_dashboard_assets.py -v`
Expected: 3 passed. If the brace/paren/bracket balance test fails, re-count by eye against the code above before touching anything else — it means a typo was introduced while copying.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/dashboard_assets.py tests/lib/test_dashboard_assets.py
git commit -m "feat: add dashboard_assets module (interactive table/choropleth CSS+JS)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: New "Methodology: International GIS Standards" section

**Files:**
- Modify: `scripts/14_build_html_report.py`

**Interfaces:**
- Produces: `methodology_html() -> str` (new module-level function, no arguments — the content is static prose, not data-driven).
- Consumes: nothing from Tasks 1-2.

- [ ] **Step 1: Add the `methodology_html()` function**

In `scripts/14_build_html_report.py`, add this new function directly after `horizon_table_html` (which ends just before `def build(source_boundary, source_population_note):`):

```python
def methodology_html():
    return """<section id="methodology-global">
<h2>Methodology: International GIS Standards</h2>
<p>The accessibility and siting methods used elsewhere in this report are simplified, transparent heuristics
chosen to be reproducible from open data in a single pipeline run &mdash; not novel research. This section states
plainly how they relate to the established international toolkit for GIS-based health-system planning, so a
reader familiar with that literature can see exactly what this report does and does not attempt.</p>
<ul>
  <li><strong>Two-Step Floating Catchment Area (2SFCA) / Enhanced 2SFCA:</strong> the standard method for
  measuring spatial accessibility, pairing population points with facilities within a threshold distance or
  travel time and weighting by facility capacity and demand competition. This report's <code>accessibility_km</code>
  metric is a straight-line distance to the nearest mapped facility only &mdash; it does not weight by facility
  capacity or account for competing demand from neighboring districts the way 2SFCA does. Adopting full 2SFCA is
  the natural next step once network-distance routing is available.</li>
  <li><strong>WHO AccessMod:</strong> WHO's own GIS extension for modeling travel-time accessibility to health
  facilities from a DEM, road network, and land cover. This pipeline already fetches the two spatial inputs
  AccessMod needs &mdash; the Copernicus GLO-30 DEM (<code>gis/KP_DEM.tif</code>) and OpenStreetMap roads
  (<code>data/raw/osm_roads.json</code>) &mdash; but currently uses the DEM only for the terrain-difficulty score
  and the roads only for the road-length figures in Official Infrastructure Context, not for routing.
  Accessibility here is straight-line distance, not the travel-time surface AccessMod would produce.</li>
  <li><strong>WHO Service Availability and Readiness Assessment (SARA):</strong> WHO's framework splitting
  facility adequacy into <em>availability</em> (infrastructure and staff physically present) and
  <em>readiness</em> (equipment, medicines, and capacity to actually deliver a given service). This report's
  Institutions/Beds-per-1,000/Doctors-per-1,000 figures are availability indicators in SARA's sense; it has no
  readiness data (equipment, medicine stock, diagnostic capacity) at facility level, since neither the KPHCC
  registry nor Development Statistics 2025 publishes it.</li>
  <li><strong>Location-allocation siting (p-median / Maximal Covering Location Problem):</strong> the standard
  optimization approach for deciding where to place new facilities &mdash; p-median minimizes population-weighted
  average distance to the nearest facility for a fixed number of new sites; MCLP maximizes the population covered
  within a fixed service radius. This report's Suggested New Sites (see Data Sources &amp; Methodology, above)
  uses population-weighted K-Means clustering of settlement points ranked by distance from the nearest existing
  facility &mdash; an approximate maximum-coverage heuristic, not a solved p-median/MCLP instance. It will tend to
  agree with a full solve on which districts need new sites, but not necessarily on the exact optimal point
  within each district.</li>
</ul>
<p>None of the above changes this report's current numbers &mdash; they describe where the fuller international
methods would refine what's here, tracked as follow-on work rather than implemented in this pass.</p>
</section>

"""
```

- [ ] **Step 2: Insert the section into `build()`'s HTML and call the function**

In `scripts/14_build_html_report.py`, find this exact text (the end of the `#sources` section, right before `#current-state` begins):

```
</ul>
</section>

<section id="current-state">
```

Replace it with:

```
</ul>
</section>

{methodology_html()}<section id="current-state">
```

- [ ] **Step 3: Verify the report still builds**

Run: `python scripts/14_build_html_report.py`
Expected: exits 0, prints `Wrote report/KP_Healthcare_Plan.html`

- [ ] **Step 4: Verify the new section is present**

Run: `python -c "html = open('report/KP_Healthcare_Plan.html', encoding='utf-8').read(); assert '<section id=\"methodology-global\">' in html; assert 'Two-Step Floating Catchment Area' in html; assert 'p-median' in html.lower(); print('OK')"`
Expected: prints `OK`

- [ ] **Step 5: Commit**

```bash
git add scripts/14_build_html_report.py report/KP_Healthcare_Plan.html
git commit -m "feat: add International GIS Standards methodology section to report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Interactive choropleth — replace the static population/gap-score maps

**Files:**
- Modify: `scripts/14_build_html_report.py`

**Interfaces:**
- Consumes: `scripts.lib.dashboard_data.build_dashboard_payload` (Task 1), `scripts.lib.dashboard_assets.DASHBOARD_CSS` / `DASHBOARD_JS` (Task 2). DOM contract from Task 2's Interfaces block (`#dashboard-data`, `#choropleth-svg`, `#choropleth-legend`, `.metric-toggle button[data-metric]`).
- Produces: nothing new for later tasks (this is the last piece that needs the choropleth's markup IDs — Task 5 only needs the table, which already exists).

- [ ] **Step 1: Add the new imports**

In `scripts/14_build_html_report.py`, find:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

Replace with:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.dashboard_assets import DASHBOARD_CSS, DASHBOARD_JS
from scripts.lib.dashboard_data import build_dashboard_payload
```

- [ ] **Step 2: Delete `render_population_map` and `render_gap_score_map`**

In `scripts/14_build_html_report.py`, delete these two full function definitions (everything from `def render_population_map` through the end of `render_gap_score_map`, i.e. up to but not including `def render_facility_map`):

```python
def render_population_map(boundaries, metrics_by_district):
    fig, ax = plt.subplots(figsize=(6.2, 7.4))
    densities = [float(m["pop_density"]) for m in metrics_by_district.values()]
    lo, hi = min(densities), max(densities)
    for d in boundaries["districts"]:
        m = metrics_by_district.get(d["district"])
        density = float(m["pop_density"]) if m else 0
        t = (density - lo) / (hi - lo) if hi > lo else 0
        color = plt.cm.YlOrRd(0.12 + 0.82 * t)
        _plot_polygon(ax, shape(d["geometry"]), facecolor=color, edgecolor="#16211f", linewidth=0.4)
    ax.set_title("Population Density (2023 Census)", fontsize=12, color="#16211f")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


def render_gap_score_map(boundaries, metrics_by_district):
    fig, ax = plt.subplots(figsize=(6.2, 7.4))
    for d in boundaries["districts"]:
        m = metrics_by_district.get(d["district"])
        score = float(m["gap_score"]) if m else 0
        color = plt.cm.RdYlGn_r(score / 100.0)
        _plot_polygon(ax, shape(d["geometry"]), facecolor=color, edgecolor="#16211f", linewidth=0.4)
    ax.set_title("Healthcare Access Gap Score", fontsize=12, color="#16211f")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


```

- [ ] **Step 3: Remove their call sites and add the dashboard payload**

Find:

```python
    pop_map_b64 = render_population_map(boundaries, metrics_by_district)
    gap_map_b64 = render_gap_score_map(boundaries, metrics_by_district)
    fac_map_b64 = render_facility_map(boundaries, facilities)
    dem_map_b64 = render_dem_map(GIS_DIR / "KP_DEM.tif")
```

Replace with:

```python
    fac_map_b64 = render_facility_map(boundaries, facilities)
    dem_map_b64 = render_dem_map(GIS_DIR / "KP_DEM.tif")
    dashboard_payload = build_dashboard_payload(boundaries, metrics)
```

- [ ] **Step 4: Replace the `#current-state` figure grid with the choropleth**

Find:

```
<section id="current-state">
<h2>Current State</h2>
<div class="figure-grid">
  <figure><img src="data:image/png;base64,{pop_map_b64}" alt="Population density map"><figcaption>Population density by district.</figcaption></figure>
  <figure><img src="data:image/png;base64,{gap_map_b64}" alt="Gap score map"><figcaption>Healthcare access gap score by district.</figcaption></figure>
</div>
<figure style="margin-top:1.25rem">
  <img src="data:image/png;base64,{fac_map_b64}" alt="Facility distribution map">
  <figcaption>Known healthcare facility distribution (KPHCC + OSM, deduplicated).</figcaption>
</figure>
</section>
```

Replace with:

```
<section id="current-state">
<h2>Current State</h2>
<div id="choropleth-wrap">
  <div class="metric-toggle">
    <button type="button" data-metric="gap_score" aria-pressed="true">Gap score</button>
    <button type="button" data-metric="pop_density" aria-pressed="false">Population density</button>
  </div>
  <svg id="choropleth-svg" viewBox="0 0 620 740" role="img" aria-label="District choropleth map"></svg>
  <div id="choropleth-legend" class="choropleth-legend"></div>
  <p class="dek" style="font-size:0.85rem">Click a district to highlight its row in the table below; use the toggle to switch what the map is colored by.</p>
</div>
<figure style="margin-top:1.25rem">
  <img src="data:image/png;base64,{fac_map_b64}" alt="Facility distribution map">
  <figcaption>Known healthcare facility distribution (KPHCC + OSM, deduplicated).</figcaption>
</figure>
</section>
```

- [ ] **Step 5: Embed the JSON payload, CSS, and JS**

In `scripts/14_build_html_report.py`, find the end of the `<style>` block:

```
</style>
```

Replace with:

```
{DASHBOARD_CSS}
</style>
```

Then find the end of the document:

```
</main>
</body>
</html>
"""
```

Replace with:

```
</main>
<script type="application/json" id="dashboard-data">{json.dumps(dashboard_payload)}</script>
<script>{DASHBOARD_JS}</script>
</body>
</html>
"""
```

- [ ] **Step 6: Verify the report still builds**

Run: `python scripts/14_build_html_report.py`
Expected: exits 0, prints `Wrote report/KP_Healthcare_Plan.html`

- [ ] **Step 7: Verify the embedded payload and markup**

Run:

```bash
python -c "
import json
html = open('report/KP_Healthcare_Plan.html', encoding='utf-8').read()
assert '<svg id=\"choropleth-svg\"' in html
assert 'id=\"dashboard-data\"' in html
start = html.index('id=\"dashboard-data\">') + len('id=\"dashboard-data\">')
end = html.index('</script>', start)
payload = json.loads(html[start:end])
assert len(payload['districts']) >= 25, len(payload['districts'])
assert all(d['path'].startswith('M') for d in payload['districts'])
assert 'render_population_map' not in html
print('OK', len(payload['districts']), 'districts in payload')
"
```

Expected: prints `OK <N> districts in payload` with no assertion error.

- [ ] **Step 8: Commit**

```bash
git add scripts/14_build_html_report.py report/KP_Healthcare_Plan.html
git commit -m "feat: replace static population/gap-score maps with interactive choropleth

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Interactive district table — sort, search, tier filter

**Files:**
- Modify: `scripts/14_build_html_report.py`

**Interfaces:**
- Consumes: DOM contract from Task 2 (`#district-table`, `#district-search`, `.tier-filter-chip[data-tier]`, `th[data-sort-key]`).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Add `data-*` attributes to `district_rows_html`**

In `scripts/14_build_html_report.py`, find:

```python
def district_rows_html(metrics):
    rows = []
    for m in sorted(metrics, key=lambda r: float(r["gap_score"]), reverse=True):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{m['district']}</td>"
            f"<td class=\"num\">{int(float(m['population_2023'])):,}</td>"
            f"<td class=\"num\">{float(m['area_km2']):.0f}</td>"
            f"<td class=\"num\">{float(m['pop_density']):.1f}</td>"
            f"<td class=\"num\">{m['govt_pvt_institutions']}</td>"
            f"<td class=\"num\">{float(m['beds_per_1000']):.2f}</td>"
            f"<td class=\"num\">{float(m['doctors_per_1000']):.2f}</td>"
            f"<td>{m['terrain'].title()}</td>"
            f"<td class=\"num\">{float(m['gap_score']):.1f}</td>"
            f"<td>{tier_chip(m['need_tier'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)
```

Replace with:

```python
def district_rows_html(metrics):
    rows = []
    for m in sorted(metrics, key=lambda r: float(r["gap_score"]), reverse=True):
        attrs = (
            f'data-district="{m["district"]}" '
            f'data-population="{int(float(m["population_2023"]))}" '
            f'data-area="{float(m["area_km2"]):.1f}" '
            f'data-density="{float(m["pop_density"]):.1f}" '
            f'data-institutions="{m["govt_pvt_institutions"]}" '
            f'data-beds="{float(m["beds_per_1000"]):.2f}" '
            f'data-doctors="{float(m["doctors_per_1000"]):.2f}" '
            f'data-terrain="{m["terrain"]}" '
            f'data-gap-score="{float(m["gap_score"]):.1f}" '
            f'data-tier="{m["need_tier"]}"'
        )
        rows.append(
            f"<tr {attrs}>"
            f"<td class=\"col-name\">{m['district']}</td>"
            f"<td class=\"num\">{int(float(m['population_2023'])):,}</td>"
            f"<td class=\"num\">{float(m['area_km2']):.0f}</td>"
            f"<td class=\"num\">{float(m['pop_density']):.1f}</td>"
            f"<td class=\"num\">{m['govt_pvt_institutions']}</td>"
            f"<td class=\"num\">{float(m['beds_per_1000']):.2f}</td>"
            f"<td class=\"num\">{float(m['doctors_per_1000']):.2f}</td>"
            f"<td>{m['terrain'].title()}</td>"
            f"<td class=\"num\">{float(m['gap_score']):.1f}</td>"
            f"<td>{tier_chip(m['need_tier'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)
```

- [ ] **Step 2: Add search box, tier filter chips, table id, and sortable headers**

In `scripts/14_build_html_report.py`, find:

```
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Population (2023)</th><th>Area (km&sup2;)</th><th>Density (/km&sup2;)</th>
<th>Institutions (Dev Stats)</th><th>Beds/1,000</th><th>Doctors/1,000</th><th>Terrain</th><th>Gap Score</th><th>Need Tier</th></tr></thead>
<tbody>
{district_rows_html(metrics)}
</tbody>
</table>
</div>
</section>

<section id="findings">
```

Replace with:

```
<div class="dashboard-controls">
  <input type="search" id="district-search" placeholder="Search district..." aria-label="Search districts">
  <button type="button" class="tier-filter-chip" data-tier="Critical" style="--tier-color:#d03b3b" aria-pressed="false">Critical</button>
  <button type="button" class="tier-filter-chip" data-tier="High" style="--tier-color:#ec835a" aria-pressed="false">High</button>
  <button type="button" class="tier-filter-chip" data-tier="Moderate" style="--tier-color:#fab219" aria-pressed="false">Moderate</button>
  <button type="button" class="tier-filter-chip" data-tier="Low" style="--tier-color:#0ca30c" aria-pressed="false">Low</button>
</div>
<div class="table-wrap">
<table id="district-table" class="dashboard-table">
<thead><tr>
<th data-sort-key="district">District</th><th data-sort-key="population">Population (2023)</th>
<th data-sort-key="area">Area (km&sup2;)</th><th data-sort-key="density">Density (/km&sup2;)</th>
<th data-sort-key="institutions">Institutions (Dev Stats)</th><th data-sort-key="beds">Beds/1,000</th>
<th data-sort-key="doctors">Doctors/1,000</th><th data-sort-key="terrain">Terrain</th>
<th data-sort-key="gap-score">Gap Score</th><th data-sort-key="tier">Need Tier</th></tr></thead>
<tbody>
{district_rows_html(metrics)}
</tbody>
</table>
</div>
</section>

<section id="findings">
```

- [ ] **Step 3: Verify the report still builds**

Run: `python scripts/14_build_html_report.py`
Expected: exits 0, prints `Wrote report/KP_Healthcare_Plan.html`

- [ ] **Step 4: Verify the table markup**

Run:

```bash
python -c "
html = open('report/KP_Healthcare_Plan.html', encoding='utf-8').read()
assert 'id=\"district-table\"' in html
assert 'id=\"district-search\"' in html
assert 'data-sort-key=\"gap-score\"' in html
assert html.count('class=\"tier-filter-chip\"') == 4
assert 'data-district=\"Peshawar\"' in html
assert 'data-gap-score=' in html
print('OK')
"
```

Expected: prints `OK`

- [ ] **Step 5: Commit**

```bash
git add scripts/14_build_html_report.py report/KP_Healthcare_Plan.html
git commit -m "feat: make district table sortable/searchable/filterable

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `tests/verify_dashboard.py` — end-to-end verification

**Files:**
- Create: `tests/verify_dashboard.py`

**Interfaces:**
- Consumes: `report/KP_Healthcare_Plan.html` (built output from Tasks 3-5), `data/processed/district_metrics.csv`, `data/processed/boundaries.json`.
- Produces: nothing (terminal verification script, per the `tests/verify_*.py` convention — see `tests/verify_district_metrics.py`).

- [ ] **Step 1: Write `tests/verify_dashboard.py`**

```python
"""End-to-end check on the generated dashboard: run after
scripts/14_build_html_report.py to confirm the interactive markup and
embedded data are present, consistent with the source CSVs, and that no
TBD/blank-content regressions crept in (see the report audit in
docs/superpowers/specs/2026-08-15-interactive-dashboard-phase1-design.md,
section 2).
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "report" / "KP_Healthcare_Plan.html"
METRICS_PATH = ROOT / "data" / "processed" / "district_metrics.csv"
BOUNDARIES_PATH = ROOT / "data" / "processed" / "boundaries.json"


def load_metrics():
    with open(METRICS_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def extract_dashboard_payload(html):
    marker = 'id="dashboard-data">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def main():
    html = REPORT_PATH.read_text(encoding="utf-8")
    metrics = load_metrics()
    boundaries = json.loads(BOUNDARIES_PATH.read_text(encoding="utf-8"))

    # Interactive markup hooks are present.
    for hook in (
        'id="district-table"',
        'id="district-search"',
        'class="tier-filter-chip"',
        'id="choropleth-svg"',
        'id="choropleth-legend"',
        'id="dashboard-data"',
        'id="methodology-global"',
    ):
        assert hook in html, f"missing dashboard hook: {hook}"

    # Embedded JSON payload parses and matches the metrics CSV.
    payload = extract_dashboard_payload(html)
    payload_districts = {d["district"] for d in payload["districts"]}
    metrics_districts = {m["district"] for m in metrics}
    boundary_districts = {d["district"] for d in boundaries["districts"]}

    assert payload_districts, "dashboard payload has no districts"
    assert payload_districts <= metrics_districts, (
        f"payload has districts not in metrics CSV: {payload_districts - metrics_districts}"
    )
    missing_boundaries = metrics_districts - boundary_districts
    assert not missing_boundaries, f"metrics districts with no boundary geometry: {missing_boundaries}"
    # Every metrics district that also has a boundary must make it into the payload
    # (build_dashboard_payload only drops districts absent from the metrics CSV).
    expected_in_payload = metrics_districts & boundary_districts
    assert payload_districts == expected_in_payload, (
        f"payload/metrics mismatch: {expected_in_payload.symmetric_difference(payload_districts)}"
    )

    for d in payload["districts"]:
        assert d["path"].startswith("M") and d["path"].rstrip().endswith("Z"), f"malformed SVG path for {d['district']}"
        assert d["need_tier"] in ("Critical", "High", "Moderate", "Low"), f"bad tier for {d['district']}"

    # Table rows carry the data-* attributes the JS sorts/filters on.
    row_count = html.count("data-gap-score=")
    assert row_count == len(metrics), f"expected {len(metrics)} table rows with data-gap-score, found {row_count}"

    # No blank/placeholder regressions (mirrors the manual audit that kicked off this project).
    for bad in ("TBD", "TODO", "undefined", "{{", "}}"):
        assert bad not in html, f"found placeholder marker in report: {bad!r}"
    empty_cells = re.findall(r"<td[^>]*>\s*</td>", html)
    assert not empty_cells, f"found {len(empty_cells)} empty table cell(s)"

    print(f"OK: dashboard verified - {len(payload['districts'])} districts in payload, {row_count} table rows")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python tests/verify_dashboard.py`
Expected: prints `OK: dashboard verified - <N> districts in payload, <N> table rows` (N should match district count, e.g. 35). If any assertion fails, fix the corresponding Task 3/4/5 markup before proceeding — do not weaken this script's assertions to make it pass.

- [ ] **Step 3: Commit**

```bash
git add tests/verify_dashboard.py
git commit -m "test: add end-to-end dashboard verification script

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Full regeneration, full test suite, manual browser check

**Files:**
- Modify: `report/KP_Healthcare_Plan.html` (regenerated; should already be up to date from Task 5, this is a clean confirmation pass)

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: the final, committed phase-1 dashboard.

- [ ] **Step 1: Rebuild the report from scratch**

Run: `python scripts/14_build_html_report.py`
Expected: exits 0, prints `Wrote report/KP_Healthcare_Plan.html`

- [ ] **Step 2: Run the full pytest suite**

Run: `pytest tests/ -v`
Expected: all tests pass, including the new `tests/lib/test_dashboard_data.py` and `tests/lib/test_dashboard_assets.py`.

- [ ] **Step 3: Run every `verify_*.py` script**

Run (PowerShell):

```powershell
Get-ChildItem tests/verify_*.py | ForEach-Object { python $_.FullName }
```

Expected: every script prints its own `OK: ...` line, including `tests/verify_dashboard.py`. If any pre-existing verify script fails, stop — that's a regression this plan introduced, not a pre-existing issue (the report review at the start of this project found none).

- [ ] **Step 4: Manual browser check**

Open `report/KP_Healthcare_Plan.html` directly by double-clicking it (or `start report/KP_Healthcare_Plan.html` on Windows) — no server. Confirm:
- The choropleth renders 35 colored district shapes; clicking one highlights the matching table row and scrolls to it.
- The "Population density" toggle button recolors the map and swaps the legend.
- Typing in the search box filters the table live; clicking a tier chip filters both the table and dims non-matching map districts; clicking it again clears the filter.
- Clicking a sortable column header (e.g. "Gap Score") reorders the table; clicking again reverses the order.
- The "Methodology: International GIS Standards" section appears between "Data Sources & Methodology" and "Current State".
- The facility-distribution and DEM elevation maps still render as before (unchanged, static images).

If any of these fail, this is a real bug to fix before committing — do not report success without having actually opened the file and checked each item.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: regenerate report after phase-1 dashboard work

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

(If Step 1 produced no diff versus Task 5's last commit, skip this commit — nothing to add.)
