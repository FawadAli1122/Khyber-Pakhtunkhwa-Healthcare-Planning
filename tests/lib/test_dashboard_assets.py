from scripts.lib import dashboard_assets


def test_dashboard_css_contains_key_selectors():
    for selector in ["#choropleth-svg", ".tier-filter-chip", ".dashboard-table", ".metric-toggle", "#district-search"]:
        assert selector in dashboard_assets.DASHBOARD_CSS, f"missing selector: {selector}"


def test_dashboard_js_contains_key_hooks():
    for token in [
        "DOMContentLoaded",
        "dashboard-data",
        "choropleth-svg",
        "choropleth-legend",
        "district-table",
        "district-search",
        "tier-filter-chip",
        "data-sort-key",
        "metric-toggle",
    ]:
        assert token in dashboard_assets.DASHBOARD_JS, f"missing JS hook: {token}"


def test_dashboard_js_braces_and_parens_balance():
    js = dashboard_assets.DASHBOARD_JS
    assert js.count("{") == js.count("}")
    assert js.count("(") == js.count(")")
    assert js.count("[") == js.count("]")
