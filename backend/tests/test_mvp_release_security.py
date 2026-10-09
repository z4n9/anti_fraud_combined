"""Independent acceptance of release origin, proxy and readiness boundaries."""
from fastapi.testclient import TestClient
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.main import app


LOGIN = {"test_iin": "TEST0001", "password": "Aman-Test-0001!"}


@pytest.fixture(autouse=True)
def isolated_release_environment(monkeypatch):
    for name in ("AMAN_PUBLIC_ORIGIN", "AMAN_COOKIE_SECURE", "AMAN_TRUSTED_PROXY_IPS"):
        monkeypatch.delenv(name, raising=False)


def test_https_public_origin_accepts_browser_login_through_http_proxy(client, monkeypatch):
    monkeypatch.setenv("AMAN_PUBLIC_ORIGIN", "https://contest.example")
    response = client.post("/api/auth/login", json=LOGIN,
                           headers={"Origin": "https://contest.example"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert response.headers["cache-control"] == "no-store"


def test_configured_origin_cannot_be_replaced_by_host_or_forwarded_headers(client, monkeypatch):
    monkeypatch.setenv("AMAN_PUBLIC_ORIGIN", "https://contest.example")
    before = client.get("/api/cards").json()
    response = client.post("/api/auth/logout", headers={
        "Host": "attacker.example", "Origin": "https://attacker.example",
        "X-Forwarded-Host": "attacker.example", "X-Forwarded-Proto": "https",
        "Forwarded": "for=127.0.0.1;host=attacker.example;proto=https",
    })
    assert response.status_code == 403
    assert client.get("/api/cards").json() == before
    assert client.post("/api/auth/logout", headers={
        "Origin": "https://contest.example", "Sec-Fetch-Site": "cross-site",
    }).status_code == 403


@pytest.mark.parametrize("peer,expected,secure", [
    ("10.20.30.40", 200, True),
    ("203.0.113.40", 403, False),
])
def test_forwarded_scheme_only_changes_origin_for_trusted_proxy(client, peer, expected, secure):
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="10.20.30.40")
    with TestClient(wrapped, client=(peer, 50000)) as browser:
        response = browser.post("/api/auth/login", json=LOGIN, headers={
            "Origin": "https://testserver", "X-Forwarded-Proto": "https",
        })
        assert response.status_code == expected
        if secure:
            assert "Secure" in response.headers["set-cookie"]
        else:
            accepted = browser.post("/api/auth/login", json=LOGIN, headers={
                "Origin": "http://testserver", "X-Forwarded-Proto": "https",
            })
            assert accepted.status_code == 200
            assert "Secure" not in accepted.headers["set-cookie"]


def test_readiness_requires_built_interface_and_analyst_storage(client, monkeypatch, tmp_path):
    import app.main as main

    index = tmp_path / "analyst-index.html"
    monkeypatch.setattr(main, "ANALYST_INDEX_PATH", index)
    assert client.get("/api/readiness").status_code == 503
    index.write_text("<html></html>", encoding="utf-8")
    ready = client.get("/api/readiness")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "bank": "ready", "analyst": "ready"}
    monkeypatch.setattr(app.state, "analysis_manager", None)
    assert client.get("/api/readiness").status_code == 503
    assert client.get("/api/health").status_code == 200


def test_persistent_release_storage_is_never_served_as_public_assets(client):
    for path in ("/data/aman_bank.db", "/data/analyst/semantic_dictionary.json",
                 "/data/analyst/model-registry/registry.sqlite3", "/deploy/run.py",
                 "/Dockerfile", "/compose.yaml", "/.env", "/requirements.lock"):
        assert client.get(path).status_code == 404, path


@pytest.mark.parametrize("fail_during_parse", [False, True])
def test_multipart_spools_to_selected_temp_storage_and_closes_on_failure(
    client, monkeypatch, tmp_path, fail_during_parse,
):
    """Exercise rollover with a small payload rather than allocate 350 MB in CI."""
    import errno
    import tempfile
    import starlette.formparsers as parser

    storage = tmp_path / "upload-temp"
    storage.mkdir(mode=0o700)
    monkeypatch.setattr(tempfile, "tempdir", str(storage))
    monkeypatch.setattr(parser.MultiPartParser, "spool_max_size", 32)
    opened, disk_directories = [], []
    original_spool = parser.SpooledTemporaryFile
    original_tempfile = tempfile.TemporaryFile

    def disk_file(*args, **kwargs):
        disk_directories.append(kwargs.get("dir") or tempfile.gettempdir())
        return original_tempfile(*args, **kwargs)

    def spool(*args, **kwargs):
        file = original_spool(*args, **kwargs)
        opened.append(file)
        if fail_during_parse:
            write = file.write

            def fail_after_rollover(data):
                write(data)
                raise OSError(errno.ENOSPC, "Simulated full temporary upload storage")

            file.write = fail_after_rollover
        return file

    monkeypatch.setattr(tempfile, "TemporaryFile", disk_file)
    monkeypatch.setattr(parser, "SpooledTemporaryFile", spool)
    assert client.post("/api/auth/login", json={
        "test_iin": "TEST0099", "password": "Aman-Test-0099!",
    }).status_code == 200
    monkeypatch.setattr(app.state, "max_input_bytes", 64)
    payload = b"transaction_id,transaction_amount\n" + b"T1,100\n" * 20
    response = client.post("/api/analyst/analyses?auto_run=false",
                           files={"file": ("transactions.csv", payload, "text/csv")})
    assert response.status_code == (400 if fail_during_parse else 413), response.text
    if not fail_during_parse:
        assert response.json()["error"]["code"] == "file_too_large"
    assert opened and all(file.closed for file in opened)
    assert disk_directories and all(str(directory) == str(storage) for directory in disk_directories)
    assert list(storage.iterdir()) == []
    assert list(app.state.analysis_manager.incoming_dir.iterdir()) == []
