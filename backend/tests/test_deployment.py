"""Deployment policy preserves CSRF checks behind an explicit public origin."""
import pytest
from app.core.deployment import deployment_settings, trusted_proxy_ips


@pytest.mark.parametrize("origin", ["https://bank.test/path", "https://user:password@bank.test", "https://*.test", "https://bank.test:99999", "https://bank.test?x=1", "ftp://bank.test"])
def test_invalid_public_origin_fails_closed(monkeypatch, origin):
    monkeypatch.setenv("AMAN_PUBLIC_ORIGIN", origin)
    with pytest.raises(ValueError):
        deployment_settings()


def test_https_origin_cannot_disable_secure_cookie(monkeypatch):
    monkeypatch.setenv("AMAN_PUBLIC_ORIGIN", "https://bank.test")
    monkeypatch.setenv("AMAN_COOKIE_SECURE", "false")
    assert deployment_settings().cookie_secure


@pytest.mark.parametrize("proxies", ["*", "proxy.example", "127.0.0.1,*", "127.0.0.1,", "0.0.0.0/0", "::/0"])
def test_trusted_proxy_rejects_wildcard_or_hostnames(monkeypatch, proxies):
    monkeypatch.setenv("AMAN_TRUSTED_PROXY_IPS", proxies)
    with pytest.raises(ValueError):
        trusted_proxy_ips()


def test_explicit_proxy_networks(monkeypatch):
    monkeypatch.setenv("AMAN_TRUSTED_PROXY_IPS", "127.0.0.1, 172.20.0.2/32, ::1")
    assert trusted_proxy_ips() == "127.0.0.1,172.20.0.2/32,::1"


def test_https_proxy_login_secure_cookie_and_exact_origin(client, monkeypatch):
    monkeypatch.setenv("AMAN_PUBLIC_ORIGIN", "https://bank.test")
    login = {"test_iin": "TEST0001", "password": "Aman-Test-0001!"}
    result = client.post("/api/auth/login", json=login, headers={"Origin": "https://bank.test"})
    assert result.status_code == 200
    assert "Secure" in result.headers["set-cookie"]
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=strict" in result.headers["set-cookie"]
    assert client.post("/api/auth/login", json=login, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/auth/login", json=login, headers={"Origin": "https://bank.test", "Sec-Fetch-Site": "cross-site"}).status_code == 403
    logout = client.post("/api/auth/logout", headers={"Origin": "https://bank.test"})
    assert logout.status_code == 200
    assert "Secure" in logout.headers["set-cookie"]


def test_local_origin_policy_preserved(client, monkeypatch):
    monkeypatch.delenv("AMAN_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("AMAN_COOKIE_SECURE", raising=False)
    payload = {"test_iin": "TEST0001", "password": "Aman-Test-0001!"}
    assert client.post("/api/auth/login", json=payload, headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/auth/login", json=payload, headers={"Origin": "http://other"}).status_code == 403


def test_readiness_requires_analyst_storage_and_build(client, monkeypatch, tmp_path):
    import app.main as main
    index = tmp_path / "index.html"
    monkeypatch.setattr(main, "ANALYST_INDEX_PATH", index)
    assert client.get("/api/readiness").status_code == 503
    index.write_text("<!doctype html>", encoding="utf-8")
    assert client.get("/api/readiness").status_code == 200
    monkeypatch.setattr(main.app.state, "analysis_manager", None)
    assert client.get("/api/readiness").status_code == 503


@pytest.fixture
def analyst_assets_client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.analyst_assets import AnalystAssets
    directory = tmp_path / "public"
    directory.mkdir()
    (directory / "index.html").write_text("<!doctype html><title>Risk Ledger</title>", encoding="utf-8")
    assets = directory / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('public');", encoding="utf-8")
    app = FastAPI()
    app.mount("/analyst", AnalystAssets(directory=directory, html=True))
    with TestClient(app) as browser:
        yield browser


@pytest.mark.parametrize("path", ["new-analysis", "bank-events", "overview", "risk-records", "transactions", "relationships", "model-quality", "data-quality", "bank-events/"])
def test_dashboard_deep_links_serve_spa(analyst_assets_client, path):
    response = analyst_assets_client.get(f"/analyst/{path}")
    assert response.status_code == 200
    assert "<title>Risk Ledger</title>" in response.text
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", ["package.json", "backend", "deploy/.env", "bank-events/private", "unknown-route", "assets/missing.js"])
def test_spa_fallback_does_not_expose_unknown_paths(analyst_assets_client, path):
    assert analyst_assets_client.get(f"/analyst/{path}").status_code == 404


def test_spa_assets_and_http_methods(analyst_assets_client):
    assert analyst_assets_client.get("/analyst/assets/app.js").status_code == 200
    assert analyst_assets_client.head("/analyst/bank-events").status_code == 200
    assert analyst_assets_client.post("/analyst/bank-events").status_code == 405
