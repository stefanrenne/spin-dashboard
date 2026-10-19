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
