import gzip
import json
import os
import threading
import urllib.error
import urllib.request
from functools import partial

import pytest


@pytest.fixture
def server(sd, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>")
    (tmp_path / "secret.txt").write_text("nee")
    os.makedirs(sd.DAYS)
    open(sd.day_path("2026-10-09"), "w").write("1791496800,spin,sdd,active\n")
    open(sd.day_path("2026-10-08"), "w").write("x\n")
    open(os.path.join(sd.DAYS, "notes.txt"), "w").write("nee")
    srv = sd.http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(sd.Handler, directory=str(static)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    try:
        r = urllib.request.urlopen(url, timeout=5)
        return r.status, r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, "", e.headers


def test_serves_dashboard_day_list_and_day_files(server):
    assert get(server + "/")[:2] == (200, "<html>ok</html>")
    status, body, headers = get(server + "/data/days.json")
    # bytes: total size of the day files, for the progress bar ("x\n" + 27 characters)
    assert status == 200 and json.loads(body) == {"days": ["2026-10-08", "2026-10-09"], "bytes": 2 + 27,
                                                   "sizes": [2, 27]}
    assert headers["Cache-Control"] == "no-cache"
    status, body, headers = get(server + "/data/days/2026-10-09.csv")
    assert (status, body) == (200, "1791496800,spin,sdd,active\n")
    assert headers["Cache-Control"] == "no-cache"


def test_missing_day_is_404(server):
    assert get(server + "/data/days/2026-01-01.csv")[0] == 404


def test_only_day_files_from_data_dir(server):
    assert get(server + "/data/../secret.txt")[0] == 404
    assert get(server + "/data/%2e%2e/secret.txt")[0] == 404
    assert get(server + "/data/days/notes.txt")[0] == 404
    assert get(server + "/data/days/../../secret.txt")[0] == 404
    assert get(server + "/data/spin.csv")[0] == 404


def test_broadcast_fills_stream_queues(sd):
    import queue
    q1, q2 = queue.Queue(), queue.Queue()
    sd.streams.extend([q1, q2])
    try:
        sd.broadcast("1791496800,who,container,plex,1,geopend,/mnt/trunk/a.mkv")
    finally:
        sd.streams.clear()
    assert q1.get_nowait() == "1791496800,who,container,plex,1,geopend,/mnt/trunk/a.mkv"
    assert q2.get_nowait() == "1791496800,who,container,plex,1,geopend,/mnt/trunk/a.mkv"


def test_broadcast_skips_full_queue(sd):
    import queue
    full = queue.Queue(maxsize=1)
    full.put("x")
    sd.streams.append(full)
    try:
        sd.broadcast("1791496800,who,container,plex,1,geopend,/mnt/trunk/a.mkv")   # must not block
    finally:
        sd.streams.clear()
    assert full.get_nowait() == "x"     # only the old item; the broadcast was skipped


def test_stream_endpoint_delivers_broadcast(sd, server):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", int(server.rsplit(":", 1)[1]), timeout=5)
    conn.request("GET", "/data/stream")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "text/event-stream"
    line = resp.readline()              # ": ok" greeting
    assert line.startswith(b":")
    assert len(sd.streams) == 1
    sd.broadcast("1791496800,who,container,plex,1,geopend,/mnt/trunk/a.mkv")
    body = resp.readline() + resp.readline()
    assert b"data: 1791496800,who,container,plex,1,geopend,/mnt/trunk/a.mkv" in body
    conn.close()


def fetch(url, **headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, b"", e.headers


def test_day_files_are_gzipped_when_accepted(server):
    status, body, headers = fetch(server + "/data/days/2026-10-09.csv", **{"Accept-Encoding": "gzip, deflate"})
    assert status == 200 and headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(body) == b"1791496800,spin,sdd,active\n"
    assert headers["Vary"] == "Accept-Encoding" and headers["Content-Type"].startswith("text/csv")
    status, body, headers = fetch(server + "/data/days/2026-10-09.csv")          # without gzip
    assert (status, body, headers["Content-Encoding"]) == (200, b"1791496800,spin,sdd,active\n", None)


def test_unchanged_day_gives_304(server, sd):
    _, _, h = fetch(server + "/data/days/2026-10-09.csv", **{"Accept-Encoding": "gzip"})
    status, body, _ = fetch(server + "/data/days/2026-10-09.csv",
                            **{"Accept-Encoding": "gzip", "If-None-Match": h["ETag"]})
    assert (status, body) == (304, b"")
    with open(sd.day_path("2026-10-09"), "a") as f:                            # day grows: 200 again
        f.write("1791496900,spin,sdd,standby\n")
    status, body, _ = fetch(server + "/data/days/2026-10-09.csv",
                            **{"Accept-Encoding": "gzip", "If-None-Match": h["ETag"]})
    assert status == 200 and gzip.decompress(body).count(b"\n") == 2


def test_day_file_includes_io_lines_that_wait_in_memory(server, sd):
    """io lines are not written while every HDD sleeps, but the dashboard sees them anyway."""
    _, _, h = fetch(server + "/data/days/2026-10-09.csv", **{"Accept-Encoding": "gzip"})
    sd.io_pending.extend(["1791496860,io,nvme0n1,4,8,1,2", "1791583260,io,nvme0n1,1,1,1,1"])   # today, tomorrow
    status, body, h2 = fetch(server + "/data/days/2026-10-09.csv",
                             **{"Accept-Encoding": "gzip", "If-None-Match": h["ETag"]})
    assert status == 200 and h2["ETag"] != h["ETag"]
    assert gzip.decompress(body) == b"1791496800,spin,sdd,active\n1791496860,io,nvme0n1,4,8,1,2\n"
    status, body, _ = fetch(server + "/data/days/2026-10-09.csv",
                            **{"Accept-Encoding": "gzip", "If-None-Match": h2["ETag"]})
    assert status == 304
    sd.io_pending.append("1791496920,io,nvme0n1,1,0,1,0")                    # a new one: 200 again
    assert fetch(server + "/data/days/2026-10-09.csv", **{"If-None-Match": h2["ETag"]})[0] == 200


# ---------- settings (hidden paths) ----------
def post(url, body, content_type="application/json"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": content_type})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, None


def test_settings_are_empty_until_saved(server, sd):
    status, body, _ = get(server + "/data/settings.json")
    assert (status, json.loads(body)) == (200, {})
    assert not os.path.exists(sd.SETTINGS)


def test_saved_hidden_paths_are_returned_and_stored_in_data_dir(server, sd):
    status, saved = post(server + "/data/settings.json", {"hide": [" /mnt/user/appdata/* ", "", "*.tmp", "*.tmp"]})
    assert (status, saved) == (200, {"hide": ["/mnt/user/appdata/*", "*.tmp"]})
    assert json.loads(get(server + "/data/settings.json")[1]) == {"hide": ["/mnt/user/appdata/*", "*.tmp"]}
    assert json.load(open(sd.SETTINGS)) == {"hide": ["/mnt/user/appdata/*", "*.tmp"]}
    assert os.path.dirname(sd.SETTINGS) == sd.DATA
    assert not os.path.exists(sd.SETTINGS + ".tmp")
    # an empty list is a choice too: the key stays, so the browser does not migrate again
    assert post(server + "/data/settings.json", {"hide": []}) == (200, {"hide": []})
    assert json.loads(get(server + "/data/settings.json")[1]) == {"hide": []}


def test_unknown_settings_keys_are_ignored(server, sd):
    assert post(server + "/data/settings.json", {"hide": ["a"], "evil": "x"}) == (200, {"hide": ["a"]})
    assert post(server + "/data/settings.json", {"other": 1}) == (200, {"hide": ["a"]})


@pytest.mark.parametrize("body", [
    [], "x", {"hide": "a"}, {"hide": [1]}, {"hide": [None]}, {"hide": ["a\nb"]},
    {"hide": ["x" * 501]}, {"hide": [f"p{i}" for i in range(201)]},
])
def test_invalid_settings_are_rejected_and_nothing_changes(server, sd, body):
    post(server + "/data/settings.json", {"hide": ["keep"]})
    status, err = post(server + "/data/settings.json", body)
    assert status == 400 and "error" in err
    assert json.loads(get(server + "/data/settings.json")[1]) == {"hide": ["keep"]}


def test_settings_post_needs_json_and_a_sane_size(server, sd):
    assert post(server + "/data/settings.json", b"{nope", "application/json")[0] == 400
    assert post(server + "/data/settings.json", b"\xff\xfe", "application/json")[0] == 400
    # plain form (can be cross-site without a preflight): refused
    assert post(server + "/data/settings.json", b"hide=x", "application/x-www-form-urlencoded")[0] == 415
    assert post(server + "/data/settings.json", b"{}", "text/plain")[0] == 415
    big = json.dumps({"hide": ["a" * 400] * 200}).encode()
    assert len(big) > sd.SETTINGS_BODY_MAX
    assert post(server + "/data/settings.json", big)[0] == 413
    assert post(server + "/data/days.json", {"hide": []})[0] == 404
    assert not os.path.exists(sd.SETTINGS)


def test_unreadable_settings_file_counts_as_empty(sd):
    open(sd.SETTINGS, "w").write("{kapot")
    assert sd.read_settings() == {}
    open(sd.SETTINGS, "w").write(json.dumps({"hide": [3]}))
    assert sd.read_settings() == {}
    assert sd.save_settings({"hide": ["x"]}) == {"hide": ["x"]}


# ---------- /data/spins.csv: only the spin lines of all days ----------
def test_spins_returns_only_disk_state_and_spin_lines_of_every_day(server, sd):
    open(sd.day_path("2026-10-08"), "w").write(
        "1791410400,disk,sdd,trunk\n1791410400,state,sdd,standby\n"
        "1791420000,who,container,plex,1,geopend,/mnt/trunk/a,b.mkv\n1791420010,spin,sdd,active\n")
    status, body, headers = get(server + "/data/spins.csv")
    assert status == 200
    assert body == ("1791410400,disk,sdd,trunk\n1791410400,state,sdd,standby\n1791420010,spin,sdd,active\n"
                    "1791496800,spin,sdd,active\n")
    assert headers["Content-Type"].startswith("text/csv") and headers["ETag"]


def test_spins_is_gzipped_and_answers_304_until_a_day_changes(server, sd):
    req = urllib.request.Request(server + "/data/spins.csv", headers={"Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=5)
    assert r.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(r.read()).decode() == "1791496800,spin,sdd,active\n"
    etag = r.headers["ETag"]
    req = urllib.request.Request(server + "/data/spins.csv", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 304
    with open(sd.day_path("2026-10-09"), "a") as f:
        f.write("1791500000,spin,sdd,standby\n")
    r = urllib.request.urlopen(req, timeout=5)      # day changed: new content
    assert gzip.decompress(r.read()).decode().endswith("1791500000,spin,sdd,standby\n")


def test_spins_forgets_removed_days(sd):
    os.makedirs(sd.DAYS)
    open(sd.day_path("2026-10-08"), "w").write("1791410400,spin,sdd,active\n")
    assert sd.spins_body()[0] == b"1791410400,spin,sdd,active\n"
    os.remove(sd.day_path("2026-10-08"))
    assert sd.spins_body()[0] == b""
    assert sd.day_path("2026-10-08") not in sd._spin_cache


def test_spins_adds_up_io_per_drive_per_hour_and_keeps_wear(server, sd):
    """The key figures compare the SSD writes with the previous period without every day file."""
    open(sd.day_path("2026-10-08"), "w").write(
        "1791410400,disk,nvme0n1,cache,ssd\n1791410460,io,nvme0n1,1,10,1,2\n1791410520,io,nvme0n1,2,20,1,2\n"
        "1791414000,io,nvme0n1,4,40,1,1\n1791410500,wear,nvme0n1,3,999\n")
    body = get(server + "/data/spins.csv")[1].splitlines()
    assert body[:4] == ["1791410400,disk,nvme0n1,cache,ssd", "1791410500,wear,nvme0n1,3,999",
                        "1791410520,io,nvme0n1,3,30,2,4", "1791414000,io,nvme0n1,4,40,1,1"]


def test_spins_includes_waiting_io_lines_and_changes_its_etag(server, sd):
    _, _, h = fetch(server + "/data/spins.csv")
    sd.io_pending.append("1791496860,io,nvme0n1,4,8,1,2")
    status, body, h2 = fetch(server + "/data/spins.csv", **{"If-None-Match": h["ETag"]})
    assert status == 200 and h2["ETag"] != h["ETag"]
    assert body.decode().splitlines()[-1] == "1791496860,io,nvme0n1,4,8,1,2"


def test_a_closed_stream_is_forgotten(sd, server):
    """A browser that closes the page: its queue disappears with the next line that cannot be sent."""
    import socket, time
    s = socket.create_connection(("127.0.0.1", int(server.rsplit(":", 1)[1])), timeout=5)
    s.sendall(b"GET /data/stream HTTP/1.1\r\nHost: dash\r\n\r\n")
    assert s.recv(100).startswith(b"HTTP/1.0 200")
    end = time.time() + 5
    while not sd.streams and time.time() < end:
        time.sleep(0.01)
    assert len(sd.streams) == 1
    s.close()
    while sd.streams and time.time() < end:
        sd.broadcast("1791496800,who,container,plex,1,geopend,/mnt/trunk/a.mkv")
        time.sleep(0.05)
    assert sd.streams == []


def part(server, day, **q):
    """GET a day file with ?from=…: (status, body, headers)."""
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return fetch(f"{server}/data/days/{day}.csv?{qs}")


def test_day_part_sends_only_what_was_added(server, sd):
    status, body, h = part(server, "2026-10-09", **{"from": 0})
    assert (status, body, h["X-From"], h["X-Size"]) == (200, b"1791496800,spin,sdd,active\n", "0", "27")
    fid = h["X-File"]
    with open(sd.day_path("2026-10-09"), "a") as f:
        f.write("1791496900,spin,sdd,standby\n1791497000,who,unraid,x,1,geo")   # the last line is not complete yet
    status, body, h = part(server, "2026-10-09", **{"from": 27, "file": fid})
    assert (body, h["X-From"], h["X-Size"]) == (b"1791496900,spin,sdd,standby\n", "27", "55")
    status, body, h = part(server, "2026-10-09", **{"from": 55, "file": fid})
    assert (body, h["X-Size"]) == (b"", "55")                                   # nothing whole added yet


def test_day_part_starts_over_when_the_file_was_rewritten(server, sd):
    _, _, h = part(server, "2026-10-09", **{"from": 0})
    path = sd.day_path("2026-10-09")
    open(path + ".tmp", "w").write("1791496800,spin,sdd,active\n1791496801,spin,sdd,standby\n")
    sd.os.replace(path + ".tmp", path)                                           # like relabel_existing()
    _, body, h2 = part(server, "2026-10-09", **{"from": 27, "file": h["X-File"]})
    assert h2["X-From"] == "0" and body.count(b"\n") == 2 and h2["X-File"] != h["X-File"]
    _, body, h3 = part(server, "2026-10-09", **{"from": 999})                    # beyond the end
    assert h3["X-From"] == "0"
    assert part(server, "2026-10-09", **{"from": "x"})[0] == 400
    assert part(server, "2026-01-01", **{"from": 0})[0] == 404


def test_day_part_leaves_the_waiting_lines_to_pending(server, sd):
    sd.io_pending.extend(["1791496860,io,nvme0n1,4,8,1,2", "1791583260,wear,nvme0n1,3,"])
    _, body, _ = part(server, "2026-10-09", **{"from": 0})
    assert b",io," not in body
    status, body, h = fetch(server + "/data/pending.csv")
    assert (status, body) == (200, b"1791496860,io,nvme0n1,4,8,1,2\n1791583260,wear,nvme0n1,3,\n")
    assert fetch(server + "/data/pending.csv", **{"If-None-Match": h["ETag"]})[0] == 304
    sd.io_pending.clear()
    assert fetch(server + "/data/pending.csv")[1] == b""


def test_spins_keeps_the_file_summary_and_only_adds_the_waiting_lines(server, sd, monkeypatch):
    open(sd.day_path("2026-10-09"), "w").write("1791496800,disk,nvme0n1,cache,ssd\n1791496860,io,nvme0n1,1,2,1,1\n")
    get(server + "/data/spins.csv")
    reads = []
    real = sd.summary_parts
    def counting(lines, hours=None):
        lines = list(lines)
        reads.append(len(lines))
        return real(lines, hours)
    monkeypatch.setattr(sd, "summary_parts", counting)
    sd.io_pending.append("1791496920,io,nvme0n1,1,2,1,1")
    body = get(server + "/data/spins.csv")[1].splitlines()
    assert "1791496920,io,nvme0n1,2,4,2,2" in body                               # file and waiting lines in one hour
    assert reads == [1]                                                          # only the waiting line was read
