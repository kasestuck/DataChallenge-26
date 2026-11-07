"""Single-origin startup serves public frontend assets without exposing the repository."""

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend import main as application_module


@pytest.fixture
def static_project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(application_module, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated.sqlite3"))
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5180")
    monkeypatch.setenv("MAX_CONCURRENCY", "4")
    monkeypatch.setenv("MAX_DATASET_BYTES", "20971520")

    def put(relative_path, content):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    put("index.html", "<html><body>integrated frontend fixture</body></html>")
    for folder, filename in (
        ("assets", "logo.svg"), ("css", "styles.css"),
        ("js", "main.js"), ("src", "i18n/ru.js"),
    ):
        put(f"{folder}/{filename}", f"public fixture: {folder}")
    for path in (
        ".env", "data/private.db", "backend/private.py",
        ".git/config", "requirements.txt", "README.md",
    ):
        put(path, "PRIVATE_STATIC_FIXTURE=not-for-http\n")
    return root, tmp_path / "isolated.sqlite3", put


def test_public_frontend_and_api_share_one_app(static_project):
    _, database, _ = static_project
    with TestClient(application_module.create_app(database)) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "text/html" in home.headers["content-type"]
        assert "integrated frontend fixture" in home.text
        for path in ("/assets/logo.svg", "/css/styles.css", "/js/main.js", "/src/i18n/ru.js"):
            asset = client.get(path)
            assert asset.status_code == 200, path
            assert asset.text.startswith("public fixture:")
        assert client.get("/api/health").json() == {"status": "ok", "database": "ok"}
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
        assert "/api/evaluations" in schema["paths"]
        assert "/" not in schema["paths"]
        response = client.options("/api/evaluations", headers={
            "Origin": "http://localhost:5180", "Access-Control-Request-Method": "POST",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5180"


@pytest.mark.parametrize("path", [
    "/.env", "/data/private.db", "/backend/private.py", "/.git/config",
    "/requirements.txt", "/README.md", "/api/not-a-real-route",
    "/assets/%2e%2e/.env", "/js/%2e%2e/backend/private.py",
    "/css/%2e%2e/data/private.db", "/src/%2e%2e/.git/config",
])
def test_private_repository_files_are_not_static(static_project, path):
    _, database, _ = static_project
    with TestClient(application_module.create_app(database)) as client:
        response = client.get(path)
        assert response.status_code == 404, path
        assert "PRIVATE_STATIC_FIXTURE" not in response.text


def test_api_still_starts_without_frontend(tmp_path, monkeypatch):
    monkeypatch.setattr(application_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("MAX_CONCURRENCY", "4")
    with TestClient(application_module.create_app(tmp_path / "api.sqlite3")) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200
        assert client.get("/docs").status_code == 200


def test_existing_frontend_directory_takes_priority(static_project):
    _, database, put = static_project
    put("frontend/index.html", "<html><body>dedicated frontend fixture</body></html>")
    put("frontend/js/main.js", "dedicated frontend asset")
    with TestClient(application_module.create_app(database)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "dedicated frontend fixture" in response.text
        assert "integrated frontend fixture" not in response.text
        assert client.get("/js/main.js").text == "dedicated frontend asset"
        assert client.get("/api/health").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/.env").status_code == 404
