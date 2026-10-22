import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import spindash  # noqa: E402


@pytest.fixture
def sd(tmp_path, monkeypatch):
    """spindash with all host paths and output files in a temporary folder."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(spindash, "DATA", str(data))
    monkeypatch.setattr(spindash, "SPIN", str(data / "spin.csv"))
    monkeypatch.setattr(spindash, "DISKS", str(data / "disks.csv"))
    monkeypatch.setattr(spindash, "WHO", str(data / "who.csv"))
    monkeypatch.setattr(spindash, "ACTIVITY", str(data / "activity.csv"))
    monkeypatch.setattr(spindash, "DAYS", str(data / "days"))
    monkeypatch.setattr(spindash, "LEGACY", str(data / "legacy"))
    monkeypatch.setattr(spindash, "SETTINGS", str(data / "spindash-settings.json"))
    monkeypatch.setattr(spindash, "DOCKER_SOCK", str(tmp_path / "no-docker.sock"))
    monkeypatch.setattr(spindash, "procs_seen", {})
    monkeypatch.setattr(spindash, "health", {"started": 1000.0, "poll": None, "poll_error": None, "mounts": [], "docker": None})
    monkeypatch.setattr(spindash, "threads", {})
    monkeypatch.setattr(spindash, "_fd_snap", {"t": 0.0, "by_name": {}})
    monkeypatch.setattr(spindash, "notified", {})
    monkeypatch.setattr(spindash, "io_pending", [])
    monkeypatch.setattr(spindash, "ssd_wear", lambda dev: (None, None))     # no smartctl in tests
    monkeypatch.setattr(spindash, "_containers_cache", {"t": 0.0, "body": None})
    monkeypatch.setenv("TZ", "Europe/Amsterdam")      # fixed days and day start for the tests
    time.tzset()
    monkeypatch.setattr(spindash, "EMHTTP", str(tmp_path / "emhttp"))
    monkeypatch.setattr(spindash, "DOCKER_ICONS", str(tmp_path / "emhttp" / "plugins" / "dynamix.docker.manager" / "images"))
    monkeypatch.setattr(spindash, "SYS_BLOCK", str(tmp_path / "sys"))
    monkeypatch.setattr(spindash, "PROC", str(tmp_path / "proc"))
    monkeypatch.setattr(spindash, "MOUNTS_FILE", str(tmp_path / "mounts"))
    monkeypatch.setattr(spindash, "MOUNTINFO", str(tmp_path / "mountinfo"))
    monkeypatch.setattr(spindash, "HOST_CGROUP", str(tmp_path / "cgroup"))
    monkeypatch.setattr(spindash, "own_pids", {})
    monkeypatch.setattr(spindash, "procs", [])
    monkeypatch.setattr(spindash, "streams", [])
    monkeypatch.setattr(spindash, "writes_only", set())
    monkeypatch.setattr(spindash, "watching", {})
    monkeypatch.setattr(spindash, "quiet_io", {"window": None, "kib": {}, "sent": set()})
    monkeypatch.setattr(spindash, "wear_today", {"day": None, "kib": {}, "sent": set()})
    monkeypatch.setattr(spindash, "events_seen", {"nano": None})
    monkeypatch.setattr(spindash, "recent", spindash.OrderedDict())
    monkeypatch.setattr(spindash, "drive_states", {})
    monkeypatch.setattr(spindash, "known_devices", {})
    monkeypatch.setattr(spindash, "names_failed", {})
    monkeypatch.setattr(spindash, "_shares_cache", {"t": 0.0, "body": None})
    monkeypatch.setattr(spindash, "USER_SHARES", str(tmp_path / "user"))
    monkeypatch.setattr(spindash, "WATCH_OVERRIDE", [])
    monkeypatch.setattr(spindash, "names", {})
    monkeypatch.setattr(spindash, "seen", {})
    monkeypatch.setattr(spindash, "ignored_comms", {})
    monkeypatch.setattr(spindash, "_ignore_seen", {"t": None, "on": False})
    monkeypatch.setattr(spindash, "log", lambda *a: None)
    (tmp_path / "emhttp").mkdir()
    (tmp_path / "sys").mkdir()
    (tmp_path / "proc").mkdir()
    spindash._tmp = tmp_path
    yield spindash
    monkeypatch.undo()
    time.tzset()


def make_disk(tmp_path, dev, rotational=True):
    q = tmp_path / "sys" / dev / "queue"
    q.mkdir(parents=True)
    (q / "rotational").write_text("1\n" if rotational else "0\n")


def set_io(tmp_path, dev, rd_sectors, wr_sectors, rd_ops, wr_ops):
    """/sys/block/<dev>/stat with the given counters (the other fields zero)."""
    f = [rd_ops, 0, rd_sectors, 0, wr_ops, 0, wr_sectors, 0, 0, 0, 0]
    (tmp_path / "sys" / dev / "stat").write_text(" ".join(f"{x:8d}" for x in f) + "\n")


def make_proc(tmp_path, pid, comm, ppid=1, cgroup="0::/\n"):
    d = tmp_path / "proc" / str(pid)
    d.mkdir(parents=True)
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0 0\n")
    (d / "cgroup").write_text(cgroup)
    (d / "fd").mkdir()
    return d


class FakeDocker:
    """Docker API on a Unix socket: routes {path: JSON} or {path: (lines, ...)} (a tuple) for
    a chunked stream such as /events, which closes afterwards."""
    def __init__(self, routes):
        import socketserver, http.server, tempfile, json, threading
        self.dir = tempfile.mkdtemp(dir="/tmp")         # short path: Unix sockets have a length limit
        self.path = os.path.join(self.dir, "d.sock")
        self.requests = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def address_string(self):
                return "docker"
            def log_message(self, *a):
                pass
            def do_GET(self):
                outer.requests.append(self.path)
                key = next((k for k in routes if self.path.split("?")[0] == k), None)
                if key is None:
                    self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
                val = routes[key]
                if isinstance(val, tuple):
                    self.send_response(200)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for line in val:
                        data = (line + "\n").encode()
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                    self.wfile.write(b"0\r\n\r\n")
                    self.close_connection = True
                    return
                body = json.dumps(val).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
        self.server = S(self.path, H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        import shutil
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)
