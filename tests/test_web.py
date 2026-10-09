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
    assert status == 200 and json.loads(body) == {"days": ["2026-10-08", "2026-10-09"]}
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
