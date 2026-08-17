from scripts.lib import facility_readiness


def make_record(district, facility, category, label, detail, added_at="2026-08-16T00:00:00+00:00"):
    return {
        "district": district, "facility": facility, "category": category, "label": label,
        "detail": detail, "source_document": "test.pdf", "added_at": added_at,
    }


def test_tracer_items_has_five_domains_and_43_items():
    assert set(facility_readiness.TRACER_ITEMS.keys()) == {
        "Basic Amenities", "Basic Equipment", "Standard Precautions for Infection Prevention",
        "Diagnostic Capacity", "Essential Medicines",
    }
    total = sum(len(items) for items in facility_readiness.TRACER_ITEMS.values())
    assert total == 43


def test_compute_readiness_scores_ignores_non_tracer_records():
    records = [make_record("Peshawar", "DHQ Hospital", "outbreak", "Cholera", "12 cases")]
    result = facility_readiness.compute_readiness_scores(records)
    assert result["facilities"] == []
    assert result["districts"] == []


def test_compute_readiness_scores_single_facility_single_domain():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Stethoscope", "present"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Adult scale", "absent"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    assert len(result["facilities"]) == 1
    f = result["facilities"][0]
    assert f["facility"] == "DHQ Hospital"
    assert f["district"] == "Peshawar"
    assert f["domain_scores"] == {"Basic Equipment": 2 / 3}
    assert f["overall_score"] == 2 / 3


def test_compute_readiness_scores_domain_with_zero_assessed_items_is_omitted():
    # Only Basic Equipment has any records - Essential Medicines etc. must
    # not appear in domain_scores at all, and must not drag overall_score
    # toward 0.
    records = [make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present")]
    result = facility_readiness.compute_readiness_scores(records)
    f = result["facilities"][0]
    assert set(f["domain_scores"].keys()) == {"Basic Equipment"}
    assert f["overall_score"] == 1.0


def test_compute_readiness_scores_multi_domain_overall_is_mean_of_domain_scores():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Stethoscope", "absent"),
        make_record("Peshawar", "DHQ Hospital", "Essential Medicines", "Paracetamol", "present"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    f = result["facilities"][0]
    # Basic Equipment: 1/2 = 0.5, Essential Medicines: 1/1 = 1.0, mean = 0.75
    assert f["domain_scores"] == {"Basic Equipment": 0.5, "Essential Medicines": 1.0}
    assert f["overall_score"] == 0.75


def test_compute_readiness_scores_empty_detail_counts_as_present():
    records = [make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "")]
    result = facility_readiness.compute_readiness_scores(records)
    assert result["facilities"][0]["domain_scores"]["Basic Equipment"] == 1.0


def test_compute_readiness_scores_quantity_detail_counts_as_present():
    records = [make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "3 units")]
    result = facility_readiness.compute_readiness_scores(records)
    assert result["facilities"][0]["domain_scores"]["Basic Equipment"] == 1.0


def test_compute_readiness_scores_dedupes_keeping_latest_added_at():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "absent", added_at="2026-08-01T00:00:00+00:00"),
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present", added_at="2026-08-16T00:00:00+00:00"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    # The later record (present) must win, not both counted.
    assert result["facilities"][0]["domain_scores"]["Basic Equipment"] == 1.0


def test_compute_readiness_scores_multiple_facilities_and_district_aggregation():
    records = [
        make_record("Peshawar", "DHQ Hospital", "Basic Equipment", "Thermometer", "present"),
        make_record("Peshawar", "City Clinic", "Basic Equipment", "Thermometer", "absent"),
        make_record("Mardan", "MMC Hospital", "Basic Equipment", "Thermometer", "present"),
    ]
    result = facility_readiness.compute_readiness_scores(records)
    assert len(result["facilities"]) == 3
    districts_by_name = {d["district"]: d for d in result["districts"]}
    assert districts_by_name["Peshawar"]["facilities_assessed"] == 2
    assert districts_by_name["Peshawar"]["mean_score"] == (1.0 + 0.0) / 2
    assert districts_by_name["Mardan"]["facilities_assessed"] == 1
    assert districts_by_name["Mardan"]["mean_score"] == 1.0
