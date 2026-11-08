from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Float, Integer, LargeBinary, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc).timestamp()


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    dataset: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    day: Mapped[str | None] = mapped_column(String(10), index=True)
    payload: Mapped[dict | list] = mapped_column(JSON)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class ActivityFile(Base):
    __tablename__ = "activity_files"
    activity_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class RawResponse(Base):
    __tablename__ = "raw_responses"
    dataset: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload: Mapped[dict | list | None] = mapped_column(JSON)
    captured_at: Mapped[float] = mapped_column(Float, default=now)


class Job(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    kind: Mapped[str] = mapped_column(String(20))
    dedupe_key: Mapped[str | None] = mapped_column(String(20), unique=True)
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    requests_done: Mapped[int] = mapped_column(Integer, default=0)
    records_saved: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    retry_at: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    finished_at: Mapped[float | None] = mapped_column(Float)


class DatasetStatus(Base):
    __tablename__ = "dataset_status"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_checked: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str | None] = mapped_column(Text)


class Lease(Base):
    __tablename__ = "worker_leases"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    owner: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[float] = mapped_column(Float)
    cooldown_until: Mapped[float] = mapped_column(Float, default=0)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[float] = mapped_column(Float)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    successful: Mapped[bool] = mapped_column(Boolean)


def make_engine(url):
    args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=args, pool_pre_ping=True, hide_parameters=True)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def sqlite_pragmas(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


def sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)


def upsert(session, model, values, keys):
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(model).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=keys,
            set_={key: value for key, value in values.items() if key not in keys},
        )
    )
