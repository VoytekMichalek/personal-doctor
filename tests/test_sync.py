import io
import zipfile

from sqlalchemy import func, select

from personal_doctor.catalog import DATASETS
from personal_doctor.db import ActivityFile, DatasetStatus, Job, Lease, RawResponse, Record, now
from personal_doctor.garmin import GarminError
from personal_doctor.storage import save_response
from personal_doctor.sync import Worker, enqueue, job_action


class FakeGarmin:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, path, params=None, binary=False):
        self.calls.append((path, params))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        pass


def release(factory, owner):
    with factory.begin() as s:
        lease = s.get(Lease, "garmin")
        lease.expires_at = 0


def test_upsert_preserves_original_response_and_updates_without_duplicates(factory):
    response = {
        "data": [{"calendarDate": "2026-11-01", "movement": {"steps": {"value": 123}}, "newGarminField": 9}]
    }
    with factory.begin() as s:
        save_response(s, DATASETS[0], "2026-11-01", "2026-11-01", response)
    response["data"][0]["movement"]["steps"]["value"] = 456
    with factory.begin() as s:
        save_response(s, DATASETS[0], "2026-11-01", "2026-11-01", response)
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(Record)) == 1
        assert s.get(Record, ("daily_summaries", "2026-11-01")).payload["movement"]["steps"]["value"] == 456
        assert s.get(RawResponse, ("daily_summaries", "2026-11-01:2026-11-01")).payload == response


def test_windows_resume_and_single_worker_lease(factory, settings):
    job_id = enqueue(factory, "history", "2026-09-01", "2026-11-01")
    assert enqueue(factory, "history", "2020-01-01", "2026-11-01") == job_id
    first = Worker(factory, settings, FakeGarmin([{"data": []}]))
    assert first.tick(schedule=False)
    with factory() as s:
        assert s.get(Job, job_id).cursor["end"] == "2026-10-04"
    second = Worker(factory, settings, FakeGarmin([{"data": []}]))
    assert not second.tick(schedule=False)
    release(factory, first.owner)
    assert second.tick(schedule=False)
    assert second.client.calls[0][1] == {"startDate": "2026-09-07", "endDate": "2026-10-04"}
    job_action(factory, job_id, "pause")
    assert not second.tick(schedule=False)
    job_action(factory, job_id, "resume")
    with factory() as s:
        assert s.get(Job, job_id).cursor["end"] == "2026-09-06"


def test_429_preserves_checkpoint_and_blocks_other_jobs(factory, settings):
    job_id = enqueue(factory, "history", "2026-11-01", "2026-11-01")
    worker = Worker(factory, settings, FakeGarmin([GarminError("Waiting", status=429, retry_after=120)]))
    assert not worker.tick(schedule=False)
    enqueue(factory, "recent", "2026-11-01", "2026-11-01")
    assert not worker.tick(schedule=False)
    with factory() as s:
        job = s.get(Job, job_id)
        assert job.status == "waiting"
        assert job.cursor == {}
        assert s.get(Lease, "garmin").cooldown_until > now() + 100
    assert len(worker.client.calls) == 1


def test_missing_day_continues_older_dates(factory, settings):
    job_id = enqueue(factory, "history", "2026-11-01", "2026-11-03")
    index = next(i for i, d in enumerate(DATASETS) if d.name == "hrv")
    with factory.begin() as s:
        s.get(Job, job_id).cursor = {"phase": "datasets", "dataset": index, "end": "2026-11-03"}
    worker = Worker(factory, settings, FakeGarmin([GarminError("Missing day", status=404)]))
    assert worker.tick(schedule=False)
    with factory() as s:
        assert s.get(Job, job_id).cursor["end"] == "2026-11-02"
        assert s.get(Job, job_id).cursor["dataset"] == index


def test_empty_response_is_not_a_sensor_record(factory):
    hydration = next(d for d in DATASETS if d.name == "hydration")
    with factory.begin() as s:
        assert save_response(s, hydration, "2026-11-01", "2026-11-03", []) == 0
    with factory() as s:
        assert s.get(DatasetStatus, "hydration").status == "empty"
        assert s.scalar(select(func.count()).select_from(Record)) == 0
        assert s.scalar(select(func.count()).select_from(RawResponse)) == 1


def test_activity_download_and_resume_after_page(factory, settings):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("activity.fit", b"fit data")
    client = FakeGarmin(
        [
            [{"activityId": 123, "startTimeLocal": "2026-11-01 08:00:00"}],
            {"activityId": 123, "summaryDTO": {"distance": 100}},
            [],
            archive.getvalue(),
        ]
    )
    job_id = enqueue(factory, "history", "2026-11-01", "2026-11-01")
    with factory.begin() as s:
        s.get(Job, job_id).cursor = {"phase": "activities", "offset": 0}
    worker = Worker(factory, settings, client)
    for _ in range(5):
        assert worker.tick(schedule=False)
    with factory() as s:
        assert s.get(Job, job_id).status == "complete"
        assert s.get(ActivityFile, "123").content == archive.getvalue()
        assert s.get(Record, ("activities", "123")).day == "2026-11-01"
        assert s.get(Job, job_id).dedupe_key is None
    assert client.calls[0][1]["excludeChildren"] == "false"


def test_missing_activity_details_do_not_block_archive(factory, settings):
    job_id = enqueue(factory, "history", "2026-11-01", "2026-11-01")
    with factory.begin() as s:
        s.get(Job, job_id).cursor = {
            "phase": "activity",
            "ids": ["123"],
            "index": 0,
            "sub": 0,
            "offset": 1,
            "last_page": True,
        }
    client = FakeGarmin([GarminError("Missing", status=404), [], GarminError("Missing", status=404)])
    worker = Worker(factory, settings, client)
    for _ in range(4):
        assert worker.tick(schedule=False)
    with factory() as s:
        assert s.get(Job, job_id).status == "complete"
        assert s.get(DatasetStatus, "activity_details").status == "unavailable"
