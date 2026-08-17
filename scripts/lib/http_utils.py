"""Polite HTTP helpers: a session with a descriptive User-Agent, and a
rate-limited GET for services with usage policies (e.g. OSM Nominatim
requires >=1 second between requests)."""
import time
import requests

USER_AGENT = "KP-Healthcare-GIS-Planning/1.0 (open-data planning research)"


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def rate_limited_get(session, url, params=None, min_interval=1.1, max_retries=3):
    last_call = getattr(rate_limited_get, "_last_call", 0.0)
    wait = min_interval - (time.time() - last_call)
    if wait > 0:
        time.sleep(wait)
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, timeout=30)
            rate_limited_get._last_call = time.time()
            if resp.status_code == 200:
                return resp
            last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
        except requests.RequestException as exc:
            last_exc = exc
        time.sleep(2 ** attempt)
    rate_limited_get._last_call = time.time()
    raise RuntimeError(f"Failed to GET {url} after {max_retries} attempts") from last_exc
