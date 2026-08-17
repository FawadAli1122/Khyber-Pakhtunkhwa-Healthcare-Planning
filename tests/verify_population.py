"""Cross-check the compiled population CSV against the officially published
KP provincial total (independent of the per-district PBS dashboard API
pulled in Task 4), to catch gross transcription/parsing errors."""
import csv
from pathlib import Path

POPULATION_CSV = Path(__file__).resolve().parent.parent / "data" / "processed" / "kp_district_population_2023.csv"

# KP's published 2023 Digital Census provincial total population, from the
# PBS Provincial Census Report KP (Table "Population and Housing Census-2023
# at a Glance"): https://www.pbs.gov.pk/wp-content/uploads/2020/07/Provincial-Census-Report-2023-KP.pdf
KP_PROVINCIAL_TOTAL_2023 = 40_856_097


def main():
    with open(POPULATION_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    total = sum(int(r["population_2023"]) for r in rows)
    diff_pct = abs(total - KP_PROVINCIAL_TOTAL_2023) / KP_PROVINCIAL_TOTAL_2023 * 100
    assert diff_pct < 5, (
        f"Summed district populations ({total}) differ from the published "
        f"provincial total ({KP_PROVINCIAL_TOTAL_2023}) by {diff_pct:.1f}% "
        "- check for a missed/duplicated district or a transcription error."
    )
    print(f"OK: district sum {total} within {diff_pct:.2f}% of provincial total")


if __name__ == "__main__":
    main()
