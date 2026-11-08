"""Disposable local preview. Never loads .env or connects to Garmin."""

import math
import random
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Base, DatasetStatus, Job, RawResponse, Record, make_engine, now, sessions


def seed_demo(factory, settings):
    today = settings.today()
    with factory.begin() as session:
        for offset in range(91):
            day = today - timedelta(days=90 - offset)
            key = str(day)
            rng = random.Random(day.toordinal())
            steps = rng.randint(6200, 16100)
            battery = rng.randint(56, 96)
            score = rng.randint(68, 96)
            sleep_seconds = rng.randint(390, 530) * 60
            rhr = rng.randint(48, 56)
            hrv = rng.randint(58, 84)
            if day == today:
                steps, battery, score, sleep_seconds, rhr, hrv = 10842, 87, 92, 28020, 49, 76
            payloads = {
                "daily_summaries": {
                    "calendarDate": key,
                    "movement": {"steps": {"value": steps}},
                    "heartRate": {"restingValue": rhr},
                    "stress": {"avgLevel": rng.randint(19, 38)},
                    "bodyBattery": {"maxValue": battery},
                },
                "sleep": {
                    "calendarDate": key,
                    "sleepTimeSeconds": sleep_seconds,
                    "deepSleepSeconds": round(sleep_seconds * 0.22),
                    "remSleepSeconds": round(sleep_seconds * 0.26),
                    "lightSleepSeconds": round(sleep_seconds * 0.52),
                    "sleepScores": {"overall": {"value": score}},
                },
                "hrv": {"calendarDate": key, "hrvSummary": {"lastNightAvg": hrv, "status": "BALANCED"}},
            }
            midnight = datetime.combine(day, time(), ZoneInfo(settings.timezone)).timestamp()
            readings = []
            for minute in range(0, 1440, 5):
                rate = 51 + 5 * math.sin(minute / 20) + rng.randint(-4, 4)
                if minute >= 390:
                    rate += 16
                if 395 <= minute <= 445:
                    rate += 73 + 12 * math.sin(minute / 8)
                if 1080 <= minute <= 1130:
                    rate += 24
                readings.append([round((midnight + minute * 60) * 1000), round(rate)])
            payloads["heart_rate"] = {"calendarDate": key, "heartRateValues": readings}
            for dataset, payload in payloads.items():
                session.add(Record(dataset=dataset, key=key, day=key, payload=payload))
                session.add(RawResponse(dataset=dataset, request_key=f"{key}:{key}", payload=payload))
            if offset % 4 != 1:
                kind = ["running", "cycling", "strength_training", "running"][(offset // 4 + offset) % 4]
                duration = {"running": 2562, "cycling": 4210, "strength_training": 3020}[kind]
                distance = {"running": 8240, "cycling": 28600, "strength_training": 0}[kind]
                name = {
                    "running": "Morning miles",
                    "cycling": "River loop",
                    "strength_training": "Full-body strength",
                }[kind]
                payload = {
                    "activityId": 900000 + offset,
                    "activityName": name,
                    "activityType": {"typeKey": kind},
                    "startTimeLocal": f"{key} 06:35:00",
                    "duration": duration + rng.randint(-120, 120),
                    "distance": distance,
                    "averageHR": 148 if kind == "running" else 127,
                    "calories": rng.randint(320, 620),
                }
                session.add(Record(dataset="activities", key=str(900000 + offset), day=key, payload=payload))
                session.add(
                    RawResponse(dataset="activities", request_key=str(900000 + offset), payload=payload)
                )
        for dataset in ["daily_summaries", "sleep", "hrv", "heart_rate", "activities"]:
            session.add(DatasetStatus(name=dataset, last_checked=now(), status="available"))
        session.add(
            Job(
                kind="demo",
                start_date=str(today - timedelta(days=90)),
                end_date=str(today),
                status="complete",
                requests_done=365,
                records_saved=432,
                finished_at=now(),
            )
        )


def run_demo(port=8001):
    import uvicorn
    from .app import create_app

    with TemporaryDirectory(prefix="personal-doctor-demo-") as directory:
        settings = Settings(
            database_url=f"sqlite:///{Path(directory) / 'demo.db'}",
            origin=f"http://127.0.0.1:{port}",
            local_auth_bypass=True,
            worker_enabled=False,
            demo=True,
        ).validate()
        engine = make_engine(settings.database_url)
        Base.metadata.create_all(engine)
        seed_demo(sessions(engine), settings)
        print(f"Demo preview: {settings.origin} — synthetic data; Garmin is disconnected.", flush=True)
        uvicorn.run(create_app(settings, engine), host="127.0.0.1", port=port, access_log=False)
