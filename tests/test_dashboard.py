from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from personal_doctor.app import create_app
from personal_doctor.config import Settings
from personal_doctor.db import Job, Record
from personal_doctor.demo import seed_demo
from personal_doctor.views import overview


def demo_settings():
    return Settings(demo=True, local_auth_bypass=True, worker_enabled=False)


def test_demo_has_explorable_records_and_blocks_imports(engine, factory):
    settings = demo_settings()
    seed_demo(factory, settings)
    with TestClient(create_app(settings, engine)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "LIVE DEMO" in response.text
        assert "10,842" in response.text
        assert "7h 47m" in response.text
        for path in ["/activities", "/activities/900090", "/data", "/data/sleep", "/sync", "/guide"]:
            assert client.get(path).status_code == 200
        assert "Start historical import" not in client.get("/sync").text
        assert client.get("/static/fonts/UbuntuSans.ttf").status_code == 200
        rows = client.get("/api/trends?days=90").json()
        assert len(rows) == 90
        assert rows[-1]["sleep"] == 92
        assert rows[-1]["battery"] == 87
        today = str(settings.today())
        assert client.get(f"/export/sleep?key={today}:{today}").json()["sleepTimeSeconds"] == 28020
        csrf = client.cookies["pd_csrf"]
        assert (
            client.post(
                "/sync",
                data={"csrf": csrf, "kind": "history", "start": today, "end": today},
                headers={"Origin": settings.origin},
            ).status_code
            == 403
        )
        with factory() as session:
            job_id = session.scalar(select(Job.id))
            assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert (
            client.post(
                f"/sync/{job_id}/resume", data={"csrf": csrf}, headers={"Origin": settings.origin}
            ).status_code
            == 403
        )


def test_date_selection_preserves_gaps_and_does_not_fall_back(factory, settings):
    today = settings.today()
    old = str(today - timedelta(days=2))
    with factory.begin() as session:
        session.add(
            Record(
                dataset="daily_summaries", key=old, day=old, payload={"movement": {"steps": {"value": 4321}}}
            )
        )
        session.add(
            Record(
                dataset="sleep",
                key=str(today),
                day=str(today),
                payload={"sleepScores": {"overall": {"value": 80}}, "sleepTimeSeconds": 25200},
            )
        )
    with factory() as session:
        result = overview(session, settings, 7, today)
        assert result["selected"] == str(today)
        assert result["gauges"][2]["value"] == "—"
        assert result["gauges"][1]["value"] == "80"
        assert len(result["trends"]) == 7
        assert result["trends"][-3]["steps"] == 4321
        assert result["trends"][-2]["steps"] is None
        assert result["trends"][-1]["sleep"] == 80
        assert overview(session, settings)["selected"] == old


def test_demo_does_not_load_real_environment(monkeypatch):
    from personal_doctor import __main__, demo

    ports = []
    monkeypatch.setattr("sys.argv", ["personal_doctor", "demo", "--port", "8123"])
    monkeypatch.setattr(demo, "run_demo", ports.append)
    monkeypatch.setattr(__main__, "load_settings", lambda: pytest.fail("Demo must never load .env"))
    __main__.main()
    assert ports == [8123]


@pytest.mark.parametrize(
    "overrides",
    [
        dict(worker_enabled=True),
        dict(garmin_key="not-a-real-key"),
        dict(local_auth_bypass=False),
        dict(environment="production"),
    ],
)
def test_demo_configuration_cannot_contact_garmin_or_run_in_production(overrides):
    settings = demo_settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    with pytest.raises(ValueError, match="Demo mode"):
        settings.validate()


def test_real_dashboard_never_shows_a_simulated_live_stream(engine, settings):
    settings.local_auth_bypass = True
    with TestClient(create_app(settings, engine)) as client:
        page = client.get("/").text
        assert "LIVE DEMO" not in page
        assert "data-stream-toggle" not in page
        assert 'data-demo="false"' in page
        assert client.get("/?on=0001-01-01").status_code == 422
        assert client.get("/api/trends?on=0001-01-01").status_code == 422
