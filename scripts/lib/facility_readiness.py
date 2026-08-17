"""WHO Service Availability and Readiness Assessment (SARA) reference
data and scoring, shared by server/supplemental_data.py (AI-extraction
prompt guidance) and scripts/14_build_html_report.py (scoring/display).
Lives here, not in server/, because scripts/ pipeline code (including
the report builder, which runs standalone outside the FastAPI app)
cannot import from server/ - the same layering
scripts/lib/supplemental_records.py already established for the same
reason.

TRACER_ITEMS: the 5 real WHO SARA General Service Readiness domains and
their tracer items - the first four domains verified against Table 2 of
a published SARA methodology analysis (Burkina Faso 2014 SARA data, BMC
Public Health 10.1186/s12889-020-09994-7); the fifth (Essential
Medicines) is WHO's own standard 14-item core tracer list, not a
country-specific adaptation. 43 items total, not invented.

No new data store - data/processed/supplemental_records.csv's existing
schema (district, facility, category, label, detail, source_document,
added_at) is reused entirely. A record counts as a SARA tracer item when
its category/label exactly match an entry here; everything else is
ignored by compute_readiness_scores() and continues to work as ordinary
free-form supplemental data. See docs/superpowers/specs/
2026-08-16-facility-readiness-design.md."""

TRACER_ITEMS = {
    "Basic Amenities": [
        "Power (electric or solar device)",
        "Improved water source inside or within the ground of the facility",
        "Room with auditory and visual privacy for patient consultations",
        "Access to adequate sanitation facilities for clients",
        "Communication equipment (phone or SW radio)",
        "Facility has access to computer with E-mail/Internet access",
        "Emergency transportation",
    ],
    "Basic Equipment": [
        "Adult scale",
        "Child scale",
        "Thermometer",
        "Stethoscope",
        "Blood pressure apparatus",
        "Light source",
    ],
    "Standard Precautions for Infection Prevention": [
        "Safe final disposal of sharp materials",
        "Safe final disposal of infectious wastes",
        "Appropriate storage of sharp waste",
        "Appropriate storage of infectious waste",
        "Disinfectant",
        "Single use (standard disposable or auto-disable syringes)",
        "Soap and running water or alcohol based hand rub",
        "Latex gloves",
        "Guidelines for standard precautions",
    ],
    "Diagnostic Capacity": [
        "Haemoglobin",
        "Blood glucose",
        "Malaria diagnostic capacity",
        "Urine dipstick-protein",
        "Urine dipstick-glucose",
        "HIV diagnostic capacity",
        "Urine test for pregnancy",
    ],
    "Essential Medicines": [
        "Amitriptyline",
        "Amoxicillin",
        "Atenolol",
        "Captopril",
        "Ceftriaxone",
        "Ciprofloxacin",
        "Co-trimoxazole",
        "Diazepam",
        "Diclofenac",
        "Glibenclamide",
        "Omeprazole",
        "Paracetamol",
        "Simvastatin",
        "Salbutamol",
    ],
}

# Every (domain, tracer item) pair, for O(1) membership checks.
_KNOWN_TRACER_ITEMS = {(domain, item) for domain, items in TRACER_ITEMS.items() for item in items}


def _is_present(detail):
    """"absent" (case/whitespace-insensitive) is the only way a record
    marks an item as confirmed not-present. Everything else - "present",
    a quantity, a note, or even an empty string - counts as present,
    matching how the rest of the free-form supplemental records already
    treat "a record exists" as "the fact is true"."""
    return (detail or "").strip().lower() != "absent"


def compute_readiness_scores(records):
    """records: supplemental_records.csv rows (any mix of SARA tracer
    items and ordinary free-form facts - non-tracer records are ignored
    here, untouched otherwise). Returns {"facilities": [...],
    "districts": [...]} - see module docstring / spec section 5 for the
    exact scoring rules (per-domain, per-facility overall, district
    aggregation, deduplication, omit-unassessed-domains)."""
    relevant = [r for r in records if (r.get("category"), r.get("label")) in _KNOWN_TRACER_ITEMS]

    # Dedupe by (facility, district, category, label), keeping the
    # record with the latest added_at - supplemental_records.csv is
    # purely additive, so the same tracer item can legitimately appear
    # more than once (a second document upload, a status that changed).
    latest = {}
    for r in relevant:
        key = (r.get("facility", ""), r.get("district", ""), r["category"], r["label"])
        existing = latest.get(key)
        if existing is None or r.get("added_at", "") >= existing.get("added_at", ""):
            latest[key] = r
    deduped = list(latest.values())

    # Group into per-facility, per-domain [present_count, assessed_count].
    facility_domain_counts = {}
    for r in deduped:
        fkey = (r.get("facility", ""), r.get("district", ""))
        domain = r["category"]
        counts = facility_domain_counts.setdefault(fkey, {}).setdefault(domain, [0, 0])
        counts[1] += 1
        if _is_present(r.get("detail")):
            counts[0] += 1

    facilities_out = []
    for (facility, district), domains in facility_domain_counts.items():
        domain_scores = {d: present / assessed for d, (present, assessed) in domains.items() if assessed > 0}
        overall = sum(domain_scores.values()) / len(domain_scores) if domain_scores else None
        facilities_out.append({
            "facility": facility,
            "district": district,
            "domain_scores": domain_scores,
            "overall_score": overall,
        })

    district_scores = {}
    for f in facilities_out:
        if f["overall_score"] is None:
            continue
        district_scores.setdefault(f["district"], []).append(f["overall_score"])
    districts_out = [
        {"district": d, "mean_score": sum(scores) / len(scores), "facilities_assessed": len(scores)}
        for d, scores in district_scores.items()
    ]

    return {"facilities": facilities_out, "districts": districts_out}
