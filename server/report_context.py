"""Builds a compact text digest of the healthcare plan's data - the AI's
grounding context for the "Ask AI" chat panel and the Telegram bot's
/ask command (both call this exact function - no logic duplicated
between the two interfaces). Deliberately not the raw report HTML:
markup is wasteful token-wise and invites the model to comment on
styling instead of data. See docs/superpowers/specs/
2026-08-15-ai-chat-panel-phase3-design.md section 3,
2026-08-15-supplemental-facility-data-phase4b-design.md section 6, and
2026-08-16-telegram-connector-design.md.

Originally covered only per-district aggregates (population, gap score,
beds/doctors per 1000) and supplemental records - a real gap, found live
via the Telegram bot: asked "total number of health facilities in KP",
the AI correctly and honestly said the digest didn't contain that,
because it genuinely didn't - facilities_merged.csv was never read here
at all, on the web panel or the bot. Extended to include facility counts/
categories/per-district breakdowns, matching scripts/14_build_html_report.py's
own facility-counting rule exactly (is_duplicate_of records are flagged,
not dropped, from the merge - counted here too, not filtered out, so
this never contradicts the number the report itself already shows).
"""
import csv
from collections import Counter
from pathlib import Path

from scripts.lib import custom_tables as custom_tables_lib
from server import supplemental_data

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
METRICS_PATH = PROCESSED / "district_metrics.csv"
FACILITIES_PATH = PROCESSED / "facilities_merged.csv"

TIER_ORDER = ("Critical", "High", "Moderate", "Low")


def load_metrics(path=METRICS_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_facilities(path=FACILITIES_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_context(metrics=None, supplemental_records=None, facilities=None, custom_tables=None):
    if metrics is None:
        metrics = load_metrics()
    if supplemental_records is None:
        supplemental_records = supplemental_data.load_records()
    if facilities is None:
        facilities = load_facilities()
    if custom_tables is None:
        custom_tables = custom_tables_lib.list_tables_with_data()

    total_population = sum(int(float(m["population_2023"])) for m in metrics)
    tier_counts = {tier: 0 for tier in TIER_ORDER}
    for m in metrics:
        tier = m["need_tier"]
        if tier in tier_counts:
            tier_counts[tier] += 1

    lines = [
        "Khyber Pakhtunkhwa Healthcare System Planning Report - data digest",
        f"Total districts: {len(metrics)}",
        f"Total population (2023 census): {total_population:,}",
        "Need-tier counts: " + ", ".join(f"{tier}={count}" for tier, count in tier_counts.items()),
        "",
        "Per-district data (sorted by gap score, most underserved first):",
        "district | need_tier | gap_score | population_2023 | beds_per_1000 | doctors_per_1000 | terrain",
    ]
    ranked = sorted(metrics, key=lambda m: float(m["gap_score"]), reverse=True)
    for m in ranked:
        lines.append(
            f"{m['district']} | {m['need_tier']} | {float(m['gap_score']):.1f} | "
            f"{int(float(m['population_2023'])):,} | {float(m['beds_per_1000']):.2f} | "
            f"{float(m['doctors_per_1000']):.2f} | {m['terrain']}"
        )

    if facilities:
        lines.append("")
        lines.append(f"Total known facilities: {len(facilities)} (KPHCC + OSM + Marham.pk + Telegram-bot-added, "
                      "including records flagged as likely duplicates of another - matches the report's own count)")
        category_counts = Counter(f["category"] for f in facilities if f.get("category"))
        lines.append("By category: " + ", ".join(f"{cat}: {count}" for cat, count in category_counts.most_common()))
        district_counts = Counter(f["district"] for f in facilities if f.get("district"))
        lines.append(
            "By district: " + ", ".join(f"{d}: {c}" for d, c in sorted(district_counts.items(), key=lambda kv: -kv[1]))
        )

    if supplemental_records:
        lines.append("")
        lines.append("Additional facility/district information (from uploaded documents):")
        lines.append("district | facility | category | label | detail")
        ranked_supplemental = sorted(
            # r.get("facility", "") only substitutes when the key is
            # missing, not when it's present with a None value - guard
            # explicitly so a None facility can't crash this comparison.
            supplemental_records, key=lambda r: (r["district"], r.get("facility") or "", r["category"])
        )
        for r in ranked_supplemental:
            facility = r.get("facility") or "(district-wide)"
            lines.append(f"{r['district']} | {facility} | {r['category']} | {r['label']} | {r.get('detail', '')}")

    if custom_tables:
        lines.append("")
        lines.append("Admin-Defined Custom Data Tables:")
        for table in custom_tables:
            columns = ", ".join(c["label"] for c in table["columns"])
            lines.append(f"- {table['label']} ({len(table['rows'])} records) - columns: {columns}")

    return "\n".join(lines)
