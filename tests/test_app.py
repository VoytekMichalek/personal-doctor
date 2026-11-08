import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from personal_doctor.app import create_app
from personal_doctor.auth import password_hash
from personal_doctor.config import Settings
from personal_doctor.db import Job
from personal_doctor.views import overview


def test_private_pages_and_exports_require_login(engine, settings):
    with TestClient(create_app(settings, engine)) as client:
        for path in [
            "/",
            "/api/trends",
            "/api/sync",
            "/data",
            "/export/sleep?key=x",
            "/activities/1/original",
        ]:
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"] == "/login"
        assert client.get("/ready").json()["ok"] is True


def test_login_csrf_and_logout_revocation(engine, settings):
    settings.password_hash = password_hash("a sufficiently long owner password")
    with TestClient(create_app(settings, engine)) as client:
        client.get("/login")
        token = client.cookies["pd_csrf"]
        data = {"password": "a sufficiently long owner password", "csrf": token}
        assert client.post("/login", data=data).status_code == 403
        assert (
            client.post("/login", headers={"Origin": "https://attacker.example"}, data=data).status_code
            == 403
        )
        response = client.post(
            "/login", headers={"Origin": settings.origin}, data=data, follow_redirects=False
        )
        assert response.status_code == 303
        session = client.cookies["pd_session"]
        assert client.get("/").status_code == 200
        assert (
            client.post(
                "/logout", headers={"Origin": settings.origin}, data={"csrf": token}, follow_redirects=False
            ).status_code
            == 303
        )
        client.cookies.set("pd_session", session)
        assert client.get("/", follow_redirects=False).status_code == 303


def test_browser_import_form_works_and_is_idempotent(engine, settings, factory):
    settings.local_auth_bypass = True
    with TestClient(create_app(settings, engine)) as client:
        client.get("/sync")
        data = {
            "csrf": client.cookies["pd_csrf"],
            "kind": "history",
            "start": "2026-01-01",
            "end": "2026-01-03",
        }
        for _ in range(2):
            response = client.post(
                "/sync", data=data, headers={"Origin": settings.origin}, follow_redirects=False
            )
            assert response.status_code == 303
        with factory() as s:
            assert len(s.scalars(select(Job)).all()) == 1


def test_missing_readings_render_as_dashes(factory, settings):
    with factory() as s:
        result = overview(s, settings)
    assert all(m["value"] == "—" for m in result["metrics"])


def test_production_cannot_enable_bypass_or_use_sqlite():
    with pytest.raises(ValueError):
        Settings(environment="production", local_auth_bypass=True).validate()
    with pytest.raises(ValueError):
        Settings(environment="production", origin="https://doctor.example", password_hash="x").validate()


def test_all_pages_render_without_private_data(engine, settings):
    settings.local_auth_bypass = True
    with TestClient(create_app(settings, engine)) as client:
        for path in ["/", "/sync", "/data", "/activities", "/data/sleep", "/guide"]:
            response = client.get(path)
            assert response.status_code == 200
            assert settings.garmin_key not in response.text
            assert "no-store" in response.headers["cache-control"]
