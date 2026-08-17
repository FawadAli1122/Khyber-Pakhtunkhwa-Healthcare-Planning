"""CSS and JS string constants for the interactive dashboard, kept as plain
(non-f) strings so scripts/14_build_html_report.py can splice them into its
report-building f-string via {DASHBOARD_CSS}/{DASHBOARD_JS} without needing
to escape every brace in this file.

DOM contract these depend on (see docs/superpowers/plans/
2026-08-15-interactive-dashboard-phase1.md, Task 2, for the source of truth):
- <script type="application/json" id="dashboard-data"> holds the JSON from
  dashboard_data.build_dashboard_payload(...).
- <svg id="choropleth-svg"> is the mount point for one
  <path data-district="..."> per district.
- <div id="choropleth-legend"> is the mount point for the legend swatches.
- .metric-toggle button[data-metric="gap_score"|"pop_density"] switch the
  choropleth's fill metric.
- #district-table is a <table> whose <tbody> rows each carry data-district,
  data-population, data-area, data-density, data-institutions, data-beds,
  data-doctors, data-terrain, data-gap-score, data-tier.
- #district-search is the free-text search <input>.
- .tier-filter-chip[data-tier="Critical"|"High"|"Moderate"|"Low"] are the
  tier filter buttons.
- th[data-sort-key="district"|"population"|"area"|"density"|"institutions"|
  "beds"|"doctors"|"terrain"|"gap-score"|"tier"] make a column header
  sortable.
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
