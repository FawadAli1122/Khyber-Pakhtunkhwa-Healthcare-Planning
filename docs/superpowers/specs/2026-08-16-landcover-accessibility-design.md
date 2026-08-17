# Land-Cover-Weighted Accessibility — Design Spec

## 1. Problem

`accessibility_min` (network- and terrain-adjusted travel time to the nearest facility, 20% of the gap-score weight) is currently routed from each district's **plain geometric centroid** — the shape's mathematical center, not where anyone actually lives. `scripts/16b_compute_travel_time_accessibility.py`'s own docstring already flags this: *"the district polygon's plain geometric centroid... despite that script's docstring historically saying 'population centroid' — not actually population-weighted."* For an oddly-shaped or unevenly-populated district, this can measure accessibility from a point nobody lives near, while real settlements cluster elsewhere. Land cover's Built-up class (from `gis/KP_LandCover.tif`, ESA WorldCover 2021) directly identifies where people actually live, and can fix this without inventing a new metric.

## 2. Scope

**In scope:**
- Replace the routing origin in `build_districts_with_centroids()` with a Built-up-pixel-weighted point per district, falling back to the existing plain geometric centroid when a district has zero Built-up pixels.
- Track `centroid_shift_km` (the real distance between the two points) per district, threaded through `district_travel_time.csv` → `district_metrics.csv`.
- Report: update the Methodology section's description of `accessibility_min`; flag districts where `centroid_shift_km` exceeds 5km with the real distance named.

**Explicit non-goals:**
- **No change to `scripts/09_gap_score_and_clusters.py`'s formula or weights.** `accessibility_min` is consumed exactly as it already is — only its upstream routing origin changes. This is deliberate: it fixes a real, already-acknowledged accuracy gap in an existing feature rather than adding a new, separately-weighted signal (the alternative considered and explicitly declined - a new standalone "built-up-area-to-facility" gap-score component - would have required picking a new weight and risked double-counting the existing accessibility signal).
- No change to `scripts/11_suggest_new_sites.py`'s land-cover filter (already shipped, separate feature).
- No per-pixel or sub-district accessibility surface - one representative point per district, matching every other district-level feature already in the gap score.

## 3. Built-Up-Weighted Centroid

For each district polygon, clip `gis/KP_LandCover.tif` to the polygon (same `rasterio.mask.mask` technique already used in `23_fetch_landcover.py`), find every pixel classified `50` (Built-up), and take the mean of their coordinates (via the raster's affine transform) as the new routing origin. If a district has zero Built-up pixels, keep the existing plain geometric centroid unchanged - a district with literally no mapped built-up area doesn't have a better signal to route from, and this is a real, honestly-surfaced case, not an error.

The pixel-averaging logic itself is a pure function (given a land-cover array + affine transform, return the mean coordinate of pixels equal to a target class or `None`) - independently unit-testable without any real raster I/O, matching this project's established pattern (e.g. `_adjust_for_landcover` in the site-suggestion filter). The real `rasterio.mask.mask()` call around it is not unit-tested, matching every other real-raster-I/O code in this pipeline (`15_fetch_dem.py`, `23_fetch_landcover.py`).

## 4. Data Flow

- `district_travel_time.csv` gains two columns: `centroid_shift_km` (float, 0.0 when the fallback fired) and `point_source` (`"built_up_weighted"` or `"geometric_centroid"`, for transparency/debugging - not otherwise consumed downstream).
- `scripts/08_compute_district_metrics.py` merges `centroid_shift_km` into `district_metrics.csv` alongside the existing `accessibility_min` merge (same `load_travel_time()` lookup, no new load function needed).
- `scripts/14_build_html_report.py`:
  - Methodology section: one paragraph explaining the built-up-weighted routing point replaces the plain centroid, referencing the real fallback behavior.
  - A new short paragraph (only rendered when at least one district exceeds the 5km threshold) listing districts with their real `centroid_shift_km` value, framed as "population is measurably clustered away from the district's geometric center" - not framed as a data-quality problem, since it isn't one.

## 5. Testing

- The pure pixel-averaging function gets real unit tests (construct a small numpy array + a real `rasterio.Affine` transform, no file I/O).
- `build_districts_with_centroids()`'s real-raster-I/O wrapper gets no automated test, matching `15_fetch_dem.py`/`23_fetch_landcover.py`'s own established precedent for this class of code - verified manually against the real pipeline (a real full run, confirming `centroid_shift_km` values are sane, e.g. checking a real district with an odd shape or offset population center against the report's own narrative).
- No test needed for `09_gap_score_and_clusters.py` since it is explicitly unchanged - existing tests for that module continue to cover it unmodified.

## 6. Open Questions / Risks Explicitly Accepted

- Changing the routing origin will shift `accessibility_min` for some districts, which can shift `gap_score` and therefore `need_tier` classifications - this is the entire point of the change (a more accurate signal should produce different, more accurate rankings where districts were previously mismeasured), not a side effect to avoid. The report's methodology section makes the reasoning auditable rather than silent.
- The 5km flagging threshold is a simple, fixed, explainable value (not adaptive per district size), matching this project's established preference for simple, explainable thresholds over more sophisticated but opaque ones (e.g. the existing 5% facility-count cross-validation tolerance).
