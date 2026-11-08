from datetime import date, timedelta

from sqlalchemy import func, select

from .catalog import DATASETS, LABELS
from .db import ActivityFile, DatasetStatus, Job, Lease, Record, now


def nested(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def number(value, precision=0):
    return f"{value:,.{precision}f}" if isinstance(value, (int, float)) else "—"


def overview(session, settings, days=30, on=None):
    today = settings.today()
    end = min(on, today) if on else today
    start = end - timedelta(days=days - 1)
    records = session.scalars(
        select(Record)
        .where(Record.dataset == "daily_summaries", Record.day >= str(start), Record.day <= str(end))
        .order_by(Record.day)
    ).all()
    # Prefer the last completed day; current-day totals are explicitly labelled when used.
    complete = [r for r in records if r.day < str(end)]
    latest = (complete or records)[-1] if records else None
    if on or settings.demo:
        latest = next((r for r in records if r.day == str(end)), None)
    day = latest.day if latest else str(end) if on or settings.demo else None
    daily = latest.payload if latest else {}
    sleep_record = (
        session.scalar(
            select(Record)
            .where(Record.dataset.in_(("sleep", "sleep_summaries")), Record.day == day)
            .order_by((Record.dataset == "sleep").desc())
            .limit(1)
        )
        if day
        else None
    )
    sleep = sleep_record.payload if sleep_record else {}
    hrv_record = (
        session.scalar(
            select(Record)
            .where(Record.dataset.in_(("hrv", "hrv_summaries")), Record.day == day)
            .order_by((Record.dataset == "hrv").desc())
            .limit(1)
        )
        if day
        else None
    )
    hrv = hrv_record.payload if hrv_record else {}
    hrv = hrv.get("hrvSummary", hrv)
    # The v2 schema is nested; use explicit unit-aware fields, never infer missing values as zero.
    steps = nested(daily, "movement", "steps", "value")
    resting = nested(daily, "heartRate", "restingValue")
    metrics = [
        dict(label="Steps", value=number(steps), unit="steps", tone="green"),
        dict(
            label="Sleep",
            value=number(sleep.get("sleepTimeSeconds", 0) / 3600, 1)
            if sleep.get("sleepTimeSeconds")
            else "—",
            unit="hours",
            tone="purple",
        ),
        dict(label="Resting heart rate", value=number(resting), unit="bpm", tone="rose"),
        dict(label="Overnight HRV", value=number(hrv.get("lastNightAvg")), unit="ms", tone="blue"),
    ]
    sleep_rows = session.scalars(
        select(Record)
        .where(
            Record.dataset.in_(("sleep", "sleep_summaries")), Record.day >= str(start), Record.day <= str(end)
        )
        .order_by((Record.dataset == "sleep").asc())
    ).all()
    sleep_by_day = {r.day: r.payload for r in sleep_rows}
    hrv_rows = session.scalars(
        select(Record)
        .where(Record.dataset.in_(("hrv", "hrv_summaries")), Record.day >= str(start), Record.day <= str(end))
        .order_by((Record.dataset == "hrv").asc())
    ).all()
    hrv_by_day = {r.day: r.payload.get("hrvSummary", r.payload) for r in hrv_rows}
    trends = []
    daily_by_day = {r.day: r.payload for r in records}
    # Include each date and independently imported metrics; absent readings stay null.
    for i in range(days):
        trend_day = str(start + timedelta(days=i))
        payload = daily_by_day.get(trend_day, {})
        trends.append(
            {
                "day": trend_day,
                "steps": nested(payload, "movement", "steps", "value"),
                "rhr": nested(payload, "heartRate", "restingValue"),
                "battery": nested(payload, "bodyBattery", "maxValue"),
                "sleep": nested(sleep_by_day.get(trend_day, {}), "sleepScores", "overall", "value"),
                "hrv": hrv_by_day.get(trend_day, {}).get("lastNightAvg"),
            }
        )
    selected = date.fromisoformat(day) if day else end
    battery = nested(daily, "bodyBattery", "maxValue")
    sleep_score = nested(sleep, "sleepScores", "overall", "value")
    seconds = sleep.get("sleepTimeSeconds")
    sleep_duration = f"{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m" if seconds else "—"
    gauges = [
        dict(
            label="Recovery",
            value=number(battery),
            unit="/ 100",
            progress=battery,
            tone="recovery-tone",
            caption="Peak Body Battery",
            detail="Your daily energy reserve",
            dataset="daily_summaries",
        ),
        dict(
            label="Sleep",
            value=number(sleep_score),
            unit="/ 100",
            progress=sleep_score,
            tone="sleep-tone",
            caption="Garmin sleep score",
            detail=f"{sleep_duration} recorded",
            dataset="sleep",
        ),
        dict(
            label="Movement",
            value=number(steps),
            unit="steps",
            progress=min(100, steps / 100) if isinstance(steps, (int, float)) else None,
            tone="movement-tone",
            caption="Daily steps",
            detail="Ring reference · 10,000 steps",
            dataset="daily_summaries",
        ),
    ]
    for gauge in gauges:
        progress = gauge["progress"]
        gauge["offset"] = 100 - min(100, max(0, progress)) if isinstance(progress, (int, float)) else 100
    week_start = selected - timedelta(days=selected.weekday())
    return dict(
        metrics=metrics,
        day=day,
        trends=trends,
        days=days,
        selected=str(selected),
        date_label=selected.strftime("%A, %d %B"),
        previous_day=str(selected - timedelta(days=1)),
        next_day=str(selected + timedelta(days=1)) if selected < today else None,
        week=[
            dict(
                day=str(week_start + timedelta(days=i)),
                label=(week_start + timedelta(days=i)).strftime("%a"),
                number=(week_start + timedelta(days=i)).day,
                future=week_start + timedelta(days=i) > today,
            )
            for i in range(7)
        ],
        gauges=gauges,
        sleep_duration=sleep_duration,
        hrv_value=hrv.get("lastNightAvg"),
        resting=resting,
        activity_count=session.scalar(
            select(func.count()).select_from(Record).where(Record.dataset == "activities")
        ),
        record_count=session.scalar(select(func.count()).select_from(Record)),
        file_count=session.scalar(select(func.count()).select_from(ActivityFile)),
        earliest=session.scalar(select(func.min(Record.day))),
        activities=session.scalars(
            select(Record)
            .where(Record.dataset == "activities", Record.day <= str(selected))
            .order_by(Record.day.desc(), Record.key.desc())
            .limit(6)
        ).all(),
        last_sync=session.scalar(select(func.max(Job.finished_at))),
        sleep_score=sleep_score,
        stress=nested(daily, "stress", "avgLevel"),
        battery=battery,
        hrv_status=hrv.get("status"),
        worker=session.get(Lease, "garmin"),
    )


def coverage(session):
    counts = {
        name: (count, first, last)
        for name, count, first, last in session.execute(
            select(Record.dataset, func.count(), func.min(Record.day), func.max(Record.day)).group_by(
                Record.dataset
            )
        )
    }
    states = {row.name: row for row in session.scalars(select(DatasetStatus))}
    result = []
    for name, label in LABELS.items():
        count, first, last = counts.get(name, (0, None, None))
        if name == "activity_files":
            count = session.scalar(select(func.count()).select_from(ActivityFile))
        state = states.get(name)
        result.append(
            dict(
                name=name,
                label=label,
                count=count,
                first=first,
                last=last,
                status=(
                    "Stored"
                    if count
                    else "No records"
                    if state and state.status == "empty"
                    else "Unavailable"
                    if state and state.status == "unavailable"
                    else "Pending"
                ),
                message=state.message if state else None,
            )
        )
    return result


def import_status(session):
    jobs = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(20)).all()
    result = []
    for job in jobs:
        cursor = job.cursor
        i = cursor.get("dataset", 0)
        phase = (
            DATASETS[i].label
            if cursor.get("phase", "datasets") == "datasets" and i < len(DATASETS)
            else "Activities & original files"
        )
        result.append(
            dict(
                id=job.id,
                kind=job.kind,
                status=job.status,
                start=job.start_date,
                end=job.end_date,
                phase=phase,
                cursor_date=cursor.get("end", job.end_date),
                requests=job.requests_done,
                records=job.records_saved,
                error=job.error,
                retry_seconds=max(0, round(job.retry_at - now())),
            )
        )
    return result
