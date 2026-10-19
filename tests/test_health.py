import json
import threading
import urllib.error
import urllib.request
from functools import partial


class T:
    def __init__(self, alive):
        self.alive = alive

    def is_alive(self):
        return self.alive


def test_healthy_after_start_and_while_the_poller_keeps_succeeding(sd):
    sd.threads.update(poller=T(True), watcher=T(True), housekeeping=T(True), docker_events=T(True))
    ok, body = sd.health_status(now=1010)                     # just started, no poll yet
    assert ok and body["poller"]["last_success"] is None and body["docker"] == "connecting"
    sd.health.update(poll=1500, mounts=["/mnt/trunk"], docker="connected")
    ok, body = sd.health_status(now=1560)
    assert ok
    assert body == {"ok": True, "uptime": 560, "poller": {"last_success": 60, "error": None},
                    "watcher": {"mounts": ["/mnt/trunk"], "fatrace": 0}, "docker": "connected",
                    "threads": {"poller": True, "watcher": True, "housekeeping": True, "docker_events": True}}


def test_unhealthy_when_the_poller_stalls_or_a_thread_dies(sd):
    sd.threads.update(poller=T(True), watcher=T(True), housekeeping=T(True))
    sd.health.update(poll=1000, poll_error="OSError('hdparm')")
    assert sd.health_status(now=1000 + 3 * sd.POLL_INTERVAL + 59)[0] is True
    ok, body = sd.health_status(now=1000 + 3 * sd.POLL_INTERVAL + 61)
    assert not ok and body["poller"]["error"] == "OSError('hdparm')"
    sd.health["poll"] = 2000
    sd.threads["watcher"] = T(False)
    assert sd.health_status(now=2010)[0] is False


def test_no_docker_socket_or_fatrace_does_not_make_it_unhealthy(sd):
    """Otherwise an auto-heal container restarts it forever on a system without a socket or HDDs."""
    sd.threads.update(poller=T(True), watcher=T(True), housekeeping=T(True), docker_events=T(False))
    sd.health.update(poll=1000, docker="no socket", mounts=[])
    ok, body = sd.health_status(now=1010)
    assert ok and body["docker"] == "no socket" and body["watcher"] == {"mounts": [], "fatrace": 0}


def test_health_endpoint_answers_200_or_503(sd):
    srv = sd.Server(("127.0.0.1", 0), partial(sd.Handler, directory=str(sd._tmp)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/health"
    try:
        sd.health["poll"] = __import__("time").time()
        r = urllib.request.urlopen(url, timeout=5)
        assert r.status == 200 and json.loads(r.read())["ok"] is True
        sd.health["poll"] = 1.0                                # long ago
        try:
            urllib.request.urlopen(url, timeout=5)
            raise AssertionError("verwacht 503")
        except urllib.error.HTTPError as e:
            assert e.code == 503 and json.loads(e.read())["ok"] is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_dockerfile_healthcheck_uses_the_health_endpoint():
    import os
    df = open(os.path.join(os.path.dirname(__file__), "..", "Dockerfile")).read()
    assert "/health'" in df and ".read()" in df
