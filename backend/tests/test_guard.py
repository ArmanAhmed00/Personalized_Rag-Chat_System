"""The local-access guard: who may connect, DNS rebinding and CSRF."""

from __future__ import annotations

from sidelamp.config import Settings
from tests.conftest import make_client

REMOTE = ("192.168.1.20", 50000)


def test_loopback_client_is_allowed(settings: Settings) -> None:
    with make_client(settings) as c:
        assert c.get("/api/documents").status_code == 200


def test_ipv6_loopback_is_allowed(settings: Settings) -> None:
    with make_client(settings, client=("::1", 50000), base_url="http://[::1]:8642") as c:
        assert c.get("/api/documents").status_code == 200


def test_remote_client_is_refused_without_token(settings: Settings) -> None:
    with make_client(settings, client=REMOTE) as c:
        response = c.get("/api/documents")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden_client"
        assert c.get("/").status_code == 403  # the web app too


def test_health_and_config_stay_open(settings: Settings) -> None:
    with make_client(settings, client=REMOTE) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/config").status_code == 200


def test_token_required_when_set(settings: Settings) -> None:
    settings.access_token = "s3cret-token"
    with make_client(settings, client=REMOTE) as c:
        assert c.get("/api/documents").status_code == 401
        wrong = c.get("/api/documents", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        assert wrong.json()["error"]["code"] == "bad_token"
        ok = c.get("/api/documents", headers={"Authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200
        alt = c.get("/api/documents", headers={"X-Sidelamp-Token": "s3cret-token"})
        assert alt.status_code == 200
        assert c.get("/api/config").json()["token_required"] is True


def test_token_also_required_for_loopback(settings: Settings) -> None:
    settings.access_token = "abc"
    with make_client(settings) as c:
        assert c.get("/api/documents").status_code == 401


def test_docker_mode_accepts_bridge_clients(settings: Settings) -> None:
    settings.docker = True
    with make_client(settings, client=("172.17.0.1", 50000)) as c:
        assert c.get("/api/documents").status_code == 200


def test_dns_rebinding_host_is_refused(settings: Settings) -> None:
    with make_client(settings, base_url="http://evil.example:8642") as c:
        response = c.get("/api/health")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden_host"


def test_explicitly_allowed_host(settings: Settings) -> None:
    settings.allowed_hosts = ["sidelamp.home.arpa"]
    with make_client(settings, base_url="http://sidelamp.home.arpa:8642") as c:
        assert c.get("/api/health").status_code == 200


def test_localhost_names_accepted(settings: Settings) -> None:
    for base in ("http://localhost:8642", "http://127.0.0.1", "http://[::1]:9000"):
        with make_client(settings, base_url=base) as c:
            assert c.get("/api/health").status_code == 200, base


def test_state_change_without_custom_header_is_refused(settings: Settings) -> None:
    with make_client(settings, csrf=False) as c:
        response = c.delete("/api/documents/abc")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "missing_header"
        assert c.delete("/api/documents/abc", headers={"X-Sidelamp": "1"}).status_code == 404


def test_cross_origin_request_is_refused(settings: Settings) -> None:
    with make_client(settings) as c:
        response = c.delete("/api/documents/abc", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "cross_origin"
        same = c.delete("/api/documents/abc", headers={"Origin": "http://127.0.0.1:8642"})
        assert same.status_code == 404


def test_cross_origin_preflight_is_refused(settings: Settings) -> None:
    with make_client(settings) as c:
        response = c.options(
            "/api/documents",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers


def test_dev_origin_preflight_allowed(settings: Settings) -> None:
    settings.dev_origins = ["http://localhost:3000"]
    with make_client(settings) as c:
        pre = c.options(
            "/api/documents",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
        )
        assert pre.status_code == 204
        assert pre.headers["access-control-allow-origin"] == "http://localhost:3000"
        real = c.get("/api/documents", headers={"Origin": "http://localhost:3000"})
        assert real.headers["access-control-allow-origin"] == "http://localhost:3000"
