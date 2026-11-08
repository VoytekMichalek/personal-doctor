import argparse
import getpass
import logging
import signal
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from .auth import password_hash
from .config import load_settings
from .db import LoginSession, make_engine, sessions
from .sync import Worker, enqueue


def migrate():
    command.upgrade(Config("alembic.ini"), "head")


def main():
    parser = argparse.ArgumentParser(description="Private Garmin archive")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("worker")
    sub.add_parser("password", help="Print an owner password hash; the password is entered invisibly")
    sub.add_parser("revoke-sessions")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    demo = sub.add_parser("demo", help="Run an isolated dashboard with synthetic data and no Garmin connection")
    demo.add_argument("--port", type=int, default=8001)
    sync = sub.add_parser("sync")
    sync.add_argument("--start", required=True)
    sync.add_argument("--end", required=True)
    sync.add_argument("--kind", choices=["history", "recent"], default="history")
    args = parser.parse_args()
    if args.command == "demo":
        from .demo import run_demo

        run_demo(args.port)
        return
    if args.command == "password":
        password = getpass.getpass("New owner password (at least 16 characters): ")
        if len(password) < 16 or password != getpass.getpass("Confirm password: "):
            sys.exit("Use at least 16 characters and matching entries.")
        print(password_hash(password))
        return
    settings = load_settings()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "migrate":
        migrate()
    elif args.command == "serve":
        if settings.local_auth_bypass and args.host not in ("127.0.0.1", "::1"):
            sys.exit("Local auth bypass must bind to a loopback address.")
        migrate()
        import uvicorn
        from .app import create_app

        uvicorn.run(
            create_app(settings),
            host=args.host,
            port=args.port,
            access_log=False,
            proxy_headers=settings.production,
            forwarded_allow_ips="127.0.0.1",
        )
    else:
        factory = sessions(make_engine(settings.database_url))
        if args.command == "sync":
            print("Queued import:", enqueue(factory, args.kind, args.start, args.end))
        elif args.command == "revoke-sessions":
            with factory.begin() as session:
                session.execute(delete(LoginSession))
            print("All owner sessions revoked.")
        elif args.command == "worker":
            migrate()
            worker = Worker(factory, settings)
            signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
            signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
            worker.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never emit database connection URLs or SQL parameter values at the CLI boundary.
        sys.exit(
            "Startup failed. Check environment values and database availability; no secret details were logged."
        )
