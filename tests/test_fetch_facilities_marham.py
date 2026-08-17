import importlib

marham_mod = importlib.import_module("scripts.21_fetch_facilities_marham")


def test_infer_category_hospital():
    assert marham_mod.infer_category("Combined Military Hospital") == "Hospital"
    assert marham_mod.infer_category("Dhq Hospital") == "Hospital"


def test_infer_category_clinic():
    assert marham_mod.infer_category("Specialist Psychiatry Clinic") == "Clinic"


def test_infer_category_pharmacy():
    assert marham_mod.infer_category("Zafran Medical store") == "Pharmacy"
    assert marham_mod.infer_category("City Pharmacy") == "Pharmacy"


def test_infer_category_facility_for_labs():
    assert marham_mod.infer_category("Doctors Lab") == "Facility"
    assert marham_mod.infer_category("Zeeshan Medical Store and Azam laboratory") == "Pharmacy"  # "medical store" checked before "laboratory"


def test_infer_category_other_fallback():
    assert marham_mod.infer_category("Abaseen Center") == "Other"


HOSPITAL_JSON_LD_HTML = """
<html><body>
<script type="application/ld+json">
[
  {
    "@context": "https://schema.org/",
    "@type": "Hospital",
    "medicalSpecialty": "Multi-Speciality (M, u & more)",
    "name": "Bukhari Medical and Surgical Complex",
    "url": "https://www.marham.pk/hospitals/bannu/bukhari-medical-and-surgical-complex/kpk",
    "telephone": "03130918921",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "D I Khan Rd, near Bannu Woollen Mills Ltd",
      "addressLocality": "KPK",
      "addressRegion": "Bannu",
      "addressCountry": "Pakistan",
      "postalCode": "54000"
    },
    "geo": {"@type": "GeoCoordinates", "latitude": 32.98092479495509, "longitude": 70.618048230688}
  },
  {
    "@context": "https://schema.org/",
    "@type": "Hospital",
    "name": "Combined Military Hospital",
    "url": "https://www.marham.pk/hospitals/bannu/combined-military-hospital/bannu-cantonment",
    "telephone": "03339988128",
    "address": {"@type": "PostalAddress", "streetAddress": "Cantt", "addressLocality": "Bannu Cantonment"},
    "geo": {"@type": "GeoCoordinates", "latitude": 0, "longitude": 0}
  },
  {
    "@context": "https://schema.org/",
    "@type": "Pharmacy",
    "name": "Some Other Schema Type - Not A Hospital",
    "url": "https://www.marham.pk/pharmacies/bannu/irrelevant"
  }
]
</script>
</body></html>
"""


def test_parse_hospital_entries_extracts_only_hospital_type():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    assert len(entries) == 2  # the "Pharmacy" @type entry is excluded
    names = {e["name"] for e in entries}
    assert names == {"Bukhari Medical and Surgical Complex", "Combined Military Hospital"}


def test_parse_hospital_entries_detects_real_coordinates():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    by_name = {e["name"]: e for e in entries}
    real = by_name["Bukhari Medical and Surgical Complex"]
    assert real["has_real_coords"] is True
    assert real["lat"] == 32.98092479495509
    assert real["lon"] == 70.618048230688


def test_parse_hospital_entries_treats_zero_zero_as_placeholder():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    by_name = {e["name"]: e for e in entries}
    placeholder = by_name["Combined Military Hospital"]
    assert placeholder["has_real_coords"] is False
    assert placeholder["lat"] is None
    assert placeholder["lon"] is None


def test_parse_hospital_entries_extracts_url_and_address_fields():
    entries = marham_mod.parse_hospital_entries(HOSPITAL_JSON_LD_HTML)
    by_name = {e["name"]: e for e in entries}
    real = by_name["Bukhari Medical and Surgical Complex"]
    assert real["url"] == "https://www.marham.pk/hospitals/bannu/bukhari-medical-and-surgical-complex/kpk"
    assert real["telephone"] == "03130918921"
    assert real["street_address"] == "D I Khan Rd, near Bannu Woollen Mills Ltd"


def test_parse_hospital_entries_no_json_ld_returns_empty():
    assert marham_mod.parse_hospital_entries("<html><body>no listings here</body></html>") == []


def test_extract_header_count_finds_the_number():
    assert marham_mod.extract_header_count("<title>18 Best Hospitals in Bannu | Marham</title>") == 18
    assert marham_mod.extract_header_count("<h1>503 Best Hospitals in Peshawar</h1>") == 503
    assert marham_mod.extract_header_count("<title>1 Best Hospitals in Tank City | Marham</title>") == 1


def test_extract_header_count_returns_none_when_absent():
    assert marham_mod.extract_header_count("<html><body>nothing relevant</body></html>") is None


def test_fetch_district_facilities_dedupes_across_sliding_pagination():
    # Mirrors the real observed Bannu shape: page 1 returns facilities
    # A-J, page 2 returns G-J (repeated) + K-P (new), page 3 returns
    # nothing new -> pagination stops, 16 unique facilities total.
    def make_page(names, page_num):
        entries = ",\n".join(
            f'{{"@type": "Hospital", "name": "{n}", "url": "https://www.marham.pk/hospitals/test/{n.lower()}", '
            f'"telephone": "0000", "address": {{"streetAddress": "Some Rd"}}, '
            f'"geo": {{"latitude": 0, "longitude": 0}}}}'
            for n in names
        )
        return f'<html><body><title>16 Best Hospitals in Test</title><script type="application/ld+json">[{entries}]</script></body></html>'

    names_a_to_j = [chr(ord("A") + i) for i in range(10)]
    names_g_to_p = [chr(ord("A") + i) for i in range(6, 16)]
    pages = {
        1: make_page(names_a_to_j, 1),
        2: make_page(names_g_to_p, 2),
        3: make_page([], 3),
    }

    calls = []

    class FakeSession:
        pass

    def fake_rate_limited_get(session, url, params=None, **kwargs):
        page_num = (params or {}).get("page", 1)
        calls.append(page_num)

        class FakeResp:
            text = pages[page_num]

        return FakeResp()

    entries, header_count = marham_mod.fetch_district_facilities(FakeSession(), "test", get_fn=fake_rate_limited_get)
    assert len(entries) == 16
    assert header_count == 16
    assert calls == [1, 2, 3]  # stopped after page 3 contributed nothing new


def test_count_mismatch_message_none_when_exact_match():
    assert marham_mod.count_mismatch_message("bannu", 18, 18) is None


def test_count_mismatch_message_none_when_within_5_percent():
    # Verified real case: Peshawar extracted 499, header said 503 - a
    # 0.8% gap, Marham's own header being slightly stale, not a bug here.
    assert marham_mod.count_mismatch_message("peshawar", 499, 503) is None


def test_count_mismatch_message_none_when_header_count_missing():
    assert marham_mod.count_mismatch_message("bannu", 18, None) is None


def test_count_mismatch_message_raises_message_when_gap_is_large():
    msg = marham_mod.count_mismatch_message("bannu", 5, 18)
    assert msg is not None
    assert "bannu" in msg
    assert "5" in msg and "18" in msg


def test_fetch_district_facilities_single_page_district():
    def fake_rate_limited_get(session, url, params=None, **kwargs):
        class FakeResp:
            text = (
                '<html><body><title>1 Best Hospitals in Tank City</title>'
                '<script type="application/ld+json">'
                '[{"@type": "Hospital", "name": "Only Clinic", "url": "https://www.marham.pk/hospitals/tank-city/only", '
                '"telephone": "0000", "address": {"streetAddress": "Main Rd"}, '
                '"geo": {"latitude": 0, "longitude": 0}}]'
                "</script></body></html>"
            )

        return FakeResp()

    entries, header_count = marham_mod.fetch_district_facilities(None, "tank-city", get_fn=fake_rate_limited_get)
    assert len(entries) == 1
    assert header_count == 1
