"""District terrain-difficulty scoring, shared by
scripts/08_compute_district_metrics.py (the report's terrain_difficulty
and terrain columns) and scripts/16b_compute_travel_time_accessibility.py
(edge-speed derating for routed accessibility) - both derive the same
continuous, DEM-based score from data/processed/district_terrain.csv's
raw elevation/slope, so it's defined once here rather than duplicated.
See docs/superpowers/specs/2026-08-15-travel-time-routing-design.md
section 3b for why this couldn't just live in one of the two consumers."""


def compute_terrain_difficulty(rows):
    """rows: list of dicts with mean_elev_m and mean_slope_deg (numeric).
    Returns the same rows with a terrain_difficulty field added: the mean
    of independently min-max-scaled elevation and slope, in [0,1]. Scaling
    is relative to the full set of rows passed in (i.e. call this once
    across all districts together, not per-row), so a district's score
    reflects its terrain difficulty relative to the rest of KP."""
    elevs = [float(r["mean_elev_m"]) for r in rows]
    slopes = [float(r["mean_slope_deg"]) for r in rows]
    elev_lo, elev_hi = min(elevs), max(elevs)
    slope_lo, slope_hi = min(slopes), max(slopes)
    out = []
    for r in rows:
        elev_n = (float(r["mean_elev_m"]) - elev_lo) / (elev_hi - elev_lo) if elev_hi > elev_lo else 0.0
        slope_n = (float(r["mean_slope_deg"]) - slope_lo) / (slope_hi - slope_lo) if slope_hi > slope_lo else 0.0
        row = dict(r)
        row["terrain_difficulty"] = round((elev_n + slope_n) / 2, 4)
        out.append(row)
    return out


def terrain_label(terrain_difficulty):
    return "mountainous" if terrain_difficulty > 0.5 else "plains"
