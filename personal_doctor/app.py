import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select

from . import __version__
from .auth import authenticated, login, token_hash
from .catalog import LABELS
from .config import load_settings
from .db import ActivityFile, LoginSession, RawResponse, Record, make_engine, sessions
from .sync import Worker, enqueue, job_action
from .views import coverage, import_status, number, overview

ROOT = Path(__file__).parent
log = logging.getLogger(__name__)


def create_app(settings=None, engine=None):
    settings = (settings or load_settings()).validate()
    engine = engine or make_engine(settings.database_url)
    factory = sessions(engine)
    worker = Worker(factory, settings)
    templates = Jinja2Templates(directory=ROOT / "templates")
    templates.env.filters["number"] = number
    templates.env.filters["clock"] = lambda ts: (
        datetime.fromtimestamp(ts, ZoneInfo(settings.timezone)).strftime("%d %b, %H:%M") if ts else "Not yet"
    )

    @asynccontextmanager
    async def lifespan(app):
        # Migrations run before the process starts; fail readiness on an unprepared database.
        with engine.connect() as connection:
            connection.execute(select(Record.dataset).limit(1))
        if settings.worker_enabled:
            worker.start()
        yield
        worker.shutdown()
        engine.dispose()

    app = FastAPI(title="Personal Doctor", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.factory, app.state.settings = factory, settings
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def render(request, name, **context):
        return templates.TemplateResponse(
            request=request,
            name=name,
            context={
                "csrf": request.state.csrf,
                "local": settings.local_auth_bypass,
                "demo": settings.demo,
                "timezone": settings.timezone,
                "version": __version__,
                "today": str(settings.today()),
                "page": name.removesuffix(".html"),
                "garmin_configured": bool(settings.garmin_key),
                **context,
            },
        )

    @app.middleware("http")
    async def security(request, call_next):
        expected = urlparse(settings.origin)
        allowed_hosts = {expected.hostname}
        if request.url.path in ("/health", "/ready"):
            allowed_hosts |= {"127.0.0.1", "localhost"}
        if not settings.production:
            allowed_hosts |= {"127.0.0.1", "localhost", "testserver"}
        if request.url.hostname not in allowed_hosts:
            return Response("Unrecognised host", status_code=400)
        request.state.csrf = request.cookies.get("pd_csrf") or secrets.token_urlsafe(32)
        public = request.url.path in (
            "/login",
            "/health",
            "/ready",
            "/robots.txt",
        ) or request.url.path.startswith("/static/")
        if not public and not authenticated(request, factory, settings):
            return RedirectResponse("/login", status_code=303)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("origin") != settings.origin:
                return Response(
                    "Request origin did not match. Reload the page and try again.", status_code=403
                )
            if int(request.headers.get("content-length", "0")) > 16384:
                return Response("Request too large", status_code=413)
            form = await request.form()
            supplied = str(form.get("csrf", ""))
            if not supplied or not hmac.compare_digest(supplied, request.cookies.get("pd_csrf", "")):
                return Response("This form expired. Reload the page and try again.", status_code=403)
            # Save parsed data; BaseHTTPMiddleware replays the body, not its parsed form cache.
            request.state.form = dict(form)
        try:
            response = await call_next(request)
        except Exception:
            log.error("request_failed")
            response = Response(
                "Something went wrong. Reload this page to retry; your saved data is safe.", status_code=500
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if settings.production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if not request.cookies.get("pd_csrf"):
            response.set_cookie(
                "pd_csrf", request.state.csrf, httponly=True, secure=settings.production, samesite="strict"
            )
        return response

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(select(Record.dataset).limit(1))
            return {"ok": True, "version": __version__}
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)

    @app.get("/robots.txt")
    def robots():
        return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")

    @app.get("/login")
    def login_page(request: Request):
        return render(request, "login.html", error=None)

    @app.post("/login")
    def login_submit(request: Request):
        token, error = login(factory, settings, str(request.state.form.get("password", "")))
        if error:
            return render(request, "login.html", error=error)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            "pd_session", token, max_age=604800, secure=settings.production, httponly=True, samesite="strict"
        )
        return response

    @app.post("/logout")
    def logout(request: Request):
        with factory.begin() as s:
            s.execute(
                delete(LoginSession).where(
                    LoginSession.token_hash == token_hash(request.cookies.get("pd_session", ""))
                )
            )
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("pd_session")
        return response

    @app.get("/")
    def dashboard(request: Request, days: int = 7, on: date | None = None):
        if on and on.year < 1970:
            raise HTTPException(422, "Select a date from 1970 onwards")
        days = days if days in (7, 30, 90, 365) else 30
        with factory() as s:
            return render(request, "dashboard.html", **overview(s, settings, days, on), jobs=import_status(s))

    @app.get("/api/trends")
    def trends(days: int = 30, on: date | None = None):
        if on and on.year < 1970:
            raise HTTPException(422, "Select a date from 1970 onwards")
        with factory() as s:
            return overview(s, settings, min(365, max(1, days)), on)["trends"]

    @app.get("/activities")
    def activities(request: Request, page: int = 1):
        page = min(100000, max(1, page))
        with factory() as s:
            rows = s.scalars(
                select(Record)
                .where(Record.dataset == "activities")
                .order_by(Record.day.desc(), Record.key.desc())
                .offset((page - 1) * 30)
                .limit(31)
            ).all()
            return render(
                request, "activities.html", activities=rows[:30], next_page=len(rows) > 30, current=page
            )

    @app.get("/activities/{activity_id}")
    def activity(request: Request, activity_id: int):
        with factory() as s:
            row = s.get(Record, ("activities", str(activity_id)))
            if not row:
                raise HTTPException(
                    404, "Activity not imported yet. Return to Activities and refresh after syncing."
                )
            return render(
                request,
                "activity.html",
                activity=row,
                has_file=s.get(ActivityFile, str(activity_id)) is not None,
                detail=s.get(Record, ("activity_details", str(activity_id))),
            )

    @app.get("/activities/{activity_id}/original")
    def original(activity_id: int):
        with factory() as s:
            file = s.get(ActivityFile, str(activity_id))
            if not file:
                raise HTTPException(404, "Original file not imported yet. Resume the import and retry.")
            return Response(
                file.content,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="activity-{activity_id}.zip"'},
            )

    @app.get("/data")
    def datasets(request: Request):
        with factory() as s:
            return render(request, "data.html", datasets=coverage(s))

    @app.get("/data/{dataset}")
    def dataset_records(request: Request, dataset: str, page: int = 1):
        if dataset not in LABELS:
            raise HTTPException(404)
        page = min(100000, max(1, page))
        with factory() as s:
            rows = s.scalars(
                select(RawResponse)
                .where(RawResponse.dataset == dataset)
                .order_by(RawResponse.request_key.desc())
                .offset((page - 1) * 30)
                .limit(31)
            ).all()
            return render(
                request,
                "records.html",
                dataset=dataset,
                label=LABELS[dataset],
                records=rows[:30],
                next_page=len(rows) > 30,
                current=page,
            )

    @app.get("/export/{dataset}")
    def export(dataset: str, key: str):
        if dataset not in LABELS or len(key) > 120:
            raise HTTPException(404)
        with factory() as s:
            row = s.get(RawResponse, (dataset, key))
            if not row:
                raise HTTPException(404)
            return JSONResponse(
                row.payload, headers={"Content-Disposition": f'attachment; filename="{dataset}.json"'}
            )

    @app.get("/sync")
    def sync_page(request: Request):
        with factory() as s:
            return render(
                request,
                "sync.html",
                jobs=import_status(s),
                error=None,
                first_date=s.scalar(select(func.min(Record.day))) or "2020-03-01",
            )

    @app.post("/sync")
    def sync_submit(request: Request):
        if settings.demo:
            raise HTTPException(403, "Demo mode uses synthetic records. Garmin imports are disabled.")
        form = request.state.form
        try:
            end = date.fromisoformat(form.get("end", ""))
            if end > settings.today():
                raise ValueError("The end date cannot be in the future")
            enqueue(factory, form.get("kind", "history"), form.get("start", ""), end)
        except ValueError as error:
            with factory() as s:
                return render(
                    request,
                    "sync.html",
                    jobs=import_status(s),
                    error=str(error),
                    first_date=form.get("start", "2020-03-01"),
                )
        return RedirectResponse("/sync", status_code=303)

    @app.post("/sync/{job_id}/{action}")
    def action(request: Request, job_id: str, action: str):
        if settings.demo:
            raise HTTPException(403, "Demo mode uses synthetic records. Garmin imports are disabled.")
        try:
            job_action(factory, job_id, action)
        except ValueError as error:
            with factory() as s:
                return render(
                    request, "sync.html", jobs=import_status(s), error=str(error), first_date="2020-03-01"
                )
        return RedirectResponse("/sync", status_code=303)

    @app.get("/api/sync")
    def sync_status():
        with factory() as s:
            return import_status(s)

    @app.get("/guide")
    def guide(request: Request):
        return render(request, "guide.html")

    return app
