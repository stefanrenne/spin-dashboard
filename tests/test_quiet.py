import http.server
import json
import threading
import urllib.error
import urllib.request
from functools import partial

import pytest

from conftest import make_disk

T = 1791496800                    # 2026-10-09 00:00 in Amsterdam


def at(hh, mm=0):
    return T + hh * 3600 + mm * 60


# ---------- settings ----------
def test_quiet_and_webhook_are_validated_and_saved(sd):
    saved = sd.save_settings({"quiet": {"from": "23:00", "to": "07:00", "drives": ["trunk", "array", "trunk"]},
                              "webhook": " https://ntfy.example/spin "})
    assert saved == {"quiet": {"from": "23:00", "to": "07:00", "drives": ["trunk", "array"]},
                     "webhook": "https://ntfy.example/spin"}
    assert sd.read_settings() == saved
    assert sd.save_settings({"quiet": None, "webhook": ""}) == {"quiet": None, "webhook": ""}


@pytest.mark.parametrize("bad", [
    {"quiet": {"from": "23:00", "to": "23:00"}}, {"quiet": {"from": "24:00", "to": "07:00"}},
    {"quiet": {"from": "7:00", "to": "08:00"}}, {"quiet": {"from": "23:00", "to": "07:00", "drives": ["../x"]}},
    {"quiet": "23-07"}, {"webhook": "ftp://x"}, {"webhook": "https://"}, {"webhook": "https://a b"},
    {"webhook": "https://x/" + "a" * 500}, {"webhook": 5},
])
def test_invalid_quiet_or_webhook_is_rejected_and_nothing_changes(sd, bad):
    sd.save_settings({"hide": ["keep"]})
    with pytest.raises(ValueError):
        sd.save_settings({**bad, "hide": ["changed"]})
    assert sd.read_settings() == {"hide": ["keep"]}


def test_an_invalid_key_in_the_file_does_not_drop_the_others(sd):
    open(sd.SETTINGS, "w").write(json.dumps({"hide": ["a"], "quiet": {"from": "x"}, "webhook": "https://h/x"}))
    assert sd.read_settings() == {"hide": ["a"], "webhook": "https://h/x"}


# ---------- window ----------
def test_in_quiet_handles_a_window_over_midnight(sd):
    q = {"from": "23:00", "to": "07:00", "drives": []}
    assert sd.in_quiet(at(23, 30), q) and sd.in_quiet(at(3), q) and sd.in_quiet(at(6, 59), q)
    assert not sd.in_quiet(at(7), q) and not sd.in_quiet(at(12), q) and not sd.in_quiet(at(22, 59), q)
    day = {"from": "09:00", "to": "17:00", "drives": []}
    assert sd.in_quiet(at(9), day) and not sd.in_quiet(at(17), day)
    assert not sd.in_quiet(at(3), None)


def test_lane_key_matches_the_frontend(sd):
    assert [sd.lane_key(n) for n in ("parity", "parity2", "disk1", "disk12", "trunk", "trunk2", "cache")] == \
        ["array", "array", "array", "array", "trunk", "trunk", "cache"]


# ---------- notifications ----------
def test_spin_up_in_quiet_hours_is_sent_once_per_lane(sd):
    sd.save_settings({"quiet": {"from": "23:00", "to": "07:00", "drives": ["trunk"]}, "webhook": "https://h/x"})
    got = []
    send = lambda url, p: got.append((url, p))
    sd.notify_spinups(at(2), [("sdd", "trunk"), ("sde", "trunk2"), ("sdc", "disk1")], send=send)
    assert len(got) == 1                                   # disk1 (array) does not take part; trunk + trunk2 is one lane
    url, p = got[0]
    assert url == "https://h/x" and p["lane"] == "trunk" and p["devices"] == ["sdd", "sde"]
    assert p["text"] == p["content"] == "trunk spun up at 02:00 during quiet hours (23:00-07:00)"
    sd.notify_spinups(at(2, 5), [("sdd", "trunk")], send=send)       # within 10 min: not again
    sd.notify_spinups(at(12), [("sdd", "trunk")], send=send)         # outside the quiet hours
    assert len(got) == 1
    sd.notify_spinups(at(2, 11), [("sdd", "trunk")], send=send)
    assert len(got) == 2


def test_no_notification_without_webhook_or_window(sd):
    got = []
    sd.save_settings({"quiet": {"from": "23:00", "to": "07:00"}})
    assert sd.notify_spinups(at(2), [("sdd", "trunk")], send=lambda *a: got.append(a)) == []
    sd.save_settings({"quiet": None, "webhook": "https://h/x"})
    assert sd.notify_spinups(at(2), [("sdd", "trunk")], send=lambda *a: got.append(a)) == []
    assert got == []


def test_poll_once_passes_spin_ups_to_the_notifier(sd, monkeypatch):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["trunk"]\ndevice="sdd"\n')
    make_disk(sd._tmp, "sdd")
    states = {"sdd": "standby"}
    monkeypatch.setattr(sd, "drive_state", lambda dev: states[dev])
    calls = []
    monkeypatch.setattr(sd, "notify_spinups", lambda now, ups: calls.append((now, ups)))
    last = {}
    sd.poll_once(last, now=at(2))
    states["sdd"] = "active"
    sd.poll_once(last, now=at(2, 1))
    states["sdd"] = "standby"
    sd.poll_once(last, now=at(2, 2))                       # to standby: no notification
    assert calls == [(at(2, 1), [("sdd", "trunk")])]


class Hook:
    """A webhook receiver on localhost that keeps the JSON."""
    def __init__(self, status=200):
        self.bodies = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                outer.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/hook"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_send_webhook_posts_json(sd):
    hook = Hook()
    try:
        assert sd.send_webhook(hook.url, {"text": "hi", "content": "hi"}) == 200
    finally:
        hook.close()
    assert hook.bodies == [{"text": "hi", "content": "hi"}]


def test_notify_test_endpoint_uses_the_saved_webhook(sd):
    hook = Hook()
    srv = sd.Server(("127.0.0.1", 0), partial(sd.Handler, directory=str(sd._tmp)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path):
        req = urllib.request.Request(base + path, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=5)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert post("/data/notify-test") == (400, {"ok": False, "error": "no webhook saved"})
        sd.save_settings({"webhook": hook.url})
        assert post("/data/notify-test") == (200, {"ok": True, "status": 200})
        assert hook.bodies[0]["event"] == "test" and "notifications work" in hook.bodies[0]["text"]
        sd.save_settings({"webhook": "http://127.0.0.1:9/nothing"})          # nobody listening
        status, body = post("/data/notify-test")
        assert status == 502 and body["ok"] is False
    finally:
        srv.shutdown()
        srv.server_close()
        hook.close()
