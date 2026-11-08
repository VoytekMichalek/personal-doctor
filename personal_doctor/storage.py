import hashlib
import json

from .db import DatasetStatus, RawResponse, Record, now, upsert


def rows_for(dataset, data):
    if data is None:
        return []
    if dataset.collection:
        if not isinstance(data, dict) or not isinstance(data.get(dataset.collection), list):
            raise ValueError("Garmin changed the response structure")
        return data[dataset.collection]
    if isinstance(data, list):
        return data
    if dataset.name == "weight":
        return data.get("dailyWeightSummaries", [])
    if dataset.name == "weight_goals":
        return data.get("userGoals", [])
    if dataset.name == "nutrition" and not data.get("loggedFoodsWithServingSizes"):
        return []
    return [data] if data else []


def save_response(session, dataset, start, end, data):
    key = "current" if dataset.mode == "once" else f"{start}:{end}"
    upsert(
        session,
        RawResponse,
        dict(dataset=dataset.name, request_key=key, payload=data, captured_at=now()),
        ["dataset", "request_key"],
    )
    rows = rows_for(dataset, data)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Garmin returned an unexpected record")
        day = row.get("calendarDate") or row.get("date")
        day = str(day)[:10] if day and isinstance(day, str) else None
        if not day and dataset.mode == "day":
            day = end
        identity = row.get("activityId") or row.get("activityUuid") or row.get("id")
        if dataset.name in ("weight_entries", "heart_rate_zones", "weight_goals"):
            identity = row.get("timestampGMT") or row.get("sport") or identity
        if dataset.mode == "once":
            identity = identity or "current"
        record_key = str(
            identity or day or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        )
        upsert(
            session,
            Record,
            dict(dataset=dataset.name, key=record_key, day=day, payload=row, updated_at=now()),
            ["dataset", "key"],
        )
    upsert(
        session,
        DatasetStatus,
        dict(name=dataset.name, last_checked=now(), status="available" if rows else "empty", message=None),
        ["name"],
    )
    return len(rows)
