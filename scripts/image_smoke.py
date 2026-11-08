"""Exercise the real image against a disposable Postgres container, without personal credentials."""

import os
import secrets
import subprocess
import time
import urllib.request

IMAGE = os.environ.get("DOCTOR_TEST_IMAGE", "personal-doctor:local")
SUFFIX = secrets.token_hex(4)
NETWORK, DB, APP = (f"doctor-smoke-{kind}-{SUFFIX}" for kind in ("net", "db", "app"))


def docker(*args, check=True):
    return subprocess.run(["docker", *args], text=True, capture_output=True, check=check).stdout.strip()


try:
    docker("network", "create", NETWORK)
    docker(
        "run",
        "-d",
        "--name",
        DB,
        "--network",
        NETWORK,
        "--network-alias",
        "database",
        "--tmpfs",
        "/var/lib/postgresql/data",
        "-e",
        "POSTGRES_PASSWORD=smoke-only",
        "-e",
        "POSTGRES_USER=doctor",
        "-e",
        "POSTGRES_DB=doctor_test",
        "postgres:16-alpine",
    )
    for _ in range(30):
        if (
            subprocess.run(
                ["docker", "exec", DB, "pg_isready", "-U", "doctor"], capture_output=True
            ).returncode
            == 0
        ):
            break
        time.sleep(1)
    docker(
        "run",
        "-d",
        "--name",
        APP,
        "--network",
        NETWORK,
        "-p",
        "127.0.0.1::8000",
        "-e",
        "APP_ENV=production",
        "-e",
        "APP_ORIGIN=https://doctor.invalid",
        "-e",
        "WORKER_ENABLED=false",
        "-e",
        "OWNER_PASSWORD_HASH=scrypt:00000000000000000000000000000000:" + "0" * 128,
        "-e",
        "DATABASE_URL=postgresql+psycopg://doctor:smoke-only@database/doctor_test",
        IMAGE,
    )
    port = docker("port", APP, "8000/tcp").split(":")[-1]
    for _ in range(45):
        try:
            response = urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=2)
            if response.status == 200:
                break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError("Image did not become ready")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Host": "doctor.invalid"})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    try:
        urllib.request.build_opener(NoRedirect).open(request)
        raise RuntimeError("Unauthenticated dashboard was exposed")
    except urllib.error.HTTPError as error:
        assert error.code == 303
        assert error.headers["location"] == "/login"
    assert docker("exec", APP, "id", "-u") == "10001"
    docker(
        "exec",
        APP,
        "python",
        "-c",
        "from pathlib import Path; assert not Path('/app/.env').exists(); assert not Path('/app/.secrets').exists()",
    )
    docker("restart", APP)
    # Docker can allocate a different ephemeral host port after a restart.
    port = docker("port", APP, "8000/tcp").split(":")[-1]
    for _ in range(30):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError("Image did not recover after restart")
    print(
        "Image smoke passed: migrations, readiness, private routes, non-root runtime, secret exclusion, restart."
    )
finally:
    docker("rm", "-f", APP, DB, check=False)
    docker("network", "rm", NETWORK, check=False)
