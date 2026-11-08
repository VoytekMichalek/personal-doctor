import os
import pytest
from personal_doctor.config import Settings
from personal_doctor.db import Base, make_engine, sessions


@pytest.fixture
def engine(tmp_path):
    url = os.environ.get("DOCTOR_TEST_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    if url.startswith("postgresql") and not url.endswith("/doctor_test"):
        raise ValueError("Integration tests require the dedicated doctor_test database")
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def factory(engine):
    return sessions(engine)


@pytest.fixture
def settings():
    return Settings(worker_enabled=False, garmin_key="test-key", request_interval=0)
