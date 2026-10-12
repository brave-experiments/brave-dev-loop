"""Unit tests for scripts/web_dashboard.py HTTP server."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from scripts.web_dashboard import DashboardRequestHandler


def test_web_dashboard_endpoints(tmp_path):
    # Start ephemeral server on random available port
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"

    try:
        # Test GET / (HTML index)
        req = urllib.request.Request(f"{base_url}/")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "brave-dev-loop Control Plane" in content
            assert "LIVE TELEMETRY" in content

        # Test GET /api/status
        req = urllib.request.Request(f"{base_url}/api/status")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "is_paused" in data
            assert "event_count" in data

        # Test GET /api/prd
        req = urllib.request.Request(f"{base_url}/api/prd")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "stories" in data

        # Test GET /api/diff
        req = urllib.request.Request(f"{base_url}/api/diff")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        # Test POST /api/steer
        steer_data = json.dumps({"hint": "Test steer from unit test"}).encode("utf-8")
        post_req = urllib.request.Request(
            f"{base_url}/api/steer",
            data=steer_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(post_req) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res.get("status") == "ok"

    finally:
        server.shutdown()
        server.server_close()
