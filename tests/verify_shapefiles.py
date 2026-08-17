from pathlib import Path
import shapefile

GIS_DIR = Path(__file__).resolve().parent.parent / "gis"
EXPECTED = {
    "KP_Province_Boundary": 1,
    "KP_Districts": 25,   # lower bound
    "KP_Healthcare_Facilities": 100,
    "KP_Roads": 20,
    "KP_District_Gap_Scores": 25,
    "KP_Suggested_New_Sites": 1,
}


EXPECTED_FIELDS = {
    "KP_Districts": {
        "mean_elev", "mean_slop", "terr_diff", "fac_count", "auth_inst", "govt_inst", "govt_beds",
        "pvt_hosp", "pvt_beds", "med_staff", "para_staf", "pvt_prac", "pop_pbed",
        "beds_p1k", "doc_p1k", "road_km",
        "pat_total", "pat_indr", "pat_outdr", "bcg", "opv0", "opv3", "measles", "mal_cases", "mal_trtd",
    },
    "KP_District_Gap_Scores": {"pop_2029", "pop_2031", "pop_2046", "fac_nd29", "fac_nd31", "fac_nd46", "beds_nd29", "beds_nd31", "beds_nd46"},
}


def main():
    for layer, min_count in EXPECTED.items():
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            path = GIS_DIR / f"{layer}{ext}"
            assert path.exists(), f"Missing {path}"
        r = shapefile.Reader(str(GIS_DIR / layer))
        shapes = r.shapes()
        assert len(shapes) >= min_count, f"{layer}: expected >= {min_count} features, got {len(shapes)}"
        for shp in shapes:
            assert shp.points or shp.shapeType == shapefile.NULL, f"{layer}: empty geometry found"
        if layer in EXPECTED_FIELDS:
            fields = {f[0] for f in r.fields[1:]}
            missing = EXPECTED_FIELDS[layer] - fields
            assert not missing, f"{layer}: missing expected fields {missing} (has {fields})"
        print(f"OK: {layer} has {len(shapes)} features")


if __name__ == "__main__":
    main()
