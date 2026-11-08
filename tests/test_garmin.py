import base64
import json
import time

import httpx
import pytest

from personal_doctor.garmin import GarminClient, GarminError, retry_seconds


def jwt():
    return (
        "header."
        + base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode()).decode()
        + ".signature"
    )


def test_plain_text_token_and_401_renewal():
    calls = []

    def transport(request):
        calls.append(request)
        if request.method == "POST":
            assert request.headers["accept"] == "*/*"
            assert request.headers["authorization"] == "ApiKey secret"
            return httpx.Response(200, text=jwt())
        if len(calls) == 2:
            return httpx.Response(401)
        return httpx.Response(200, json={"data": []})

    client = GarminClient("secret", 0, httpx.MockTransport(transport))
    assert client.get("/example") == {"data": []}
    assert [c.method for c in calls] == ["POST", "GET", "POST", "GET"]
    client.close()


def test_rate_limit_does_not_retry_in_request_or_expose_body():
    calls = []

    def transport(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, text=jwt())
        return httpx.Response(429, headers={"Retry-After": "120"}, text="sensitive upstream detail")

    client = GarminClient("secret", 0, httpx.MockTransport(transport))
    with pytest.raises(GarminError) as error:
        client.get("/example")
    assert error.value.retry_after == 120
    assert len(calls) == 2
    assert "sensitive" not in str(error.value)
    assert "secret" not in str(error.value)
    assert retry_seconds("bad") == 60
    client.close()


def test_no_content_and_no_redirects():
    client = GarminClient("secret", 0, httpx.MockTransport(lambda r: httpx.Response(204)))
    client.token, client.expires = "cached", time.time() + 3600
    assert client.get("/optional") is None
    assert client.http.follow_redirects is False
    client.close()
