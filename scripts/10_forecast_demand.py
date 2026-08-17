"""Project district population to 3/5/20-year horizons (computed from
today's date, not hardcoded calendar years, so this stays correct as the
project ages) using each district's own PBS-computed annual growth rate
(from data/processed/kp_district_population_2023.csv, column
growth_rate_pct — sourced directly from the census dashboard's `agr`
field per district; falls back to the KP provincial average growth rate
for any row where it's missing), then estimates both facilities needed
(a simplified Pakistan health-facility population norm, 1 basic facility
per ~30,000 population, approximating the BHU/RHC tier) and beds needed
(a simplified 1.0 beds/1,000 population planning norm) at each horizon.
Both norms are documented as simplifications in the HTML report, not
official MoH standards. Beds-needed nets out each district's current
government bed count from data/processed/dev_stats_health.csv (Development
Statistics of KP 2025) where available."""
import csv
from datetime import date
import math
from pathlib import Path

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
CENSUS_YEAR = 2023
DEFAULT_PER_FACILITY_POPULATION = 30000
DEFAULT_BEDS_PER_1000 = 1.0  # simplified planning norm, not an official MoH standard - documented in the HTML report

# Suffixes match the field-name convention (2-digit target year); offsets
# are years ahead of today, giving the 3-year, 5-year, and 20-year
# planning horizons the report presents.
HORIZON_OFFSETS = {"29": 3, "31": 5, "46": 20}
BASE_YEAR = date.today().year


def project_population(pop_current, growth_rate_pct, years_ahead):
    return pop_current * ((1 + growth_rate_pct / 100.0) ** years_ahead)


def facilities_needed(population, per_facility_population=DEFAULT_PER_FACILITY_POPULATION):
    if population <= 0:
        return 0
    return math.ceil(population / per_facility_population)


def beds_needed(population, beds_per_1000=DEFAULT_BEDS_PER_1000):
    if population <= 0:
        return 0
    return round(population / 1000 * beds_per_1000)


def load_growth_rates():
    with open(PROCESSED / "kp_district_population_2023.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rates = {}
    known = []
    for r in rows:
        if r["growth_rate_pct"]:
            rate = float(r["growth_rate_pct"])
            rates[r["district"]] = rate
            known.append(rate)
    provincial_avg = sum(known) / len(known) if known else 2.4  # KP long-run avg fallback
    return rates, provincial_avg


def load_govt_beds():
    path = PROCESSED / "dev_stats_health.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["district"]: int(r["govt_beds"] or 0) for r in csv.DictReader(f)}


def main():
    rates, provincial_avg = load_growth_rates()
    govt_beds_by_district = load_govt_beds()
    csv_path = PROCESSED / "district_metrics.csv"
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        pop_2023 = float(row["population_2023"])
        rate = rates.get(row["district"], provincial_avg)
        current_facilities = int(row["facility_count"])
        current_beds = govt_beds_by_district.get(row["district"], 0)

        for suffix, offset in HORIZON_OFFSETS.items():
            target_year = BASE_YEAR + offset
            pop_at_horizon = project_population(pop_2023, rate, target_year - CENSUS_YEAR)
            row[f"pop_{target_year}"] = round(pop_at_horizon)
            row[f"fac_nd{suffix}"] = max(facilities_needed(pop_at_horizon) - current_facilities, 0)
            row[f"beds_nd{suffix}"] = max(beds_needed(pop_at_horizon) - current_beds, 0)

    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    horizon_years = sorted(BASE_YEAR + o for o in HORIZON_OFFSETS.values())
    print(f"Updated district_metrics.csv with {horizon_years} forecasts for {len(rows)} districts")


if __name__ == "__main__":
    main()
