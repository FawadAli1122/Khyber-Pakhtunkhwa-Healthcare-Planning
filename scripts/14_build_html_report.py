"""Render report/KP_Healthcare_Plan.html: a self-contained planning report
with embedded static maps (matplotlib, plotted directly from shapely
geometries — no geopandas), data tables, transparent methodology
explanation, ranked findings, and phased recommendations. Every figure is
base64-embedded so the file has zero external dependencies.

Design: deep teal-ink ground with a burnt-ochre accent (grounded in KP's
forested north and the terracotta brickwork of old Peshawar), a serif
display face for headings against a humanist sans body, and the dataviz
skill's validated status palette (good/warning/serious/critical) reused
verbatim for the four need-tier chips.
"""
import base64
import csv
import html
import io
import json
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch, Polygon as MplPolygon
import numpy as np
import rasterio
from rasterio.enums import Resampling
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import custom_tables as custom_tables_lib
from scripts.lib.dashboard_assets import DASHBOARD_CSS, DASHBOARD_JS
from scripts.lib.dashboard_data import build_dashboard_payload
from scripts.lib.facility_readiness import compute_readiness_scores
from scripts.lib.landcover import LANDCOVER_CLASSES
from scripts.lib.supplemental_records import load_records as load_supplemental_records

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
REPORT_DIR = Path(__file__).resolve().parent.parent / "report"

TIER_COLORS = {
    "Critical": "#d03b3b",
    "High": "#ec835a",
    "Moderate": "#fab219",
    "Low": "#0ca30c",
}

# Both source registries carry much finer-grained category tags than a map
# legend can usefully show (KPHCC alone has "GP Clinic", "General
# Practitioner Clinic", "Specialist Clinic", "Dental Clinic", etc.; OSM
# contributes generic healthcare= values like "Yes", "Ngo", "Centre"). This
# collapses them to a small, map-legible taxonomy for the facility
# distribution figure only — the underlying shapefile/CSV keep the raw
# category so no information is lost, this is purely a rendering concern.
MAP_CATEGORY_GROUPS = {
    "Hospital": "Hospital",
    "Clinic": "Clinic",
    "Pharmacy": "Pharmacy",
    "Laboratory": "Laboratory",
    "GP Clinic": "Clinic",
    "General Practitioner Clinic": "Clinic",
    "General Practitioner": "Clinic",
    "Specialist Clinic": "Clinic",
    "Dental Clinic": "Clinic",
    "Dentist": "Clinic",
    "Doctor": "Clinic",
    "Doctor,_Pharmacy": "Clinic",
    "Psychotherapist": "Clinic",
    "Rehabilitation": "Clinic",
    "Optometrist": "Clinic",
    "Alternative": "Other",
    "Blood_Donation": "Other",
    "Centre": "Other",
    "Ngo": "Other",
    "Yes": "Other",
}


def map_category(raw_category):
    return MAP_CATEGORY_GROUPS.get(raw_category, "Other")


def fig_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor="none")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _plot_polygon(ax, geom, **kwargs):
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for poly in polys:
        ax.add_patch(MplPolygon(list(poly.exterior.coords), closed=True, **kwargs))


def render_facility_map(boundaries, facilities):
    fig, ax = plt.subplots(figsize=(9.4, 7.4))
    for d in boundaries["districts"]:
        _plot_polygon(ax, shape(d["geometry"]), facecolor="#eef1ef", edgecolor="#c7cdca", linewidth=0.3)
    by_cat = {}
    for f in facilities:
        by_cat.setdefault(map_category(f["category"]), []).append(f)
    colors = {"Hospital": "#d03b3b", "Clinic": "#2d6e64", "Pharmacy": "#b3651b", "Laboratory": "#5a6ea3", "Other": "#8a8f8c"}
    order = ["Hospital", "Clinic", "Pharmacy", "Laboratory", "Other"]
    order = [c for c in order if c in by_cat]
    for cat in order:
        pts = by_cat[cat]
        xs = [float(p["lon"]) for p in pts]
        ys = [float(p["lat"]) for p in pts]
        ax.scatter(xs, ys, s=7, label=cat, color=colors.get(cat, "#8a8f8c"), alpha=0.75, linewidths=0)
    ax.legend(loc="lower left", fontsize=8, frameon=False)
    ax.set_title("Known Healthcare Facility Distribution (KPHCC + OSM, deduplicated)", fontsize=12, color="#16211f")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


def render_dem_map(dem_path):
    """gis/KP_DEM.tif is ~443MB at native ~30m resolution (20671x17599 px)
    - far more detail than a report figure needs, so this reads a
    decimated overview (long side ~1400px) directly via rasterio's
    out_shape resampling rather than loading the full array."""
    fig, ax = plt.subplots(figsize=(6.2, 7.4))
    with rasterio.open(dem_path) as src:
        scale = max(src.width, src.height) / 1400
        out_height = max(1, int(src.height / scale))
        out_width = max(1, int(src.width / scale))
        data = src.read(1, out_shape=(out_height, out_width), resampling=Resampling.average)
        nodata = src.nodata
        bounds = src.bounds
    masked = np.ma.masked_equal(data, nodata) if nodata is not None else data
    im = ax.imshow(masked, cmap="terrain", extent=(bounds.left, bounds.right, bounds.bottom, bounds.top))
    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cbar.set_label("Elevation (m)", fontsize=8, color="#16211f")
    cbar.ax.tick_params(labelsize=7, labelcolor="#16211f")
    ax.set_title("Elevation (Copernicus GLO-30 DEM)", fontsize=12, color="#16211f")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


def render_landcover_map(landcover_path):
    """gis/KP_LandCover.tif is a categorical raster (ESA WorldCover class
    codes, not a continuous surface like the DEM) - reads the same kind of
    decimated overview render_dem_map() does for speed, but with
    Resampling.nearest (categorical values must never be averaged or
    interpolated into values that don't correspond to a real class) and a
    discrete class legend instead of a continuous colorbar. Uses the exact
    same 11-class ESA palette as the QGIS raster layer
    (scripts/13_build_qgis_project.py) so the same class reads as the same
    color everywhere in this project."""
    fig, ax = plt.subplots(figsize=(6.2, 7.4))
    with rasterio.open(landcover_path) as src:
        scale = max(src.width, src.height) / 1400
        out_height = max(1, int(src.height / scale))
        out_width = max(1, int(src.width / scale))
        data = src.read(1, out_shape=(out_height, out_width), resampling=Resampling.nearest)
        nodata = src.nodata
        bounds = src.bounds

    values = [value for value, _, _ in LANDCOVER_CLASSES]
    colors = [color for _, color, _ in LANDCOVER_CLASSES]
    # Lookup table mapping a raw class code -> its index into `colors`
    # (or -1 for nodata/anything unrecognized), vectorized over the whole
    # decimated array rather than looping per-pixel.
    lookup = np.full(max(values) + 1, -1, dtype=np.int16)
    for i, value in enumerate(values):
        lookup[value] = i
    safe_data = np.where((data >= 0) & (data < lookup.size), data, 0).astype(np.int64)
    indexed = lookup[safe_data]
    if nodata is not None:
        indexed = np.where(data == nodata, -1, indexed)
    masked = np.ma.masked_less(indexed, 0)

    cmap = ListedColormap(colors)
    ax.imshow(
        masked, cmap=cmap, vmin=-0.5, vmax=len(colors) - 0.5,
        extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
    )
    handles = [Patch(facecolor=color, label=label) for _, color, label in LANDCOVER_CLASSES]
    # Placed outside the axes (like render_dem_map()'s colorbar) rather than
    # inside a lower corner - an in-plot legend sat directly on top of
    # colored pixels (Built-up's red matches parts of the map itself),
    # making it unreadable. fig_to_base64()'s bbox_inches="tight" expands
    # the saved image to include this legend automatically.
    ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=8, frameon=False)
    ax.set_title("Land Cover (ESA WorldCover 2021, 10m)", fontsize=12, color="#16211f")
    ax.set_aspect("equal")
    ax.axis("off")
    return fig_to_base64(fig)


def load_data():
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    with open(PROCESSED / "district_metrics.csv", newline="", encoding="utf-8") as f:
        metrics = list(csv.DictReader(f))
    with open(PROCESSED / "facilities_merged.csv", newline="", encoding="utf-8") as f:
        facilities = list(csv.DictReader(f))
    with open(PROCESSED / "suggested_sites.csv", newline="", encoding="utf-8") as f:
        sites = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_health.csv", newline="", encoding="utf-8") as f:
        dev_health = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_roads.csv", newline="", encoding="utf-8") as f:
        dev_roads = list(csv.DictReader(f))
    with open(PROCESSED / "facility_cross_validation.csv", newline="", encoding="utf-8") as f:
        cross_val = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_patients_treated.csv", newline="", encoding="utf-8") as f:
        dev_patients = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_immunization.csv", newline="", encoding="utf-8") as f:
        dev_immunization = list(csv.DictReader(f))
    with open(PROCESSED / "dev_stats_malaria.csv", newline="", encoding="utf-8") as f:
        dev_malaria = list(csv.DictReader(f))
    dev_budget = json.loads((PROCESSED / "dev_stats_budget.json").read_text())
    supplemental_records = load_supplemental_records()
    with open(PROCESSED / "district_landcover.csv", newline="", encoding="utf-8") as f:
        district_landcover = list(csv.DictReader(f))
    with open(PROCESSED / "landcover_composition.csv", newline="", encoding="utf-8") as f:
        landcover_composition = list(csv.DictReader(f))
    return (
        boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget,
        supplemental_records, dev_patients, dev_immunization, dev_malaria,
        district_landcover, landcover_composition,
    )


def tier_chip(tier):
    color = TIER_COLORS.get(tier, "#8a8f8c")
    return f'<span class="tier-chip" style="--tier-color:{color}">{tier}</span>'


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


def findings_html(metrics):
    ranked = sorted(metrics, key=lambda r: float(r["gap_score"]), reverse=True)[:10]
    items = "\n".join(
        f"<li><strong>{m['district']}</strong> {tier_chip(m['need_tier'])} — gap score "
        f"<span class=\"num\">{float(m['gap_score']):.1f}</span>, {m['govt_pvt_institutions']} government + private "
        f"institutions (Dev Stats), {float(m['beds_per_1000']):.2f} beds and {float(m['doctors_per_1000']):.2f} doctors "
        f"per 1,000 population, {m.get('accessibility_min') or 'n/a'} min travel-time to nearest mapped facility.</li>"
        for m in ranked
    )
    return f"<ol class=\"findings-list\">{items}</ol>"


def infra_context_rows_html(dev_health, dev_roads):
    roads_by_district = {r["district"]: r for r in dev_roads}
    rows = []
    for h in sorted(dev_health, key=lambda r: r["district"]):
        rd = roads_by_district.get(h["district"], {})
        pop_per_bed = f"{int(h['pop_per_bed']):,}" if h["pop_per_bed"] != "" else "&mdash;"
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{h['district']}</td>"
            f"<td class=\"num\">{int(h['govt_institutions']):,}</td>"
            f"<td class=\"num\">{int(h['govt_beds']):,}</td>"
            f"<td class=\"num\">{int(h['pvt_hospitals']):,}</td>"
            f"<td class=\"num\">{int(h['pvt_beds']):,}</td>"
            f"<td class=\"num\">{int(h['medical_staff']):,}</td>"
            f"<td class=\"num\">{int(h['paramedical_staff']):,}</td>"
            f"<td class=\"num\">{int(h['pvt_practitioners']):,}</td>"
            f"<td class=\"num\">{float(rd.get('road_km_total', 0) or 0):,.0f}</td>"
            f"<td class=\"num\">{pop_per_bed}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def cross_val_rows_html(cross_val):
    rows = []
    for r in sorted(cross_val, key=lambda r: r["district"]):
        official = f"{int(r['govt_institutions_official']):,}" if r["govt_institutions_official"] != "" else "&mdash;"
        diff = f"{int(r['difference']):+,}" if r["difference"] != "" else "&mdash;"
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['merged_facility_count']):,}</td>"
            f"<td class=\"num\">{official}</td>"
            f"<td class=\"num\">{diff}</td>"
            f"<td>{r['note']}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def patients_treated_rows_html(dev_patients):
    rows = []
    for r in sorted(dev_patients, key=lambda r: r["district"]):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['patients_total_2024']):,}</td>"
            f"<td class=\"num\">{int(r['patients_indoor_2024']):,}</td>"
            f"<td class=\"num\">{int(r['patients_outdoor_2024']):,}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def immunization_rows_html(dev_immunization):
    rows = []
    for r in sorted(dev_immunization, key=lambda r: r["district"]):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['bcg']):,}</td>"
            f"<td class=\"num\">{int(r['opv0']):,}</td>"
            f"<td class=\"num\">{int(r['opv_dpt1']):,}</td>"
            f"<td class=\"num\">{int(r['opv_dpt2']):,}</td>"
            f"<td class=\"num\">{int(r['opv_dpt3']):,}</td>"
            f"<td class=\"num\">{int(r['measles']):,}</td>"
            f"<td class=\"num\">{int(r['tt1']):,}</td>"
            f"<td class=\"num\">{int(r['tt2']):,}</td>"
            f"<td class=\"num\">{int(r['tt3']):,}</td>"
            f"<td class=\"num\">{int(r['tt4']):,}</td>"
            f"<td class=\"num\">{int(r['tt5']):,}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def malaria_rows_html(dev_malaria):
    rows = []
    for r in sorted(dev_malaria, key=lambda r: r["district"]):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td class=\"num\">{int(r['blood_slides_examined']):,}</td>"
            f"<td class=\"num\">{int(r['malaria_cases']):,}</td>"
            f"<td class=\"num\">{int(r['malaria_cases_treated']):,}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def terrain_rows_html(metrics):
    rows = []
    for m in sorted(metrics, key=lambda r: float(r["mean_elev_m"]), reverse=True):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{m['district']}</td>"
            f"<td class=\"num\">{float(m['mean_elev_m']):,.0f}</td>"
            f"<td class=\"num\">{float(m['mean_slope_deg']):.1f}</td>"
            f"<td class=\"num\">{float(m['terrain_difficulty']):.3f}</td>"
            f"<td>{m['terrain'].title()}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def landcover_composition_rows_html(landcover_composition):
    rows = []
    for r in sorted(landcover_composition, key=lambda r: float(r["pct_area"]), reverse=True):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['label']}</td>"
            f"<td class=\"num\">{float(r['area_km2']):,.0f}</td>"
            f"<td class=\"num\">{float(r['pct_area']):.1f}%</td>"
            "</tr>"
        )
    return "\n".join(rows)


def district_landcover_rows_html(district_landcover):
    # Sorted by least Built-up first - the districts furthest from where
    # people already cluster are the ones this table is most useful for
    # surfacing to a planner.
    rows = []
    for r in sorted(district_landcover, key=lambda r: float(r["built_up_pct"]) if r["built_up_pct"] != "" else 0.0):
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{r['district']}</td>"
            f"<td>{r['dominant_class'] or 'n/a'}</td>"
            f"<td class=\"num\">{float(r['built_up_pct'] or 0):.1f}%</td>"
            f"<td class=\"num\">{float(r['tree_cover_pct'] or 0):.1f}%</td>"
            f"<td class=\"num\">{float(r['cropland_pct'] or 0):.1f}%</td>"
            f"<td class=\"num\">{float(r['bare_sparse_vegetation_pct'] or 0):.1f}%</td>"
            f"<td class=\"num\">{float(r['snow_and_ice_pct'] or 0):.1f}%</td>"
            "</tr>"
        )
    return "\n".join(rows)


def horizon_table_html(metrics, suffix, year):
    ranked = sorted(metrics, key=lambda r: float(r["gap_score"]), reverse=True)[:10]
    rows = "\n".join(
        "<tr>"
        f"<td class=\"col-name\">{m['district']}</td>"
        f"<td class=\"num\">{int(m[f'pop_{year}']):,}</td>"
        f"<td class=\"num\">{int(m[f'fac_nd{suffix}'])}</td>"
        f"<td class=\"num\">{int(m[f'beds_nd{suffix}']):,}</td>"
        "</tr>"
        for m in ranked
    )
    return f"""<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Projected Population ({year})</th><th>Add'l Facilities Needed</th><th>Add'l Beds Needed</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>"""


def supplemental_data_rows_html(records):
    if not records:
        return '<tr><td colspan="6">No additional information has been added yet.</td></tr>'
    rows = []
    for r in sorted(records, key=lambda r: (r["district"], r.get("facility", ""), r["category"])):
        facility = html.escape(r["facility"]) if r.get("facility") else "&mdash;"
        rows.append(
            "<tr>"
            f"<td class=\"col-name\">{html.escape(r['district'])}</td>"
            f"<td>{facility}</td>"
            f"<td>{html.escape(r['category'])}</td>"
            f"<td>{html.escape(r['label'])}</td>"
            f"<td>{html.escape(r.get('detail') or '')}</td>"
            f"<td>{html.escape(r.get('source_document') or '')}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def readiness_section_html(readiness):
    """readiness: scripts.lib.facility_readiness.compute_readiness_scores()'s
    return value. Two states, matching the design spec: a plain
    explanatory paragraph when nothing has been assessed yet (the real
    starting state), or a per-facility table plus a district-summary
    table once data exists. No <table> at all in the empty state -
    deliberately not an empty, confusing table."""
    facilities = readiness["facilities"]
    if not facilities:
        return (
            "<p>The WHO Service Availability and Readiness Assessment (SARA) framework is in place, but no "
            "facility readiness documents have been uploaded yet via the admin panel's document upload feature. "
            "Once uploaded, facility-level readiness scores across Basic Amenities, Basic Equipment, Standard "
            "Precautions for Infection Prevention, Diagnostic Capacity, and Essential Medicines will appear "
            "here.</p>"
        )

    facility_rows = []
    for f in sorted(facilities, key=lambda f: (f["district"], f["facility"])):
        domain_cells = "; ".join(
            f"{html.escape(d)}: {v * 100:.0f}%" for d, v in sorted(f["domain_scores"].items())
        )
        overall = f"{f['overall_score'] * 100:.0f}%" if f["overall_score"] is not None else "&mdash;"
        name = html.escape(f["facility"]) if f["facility"] else "&mdash;"
        facility_rows.append(
            "<tr>"
            f"<td class=\"col-name\">{name}</td>"
            f"<td>{html.escape(f['district'])}</td>"
            f"<td>{domain_cells}</td>"
            f"<td class=\"num\">{overall}</td>"
            "</tr>"
        )

    district_rows = []
    for d in sorted(readiness["districts"], key=lambda d: d["district"]):
        district_rows.append(
            "<tr>"
            f"<td class=\"col-name\">{html.escape(d['district'])}</td>"
            f"<td class=\"num\">{d['mean_score'] * 100:.0f}%</td>"
            f"<td class=\"num\">{d['facilities_assessed']}</td>"
            "</tr>"
        )

    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Facility</th><th>District</th><th>Domain Scores</th><th>Overall</th></tr></thead>"
        f"<tbody>{''.join(facility_rows)}</tbody>"
        "</table></div>"
        "<h3>District Summary</h3>"
        '<div class="table-wrap"><table>'
        "<thead><tr><th>District</th><th>Mean Readiness</th><th>Facilities Assessed</th></tr></thead>"
        f"<tbody>{''.join(district_rows)}</tbody>"
        "</table></div>"
    )


LANDCOVER_SHIFT_THRESHOLD_KM = 5.0


def landcover_shift_callout_html(metrics):
    shifted = [
        m for m in metrics
        if float(m.get("centroid_shift_km") or 0) > LANDCOVER_SHIFT_THRESHOLD_KM
    ]
    if not shifted:
        return ""
    shifted.sort(key=lambda m: float(m["centroid_shift_km"]), reverse=True)
    items = ", ".join(f"{m['district']} ({float(m['centroid_shift_km']):.1f} km)" for m in shifted)
    return (
        f"<p>In {len(shifted)} district(s), the built-up-weighted routing point used for accessibility "
        f"measurably differs from the district's plain geometric center - population is clustered away "
        f"from the shape's middle, not evenly distributed: {items}.</p>"
    )


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
  travel time and weighting by facility capacity and demand competition. This report's <code>accessibility_min</code>
  metric is a network- and terrain-adjusted travel-time estimate to the nearest mapped facility (shortest path over
  the OSM road network, road-class speeds derated by DEM-derived terrain difficulty, plus an assumed off-road pace
  for the last mile between a point and the mapped network) &mdash; it does not weight by facility capacity or
  account for competing demand from neighboring districts the way full 2SFCA does. Adopting that capacity-weighting
  is the natural next refinement.</li>
  <li><strong>WHO AccessMod:</strong> WHO's own GIS extension for modeling travel-time accessibility to health
  facilities from a DEM, road network, and land cover. This pipeline uses the same two spatial inputs AccessMod
  needs &mdash; the Copernicus GLO-30 DEM (<code>gis/KP_DEM.tif</code>) and OpenStreetMap roads
  (<code>data/raw/osm_roads.json</code>) &mdash; for its own routing (DEM-derived terrain difficulty derates road
  speed; roads form the routable network), not just for the terrain-difficulty score and road-length figures in
  Official Infrastructure Context. Accessibility here is a single shortest-path travel time per district, not the
  full raster travel-time surface AccessMod would produce, and it doesn't model traffic, road condition, or
  seasonal variation.</li>
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


def _insert_custom_sections(html_text, tables):
    new_sections = []
    by_anchor = {}
    for table in tables:
        section_html = custom_tables_lib.render_section_html(table)
        placement = table.get("report_placement") or "new_section"
        if placement.startswith("after:"):
            by_anchor.setdefault(placement[len("after:"):], []).append(section_html)
        else:
            new_sections.append(section_html)

    for anchor, sections in by_anchor.items():
        marker = f'<section id="{anchor}">'
        idx = html_text.find(marker)
        if idx == -1:
            new_sections.extend(sections)
            continue
        close_idx = html_text.find("</section>", idx)
        insertion_point = close_idx + len("</section>")
        html_text = html_text[:insertion_point] + "\n" + "\n".join(sections) + html_text[insertion_point:]

    if new_sections:
        html_text = html_text.replace("<footer>", "\n".join(new_sections) + "\n<footer>", 1)
    return html_text


def build(source_boundary, source_population_note):
    (
        boundaries, metrics, facilities, sites, dev_health, dev_roads, cross_val, dev_budget,
        supplemental_records, dev_patients, dev_immunization, dev_malaria,
        district_landcover, landcover_composition,
    ) = load_data()
    metrics_by_district = {m["district"]: m for m in metrics}
    readiness = compute_readiness_scores(supplemental_records)

    fac_map_b64 = render_facility_map(boundaries, facilities)
    dem_map_b64 = render_dem_map(GIS_DIR / "KP_DEM.tif")
    landcover_map_b64 = render_landcover_map(GIS_DIR / "KP_LandCover.tif")
    dashboard_payload = build_dashboard_payload(boundaries, metrics)

    total_pop = sum(int(m["population_2023"]) for m in metrics)
    total_facilities = len(facilities)
    critical_districts = [m["district"] for m in metrics if m["need_tier"] == "Critical"]
    tier_counts = {t: sum(1 for m in metrics if m["need_tier"] == t) for t in ("Critical", "High", "Moderate", "Low")}

    fy_now = sorted(dev_budget.keys())[-1]  # most recent fiscal year key, e.g. "fy2025_26"
    fy_prev = sorted(dev_budget.keys())[0]
    horizon_years = sorted(int(k.split("_")[1]) for k in metrics[0].keys() if k.startswith("pop_20"))
    y3, y5, y20 = horizon_years[0], horizon_years[1], horizon_years[2]
    top20_by_fac = sorted(metrics, key=lambda r: int(r[f"fac_nd{str(y20)[-2:]}"]), reverse=True)[:3]
    top20_by_beds = sorted(metrics, key=lambda r: int(r[f"beds_nd{str(y20)[-2:]}"]), reverse=True)[:3]
    highest_elev_districts = sorted(metrics, key=lambda r: float(r["mean_elev_m"]), reverse=True)[:5]

    # Province-wide workforce/bed aggregates for the comprehensive plan
    # section, computed directly from Dev Stats rather than averaging the
    # per-district rates (avoids weighting small and large districts
    # equally) - and checked against each district's own combined
    # doctor+paramedical density against WHO's two most-cited skilled
    # health-worker thresholds (see references in the plan text below).
    total_medical_staff = sum(int(h["medical_staff"]) for h in dev_health)
    total_paramedical_staff = sum(int(h["paramedical_staff"]) for h in dev_health)
    total_govt_pvt_beds = sum(int(h["govt_beds"]) + int(h["pvt_beds"]) for h in dev_health)
    province_doctors_per_1000 = round(total_medical_staff / total_pop * 1000, 3)
    province_skilled_hw_per_1000 = round((total_medical_staff + total_paramedical_staff) / total_pop * 1000, 3)
    province_beds_per_1000 = round(total_govt_pvt_beds / total_pop * 1000, 2)
    districts_below_who_2006_min = sum(
        1 for h in dev_health
        if int(metrics_by_district[h["district"]]["population_2023"]) > 0
        and (int(h["medical_staff"]) + int(h["paramedical_staff"]))
            / int(metrics_by_district[h["district"]]["population_2023"]) * 1000 < 2.3
    )
    worst_hr_districts = sorted(metrics, key=lambda r: float(r["doctors_per_1000"]))[:5]
    worst_bed_districts = sorted(metrics, key=lambda r: float(r["beds_per_1000"]))[:5]

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Khyber Pakhtunkhwa Healthcare System Planning Report</title>
<style>
  :root {{
    color-scheme: light;
    --ink: #16211f;
    --ink-soft: #48534f;
    --muted: #7c8580;
    --paper: #f3f6f4;
    --panel: #ffffff;
    --line: rgba(22,33,31,0.13);
    --accent: #a85a17;
    --accent-ink: #6e3b0e;
    --accent-2: #2d6e64;
    --shadow: 0 1px 2px rgba(22,33,31,0.06), 0 8px 24px rgba(22,33,31,0.05);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --ink: #eaeeec;
      --ink-soft: #b7c0bb;
      --muted: #8a9490;
      --paper: #111815;
      --panel: #182420;
      --line: rgba(234,238,236,0.14);
      --accent: #dd9247;
      --accent-ink: #f2c692;
      --accent-2: #62b0a2;
      --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --ink: #eaeeec;
    --ink-soft: #b7c0bb;
    --muted: #8a9490;
    --paper: #111815;
    --panel: #182420;
    --line: rgba(234,238,236,0.14);
    --accent: #dd9247;
    --accent-ink: #f2c692;
    --accent-2: #62b0a2;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    line-height: 1.6;
    font-size: 16px;
  }}
  h1, h2, h3, h4 {{
    font-family: Georgia, "Iowan Old Style", "Palatino Linotype", "Noto Serif", serif;
    color: var(--ink);
    text-wrap: balance;
    font-weight: 600;
    letter-spacing: -0.01em;
  }}
  .num {{ font-variant-numeric: tabular-nums; }}

  header.masthead {{
    background: var(--ink);
    color: var(--paper);
    padding: 3rem 1.75rem 2.5rem;
    border-bottom: 4px solid var(--accent);
  }}
  header.masthead .eyebrow {{
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-size: 0.72rem;
    color: var(--accent);
    font-weight: 700;
  }}
  header.masthead h1 {{
    color: #fdfaf5;
    font-size: clamp(1.7rem, 4vw, 2.5rem);
    margin: 0.5rem 0 0.6rem;
  }}
  header.masthead p.dek {{
    max-width: 62ch;
    color: #cfd8d4;
    font-size: 1.02rem;
    margin: 0;
  }}

  main {{
    max-width: 920px;
    margin: 0 auto;
    padding: 0 1.75rem 4rem;
  }}

  .stat-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1px;
    background: var(--line);
    border: 1px solid var(--line);
    border-radius: 10px;
    overflow: hidden;
    margin-top: -1.75rem;
    box-shadow: var(--shadow);
  }}
  .stat-tile {{
    background: var(--panel);
    padding: 1.1rem 1.25rem;
  }}
  .stat-tile .label {{
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.68rem;
    color: var(--muted);
    font-weight: 600;
  }}
  .stat-tile .value {{
    font-family: Georgia, "Iowan Old Style", serif;
    font-size: 1.7rem;
    color: var(--accent-ink);
    margin-top: 0.15rem;
  }}

  section {{ margin-top: 3rem; }}
  section > h2 {{
    font-size: 1.4rem;
    border-bottom: 1px solid var(--line);
    padding-bottom: 0.5rem;
    margin-bottom: 1.1rem;
  }}
  section > h3 {{ font-size: 1.08rem; margin: 1.6rem 0 0.6rem; }}
  section > h4 {{
    font-size: 0.86rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--accent-ink);
    margin: 1.3rem 0 0.5rem;
  }}
  p {{ color: var(--ink-soft); max-width: 72ch; }}
  ul, ol {{ color: var(--ink-soft); max-width: 72ch; padding-left: 1.3rem; }}
  li {{ margin: 0.35rem 0; }}
  li strong {{ color: var(--ink); }}

  .disclaimer {{
    background: var(--panel);
    border-left: 4px solid var(--accent);
    border-radius: 0 8px 8px 0;
    padding: 1.1rem 1.3rem;
    box-shadow: var(--shadow);
  }}
  .disclaimer strong {{ color: var(--accent-ink); }}
  .disclaimer p {{ margin: 0.4rem 0 0; max-width: none; }}

  .table-wrap {{
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 10px;
    box-shadow: var(--shadow);
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    min-width: 720px;
    background: var(--panel);
    font-size: 0.86rem;
  }}
  th {{
    text-align: left;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 0.68rem;
    color: var(--muted);
    font-weight: 700;
    padding: 0.65rem 0.8rem;
    border-bottom: 1px solid var(--line);
    position: sticky;
    top: 0;
    background: var(--panel);
  }}
  td {{
    padding: 0.55rem 0.8rem;
    border-bottom: 1px solid var(--line);
    color: var(--ink-soft);
  }}
  td.col-name {{ color: var(--ink); font-weight: 600; }}
  tr:last-child td {{ border-bottom: none; }}

  .tier-chip {{
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    color: #fff;
    background: var(--tier-color);
  }}

  .figure-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.25rem;
  }}
  @media (max-width: 640px) {{ .figure-grid {{ grid-template-columns: 1fr; }} }}
  figure {{ margin: 0; }}
  figure img {{
    max-width: 100%;
    height: auto;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--panel);
    box-shadow: var(--shadow);
  }}
  figcaption {{
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 0.4rem;
  }}

  footer {{
    margin-top: 4rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--line);
    font-size: 0.82rem;
    color: var(--muted);
  }}
  footer code {{ background: var(--panel); padding: 0.1rem 0.35rem; border-radius: 4px; }}
{DASHBOARD_CSS}
</style>
</head>
<body>
<header class="masthead">
  <div class="eyebrow">AI-Assisted Planning Aid &middot; Open &amp; Official Data</div>
  <h1>Khyber Pakhtunkhwa Healthcare System Planning Report</h1>
  <p class="dek">A district-by-district assessment of healthcare access and 3/5/20-year planning horizons, built from the 2023 Digital Census, the KP Health Care Commission's licensed-facility registry, OpenStreetMap, KP Bureau of Statistics' Development Statistics 2025, and the Copernicus GLO-30 DEM — generated {date.today().strftime('%d %B %Y')}.</p>
</header>

<main>
<div class="stat-row">
  <div class="stat-tile"><div class="label">Total Population</div><div class="value num">{total_pop:,}</div></div>
  <div class="stat-tile"><div class="label">Districts Analyzed</div><div class="value num">{len(metrics)}</div></div>
  <div class="stat-tile"><div class="label">Known Facilities</div><div class="value num">{total_facilities:,}</div></div>
  <div class="stat-tile"><div class="label">Critical-Tier Districts</div><div class="value num">{len(critical_districts)}</div></div>
</div>

<section id="disclaimer">
<div class="disclaimer">
<strong>Limitations &amp; disclaimer.</strong>
<p>This report is a planning aid assembled from open/official data (PBS 2023 Digital Census, KP Health Care
Commission licensed-facility registry, OpenStreetMap, KP Bureau of Statistics' <em>Development Statistics of
Khyber Pakhtunkhwa 2025</em> — its latest published edition — and the Copernicus GLO-30 DEM from ESA/Sinergise
via the AWS Open Data Registry) and simplified analytical models. It is <strong>not</strong>
an official Government of Khyber Pakhtunkhwa policy document. Facility coordinates vary in precision — exact
geocode, street-level, or district-centroid fallback (see the facilities layer's source/precision fields).
Accessibility is measured as network- and terrain-adjusted travel time to the nearest known facility (shortest
path over the mapped OSM road network, road-class speeds derated by DEM-derived terrain difficulty, plus an
assumed off-road pace for the last mile between a point and the mapped network) &mdash; it does not model
traffic, road condition, or seasonal variation. The KPHCC registry currently has no
licensed-facility entries for several newly-merged tribal districts; that reflects a genuine gap in the
licensing rollout, not a data-processing error. The population-per-facility norm (1 basic facility per ~30,000
population) and the beds-per-1,000-population norm used in the horizon projections below are simplified
planning heuristics chosen for this report, not official Ministry of Health capacity-planning standards.</p>
</div>
</section>

<section id="summary">
<h2>Executive Summary</h2>
<p>Khyber Pakhtunkhwa's 2023 Digital Census population of {total_pop:,} is distributed across {len(metrics)}
districts with sharply uneven healthcare infrastructure. Of these, <strong>{tier_counts['Critical']}</strong> districts
fall in the Critical need tier, <strong>{tier_counts['High']}</strong> in High, <strong>{tier_counts['Moderate']}</strong>
in Moderate, and <strong>{tier_counts['Low']}</strong> in Low — driven by a composite of population density,
facility density, travel-time accessibility, and mountainous terrain.</p>
<p>Districts at Critical need tier: {", ".join(critical_districts) if critical_districts else "none"}.</p>
</section>

<section id="sources">
<h2>Data Sources &amp; Methodology</h2>
<ul>
  <li><strong>Administrative boundaries:</strong> {source_boundary}</li>
  <li><strong>Population:</strong> Pakistan Bureau of Statistics 2023 Digital Census, district-wise KP figures, pulled
  directly from the official census dashboard API. {source_population_note}</li>
  <li><strong>Healthcare facilities:</strong> KP Health Care Commission Licensed Health Care Establishment registry
  (hcc.kp.gov.pk/licensed-hces), supplemented with OpenStreetMap (Overpass API) points where the KPHCC registry has
  no coverage.</li>
  <li><strong>Roads:</strong> OpenStreetMap major road network (motorway/trunk/primary/secondary), clipped to the
  province boundary.</li>
  <li><strong>Geocoding:</strong> OSM Nominatim, with district-centroid fallback where an address could not be
  resolved precisely.</li>
</ul>

<h3>AI/ML Methodology, in plain language</h3>
<ul>
  <li><strong>Gap score (0&ndash;100):</strong> a weighted composite of six factors, each normalized to a common
  0&ndash;1 scale before weighting, then rescaled to 0&ndash;100 across KP's districts: population density (25%),
  inverse institution density (15%), distance to nearest mapped facility (20%), a mountainous-terrain penalty (10%),
  inverse beds per 1,000 population (15%), and inverse doctors per 1,000 population (15%). <strong>Development
  Statistics of Khyber Pakhtunkhwa 2025</strong> &mdash; the official KP Bureau of Statistics publication &mdash;
  is the primary source for the institution, bed, and doctor terms (55% of the total weight combined), used in
  preference to this project's own KPHCC/OSM/Marham facility mapping wherever the two overlap. The one input Dev Stats
  cannot supply is distance, since it publishes district totals rather than site coordinates &mdash; accessibility
  therefore still draws on the mapped registry, and the facility-density term specifically counts Dev Stats'
  government institutions (all 8 types) plus private hospitals, not mapped locations. Higher = more underserved.
  Because the institution-density term is <em>per capita</em>, a dense city with a moderate absolute institution
  count (Peshawar, for example) can still score high on crowding pressure even though it has more institutions in
  absolute terms than remote districts. Bed and doctor capacity are kept as separate weighted terms from
  institution density rather than folded together, since a district can have many small facilities but few beds
  (or vice versa) &mdash; these are
  distinct dimensions of access, not restatements of the same thing.</li>
  <li><strong>Accessibility routing point:</strong> <code>accessibility_min</code> is routed from each
  district's Built-up-land-cover-weighted point (the mean location of ESA WorldCover 2021 Built-up pixels
  within the district, <code>gis/KP_LandCover.tif</code>) rather than the district polygon's plain geometric
  centroid, so travel time is measured from where people actually live, not the shape's mathematical middle.
  A district with no mapped Built-up pixels keeps the plain geometric centroid unchanged.</li>
  <li><strong>Terrain difficulty:</strong> a continuous 0&ndash;1 score blending each district's mean elevation and
  mean slope (both derived from the Copernicus GLO-30 DEM, min-max normalized across KP's 35 districts), replacing
  an earlier hand-classified mountainous/plains flag; districts scoring above 0.5 are labeled "mountainous" for
  readability, but the gap score itself uses the continuous value.</li>
  <li><strong>Need-tier clustering:</strong> unsupervised K-Means grouping of districts by gap score into
  Critical / High / Moderate / Low tiers.</li>
  <li><strong>Demand forecast:</strong> exponential population projection to {y3}/{y5}/{y20} — 3, 5, and 20 years
  from this report's generation date — using each district's own census-derived growth rate (PBS's own annual
  growth rate per district, falling back to the KP provincial average where unavailable), compared against a
  simplified facility-per-population norm (facilities) and a 1.0-beds-per-1,000-population norm (beds) to estimate
  additional capacity needed at each horizon.</li>
  <li><strong>New-site suggestion:</strong> population-weighted K-Means clustering of OSM settlement points within
  the ten highest-gap-score districts, ranked by distance from the nearest existing facility — an approximate
  maximum-coverage heuristic, not a full location-optimization solve.</li>
</ul>
</section>

{methodology_html()}{landcover_shift_callout_html(metrics)}<section id="current-state">
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

<section id="infrastructure-context">
<h2>Official Infrastructure Context (Development Statistics 2025)</h2>
<p>The KP Bureau of Statistics publishes <em>Development Statistics of Khyber Pakhtunkhwa</em> annually;
<strong>2025 is its latest published edition</strong> (no edition after 2025 exists at the time of writing), and
its health-sector tables give official government institution counts, private-hospital bed-capacity brackets,
staffing, and district road lengths independent of this report's own KPHCC/OSM/Marham facility mapping. Figures below
are each district's latest available year in that edition (2024 for government institutions/beds/staffing,
2023-24 for private practitioners and road lengths). Private-sector bed counts are not published as an exact
figure anywhere in the source — <strong>pvt_beds is estimated</strong> from Table 114's bed-size brackets
(Upto 15 / 16-29 / 30-49 / 50-99 / 100-299 / Above 300) using conservative bracket midpoints, not an official
count.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Govt. Institutions</th><th>Govt. Beds</th><th>Pvt. Hospitals</th>
<th>Pvt. Beds (est.)</th><th>Medical Staff</th><th>Paramedical Staff</th><th>Pvt. Practitioners</th>
<th>Road Length (km)</th><th>Pop. per Govt. Hospital Bed</th></tr></thead>
<tbody>
{infra_context_rows_html(dev_health, dev_roads)}
</tbody>
</table>
</div>

<h3>Health Sector ADP Budget</h3>
<p>The provincial Annual Development Programme (ADP) allocated <strong>Rs. {dev_budget[fy_prev]['total']:,.0f}
million</strong> to the Health sector in {fy_prev.replace('fy', '').replace('_', '-')}
({dev_budget[fy_prev]['share_pct']}% of the province's total ADP of
Rs. {dev_budget[fy_prev]['provincial_total']:,.0f} million), rising to <strong>Rs.
{dev_budget[fy_now]['total']:,.0f} million</strong> in {fy_now.replace('fy', '').replace('_', '-')}
({dev_budget[fy_now]['share_pct']}% of Rs. {dev_budget[fy_now]['provincial_total']:,.0f} million total) — a
Health sector share holding steady at roughly 9&ndash;10% of the province's total development budget across both
years.</p>

<h3>Facility Count Cross-Validation</h3>
<p>This report's own merged KPHCC+OSM+Marham facility count and Development Statistics' official government institution
count measure <strong>different things</strong> and are not expected to match: the merged count includes private
clinics and pharmacies visible to KPHCC/OSM/Marham that Dev Stats' government-only tally excludes, while Dev Stats counts
many small government BHUs and dispensaries that are not individually registered or geo-located in KPHCC/OSM/Marham. The
table below is a transparency check, not a reconciliation — large gaps in either direction are noted as a
data-quality finding in their own right, most visible in districts with many small rural government facilities
(North Waziristan, Bannu, Mansehra) versus districts where the merged count runs higher on private-sector density
(Peshawar, Haripur, Abbottabad, Dera Ismail Khan).</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Merged Facility Count</th><th>Dev Stats Govt. Institutions</th>
<th>Difference</th><th>Note</th></tr></thead>
<tbody>
{cross_val_rows_html(cross_val)}
</tbody>
</table>
</div>

<h3>Health Service Utilization (Development Statistics 2025)</h3>
<p>District-wise patients treated in 2024 (Table 120 of Development Statistics 2025), split into indoor (admitted)
and outdoor (outpatient) visits. A district with a high outdoor share relative to indoor may indicate reliance on
outpatient-only care rather than real admission capacity - context this report's own facility/bed counts don't
otherwise capture.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Total Patients (2024)</th><th>Indoor</th><th>Outdoor</th></tr></thead>
<tbody>
{patients_treated_rows_html(dev_patients)}
</tbody>
</table>
</div>

<h3>Immunization Coverage (Development Statistics 2025)</h3>
<p>District-wise counts of children immunized by dose, 2023-24 (Table 123 of Development Statistics 2025). These are
<strong>raw counts, not coverage percentages</strong> - the source table publishes no per-district child-population
denominator to compute a rate against, so none is estimated here. Comparing a district's later-round doses (e.g.
OPV round 3, or T.T-3 through T.T-5) against its birth-dose BCG count is still informative as a rough dropout signal,
even without a formal coverage rate.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>BCG</th><th>OPV-0</th><th>OPV/DPT-1</th><th>OPV/DPT-2</th><th>OPV/DPT-3</th>
<th>Measles</th><th>T.T-1</th><th>T.T-2</th><th>T.T-3</th><th>T.T-4</th><th>T.T-5</th></tr></thead>
<tbody>
{immunization_rows_html(dev_immunization)}
</tbody>
</table>
</div>

<h3>Malaria Control Activities (Development Statistics 2025)</h3>
<p>District-wise malaria surveillance and treatment, 2024 (Table 124 of Development Statistics 2025) - blood slides
examined (testing volume), confirmed malaria cases, and cases treated.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Blood Slides Examined</th><th>Malaria Cases</th><th>Cases Treated</th></tr></thead>
<tbody>
{malaria_rows_html(dev_malaria)}
</tbody>
</table>
</div>
</section>

<section id="terrain-elevation">
<h2>Terrain &amp; Elevation</h2>
<p>Elevation and slope, computed from the Copernicus GLO-30 DEM (30m resolution, clipped to KP's province
boundary), replace an earlier hand-classified mountainous/plains flag with district-level statistics computed
directly from the terrain itself. Each district's mean elevation and mean slope are independently min-max scaled
across all 35 districts, then averaged into a single 0&ndash;1 <strong>terrain difficulty</strong> score used as a
gap-score input; districts above 0.5 are labeled "mountainous" for the table below, but the score itself is
continuous, so a district doesn't need to cross that line to have its terrain meaningfully affect its gap score.
KP's elevation spans from the Peshawar valley floor near sea-level-adjacent lowland up to over 7,600m in Upper
Chitral's high peaks — among the steepest province-wide elevation ranges anywhere the pipeline's underlying
Copernicus data has been used.</p>
<figure>
  <img src="data:image/png;base64,{dem_map_b64}" alt="Elevation map">
  <figcaption>Elevation across Khyber Pakhtunkhwa (Copernicus GLO-30 DEM).</figcaption>
</figure>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Mean Elevation (m)</th><th>Mean Slope (&deg;)</th><th>Terrain Difficulty</th>
<th>Terrain</th></tr></thead>
<tbody>
{terrain_rows_html(metrics)}
</tbody>
</table>
</div>
</section>

<section id="land-cover">
<h2>Land Use &amp; Land Cover</h2>
<p>Land cover across KP, from ESA WorldCover 2021 v200 (10m resolution, clipped to the province boundary,
<code>gis/KP_LandCover.tif</code>) — the same layer used to compute each district's Built-up-weighted accessibility
routing point (see the Methodology section above). Only about {next((f"{float(r['pct_area']):.1f}" for r in landcover_composition if r['label'] == 'Built-up'), '2.0')}% of KP's land area is classified
Built-up; the rest is overwhelmingly Bare/sparse vegetation, Grassland, Tree cover, Shrubland, and Cropland, with
Snow and ice concentrated in the same high-elevation northern districts (Chitral, Kohistan) the terrain-difficulty
score above already flags as hardest to serve — a second, independent data source pointing at the same districts.
For healthcare planning, the district table below is sorted by Built-up share ascending: the districts at the top
have the least built-up land relative to their area, meaning population and (by extension) demand for care is
more likely to be dispersed across the district rather than concentrated near a single town center.</p>
<figure>
  <img src="data:image/png;base64,{landcover_map_b64}" alt="Land cover map">
  <figcaption>Land cover across Khyber Pakhtunkhwa (ESA WorldCover 2021 v200).</figcaption>
</figure>
<h3>Province-wide composition</h3>
<div class="table-wrap">
<table>
<thead><tr><th>Land Cover Class</th><th>Area (km&sup2;)</th><th>% of KP</th></tr></thead>
<tbody>
{landcover_composition_rows_html(landcover_composition)}
</tbody>
</table>
</div>
<h3>By district</h3>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Dominant Land Cover</th><th>Built-up %</th><th>Tree Cover %</th><th>Cropland %</th>
<th>Bare/Sparse %</th><th>Snow/Ice %</th></tr></thead>
<tbody>
{district_landcover_rows_html(district_landcover)}
</tbody>
</table>
</div>
</section>

<section id="district-data">
<h2>District Data</h2>
<p>The Institutions, Beds/1,000, and Doctors/1,000 columns below &mdash; and the gap score and need tier they help
compute &mdash; are sourced from <strong>Development Statistics of Khyber Pakhtunkhwa 2025</strong>, the KP Bureau
of Statistics' official publication, used throughout this analysis in preference to this project's own KPHCC/OSM/Marham
facility mapping. "Institutions" is Dev Stats' own count of government health institutions (all 8 types &mdash;
Hospitals, Dispensaries, RHCs, TB Clinics, MCH Centres, Health Sub Centres, BHUs, Leprosy Clinics) plus registered
private hospitals. The one figure Dev Stats cannot supply is location: it publishes district totals only, never
site coordinates, so the mapped KPHCC/OSM/Marham registry remains the source for travel-time accessibility and the
facility-distribution map above &mdash; see Official Infrastructure Context and Facility Count Cross-Validation
for how the two compare.</p>
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
<h2>Findings: Most Underserved Districts</h2>
{findings_html(metrics)}
</section>

<section id="future-planning">
<h2>Comprehensive Healthcare System Plan</h2>
<p>What follows is a full system plan, not a single roadmap: six pillars — human resources, bed capacity and
physical infrastructure, priority interventions, procurement and supply chain, governance and financing, and
cross-cutting infrastructure policy — each carried through three explicit horizons: {y3} (short term, 3 years
out), {y5} (medium term, 5 years out), and {y20} (long term, 20 years out), computed from this report's
generation date. Every pillar is grounded first in this project's own KP data (district-level gap scores,
DEM terrain, Development Statistics 2025), then checked against the external policy frameworks and benchmarks
below — collected specifically for this plan rather than assumed.</p>

<h3>Policy Framework &amp; External Benchmarks</h3>
<p>KP already has an active, funded reform program this plan builds on rather than duplicates: the Asian
Development Bank's <strong>Khyber Pakhtunkhwa Health Systems Strengthening Program</strong> is a $100 million
results-based loan (approved December 2024) upgrading infrastructure and equipment at 33 secondary healthcare
facilities, strengthening human-resource planning and posting, modernizing medicine supply-chain management,
introducing a health management information system with electronic medical records, and establishing quality
assurance regimes — benefiting an estimated 38 million people. It sits within the province's own <strong>KP
Health Policy 2018&ndash;2025</strong> framework and Pakistan's national <strong>Essential Package of Health
Services (EPHS)</strong>, a Disease Control Priorities 3 (DCP3)-based, evidence-prioritized set of interventions
delivered across community, BHU, RHC, THQ, and DHQ tiers. On financing and access, KP was the first Pakistani
province to reach universal coverage under the <strong>Sehat Sahulat / Sehat Card Plus</strong> program &mdash;
every resident is covered by CNIC alone, up to Rs. 40,000 per family member for secondary care and Rs. 400,000
per family for tertiary care plus a further Rs. 400,000 reserve for critical illness. On workforce, WHO's most
widely cited thresholds for skilled health workers (doctors, nurses, and midwives combined) are a 2006 minimum
of <strong>2.3 per 1,000 population</strong> below which countries fail to sustain adequate primary-care coverage,
and a stricter <strong>4.45 per 1,000</strong> composite SDG threshold. WHO sets no equivalent fixed target for
hospital beds, but Pakistan's own reported national average is <strong>0.6 beds per 1,000</strong> against a
global mean of 3.3. On essential medicines, the Drug Regulatory Authority of Pakistan (DRAP) publishes a National
Essential Medicine List (2025 edition) that provincial governments are expected to align provincial procurement
against; KP's own medicines and supplies are entirely provincially funded and currently procured through
district health offices and facility-level committees rather than a single pooled system.</p>

<h3>1. Human Resources &amp; Workforce</h3>
<p>This is the province's most severe gap by any external benchmark. KP's own Development Statistics 2025 data
puts provincial doctor density at just <strong>{province_doctors_per_1000:.2f} per 1,000</strong> population,
and combined doctor + paramedical (nurses, LHVs, dias, technicians) density at
<strong>{province_skilled_hw_per_1000:.2f} per 1,000</strong> &mdash; below WHO's 2006 minimum threshold of 2.3
per 1,000 in <strong>every one of KP's 35 districts</strong>, and below the stricter 4.45 SDG threshold in all
35 as well. The five worst-served districts by doctor density are
<strong>{", ".join(f"{d['district']} ({float(d['doctors_per_1000']):.3f}/1,000)" for d in worst_hr_districts)}</strong>
&mdash; several with fewer than 0.05 doctors per 1,000 population, an order of magnitude below even the minimum
threshold.</p>
<ul>
  <li><strong>Short term ({y3}):</strong> emergency redeployment and hardship-post incentive pay (remote-area
  allowances, guaranteed rotation length) to move existing staff toward the Critical-tier districts identified
  above, aligned with the ADB program's human-resource planning and posting-verification component rather than
  built as a parallel process; a province-wide staff-posting audit to confirm sanctioned posts are actually
  filled, not just funded.</li>
  <li><strong>Medium term ({y5}):</strong> expand medical, nursing, and paramedical college intake with
  reserved/bonded seats for students from Critical-tier districts who commit to a return-of-service period there
  &mdash; the same rural-pipeline logic used successfully elsewhere in Pakistan's health workforce strategy;
  build out the telemedicine network (below) specifically to extend specialist reach into districts where
  recruiting a resident specialist is not realistic within five years.</li>
  <li><strong>Long term ({y20}):</strong> reach WHO's 2006 minimum (2.3 skilled health workers per 1,000)
  province-wide, with an explicit equity target that no single district remains below half the provincial
  average &mdash; today's gap between {worst_hr_districts[0]['district']} and Peshawar is more than
  {(float(metrics_by_district['Peshawar']['doctors_per_1000']) / max(float(worst_hr_districts[0]['doctors_per_1000']), 0.001)):.0f}x;
  staffing growth should scale with the population projections below, planned ahead of the shortfall rather
  than reacting to it.</li>
</ul>

<h3>2. Bed Capacity &amp; Physical Infrastructure</h3>
<p>KP's provincial bed density, computed from Development Statistics 2025's own government + private bed counts,
is <strong>{province_beds_per_1000:.2f} per 1,000</strong> population &mdash; by this measure above Pakistan's
often-cited national average of 0.6 per 1,000, but that provincial figure is pulled up almost entirely by a
handful of large Peshawar tertiary hospitals and masks severe internal maldistribution: the five worst-served
districts &mdash; <strong>{", ".join(f"{d['district']} ({float(d['beds_per_1000']):.2f}/1,000)" for d in worst_bed_districts)}</strong>
&mdash; sit at or near zero. Three horizons of projected additional capacity for the ten highest gap-score
districts follow (full 35-district figures are in the <code>KP_District_Gap_Scores.shp</code> shapefile layer);
these numbers are the direct output of this report's own demand-forecast model, not external estimates.</p>
<h4>Short term ({y3}): stabilize and redeploy existing capacity</h4>
<p>No new construction assumed; this horizon is about deploying mobile health units into the highest-gap-score
Critical-tier districts, and reallocating existing beds/staff to match the near-term shortfalls below.</p>
{horizon_table_html(metrics, "29", y3)}
<h4>Medium term ({y5}): infrastructure build-out</h4>
<p>Aligned with the ADB program's 33-facility infrastructure and equipment upgrade, this horizon is where new
construction happens: at the ML-suggested sites below, and via the pilot drone-based medical resupply routes
(blood, vaccines, essential medicines) for the most terrain-isolated Critical-tier districts &mdash;
{", ".join(d["district"] for d in highest_elev_districts)} rank highest by mean elevation and are natural
candidates given seasonally unreliable road access (see the Terrain &amp; Elevation section above).</p>
{horizon_table_html(metrics, "31", y5)}
<h4>ML-Suggested New Facility Sites (Top Priority Districts)</h4>
<div class="table-wrap">
<table>
<thead><tr><th>Priority</th><th>District</th><th>Latitude</th><th>Longitude</th><th>Rationale</th></tr></thead>
<tbody>
{"".join(f"<tr><td class='num'>{s['priority']}</td><td class='col-name'>{s['district']}</td><td class='num'>{float(s['lat']):.4f}</td><td class='num'>{float(s['lon']):.4f}</td><td>{s['rationale']}</td></tr>" for s in sites)}
</tbody>
</table>
</div>
<h4>Long term ({y20}): systemic transformation</h4>
<p>Population growth compounds into shortfalls no operational measure can absorb. By facility count,
<strong>{", ".join(f"{d['district']} ({d[f'fac_nd{str(y20)[-2:]}']} additional facilities needed)" for d in top20_by_fac)}</strong>
show the largest projected {y20} gaps; by bed capacity,
<strong>{", ".join(f"{d['district']} ({int(d[f'beds_nd{str(y20)[-2:]}']):,} additional beds needed)" for d in top20_by_beds)}</strong>
lead. These districts are the strongest candidates for new DHQ/THQ-tier hospitals rather than incremental
BHU/RHC expansion. Given the terrain-difficulty findings above, any new tertiary-care facility sited in KP's
highest-elevation districts ({", ".join(d["district"] for d in highest_elev_districts[:3])}) should be built to
a climate/terrain-resilient standard (seismic considerations, winter road-access redundancy, off-grid power)
rather than the standard lowland design.</p>
{horizon_table_html(metrics, "46", y20)}

<h3>3. Priority Healthcare Interventions</h3>
<p>Pakistan's national immunization program shows a specific, addressable failure mode that KP's own coverage
data reproduces: initial-dose coverage is high (BCG and OPV-0 near 97&ndash;100%) but collapses through the
schedule &mdash; third-dose Penta and second-dose Measles coverage fall as low as 26% and 4% respectively in
recent district-level KP surveys, and full-immunization coverage in southern KP was measured at just 42.8% of
children aged 12&ndash;23 months. This is a dropout problem, not an access-only problem &mdash; households are
reached once but not retained through the full schedule. KP's maternal mortality ratio has historically been
estimated around 275 per 100,000 live births, well above the national target trajectory. KP also hosts more
Afghan refugees than any other province (over 822,000, concentrated in Peshawar, Nowshera, Haripur, and Swabi),
a sustained additional caseload on top of resident demand; in this report's own gap-score results, Peshawar,
Nowshera, and Swabi already sit in the High tier and Haripur in Moderate, consistent with that added pressure.</p>
<ul>
  <li><strong>Short term ({y3}):</strong> immunization dropout campaigns targeting third-dose/second-dose
  retention specifically (not just first-contact outreach) in Critical/High-tier and refugee-hosting districts;
  integrate maternal/newborn/child health (MNCH) outreach with the mobile health units already planned above.</li>
  <li><strong>Medium term ({y5}):</strong> full EPHS primary-tier rollout (community + BHU + RHC service
  package, per the national DCP3-based framework) in every Critical-tier district, paired with the bed/facility
  build-out above so new capacity opens already stocked with the standard intervention package rather than
  retrofitted later.</li>
  <li><strong>Long term ({y20}):</strong> as KP's epidemiological profile shifts with urbanization and an aging
  population, build out non-communicable disease (NCD) screening and chronic-care capacity at the new DHQ/THQ
  sites identified above, alongside continued MNCH/communicable-disease investment &mdash; a dual burden, not a
  replacement of one priority with another.</li>
</ul>

<h3>4. Procurement &amp; Medical Supply Chain</h3>
<p>KP's medicines and supplies are entirely provincially funded, procured through district health offices and
facility-level committees rather than a single pooled system &mdash; a decentralized model that gives local
flexibility but complicates bulk purchasing power and stock-visibility across districts. DRAP's National
Essential Medicine List (2025) is the reference formulary provincial procurement should track against.</p>
<ul>
  <li><strong>Short term ({y3}):</strong> a province-wide essential-medicines stock-out monitoring system
  (even a simple shared spreadsheet/SMS-reporting bridge ahead of full HMIS) so Critical-tier districts'
  shortages are visible centrally, not just locally.</li>
  <li><strong>Medium term ({y5}):</strong> consolidate toward pooled/regional procurement and warehouse
  modernization aligned with the ADB program's supply-chain component, reducing the number of independent
  facility-level purchasing decisions for high-volume essential medicines while keeping local committees for
  genuinely local/perishable needs.</li>
  <li><strong>Long term ({y20}):</strong> a full digital track-and-trace supply chain from provincial warehouse
  to facility, integrated with the HMIS/EMR system above, plus routine drone resupply (introduced at the medium
  term for terrain-isolated Critical-tier districts) as a standing logistics channel rather than a pilot.</li>
</ul>

<h3>5. Governance, Regulation &amp; Financing</h3>
<p>The KP Health Care Commission (KPHCC) already licenses and regulates health establishments province-wide
&mdash; this report's own facility data (Facility Count Cross-Validation, above) shows where that licensing
coverage is weakest. Sehat Sahulat/Sehat Card Plus gives KP something few other provinces have: universal
financial coverage already in place, meaning the binding constraint on access is supply (staff, beds, medicines)
far more than insurance enrollment. The province's Health Sector Development Programme allocation grew from
Rs. {dev_budget[fy_prev]['total']:,.0f} million in {fy_prev.replace('fy', '').replace('_', '-')} to
Rs. {dev_budget[fy_now]['total']:,.0f} million in {fy_now.replace('fy', '').replace('_', '-')} ({dev_budget[fy_now]['share_pct']}%
of the province's total development budget) &mdash; a real increase, but one that needs to be tracked against
the district-level gaps in this report to confirm it reaches where the data says it's needed most.</p>
<ul>
  <li><strong>Short term ({y3}):</strong> extend KPHCC licensing/registration outreach into currently
  unregistered tribal districts to establish an accurate facility baseline (see Facility Count Cross-Validation
  for where this gap is largest); publish district-level ADP health spending against the gap-score rankings in
  this report so allocation and need are visibly linked.</li>
  <li><strong>Medium term ({y5}):</strong> stand up the HMIS/electronic medical records system the ADB program
  is already funding, and connect it to a live version of this report's own gap-score model so need-tier
  rankings update from real utilization/staffing data rather than an annual snapshot.</li>
  <li><strong>Long term ({y20}):</strong> move toward outcome-based financing (budget tied to measured coverage
  and quality, not just facility counts) once the HMIS above provides the data to support it, with KPHCC's
  quality-assurance role formalized as the verification layer.</li>
</ul>

<h3>6. Cross-Cutting Infrastructure &amp; Terrain-Resilience Policy</h3>
<p>Roads and elevation are not incidental to this plan &mdash; they set the real cost and feasibility of every
other pillar. KP's road network data (Official Infrastructure Context, above) and this report's own DEM-derived
terrain-difficulty scores identify exactly which districts face compounding physical and clinical barriers.</p>
<ul>
  <li><strong>Short term ({y3}):</strong> telemedicine connectivity at existing DHQ/THQ hospitals serving
  Critical/High-tier mountainous districts, extending specialist reach without new physical construction.</li>
  <li><strong>Medium term ({y5}):</strong> road-access-redundancy planning (alternate seasonal routes, not just
  primary-route maintenance) for the terrain-isolated districts identified in the Terrain &amp; Elevation
  section, paired with the drone-resupply rollout above.</li>
  <li><strong>Long term ({y20}):</strong> every new facility in a high-elevation, high-terrain-difficulty
  district built to the climate/terrain-resilient standard described above as a matter of policy, not
  case-by-case decision &mdash; seismic design, winter access redundancy, and off-grid power as default
  specification, not an upgrade option.</li>
</ul>
</section>

<section id="supplemental-data">
<h2>Additional Facility &amp; District Information</h2>
<p>Equipment, medicine, departments, diseases treated, outbreak records, and other facts added via the
admin panel's document upload &mdash; not part of the deterministic gap-score model above.</p>
<div class="table-wrap">
<table>
<thead><tr><th>District</th><th>Facility</th><th>Category</th><th>Label</th><th>Detail</th><th>Source</th></tr></thead>
<tbody>
{supplemental_data_rows_html(supplemental_records)}
</tbody>
</table>
</div>
</section>

<section id="facility-readiness">
<h2>Facility Readiness (WHO SARA Framework)</h2>
<p>The WHO Service Availability and Readiness Assessment (SARA) framework splits facility adequacy into
<em>availability</em> (infrastructure and staff physically present - the Official Infrastructure Context section
above) and <em>readiness</em> (equipment, medicines, and capacity to actually deliver a service). Readiness scores
below come from documents uploaded via the admin panel's document-upload feature, extracted against SARA's real
tracer items (Basic Amenities, Basic Equipment, Standard Precautions for Infection Prevention, Diagnostic
Capacity, Essential Medicines) - a facility's score is the share of assessed tracer items found present; a domain
nobody has looked at yet is left out of that facility's average rather than counted against it.</p>
{readiness_section_html(readiness)}
</section>

<footer>
Built with an open-data GIS + AI pipeline (shapely, pyshp, scikit-learn, matplotlib, rasterio) — see the
accompanying QGIS project (<code>gis/KP_Healthcare_Plan.qgz</code>, including the Copernicus GLO-30 elevation
layer) and shapefiles in <code>gis/</code> for the full spatial dataset.
This report and its underlying shapefiles are a planning aid, not an official government publication.
</footer>
</main>
<script type="application/json" id="dashboard-data">{json.dumps(dashboard_payload)}</script>
<script>{DASHBOARD_JS}</script>
</body>
</html>
"""
    html = _insert_custom_sections(html, custom_tables_lib.list_tables_with_data())

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "KP_Healthcare_Plan.html").write_text(html, encoding="utf-8")
    print("Wrote report/KP_Healthcare_Plan.html")


if __name__ == "__main__":
    boundaries = json.loads((PROCESSED / "boundaries.json").read_text())
    build(
        source_boundary=boundaries["source"],
        source_population_note="See data/processed/kp_district_population_2023.csv for the per-district source URL.",
    )
