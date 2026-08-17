"""Scrape the KP Health Care Commission's public 'Licensed Health Care
Establishment' registry (https://hcc.kp.gov.pk/licensed-hces/), which is
plain server-rendered HTML paginated via a `?page=N` query param (confirmed
during design: ~28 pages / ~280 records, no JS/API needed). Writes
data/raw/kphcc_facilities.json.

Note: as of the design pass, this registry has zero entries for several
newly-merged tribal districts (Bannu, D.I. Khan, Kurram, Waziristan,
Orakzai, Tank, etc.) — that's a genuine coverage gap in KP's licensing
rollout, not a scraper bug. Do not treat an empty result for those
districts as an error.
"""
import json
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.lib.districts import normalize_district
from scripts.lib.http_utils import make_session

BASE_URL = "https://hcc.kp.gov.pk/licensed-hces/"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
REQUEST_DELAY_SECONDS = 1.0


def parse_beds(text):
    text = text.strip()
    if not text or not text.isdigit():
        return None
    return int(text)


def parse_table(html):
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table tr")
    records = []
    for row in rows:
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) != 9:
            continue  # header row or malformed row
        licence_no, issue_date, expire_date, category, pub_priv, name, address, district, beds = cells
        records.append(
            {
                "licence_no": licence_no,
                "issue_date": issue_date,
                "expire_date": expire_date,
                "category": category,
                "public_private": pub_priv,
                "name": name,
                "address": address,
                "district": normalize_district(district),
                "beds": parse_beds(beds),
            }
        )
    return records


def get_total_pages(html):
    soup = BeautifulSoup(html, "lxml")
    page_links = soup.select("a, button")
    numbers = [int(a.get_text(strip=True)) for a in page_links if a.get_text(strip=True).isdigit()]
    return max(numbers) if numbers else 1


def fetch_all():
    session = make_session()
    resp = session.get(BASE_URL, params={"search": "", "district": "", "category": "", "date": ""}, timeout=30)
    resp.raise_for_status()
    total_pages = get_total_pages(resp.text)
    all_records = parse_table(resp.text)
    print(f"Page 1/{total_pages}: {len(all_records)} records")

    # Pagination is path-based (WordPress convention), NOT a ?page=N query
    # param — confirmed by comparing licence numbers returned by each: a
    # ?page=N request silently ignores the param and re-serves page 1.
    for page in range(2, total_pages + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        resp = session.get(f"{BASE_URL}page/{page}/", timeout=30)
        resp.raise_for_status()
        page_records = parse_table(resp.text)
        print(f"Page {page}/{total_pages}: {len(page_records)} records")
        all_records.extend(page_records)

    return all_records


def dedupe_by_licence(records):
    seen = set()
    deduped = []
    for r in records:
        key = r["licence_no"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def main():
    records = dedupe_by_licence(fetch_all())
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "kphcc_facilities.json").write_text(json.dumps(records, indent=2))
    print(f"Wrote {len(records)} KPHCC facility records")


if __name__ == "__main__":
    main()
