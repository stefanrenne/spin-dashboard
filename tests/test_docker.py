import json
import os

import pytest

from conftest import FakeDocker, make_disk


# ---------- proc: which process inside a container ----------
def test_container_access_logs_the_process_name_once_per_pid_and_day(sd):
    sd.write_who("1791496800", "container", "plex", "5", "geopend", "/mnt/trunk/a.mkv", comm="Plex Media Scan")
    sd.write_who("1791496801", "container", "plex", "5", "geopend", "/mnt/trunk/b.mkv", comm="Plex Media Scan")
    sd.write_who("1791496802", "container", "plex", "5", "geopend", "/mnt/trunk/c.mkv", comm="Plex Transcoder")
    sd.write_who("1791496803", "unraid", "mover", "6", "verplaatst", "/mnt/trunk/d.mkv", comm="mover")
    lines = open(sd.day_path("2026-10-09")).read().splitlines()
    assert lines == [
        "1791496800,proc,5,Plex Media Scan",               # before the who line
        "1791496800,who,container,plex,5,geopend,/mnt/trunk/a.mkv",
        "1791496801,who,container,plex,5,geopend,/mnt/trunk/b.mkv",
        "1791496802,proc,5,Plex Transcoder",              # same PID, other name (exec)
        "1791496802,who,container,plex,5,geopend,/mnt/trunk/c.mkv",
        "1791496803,who,unraid,mover,6,verplaatst,/mnt/trunk/d.mkv",   # not a container: no proc
    ]


def test_proc_line_is_streamed_before_its_who_line(sd):
    import queue
    q = queue.Queue()
    sd.streams.append(q)
    try:
        sd.write_who("1791496800", "container", "plex", "5", "geopend", "/mnt/trunk/a.mkv", comm="Plex, Media")
    finally:
        sd.streams.clear()
    assert q.get_nowait() == "1791496800,proc,5,Plex  Media"      # comma replaced
    assert q.get_nowait().startswith("1791496800,who,container,plex,5,")


def test_handle_passes_the_fatrace_process_name(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sd, "classify", lambda pid, comm: ("container", "plex"))
    sd.handle("1.0 Plex Media Scan(5): O   /mnt/trunk/Media/a.mkv\n")
    assert calls == [(("1", "container", "plex", "5", "geopend", "/mnt/trunk/Media/a.mkv"), {"comm": "Plex Media Scan"})]


# ---------- ctr: container lifecycle ----------
def ev(action, name="radarr", t=1791496800, **attrs):
    return {"Type": "container", "Action": action, "time": t, "Actor": {"Attributes": {"name": name, **attrs}}}


def test_ctr_line_for_start_die_and_health():
    assert __import__("spindash").ctr_line(ev("start")) == "1791496800,ctr,radarr,start"
    sd = __import__("spindash")
    assert sd.ctr_line(ev("die", exitCode="137")) == "1791496800,ctr,radarr,die,137"
    assert sd.ctr_line(ev("die")) == "1791496800,ctr,radarr,die"
    assert sd.ctr_line(ev("health_status: unhealthy")) == "1791496800,ctr,radarr,unhealthy"
    assert sd.ctr_line(ev("health_status: healthy")) == "1791496800,ctr,radarr,healthy"
    assert sd.ctr_line(ev("exec_create: /bin/true")) is None             # healthcheck noise
    assert sd.ctr_line(ev("start", name="")) is None
    assert sd.ctr_line({**ev("start"), "Type": "network"}) is None


def test_docker_events_writes_ctr_lines_until_the_stream_closes(sd, monkeypatch):
    fake = FakeDocker({"/events": (json.dumps(ev("start")), "geen json", json.dumps(ev("exec_start: x")),
                                   json.dumps(ev("die", t=1791496860, exitCode="0")))})
    try:
        monkeypatch.setattr(sd, "DOCKER_SOCK", fake.path)
        sd.docker_events_once()
    finally:
        fake.close()
    assert open(sd.day_path("2026-10-09")).read().splitlines() == [
        "1791496800,ctr,radarr,start", "1791496860,ctr,radarr,die,0"]
    assert json.loads(__import__("urllib.parse").parse.unquote(fake.requests[0].split("filters=")[1])) == {"type": ["container"]}


def test_docker_events_without_a_socket_logs_and_stops(sd):
    logged = []
    sd.log = lambda *a: logged.append(" ".join(map(str, a)))
    sd.docker_events()                                   # returns right away
    assert "No Docker socket" in logged[0]


# ---------- containers.json: mounts and where the share lives ----------
SHARES = '''["appdata"]
name="appdata"
useCache="only"
cachePool="cache"
["Media"]
name="Media"
useCache="yes"
cachePool="cache"
["isos"]
useCache="no"
["downloads"]
useCache="prefer"
cachePool="trunk"
'''


@pytest.fixture
def unraid(sd):
    (sd._tmp / "emhttp" / "shares.ini").write_text(SHARES)
    (sd._tmp / "emhttp" / "disks.ini").write_text(
        '["disk1"]\ndevice="sdc"\n["trunk"]\ndevice="sdd"\n["cache"]\ndevice="nvme0n1"\n')
    make_disk(sd._tmp, "sdc")
    make_disk(sd._tmp, "sdd")
    make_disk(sd._tmp, "nvme0n1", rotational=False)
    return sd


def test_read_shares_ini_and_share_storage(unraid):
    sh = unraid.read_shares_ini()
    assert sh["appdata"]["useCache"] == "only"
    assert unraid.share_storage(sh["appdata"]) == ("pool", "cache")
    assert unraid.share_storage(sh["Media"]) == ("pool>array", "cache")
    assert unraid.share_storage(sh["isos"]) == ("array", "cache")
    assert unraid.share_storage(sh["downloads"]) == ("array>pool", "trunk")
    assert unraid.share_storage({}) == ("unknown", "cache")
    assert unraid.share_storage({"shareUseCache": "only", "shareCachePool": "fast"}) == ("pool", "fast")


def test_locate_says_where_a_path_lives_and_whether_that_is_an_hdd(unraid):
    sh, hdd = unraid.read_shares_ini(), {"disk1", "trunk"}
    L = lambda p: unraid.locate(p, sh, hdd)
    assert L("/mnt/user/appdata/plex") == {"kind": "share", "share": "appdata", "storage": "pool", "pool": "cache", "hdd": False}
    assert L("/mnt/user/Media")["hdd"] is True                        # ends up on the array through the mover
    assert L("/mnt/user/downloads")["hdd"] is True                    # pool trunk is an HDD pool
    assert L("/mnt/user0/appdata") == {"kind": "share", "share": "appdata", "storage": "array", "pool": "cache", "hdd": True}
    assert L("/mnt/user/unknown")["storage"] == "unknown" and L("/mnt/user/unknown")["hdd"] is None
    assert L("/mnt/user") == {"kind": "user", "hdd": True}
    assert L("/mnt/disk1/appdata") == {"kind": "disk", "name": "disk1", "hdd": True}
    assert L("/mnt/cache/appdata") == {"kind": "pool", "name": "cache", "hdd": False}
    assert L("/var/run/docker.sock") is None
    assert L("/mnt") is None


def test_containers_info_lists_mounts_under_mnt(unraid, monkeypatch):
    fake = FakeDocker({"/containers/json": [
        {"Names": ["/plex"], "State": "running", "Labels": {},
         "Mounts": [{"Source": "/mnt/user/appdata/plex", "Destination": "/config", "RW": True},
                    {"Source": "/mnt/user/Media", "Destination": "/media", "RW": False},
                    {"Source": "/tmp/x", "Destination": "/transcode", "RW": True}]},
        {"Names": ["/radarr"], "State": "exited", "Labels": {"com.docker.compose.project": "arr"}, "Mounts": []},
    ]})
    try:
        monkeypatch.setattr(unraid, "DOCKER_SOCK", fake.path)
        body = unraid.containers_info(now=1000)
        assert unraid.containers_info(now=1010) is body              # cached for 30 s
        assert sorted(r.split('?')[0] for r in fake.requests) == ['/containers/json', '/volumes']   # one refresh
    finally:
        fake.close()
    assert body["docker"] is True
    plex, radarr = body["containers"]
    assert (plex["name"], plex["state"], radarr["project"]) == ("plex", "running", "arr")
    assert [m["target"] for m in plex["mounts"]] == ["/config", "/media"]   # /tmp is dropped
    assert plex["mounts"][0]["where"]["storage"] == "pool"
    assert plex["mounts"][1] == {"source": "/mnt/user/Media", "target": "/media", "rw": False,
                                 "where": {"kind": "share", "share": "Media", "storage": "pool>array", "pool": "cache", "hdd": True}}


def test_containers_json_endpoint_without_docker(sd):
    import threading, urllib.request
    from functools import partial
    srv = sd.http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(sd.Handler, directory=str(sd._tmp)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/data/containers.json", timeout=5).read())
    finally:
        srv.shutdown()
    assert body == {"docker": False, "containers": []}


# ---------- old data stays valid ----------
def test_old_day_files_without_proc_or_ctr_lines_still_work(sd):
    """proc and ctr are only added: old day files lack them and need no conversion."""
    os.makedirs(sd.DAYS)
    open(sd.day_path("2026-10-08"), "w").write(
        "1791410400,disk,sdd,trunk\n1791410400,state,sdd,standby\n"
        "1791420000,who,container,plex,1,geopend,/mnt/trunk/a.mkv\n1791420010,spin,sdd,active\n")
    assert sd.spins_body()[0].decode().splitlines() == [
        "1791410400,disk,sdd,trunk", "1791410400,state,sdd,standby", "1791420010,spin,sdd,active"]
    assert sd.days_index()["days"] == ["2026-10-08"]


def test_named_volumes_that_bind_a_folder_count_as_that_folder(unraid, monkeypatch):
    """As on a real server: Compose volumes with driver_opts type=none, o=bind, device=/mnt/..."""
    fake = FakeDocker({
        "/containers/json": [{"Names": ["/plex"], "State": "running", "Mounts": [
            {"Type": "volume", "Name": "appdata", "Source": "/var/lib/docker/volumes/appdata/_data", "Destination": "/config", "RW": True},
            {"Type": "volume", "Name": "media", "Source": "/var/lib/docker/volumes/media/_data", "Destination": "/media", "RW": True},
            {"Type": "volume", "Name": "cache", "Source": "/var/lib/docker/volumes/cache/_data", "Destination": "/cache", "RW": True}]}],
        "/volumes": {"Volumes": [
            {"Name": "appdata", "Driver": "local", "Options": {"device": "/mnt/cache/appdata", "o": "bind", "type": "none"}},
            {"Name": "media", "Driver": "local", "Options": {"device": "/mnt/trunk/Media", "o": "bind,ro", "type": "none"}},
            {"Name": "cache", "Driver": "local", "Options": None}]},       # plain volume: dropped
    })
    try:
        monkeypatch.setattr(unraid, "DOCKER_SOCK", fake.path)
        plex = unraid.containers_info(now=1)["containers"][0]
    finally:
        fake.close()
    assert [(m["volume"], m["source"], m["where"]["hdd"]) for m in plex["mounts"]] == [
        ("appdata", "/mnt/cache/appdata", False), ("media", "/mnt/trunk/Media", True)]


def test_a_client_that_hangs_up_leaves_no_traceback(sd, capsys):
    srv = sd.Server(("127.0.0.1", 0), sd.Handler)
    try:
        for exc in (ConnectionResetError(104, "reset"), BrokenPipeError(32, "pipe")):
            try:
                raise exc
            except OSError:
                srv.handle_error(None, ("127.0.0.1", 1))
        assert capsys.readouterr().err == ""
        try:
            raise ValueError("echt stuk")
        except ValueError:
            srv.handle_error(None, ("127.0.0.1", 1))
        assert "ValueError: echt stuk" in capsys.readouterr().err
    finally:
        srv.server_close()
