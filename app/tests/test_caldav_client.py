"""
Tests for caldav_client.py, specifically how it authenticates to the server.
"""

from __future__ import annotations

from datetime import date

import requests

from caldav_client import fetch_range

URL = "https://dav.example.com/remote.php/dav/calendars/alice/personal/"

EMPTY_MULTISTATUS = (
    b'<?xml version="1.0"?>'
    b'<d:multistatus xmlns:d="DAV:"></d:multistatus>'
)


def _response(request: requests.PreparedRequest) -> requests.Response:
    response = requests.Response()
    response.status_code = 207
    response.reason = "Multi-Status"
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    response._content = EMPTY_MULTISTATUS  # pylint: disable=protected-access
    response.url = request.url
    response.request = request
    return response


def test_first_request_carries_basic_auth(monkeypatch):
    sent: list[requests.PreparedRequest] = []

    def fake_send(_session, request, **_kwargs):
        sent.append(request)
        return _response(request)

    monkeypatch.setattr(requests.Session, "send", fake_send)

    fetch_range(URL, "alice", "app-password", "Personal", "#000000",
                date(2026, 4, 6), date(2026, 4, 13))

    assert len(sent) == 1
    assert sent[0].method == "REPORT"
    assert sent[0].headers["Authorization"] == requests.auth._basic_auth_str(  # pylint: disable=protected-access
        "alice", "app-password")
