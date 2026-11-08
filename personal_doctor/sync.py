"""One bounded request per durable checkpoint. Run in-process or as a dedicated worker."""

import hashlib
import io
import logging
import threading
import zipfile
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from .catalog import DATASETS
from .db import ActivityFile, DatasetStatus, Job, Lease, RawResponse, Record, now, upsert
from .garmin import GarminClient, GarminError
from .storage import save_response

log = logging.getLogger(__name__)
ACTIVE = ("queued", "running", "waiting")


def enqueue(factory, kind, start, end):
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if kind not in ("recent", "history") or start > end or (end - start).days > 365 * 30:
        raise ValueError("Choose a valid date range of at most 30 years")
    with factory() as session:
        existing = session.scalar(select(Job).where(Job.dedupe_key == kind))
        if existing:
            return existing.id
        job = Job(kind=kind, dedupe_key=kind, start_date=str(start), end_date=str(end), cursor={})
        session.add(job)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return session.scalar(select(Job.id).where(Job.dedupe_key == kind))
        return job.id


def job_action(factory, job_id, action):
    with factory.begin() as session:
        job = session.get(Job, job_id)
        if not job:
            raise ValueError("Import not found")
        if action == "pause" and job.status in ACTIVE:
            job.status = "paused"
        elif action == "resume" and job.status in ("paused", "failed", "waiting"):
            job.status, job.error, job.retry_at = "queued", None, 0
        elif action == "cancel" and job.status not in ("complete", "cancelled"):
            job.status, job.dedupe_key = "cancelled", None
        else:
            raise ValueError("This import cannot perform that action in its current state")
        job.updated_at = now()


class Worker:
    def __init__(self, factory, settings, client=None):
        self.factory, self.settings = factory, settings
        self.client = client or GarminClient(settings.garmin_key, settings.request_interval)
        self.owner = str(uuid4())
        self.stop = threading.Event()
        self.thread = None
        self.last_schedule = 0

    def acquire(self):
        with self.factory.begin() as s:
            dialect = s.bind.dialect.name
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            s.execute(
                insert(Lease)
                .values(name="garmin", owner=self.owner, expires_at=0)
                .on_conflict_do_nothing(index_elements=["name"])
            )
            result = s.execute(
                update(Lease)
                .where(Lease.name == "garmin", or_(Lease.owner == self.owner, Lease.expires_at < now()))
                .values(owner=self.owner, expires_at=now() + 180)
            )
            return result.rowcount == 1

    def schedule(self):
        if not self.settings.garmin_key or now() - self.last_schedule < 30:
            return
        self.last_schedule = now()
        with self.factory() as s:
            last = s.scalar(select(Job).where(Job.kind == "recent").order_by(Job.created_at.desc()).limit(1))
            if last and (last.dedupe_key or now() - last.created_at < self.settings.sync_interval):
                return
        end = self.settings.today()
        enqueue(self.factory, "recent", end - timedelta(days=self.settings.recent_days - 1), end)

    def tick(self, schedule=True):
        if not self.acquire():
            return False
        with self.factory() as s:
            if s.get(Lease, "garmin").cooldown_until > now():
                return False
        if schedule:
            self.schedule()
        with self.factory() as s:
            # Recent refreshes can progress while a long historical import is waiting.
            job = s.scalar(
                select(Job)
                .where(Job.status.in_(ACTIVE), Job.retry_at <= now())
                .order_by((Job.kind == "recent").desc(), Job.created_at)
                .limit(1)
            )
            if not job:
                return False
            job_id, cursor = job.id, dict(job.cursor)
            s.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_(ACTIVE))
                .values(status="running", updated_at=now())
            )
            s.commit()
        try:
            saved, next_cursor, complete = self.step(job, cursor)
            with self.factory.begin() as s:
                current = s.get(Job, job_id)
                current.cursor = next_cursor
                current.requests_done += 1
                current.records_saved += saved
                current.updated_at = now()
                current.error, current.retry_at = None, 0
                # A pause/cancel made during the request takes effect after this checkpoint.
                if current.status in ACTIVE:
                    current.status = "complete" if complete else "running"
                    if complete:
                        current.finished_at, current.dedupe_key = now(), None
            return True
        except GarminError as error:
            with self.factory.begin() as s:
                current = s.get(Job, job_id)
                if error.status == 429:
                    s.get(Lease, "garmin").cooldown_until = now() + error.retry_after
                if current.status in ACTIVE:
                    current.status = "waiting" if error.retry_after else "failed"
                    current.retry_at = now() + error.retry_after
                    current.error = str(error)
                    current.updated_at = now()
            return False
        except Exception:
            # Database exceptions can embed record content; do not log exception details.
            log.error("sync_step_failed")
            with self.factory.begin() as s:
                current = s.get(Job, job_id)
                if current.status in ACTIVE:
                    current.status = "failed"
                    current.error = "Import stopped at a saved checkpoint. Check server logs, then Resume."
                    current.updated_at = now()
            return False

    def step(self, job, cursor):
        phase = cursor.get("phase", "datasets")
        if phase == "datasets":
            return self.dataset_step(job, cursor)
        return self.activity_step(job, cursor)

    def dataset_step(self, job, cursor):
        index = cursor.get("dataset", 0)
        if index >= len(DATASETS):
            return 0, {"phase": "activities", "offset": 0}, False
        dataset = DATASETS[index]
        end = date.fromisoformat(cursor.get("end", job.end_date))
        start = max(date.fromisoformat(job.start_date), end - timedelta(days=dataset.window - 1))
        path, params = dataset.request(str(start), str(end), str(self.settings.today()))
        try:
            data = self.client.get(path, params)
        except GarminError as error:
            # Unavailable optional datasets are visible, never silently treated as imported.
            if error.status not in (403, 404):
                raise
            with self.factory.begin() as s:
                upsert(
                    s,
                    DatasetStatus,
                    dict(
                        name=dataset.name,
                        last_checked=now(),
                        status="unavailable",
                        message=f"Garmin returned HTTP {error.status} for the requested window.",
                    ),
                    ["name"],
                )
            if error.status == 403 or dataset.mode == "once":
                return 0, {"phase": "datasets", "dataset": index + 1}, False
            # A missing day is not evidence that the entire dataset is unavailable.
            if str(start) == job.start_date:
                return 0, {"phase": "datasets", "dataset": index + 1}, False
            return 0, {"phase": "datasets", "dataset": index, "end": str(start - timedelta(days=1))}, False
        with self.factory.begin() as s:
            saved = save_response(s, dataset, str(start), str(end), data)
        if dataset.mode == "once" or str(start) == job.start_date:
            return saved, {"phase": "datasets", "dataset": index + 1}, False
        return saved, {"phase": "datasets", "dataset": index, "end": str(start - timedelta(days=1))}, False

    def activity_step(self, job, cursor):
        if cursor["phase"] == "activities":
            offset = cursor.get("offset", 0)
            data = self.client.get(
                "/activitylist-service/activities/search/activities",
                {
                    "start": offset,
                    "limit": 50,
                    "sortOrder": "asc",
                    "sortBy": "startLocal",
                    "startDate": job.start_date,
                    "endDate": job.end_date,
                    "excludeChildren": "false",
                },
            )
            if not isinstance(data, list):
                raise ValueError("Unexpected activity page")
            with self.factory.begin() as s:
                upsert(
                    s,
                    RawResponse,
                    dict(
                        dataset="activities",
                        request_key=f"{job.start_date}:{job.end_date}:{offset}",
                        payload=data,
                        captured_at=now(),
                    ),
                    ["dataset", "request_key"],
                )
                for item in data:
                    upsert(
                        s,
                        Record,
                        dict(
                            dataset="activities",
                            key=str(item["activityId"]),
                            day=item.get("startTimeLocal", "")[:10] or None,
                            payload=item,
                            updated_at=now(),
                        ),
                        ["dataset", "key"],
                    )
                upsert(
                    s,
                    DatasetStatus,
                    dict(
                        name="activities",
                        last_checked=now(),
                        status="available" if data else "empty",
                        message=None,
                    ),
                    ["name"],
                )
            return (
                len(data),
                {
                    "phase": "activity",
                    "ids": [str(a["activityId"]) for a in data],
                    "index": 0,
                    "sub": 0,
                    "offset": offset + len(data),
                    "last_page": len(data) < 50,
                },
                not data,
            )
        ids, index, sub = cursor["ids"], cursor.get("index", 0), cursor.get("sub", 0)
        if index >= len(ids):
            return 0, {"phase": "activities", "offset": cursor["offset"]}, cursor["last_page"]
        activity_id = str(int(ids[index]))
        name = ("activity_details", "activity_workouts", "activity_files")[sub]
        if sub == 2:
            with self.factory() as s:
                exists = s.get(ActivityFile, activity_id) is not None
            if not exists:
                try:
                    blob = self.client.get(f"/download-service/files/activity/{activity_id}", binary=True)
                    if blob:
                        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                            if not archive.infolist():
                                raise GarminError("Garmin returned an empty activity archive.")
                        with self.factory.begin() as s:
                            upsert(
                                s,
                                ActivityFile,
                                dict(
                                    activity_id=activity_id,
                                    content=blob,
                                    sha256=hashlib.sha256(blob).hexdigest(),
                                    updated_at=now(),
                                ),
                                ["activity_id"],
                            )
                except GarminError as error:
                    if error.status not in (403, 404):
                        raise
                    with self.factory.begin() as s:
                        upsert(
                            s,
                            DatasetStatus,
                            dict(
                                name=name,
                                last_checked=now(),
                                status="unavailable",
                                message="Some activities do not have a downloadable original file.",
                            ),
                            ["name"],
                        )
        else:
            path = f"/activity-service/activity/{activity_id}" + ("/workouts" if sub == 1 else "")
            try:
                data = self.client.get(path)
            except GarminError as error:
                if error.status not in (403, 404):
                    raise
                # A deleted/manual activity or missing workout must not block older history.
                with self.factory.begin() as s:
                    upsert(
                        s,
                        DatasetStatus,
                        dict(
                            name=name,
                            last_checked=now(),
                            status="unavailable",
                            message=f"Some activities returned HTTP {error.status}.",
                        ),
                        ["name"],
                    )
                next_cursor = cursor | {"sub": sub + 1}
                return 0, next_cursor, False
            with self.factory.begin() as s:
                upsert(
                    s,
                    RawResponse,
                    dict(dataset=name, request_key=activity_id, payload=data, captured_at=now()),
                    ["dataset", "request_key"],
                )
                if data:
                    original = s.get(Record, ("activities", activity_id))
                    upsert(
                        s,
                        Record,
                        dict(
                            dataset=name,
                            key=activity_id,
                            day=original.day if original else None,
                            payload=data,
                            updated_at=now(),
                        ),
                        ["dataset", "key"],
                    )
        next_cursor = cursor | {"sub": (sub + 1) % 3, "index": index + (1 if sub == 2 else 0)}
        return 1, next_cursor, False

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    did_work = self.tick()
                except Exception:
                    log.error("worker_unavailable")
                    did_work = False
                if not did_work:
                    self.stop.wait(3)
        finally:
            with self.factory.begin() as s:
                s.execute(delete(Lease).where(Lease.owner == self.owner))
            self.client.close()

    def start(self):
        self.thread = threading.Thread(target=self.run, name="garmin-sync", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=50)
