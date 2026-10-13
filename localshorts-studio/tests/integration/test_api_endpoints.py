"""Integration tests for FastAPI health, UI views, settings, and job API endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from localshorts.database import init_db, reset_engine_for_tests
from localshorts.main import app


def test_fastapi_health_and_ui_endpoints(tmp_path: Path) -> None:
    test_db = tmp_path / "api_test.db"
    reset_engine_for_tests(test_db)
    init_db(test_db)

    with TestClient(app) as client:
        # 1. Health endpoint
        resp_health = client.get("/api/health")
        assert resp_health.status_code == 200
        h_data = resp_health.json()
        assert h_data["status"] in ("healthy", "degraded")
        assert h_data["ffmpeg"] is True
        assert h_data["tts"] is True

        # 2. Status bar endpoint
        resp_sb = client.get("/api/status-bar")
        assert resp_sb.status_code == 200

        # 3. Settings GET/POST
        resp_cfg = client.get("/api/settings")
        assert resp_cfg.status_code == 200
        resp_upd = client.post("/api/settings", json={"daily_candidate_limit": 10, "ollama_num_ctx": 4096})
        assert resp_upd.status_code == 200
        assert resp_upd.json()["settings"]["daily_candidate_limit"] == 10

        # 4. All HTML views render 200 OK
        for route in ("/", "/dashboard", "/wizard", "/research", "/create", "/queue", "/library", "/settings", "/diagnostics"):
            r = client.get(route)
            assert r.status_code == 200, f"Route {route} returned {r.status_code}"
            assert "LocalShorts Studio" in r.text
