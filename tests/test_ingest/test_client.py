"""Tests for the HTTP client's error classification."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from papermarkets.ingest.client import HttpClient
from papermarkets.ingest.exceptions import RateLimitExceeded, SkipMarket


class _Handler(BaseHTTPRequestHandler):
    """Test HTTP handler that returns different status codes based on path."""

    def do_GET(self):
        if self.path == "/ok":
            self._respond(200, {"status": "ok"})
        elif self.path == "/not-found":
            self._respond(404, {"error": "not found"})
        elif self.path == "/rate-limited":
            self._respond(429, {"error": "rate limited"})
        elif self.path == "/server-error":
            self._respond(500, {"error": "internal"})
        else:
            self._respond(400, {"error": "bad request"})

    def _respond(self, code: int, body: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs during tests


@pytest.fixture()
def test_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_successful_get(test_server):
    client = HttpClient(requests_per_second=1000)
    result = client.get(f"{test_server}/ok")
    assert result == {"status": "ok"}


def test_404_raises_skip_market(test_server):
    client = HttpClient(requests_per_second=1000)
    with pytest.raises(SkipMarket):
        client.get(f"{test_server}/not-found")


def test_429_raises_rate_limit_exceeded(test_server):
    client = HttpClient(requests_per_second=1000, max_retries=0)
    with pytest.raises(RateLimitExceeded):
        client.get(f"{test_server}/rate-limited")


def test_500_retries_then_fails(test_server):
    client = HttpClient(requests_per_second=1000, max_retries=1)
    with pytest.raises(ConnectionError):
        client.get(f"{test_server}/server-error")


def test_get_many_returns_results_in_order(test_server):
    client = HttpClient(requests_per_second=1000, max_workers=4)
    urls = [f"{test_server}/ok"] * 5
    results = client.get_many(urls)
    assert len(results) == 5
    assert all(r == {"status": "ok"} for r in results)


def test_get_many_empty_list():
    client = HttpClient(requests_per_second=1000)
    assert client.get_many([]) == []


def test_get_many_propagates_exceptions(test_server):
    client = HttpClient(requests_per_second=1000, max_workers=4)
    urls = [f"{test_server}/ok", f"{test_server}/not-found"]
    with pytest.raises(SkipMarket):
        client.get_many(urls)
