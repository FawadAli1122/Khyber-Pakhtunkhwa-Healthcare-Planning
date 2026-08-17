"""Composite facility-access gap score per district (0-100, higher =
more underserved) and a KMeans need-tier clustering on the same feature
set. Weighting/method is documented in plain language in the HTML report
(scripts/14_build_html_report.py) — this is a transparent weighted score
plus an unsupervised grouping, not a black-box model. Development
Statistics of KP 2025 (the official KP Bureau of Statistics publication)
is the primary data source for institution/bed/staffing figures; the
merged KPHCC/OSM facility registry is used only for accessibility_min
(network- and terrain-adjusted travel time to the nearest facility,
searched across all of KP regardless of district - see
scripts/16b_compute_travel_time_accessibility.py), which needs real point
coordinates that Dev Stats doesn't publish."""
import csv
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import MinMaxScaler

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

# Feature weights for the composite gap score. Each feature is min-max
# normalized to [0,1] first so weights are comparable; terrain_penalty is
# a continuous DEM-derived difficulty score (see scripts/lib/terrain.py's
# compute_terrain_difficulty), already in [0,1], since mountainous terrain
# independently worsens real access beyond what travel time alone
# captures. institution density, beds_per_1000, and doctors_per_1000 all
# come from Development Statistics of KP 2025
# (data/processed/dev_stats_health.csv) - the official KP Bureau of
# Statistics publication, used as the primary source in preference to the
# merged KPHCC/OSM facility registry, which undercounts small government
# BHUs/dispensaries that are rarely individually mapped. Beds and doctors
# are kept as their own weighted terms rather than folded into institution
# density, since a district can have many small facilities but few beds,
# or vice versa - distinct dimensions of access, not restatements of the
# same thing. accessibility_min still comes from the merged registry's
# point coordinates since Dev Stats publishes district totals only, never
# facility locations.
WEIGHTS = {
    "pop_density": 0.25,               # more people per km^2 with few facilities -> higher need
    "inverse_facility_density": 0.15,  # official (Dev Stats) institutions per capita, inverted
    "accessibility_min": 0.20,         # travel time to nearest mapped facility
    "terrain_penalty": 0.10,           # flat bump for mountainous districts
    "inverse_beds_per_1000": 0.15,     # official government+private beds per capita, inverted
    "inverse_doctors_per_1000": 0.15,  # official medical staff per capita, inverted
}


def _require_accessibility_min(row):
    """With the routed accessibility_min in place, every district should
    always get a real value - a routed time, or the disconnected-
    component/no-facilities-in-KP straight-line fallback (see
    scripts/16b_compute_travel_time_accessibility.py and
    scripts/lib/routing.py). A blank value reaching this point means real
    upstream data is missing, not "this district is well-served" -
    silently treating it as 0.0 would wrongly bias that district toward
    looking well-served instead of surfacing the real problem."""
    value = row.get("accessibility_min")
    if value in (None, ""):
        raise ValueError(
            f"Missing accessibility_min for district {row.get('district')!r} - "
            "district_travel_time.csv should have a value for every district "
            "(run scripts/16b_compute_travel_time_accessibility.py before this script)"
        )
    return float(value)


def _feature_matrix(rows):
    pop_density = np.array([float(r["pop_density"]) for r in rows]).reshape(-1, 1)
    institution_count = np.array([max(float(r["govt_pvt_institutions"]), 0.0) for r in rows])
    population = np.array([float(r["population_2023"]) for r in rows])
    facility_density = np.divide(
        institution_count, population, out=np.zeros_like(institution_count), where=population > 0
    )
    inverse_facility_density = (-facility_density).reshape(-1, 1)  # more institutions -> lower gap
    accessibility = np.array([_require_accessibility_min(r) for r in rows]).reshape(-1, 1)
    terrain_penalty = np.array([float(r["terrain_difficulty"]) for r in rows]).reshape(-1, 1)
    inverse_beds = (-np.array([float(r["beds_per_1000"]) for r in rows])).reshape(-1, 1)
    inverse_doctors = (-np.array([float(r["doctors_per_1000"]) for r in rows])).reshape(-1, 1)

    scaler = MinMaxScaler()
    pop_density_n = scaler.fit_transform(pop_density)
    inv_fac_n = MinMaxScaler().fit_transform(inverse_facility_density)
    access_n = MinMaxScaler().fit_transform(accessibility)
    inv_beds_n = MinMaxScaler().fit_transform(inverse_beds)
    inv_doctors_n = MinMaxScaler().fit_transform(inverse_doctors)
    # terrain_penalty is already in [0,1] (a continuous DEM-derived score), no scaling needed

    return np.hstack([pop_density_n, inv_fac_n, access_n, terrain_penalty, inv_beds_n, inv_doctors_n])


def compute_gap_scores(rows):
    features = _feature_matrix(rows)
    weights = np.array([
        WEIGHTS["pop_density"], WEIGHTS["inverse_facility_density"], WEIGHTS["accessibility_min"],
        WEIGHTS["terrain_penalty"], WEIGHTS["inverse_beds_per_1000"], WEIGHTS["inverse_doctors_per_1000"],
    ])
    raw_scores = features @ weights  # weighted sum, already in ~[0,1] since each feature column is in [0,1]
    # Re-normalize to a clean 0-100 scale across this district set.
    lo, hi = raw_scores.min(), raw_scores.max()
    scaled = (raw_scores - lo) / (hi - lo) * 100 if hi > lo else np.zeros_like(raw_scores)
    out = []
    for row, score in zip(rows, scaled):
        row = dict(row)
        row["gap_score"] = round(float(score), 2)
        out.append(row)
    return out


def assign_need_tiers(rows, n_clusters=4):
    scores = np.array([[r["gap_score"]] for r in rows])
    n_clusters = min(n_clusters, len(rows))
    km = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    labels = km.fit_predict(scores)
    # Order cluster centers ascending -> map to tier names so "highest mean
    # gap_score cluster" is always "Critical" regardless of KMeans' arbitrary
    # label numbering.
    centers = km.cluster_centers_.flatten()
    order = np.argsort(centers)  # ascending gap score
    tier_names = ["Low", "Moderate", "High", "Critical"][-n_clusters:]
    label_to_tier = {label: tier_names[rank] for rank, label in enumerate(order)}

    out = []
    for row, label in zip(rows, labels):
        row = dict(row)
        row["need_tier"] = label_to_tier[label]
        out.append(row)
    return out


def main():
    csv_path = PROCESSED / "district_metrics.csv"
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    scored = compute_gap_scores(rows)
    tiered = assign_need_tiers(scored)

    fieldnames = list(tiered[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(tiered)
    print(f"Updated district_metrics.csv with gap_score/need_tier for {len(tiered)} districts")


if __name__ == "__main__":
    main()
