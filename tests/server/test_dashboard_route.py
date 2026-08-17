from fastapi.testclient import TestClient

from server.app import create_app


def test_dashboard_route_returns_html():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "Khyber Pakhtunkhwa" in response.text


def test_dashboard_route_includes_chat_widget():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="ai-chat-toggle"' in response.text
    assert "/api/ask" in response.text


def test_dashboard_route_includes_pdf_download_link():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="pdf-download-link"' in response.text
    assert '/report.pdf' in response.text


def test_dashboard_route_includes_admin_link():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="admin-link"' in response.text
    assert 'href="/admin"' in response.text
