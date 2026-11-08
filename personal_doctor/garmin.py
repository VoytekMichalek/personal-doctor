import base64
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"


class GarminError(Exception):
    def __init__(self, message, *, status=None, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def retry_seconds(value, default=60):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        try:
            return max(1, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return default


class GarminClient:
    """Only Garmin's official Personal API; tokens stay in process memory."""

    def __init__(self, key, interval=1, transport=None):
        self.key = key
        self.interval = interval
        self.http = httpx.Client(base_url=BASE, timeout=45, follow_redirects=False, transport=transport)
        self.token = None
        self.expires = 0
        self.last_request = 0

    def close(self):
        self.http.close()

    def _request(self, method, path, **kwargs):
        time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.RequestError:
            raise GarminError("Garmin could not be reached. The worker will retry.", retry_after=60) from None
        if response.status_code == 429:
            raise GarminError(
                "Garmin rate limit reached. Waiting before retrying.",
                status=429,
                retry_after=retry_seconds(response.headers.get("retry-after")),
            )
        if response.status_code >= 500:
            raise GarminError(
                "Garmin is temporarily unavailable. The worker will retry.",
                status=response.status_code,
                retry_after=120,
            )
        return response

    def authenticate(self):
        if not self.key:
            raise GarminError(
                "Set GARMIN_PERSONAL_API_KEY on this server, then resume the import.", status=401
            )
        response = self._request(
            "POST", TOKEN_PATH, headers={"Authorization": f"ApiKey {self.key}", "Accept": "*/*"}
        )
        if response.status_code != 200:
            raise GarminError(
                "Garmin rejected the API key. Replace it in the server environment and restart.",
                status=response.status_code,
            )
        try:
            token = response.json()
        except ValueError:
            token = response.text.strip()
        if isinstance(token, dict):
            token = token.get("access_token") or token.get("accessToken")
        try:
            claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
            self.expires = float(claims["exp"])
            self.token = token
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            raise GarminError("Garmin returned an unexpected token response.") from None

    def get(self, path, params=None, binary=False):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("An API path is required")
        for attempt in range(2):
            if not self.token or self.expires < time.time() + 60:
                self.authenticate()
            response = self._request(
                "GET",
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "*/*" if binary else "application/json",
                },
            )
            if response.status_code != 401 or attempt:
                break
            self.token = None
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise GarminError(
                f"Garmin returned HTTP {response.status_code}. Retry or check dataset access.",
                status=response.status_code,
            )
        if binary:
            if len(response.content) > 100 * 1024 * 1024:
                raise GarminError("Activity download exceeds the 100 MB safety limit.")
            return response.content
        try:
            return response.json()
        except ValueError:
            raise GarminError("Garmin returned an unexpected data response. Retry the import.") from None
