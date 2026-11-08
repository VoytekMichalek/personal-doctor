"""The 26 GET endpoints in Garmin's official Personal API, verified 2026-11-08."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    name: str
    label: str
    path: str
    mode: str = "range"
    window: int = 28
    collection: str | None = None
    query_dates: bool = False

    def request(self, start, end, today):
        path = self.path.format(start=start, end=end, date=end)
        params = {"startDate": start, "endDate": end} if self.query_dates else {}
        if self.name in ("sleep", "heart_rate"):
            params["date"] = end
        if self.name == "daily_summary":
            params["calendarDate"] = end
        if self.name == "body_battery":
            params["chunks"] = 720  # Preserve two-minute bins instead of the coarse default.
        if self.name == "weight":
            params["includeAll"] = "true"
        if self.name == "cycle_day":
            params.update(todayDate=today, includeAnalysis="true", skipbm="false", reducedResponse="false")
        return path, params


DATASETS = [
    Dataset(
        "daily_summaries",
        "Daily health",
        "/usersummary-service/v2/dailysummaries",
        collection="data",
        query_dates=True,
    ),
    Dataset(
        "sleep_summaries", "Sleep history", "/sleep-service/sleep/dailySleepSummariesByDate", query_dates=True
    ),
    Dataset(
        "hrv_summaries", "HRV history", "/hrv-service/hrv/daily/{start}/{end}", collection="hrvSummaries"
    ),
    Dataset(
        "body_battery",
        "Body Battery",
        "/wellness-service/wellness/bodyBattery/reports/daily",
        query_dates=True,
    ),
    Dataset("steps", "Steps", "/usersummary-service/stats/steps/daily/{start}/{end}"),
    Dataset("stress", "Stress", "/usersummary-service/stats/stress/daily/{start}/{end}"),
    Dataset("hydration", "Hydration", "/usersummary-service/stats/hydration/daily/{start}/{end}"),
    Dataset("weight", "Weight summaries", "/weight-service/weight/range/{start}/{end}"),
    Dataset("weight_entries", "Body composition", "/weight-service/weight/all/{start}/{end}"),
    Dataset("weight_goals", "Weight goals", "/goal-service/goal/user/effective/weightgoal/{start}/{end}"),
    Dataset(
        "wellness_activities",
        "Wellness activities",
        "/wellnessactivity-service/activity/summary/range/{start}/{end}",
    ),
    Dataset(
        "daily_summary",
        "Detailed daily health",
        "/usersummary-service/usersummary/daily",
        mode="day",
        window=1,
    ),
    Dataset("sleep", "Detailed sleep", "/sleep-service/sleep/dailySleep", mode="day", window=1),
    Dataset("hrv", "HRV readings", "/hrv-service/hrv/{date}", mode="day", window=1),
    Dataset(
        "heart_rate", "Heart-rate readings", "/wellness-service/wellness/dailyHeartRate", mode="day", window=1
    ),
    Dataset(
        "spo2",
        "Blood oxygen",
        "/wellness-service/wellness/daily/spo2acclimation/{date}",
        mode="day",
        window=1,
    ),
    Dataset("nutrition", "Food logs", "/nutrition-service/food/logs/{date}", mode="day", window=1),
    Dataset("fitness_age", "Fitness Age", "/fitnessage-service/fitnessage/v2/{date}", mode="day", window=1),
    Dataset(
        "cycle_summary",
        "Cycle summaries",
        "/periodichealth-service/menstrualcycle/summary/{date}",
        mode="day",
        window=1,
    ),
    Dataset(
        "cycle_day",
        "Cycle details",
        "/periodichealth-service/menstrualcycle/dayview/{date}",
        mode="day",
        window=1,
    ),
    Dataset("profile", "Profile settings", "/userprofile-service/userprofile/settings", mode="once"),
    Dataset("heart_rate_zones", "Heart-rate zones", "/biometric-service/heartRateZones/v2", mode="once"),
]
LABELS = {d.name: d.label for d in DATASETS} | {
    "activities": "Activities",
    "activity_details": "Activity details",
    "activity_workouts": "Activity workouts",
    "activity_files": "Original activity files",
}
