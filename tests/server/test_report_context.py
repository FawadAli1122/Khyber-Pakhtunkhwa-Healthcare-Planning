from server import report_context

_FACILITIES = [
    {"name": "Alpha General Hospital", "category": "Hospital", "district": "Alpha", "is_duplicate_of": ""},
    {"name": "Alpha Clinic", "category": "Clinic", "district": "Alpha", "is_duplicate_of": ""},
    {"name": "Beta Clinic", "category": "Clinic", "district": "Beta", "is_duplicate_of": ""},
]

_CUSTOM_TABLES = [
    {"label": "Cold Chain Equipment", "columns": [{"label": "Facility"}, {"label": "Status"}],
     "rows": [{"id": "r1"}, {"id": "r2"}]},
]


def _fixture_metrics():
    return [
        {
            "district": "Alpha", "need_tier": "Critical", "gap_score": "90.0",
            "population_2023": "100000", "beds_per_1000": "0.50",
            "doctors_per_1000": "0.10", "terrain": "plains",
        },
        {
            "district": "Beta", "need_tier": "Low", "gap_score": "10.0",
            "population_2023": "200000", "beds_per_1000": "5.00",
            "doctors_per_1000": "1.00", "terrain": "mountainous",
        },
    ]


def test_build_context_includes_totals():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Total districts: 2" in context
    assert "300,000" in context  # total population


def test_build_context_includes_tier_counts():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Critical=1" in context
    assert "Low=1" in context
    assert "High=0" in context
    assert "Moderate=0" in context


def test_build_context_ranks_by_gap_score_descending():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert context.index("Alpha") < context.index("Beta")


def test_build_context_includes_district_fields():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Alpha" in context
    assert "Critical" in context
    assert "100,000" in context


def test_build_context_loads_real_metrics_by_default(monkeypatch):
    # supplemental_records/custom_tables are mocked here (not left to
    # their own None defaults) so this test - about metrics loading, not
    # the other two - doesn't require a real running local database.
    monkeypatch.setattr(report_context.supplemental_data, "load_records", lambda: [])
    monkeypatch.setattr(report_context.custom_tables_lib, "list_tables_with_data", lambda: [])
    context = report_context.build_context()
    assert "Total districts: 35" in context
    assert "Peshawar" in context


def test_build_context_includes_supplemental_records():
    supplemental = [
        {"district": "Alpha", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit, operational"},
    ]
    context = report_context.build_context(_fixture_metrics(), supplemental, custom_tables=[])
    assert "MRI Machine" in context
    assert "DHQ Hospital" in context


def test_build_context_omits_supplemental_section_when_empty():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Additional facility/district information" not in context


def test_build_context_handles_none_facility_without_crashing():
    # build_context's own contract, independent of where its
    # supplemental_records argument comes from: a record with a None
    # facility value must not crash the sort-by-tuple comparison (None
    # vs str) and must not leak the literal string "None" into the
    # digest. (Previously constructed via a ragged CSV row read through
    # supplemental_data.load_records() - no longer possible now that
    # load_records() is backed by a real database table, which can't
    # produce a ragged row; the None-handling contract this test checks
    # belongs to build_context() itself, so it's exercised directly.)
    supplemental = [
        {"district": "Alpha", "facility": "DHQ Hospital", "category": "equipment",
         "label": "MRI Machine", "detail": "1 unit", "source_document": "a.pdf",
         "added_at": "2026-08-15T00:00:00+00:00"},
        {"district": "Alpha", "facility": None, "category": "equipment",
         "label": "X-ray", "detail": "", "source_document": "a.pdf",
         "added_at": "2026-08-15T00:00:01+00:00"},
    ]

    context = report_context.build_context(_fixture_metrics(), supplemental, custom_tables=[])
    assert "None" not in context
    assert "DHQ Hospital" in context


def test_build_context_includes_facility_totals_and_breakdowns():
    context = report_context.build_context(_fixture_metrics(), [], _FACILITIES, custom_tables=[])
    assert "Total known facilities: 3" in context
    assert "Hospital: 1" in context
    assert "Clinic: 2" in context
    assert "Alpha: 2" in context
    assert "Beta: 1" in context


def test_build_context_facility_totals_include_flagged_duplicates():
    # Matches scripts/14_build_html_report.py's own "Known Facilities"
    # stat tile: is_duplicate_of records are flagged, not dropped, from
    # the merged table - the AI's count must match what the report
    # itself already displays, not a different "distinct" number.
    facilities = _FACILITIES + [
        {"name": "Alpha Clinic (dup)", "category": "Clinic", "district": "Alpha", "is_duplicate_of": "Alpha Clinic"},
    ]
    context = report_context.build_context(_fixture_metrics(), [], facilities, custom_tables=[])
    assert "Total known facilities: 4" in context


def test_build_context_omits_facilities_section_when_empty():
    context = report_context.build_context(_fixture_metrics(), [], [], custom_tables=[])
    assert "Total known facilities" not in context


def test_build_context_loads_real_facilities_by_default(monkeypatch):
    # Same reasoning as test_build_context_loads_real_metrics_by_default:
    # mock supplemental_records/custom_tables so this facilities-loading
    # test doesn't require a real running local database.
    monkeypatch.setattr(report_context.supplemental_data, "load_records", lambda: [])
    monkeypatch.setattr(report_context.custom_tables_lib, "list_tables_with_data", lambda: [])
    context = report_context.build_context()
    assert "Total known facilities:" in context
    assert "Peshawar:" in context


def test_build_context_includes_custom_tables_summary():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=_CUSTOM_TABLES)
    assert "Cold Chain Equipment" in context
    assert "2 records" in context
    assert "Facility" in context


def test_build_context_omits_custom_tables_section_when_empty():
    context = report_context.build_context(_fixture_metrics(), [], custom_tables=[])
    assert "Admin-Defined Custom Data Tables" not in context
