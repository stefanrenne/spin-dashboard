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
                    "watcher": {"mounts": ["/mnt/trunk"], "fatrace": 0, "running": {"/mnt/trunk": False}}, "docker": "connected",
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
    assert ok and body["docker"] == "no socket" and body["watcher"] == {"mounts": [], "fatrace": 0, "running": {}}


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


class FakeProc:
    def __init__(self, mp):
        self.mp, self.code, self.terminated = mp, None, False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True


def test_watch_once_restarts_fatrace_only_when_needed(sd, monkeypatch):
    mounts, started = [["/mnt/trunk", "/mnt/frunk"]], []

    def start(mp):
        p = FakeProc(mp)
        sd.procs.append(p)
        started.append(mp)
    monkeypatch.setattr(sd, "target_mounts", lambda: mounts[0])
    monkeypatch.setattr(sd, "start_fatrace", start)
    current = sd.watch_once(None)                                   # first round: start all
    assert started == ["/mnt/trunk", "/mnt/frunk"] and sd.health["mounts"] == current
    assert sd.watch_once(current) == current and len(started) == 2   # nothing changed: nothing restarts
    old = list(sd.procs)
    old[1].code = 1                                                  # one fatrace stopped: all restart
    sd.watch_once(current)
    assert all(p.terminated for p in old) and len(started) == 4 and len(sd.procs) == 2
    mounts[0] = ["/mnt/trunk"]                                       # a pool went away
    assert sd.watch_once(current) == ["/mnt/trunk"] and started[-1] == "/mnt/trunk" and len(sd.procs) == 1
    mounts[0] = []
    assert sd.watch_once(["/mnt/trunk"]) == [] and sd.procs == [] and sd.health["mounts"] == []


def test_health_says_per_filesystem_whether_its_fatrace_runs(sd, monkeypatch):
    monkeypatch.setattr(sd, "target_mounts", lambda: ["/mnt/trunk", "/mnt/frunk"])
    def start(mp):
        p = FakeProc(mp)
        sd.procs.append(p)
        sd.watching[mp] = p
    monkeypatch.setattr(sd, "start_fatrace", start)
    sd.watch_once(None)
    sd.watching["/mnt/frunk"].code = 1
    ok, body = sd.health_status(now=sd.health["started"] + 1)
    assert body["watcher"]["running"] == {"/mnt/trunk": True, "/mnt/frunk": False}


def test_a_fanotify_limit_gets_a_hint_in_the_log(sd, monkeypatch):
    logged = []
    monkeypatch.setattr(sd, "log", lambda *a: logged.append(" ".join(map(str, a))))
    sd.fatrace_stderr("/mnt/x", "Cannot initialize fanotify: Too many open files\n")
    assert logged[0] == "fatrace /mnt/x: Cannot initialize fanotify: Too many open files"
    assert "max_user_groups" in logged[1]
    sd.fatrace_stderr("/mnt/x", "some other error\n")
    assert len(logged) == 3
