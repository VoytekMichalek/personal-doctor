# Deployment and operations

Target: `https://health.michalek.app`, on the existing `personal-apps-01` server through Dokploy Cloud. Keep the Personal Doctor services, database and volumes separate from Personal Finance.

## Services

- PostgreSQL: `postgres:16-alpine`, database/user `doctor`, a generated password, persistent volume at `/var/lib/postgresql/data`, no external port. On the 1 GB server use `-cshared_buffers=32MB -cmax_connections=30`, a 192 MB memory limit and 64 MB reservation.
- App: versioned `ghcr.io/voytekmichalek/personal-doctor:v0.2.1`, internal HTTP port `8000`, one replica, 256 MB memory limit. Dokploy/Traefik terminates HTTPS. Do not publish a direct host port. The image runs as UID 10001 and starts migrations before accepting requests.
- Domain: `health.michalek.app`, HTTPS with Let's Encrypt, container port `8000`. Its DNS A record must point to the personal server. Keep DNS-only routing unless deliberately configuring Cloudflare proxy behavior.

The container contains the frontend, API and background importer. It needs no application filesystem volume: JSON, original activity files, checkpoints and sessions are all in PostgreSQL.

## Production environment

Set these in the Doctor application's Dokploy Environment panel, not its build arguments or the repository. URL-encode special characters in the database password.

```dotenv
APP_ENV=production
APP_ORIGIN=https://health.michalek.app
APP_TIMEZONE=Australia/Brisbane
LOCAL_AUTH_BYPASS=false
DATABASE_URL=postgresql+psycopg://doctor:URL_ENCODED_PASSWORD@INTERNAL_DATABASE_HOST:5432/doctor
OWNER_PASSWORD_HASH=scrypt:REPLACE_WITH_GENERATED_HASH
GARMIN_PERSONAL_API_KEY=REPLACE_WITH_PERSONAL_KEY
WORKER_ENABLED=true
GARMIN_REQUEST_INTERVAL_SECONDS=1
SYNC_INTERVAL_SECONDS=3600
SYNC_OVERLAP_DAYS=7
```

Generate the owner password hash locally with `.venv/bin/python -m personal_doctor password`. Store the actual password in your password manager. Only its salted scrypt hash goes into the app environment. Session cookies are Secure, HttpOnly, SameSite=Strict in production, expire after seven days, and are stored hashed in the database. Login attempts are rate limited. All data pages, JSON exports and original files require login.

Garmin bearer tokens stay in process memory and renew automatically. Replace `GARMIN_PERSONAL_API_KEY` when the personal key expires, restart/redeploy, and resume a failed import in Sync. Do not put the key in a URL, frontend JavaScript or build logs.

## Release and checks

Push code to `main`; CI tests SQLite and PostgreSQL, lint, image build and the production image smoke check. A version tag matching `personal_doctor.__version__` (for example `v0.2.1`) runs validation and publishes a versioned GHCR image. The tagged commit must belong to `main`. No Garmin or production database credentials are used by CI. GHCR package visibility/access must permit Dokploy to pull the image.

The `demo` command is local-only: its disposable sample database and simulated stream are never started by the production container. The production dashboard displays archived Garmin measurements. The v0.2.1 readability update uses a 16px minimum for interface text, responsive chart labels, and shorter, factual page copy. It adds no database migrations; v0.2.0 remains a compatible rollback image.

Set the versioned image in Dokploy and deploy. Verify `/health` and `/ready`, HTTPS, unauthenticated redirects to `/login`, successful owner login, an initial recent sync, and a restart that preserves records. Start historical backfill through Sync after the recent import works. Stop the local worker before enabling the production one to avoid unnecessary duplicate Garmin requests.

For rollback, redeploy the previous versioned image. Database migrations are additive; do not downgrade or delete the production volume. Back up before future schema changes.

## Backups and recovery

Configure Dokploy PostgreSQL backups to a private destination. A database backup contains the complete health archive, original activity files, profile, sessions and job progress. Encrypt backups, restrict access and verify a restore into an isolated database. No off-server backup is configured merely by creating a persistent Docker volume.

A manual backup from the database container is `pg_dump -U doctor -d doctor -Fc`; save output to a private file with mode 0600, never a repository artifact. Restore only to a newly created, empty database with `pg_restore --no-owner -U doctor -d doctor BACKUP`. Point a worker-disabled app at the restored database, verify record/file counts and sample exports, then enable the worker. Never restore over another application's database.

The archive uses upserts to prevent duplicate records when date ranges overlap. Raw responses retain the latest response for each requested window, not an immutable version history. Original activity files are stored once. Removing an activity in Garmin does not automatically delete the archived copy.

To invalidate all owner sessions, run `python -m personal_doctor revoke-sessions` in the application container. Changing the password hash does not by itself revoke existing sessions; revoke them explicitly when rotating a compromised password.
