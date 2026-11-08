# Personal Doctor

A private, single-owner Garmin archive and dashboard. Uses Garmin's official Personal API, PostgreSQL, and a small Python web app. The server pulls directly from Garmin; your laptop and browser do not need to stay open.

## Run locally

### Preview the performance dashboard with test data

With the Python dependencies installed, run:

```sh
.venv/bin/python -m personal_doctor demo --port 8001
```

Open http://127.0.0.1:8001. The preview includes 91 days of synthetic health records and training sessions, date navigation, four interactive chart metrics, and a pausable simulated heart-rate stream. It uses a disposable SQLite database, never loads `.env`, never connects to Garmin, and disables imports. Stop with Ctrl+C; its temporary database is removed on exit. Real records are unaffected.

### Run with your Garmin archive

Requires Python 3.12 and Docker. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
chmod 600 .env
# Put GARMIN_PERSONAL_API_KEY in .env; do not commit it.
docker compose -f compose.local.yml up -d
.venv/bin/python -m personal_doctor serve
```

Open http://127.0.0.1:8000. The example environment permits local access without a password, only while bound to loopback. Production rejects that bypass. The worker refreshes the last seven days every hour. In **Sync**, choose the historical date range to backfill. Progress survives restarts; Pause and Resume retain checkpoints. Cancelling a job retains imported records.

## Data coverage

The importer covers all 26 GET endpoints in Garmin's Personal API schema verified on 8 November 2026: daily summaries, detailed daily health, sleep summaries and stages, HRV summaries and readings, heart-rate samples, steps, stress, Body Battery, blood oxygen, weight and body composition, weight goals, hydration, food logs, Fitness Age, wellness activities, cycle data, profile settings, heart-rate zones, activity lists, activity details, workout associations, and original activity ZIP/FIT files.

Data depends on the device, settings and what was recorded. An empty or unavailable response is shown explicitly. The API currently has no dedicated Training Readiness/Status, ECG, blood pressure, hill-score or endurance-score endpoints. Some other metrics appear inside daily summaries or activity files. The archive retains complete JSON responses, including fields the dashboard does not yet visualize. Original ZIP files are retained unchanged with a SHA-256 checksum. This app does not interpret medical conditions.

- [Garmin Personal API FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq)
- [Official endpoint schemas](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/)
- [Garmin's Python example](https://github.com/garmin/connect-personal-api-python)

Requests are paced, use bounded date windows, and honor rate-limit cooldowns across jobs. A database lease prevents two workers using the same database from importing simultaneously. Run only one environment against the same Garmin account during a large backfill, since separate databases do not share that lease. Historical imports can take hours. An hourly recent refresh takes priority over older history.

## Deployment

See [docs/deployment.md](docs/deployment.md) for Dokploy settings, credentials, backups and releases. The application and worker share one container; PostgreSQL is a separate persistent service. Build on GitHub Actions, not the small production server.

## Verification

```sh
.venv/bin/ruff check personal_doctor tests migrations scripts
.venv/bin/pytest -q
# Optional: isolated PostgreSQL database, whose name must be doctor_test
DOCTOR_TEST_DATABASE_URL=postgresql+psycopg://doctor:PASSWORD@127.0.0.1:5432/doctor_test .venv/bin/pytest -q
docker build -t personal-doctor:local .
.venv/bin/python scripts/image_smoke.py
```

The image smoke check creates and removes only its own temporary containers/network. It checks real PostgreSQL migrations, readiness, authentication, non-root execution, exclusion of local secrets, and recovery after restart. Tests use synthetic data and never call Garmin. No health data, credentials, database dumps or local `.env` files belong in GitHub, CI artifacts or container images.
