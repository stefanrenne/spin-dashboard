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
    assert unraid.share_storage(sh["appdata"]) == ("pool", "cache", "")
    assert unraid.share_storage(sh["Media"]) == ("pool>array", "cache", "")
    assert unraid.share_storage(sh["isos"]) == ("array", "cache", "")
    assert unraid.share_storage(sh["downloads"]) == ("array>pool", "trunk", "")
    assert unraid.share_storage({}) == ("unknown", "cache", "")
    assert unraid.share_storage({"shareUseCache": "only", "shareCachePool": "fast"}) == ("pool", "fast", "")


def test_a_second_pool_as_secondary_storage(unraid):
    """Unraid 7 (webgui sbin/mover): with cachePool2 set the mover moves between the two pools, not the array."""
    S = unraid.share_storage
    assert S({"useCache": "yes", "cachePool": "frunk", "cachePool2": "trunk"}) == ("pool>pool2", "frunk", "trunk")
    assert S({"useCache": "prefer", "cachePool": "frunk", "cachePool2": "trunk"}) == ("pool2>pool", "frunk", "trunk")
    assert S({"useCache": "only", "cachePool": "frunk", "cachePool2": "trunk"}) == ("pool", "frunk", "")
    assert S({"useCache": "only", "cachePool": "", "cachePool2": "trunk"}) == ("pool", "trunk", "")   # only cachePool2: the primary
    shares = {"Media": {"useCache": "yes", "cachePool": "cache", "cachePool2": "trunk"}}
    where = unraid.locate("/mnt/user/Media/a.mkv", shares, {"trunk"})
    assert where == {"kind": "share", "share": "Media", "storage": "pool>pool2", "pool": "cache", "pool2": "trunk", "hdd": True}
    assert unraid.locate("/mnt/user/Media/a.mkv", shares, {"disk1"})["hdd"] is False     # no array involved
    assert unraid.share_locations("Media", shares) == ["cache", "trunk"]                  # not the array disks


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
        assert sorted(r.split('?')[0] for r in fake.requests) == ['/containers/json', '/info', '/volumes']   # one refresh
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
    assert body == {"docker": False, "containers": [], "docker_root": None}


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


def test_container_name_only_remembers_real_names(sd, monkeypatch):
    """When Docker does not answer once, the container is not stuck with its short ID."""
    cid = "a" * 64
    answers, asked = [None, [{"Id": cid, "Names": ["/plex"]}]], []
    monkeypatch.setattr(sd, "docker_get", lambda path, timeout=2: asked.append(path) or answers.pop(0))
    assert sd.container_name(cid, now=1000) == "a" * 12
    assert sd.container_name(cid, now=1030) == "a" * 12        # not asked again within NAME_RETRY
    assert len(asked) == 1
    assert sd.container_name(cid, now=1061) == "plex"
    assert sd.container_name(cid, now=1062) == "plex" and len(asked) == 2


def test_container_name_resolves_every_container_in_one_call(sd, monkeypatch):
    """Based on PR #2 (RuudPuts): one /containers/json call names all containers, also the next ones asked."""
    asked = []
    monkeypatch.setattr(sd, "docker_get", lambda path, timeout=2: asked.append(path) or [
        {"Id": "a" * 64, "Names": ["/plex"]}, {"Id": "b" * 64, "Names": ["/radarr"]}])
    assert sd.container_name("a" * 64, now=1000) == "plex"
    assert sd.container_name("b" * 64, now=1000) == "radarr"
    assert asked == ["/containers/json?all=1"]
    assert sd.container_name("c" * 64, now=1000) == "c" * 12       # unknown to Docker: short ID for now
    assert sd.container_name("c" * 64, now=1002) == "c" * 12 and len(asked) == 2
    sd.container_name("c" * 64, now=1004)                           # Docker answered, so it asks again soon
    assert len(asked) == 3


def test_a_container_being_created_gets_its_name_within_seconds(sd, monkeypatch):
    """Compose recreating a container: the first events come before Docker lists it. Seen on the own server:
    a minute of plex's events was logged with the short ID while the retry waited 60 s."""
    cid = "0" * 63 + "1"
    answers = [[], [{"Id": cid, "Names": ["/plex"]}]]
    monkeypatch.setattr(sd, "docker_get", lambda path, timeout=2: answers.pop(0))
    assert sd.container_name(cid, now=1000) == cid[:12]
    assert sd.container_name(cid, now=1004) == "plex"


PNG = b"\x89PNG\r\n\x1a\n fake"
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def icon_file(sd, name, data=PNG):
    os.makedirs(sd.DOCKER_ICONS, exist_ok=True)
    with open(os.path.join(sd.DOCKER_ICONS, name + "-icon.png"), "wb") as f:
        f.write(data)


def test_containers_info_points_to_the_unraid_icon(unraid, monkeypatch):
    icon_file(unraid, "plex")
    fake = FakeDocker({"/containers/json": [
        {"Names": ["/plex"], "State": "running", "Labels": {}, "Mounts": []},
        {"Names": ["/radarr"], "State": "running", "Labels": {}, "Mounts": []}]})
    try:
        monkeypatch.setattr(unraid, "DOCKER_SOCK", fake.path)
        plex, radarr = unraid.containers_info(now=1000)["containers"]
    finally:
        fake.close()
    assert (plex["icon"], radarr["icon"]) == ("data/icons/plex.png", "")
    assert plex["id"] == ""                       # the fake gives no Id; Docker does


def test_containers_info_gives_the_container_id(unraid, monkeypatch):
    fake = FakeDocker({"/containers/json": [{"Id": "f" * 64, "Names": ["/plex"], "State": "running", "Labels": {}, "Mounts": []}]})
    try:
        monkeypatch.setattr(unraid, "DOCKER_SOCK", fake.path)
        assert unraid.containers_info(now=1000)["containers"][0]["id"] == "f" * 64
    finally:
        fake.close()


def test_icons_are_served_from_the_unraid_folder_only(sd):
    import threading, urllib.request, urllib.error
    from functools import partial
    icon_file(sd, "plex")
    with open(os.path.join(sd.EMHTTP, "shares.ini"), "w") as f:
        f.write("secret")
    srv = sd.http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(sd.Handler, directory=str(sd._tmp)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/data/icons/"

    def status(name):
        try:
            r = urllib.request.urlopen(base + name, timeout=5)
            return r.status, r.read(), r.headers.get("Content-Type")
        except urllib.error.HTTPError as e:
            return e.code, b"", None
    try:
        assert status("plex.png") == (200, PNG, "image/png")
        assert status("radarr.png")[0] == 404
        for evil in ("..%2F..%2Fshares.ini%00.png", "%2E%2E.png", "..%2Fx.png", ".hidden.png"):
            assert status(evil)[0] == 404, evil
    finally:
        srv.shutdown()


def icon_server(sd):
    import threading
    from functools import partial
    srv = sd.http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(sd.Handler, directory=str(sd._tmp)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/data/icons/"


def test_an_svg_saved_as_png_is_served_as_svg_that_runs_nothing(sd):
    """Unraid stores every icon as <name>-icon.png, also an SVG (seen: music-assistant-server)."""
    import urllib.request
    icon_file(sd, "music", SVG)
    icon_file(sd, "junk", b"not an image")
    srv, base = icon_server(sd)
    try:
        r = urllib.request.urlopen(base + "music.png", timeout=5)
        assert r.headers["Content-Type"] == "image/svg+xml"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in r.headers["Content-Security-Policy"] and "default-src 'none'" in r.headers["Content-Security-Policy"]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "junk.png", timeout=5)          # not an image: not served
    finally:
        srv.shutdown()


def test_icon_type_from_the_content(sd):
    T = sd.icon_type
    assert T(PNG) == "image/png" and T(b"\xff\xd8\xff\xe0") == "image/jpeg" and T(b"GIF89a") == "image/gif"
    assert T(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert T(SVG) == "image/svg+xml" and T(b"  <svg/>") == "image/svg+xml"
    assert T(b"<html><body>") is None and T(b"") is None


def test_a_container_with_only_an_icon_label_gets_the_image_through_the_server(unraid, monkeypatch):
    fetched = []

    class Resp:
        def __init__(self, data): self.data = data
        def read(self, n): return self.data[:n]
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def urlopen(req, timeout):
        fetched.append(req.full_url)
        if "broken" in req.full_url:
            raise OSError("down")
        return Resp(PNG)
    monkeypatch.setattr(unraid.urllib.request, "urlopen", urlopen)
    fake = FakeDocker({"/containers/json": [
        {"Names": ["/sab"], "State": "running", "Labels": {"net.unraid.docker.icon": "https://cdn.example/sab.png"}, "Mounts": []},
        {"Names": ["/odd"], "State": "running", "Labels": {"net.unraid.docker.icon": "file:///etc/passwd"}, "Mounts": []},
        {"Names": ["/down"], "State": "running", "Labels": {"net.unraid.docker.icon": "https://broken.example/x.png"}, "Mounts": []}]})
    try:
        monkeypatch.setattr(unraid, "DOCKER_SOCK", fake.path)
        icons = {c["name"]: c["icon"] for c in unraid.containers_info(now=1000)["containers"]}
        assert icons == {"down": "data/icons/down.png", "odd": "", "sab": "data/icons/sab.png"}
        assert unraid.icon_bytes("sab") == PNG
        assert unraid.icon_bytes("sab") == PNG and fetched == ["https://cdn.example/sab.png"]   # kept in memory
        assert unraid.icon_bytes("odd") is None                                               # only http(s)
        assert unraid.icon_bytes("down") is None
        unraid.icon_bytes("down")
        assert fetched.count("https://broken.example/x.png") == 1                              # not again right away
        icon_file(unraid, "sab", SVG)
        assert unraid.icon_bytes("sab") == SVG                                                # Unraid's file comes first
    finally:
        fake.close()


# the host's mount table on the own server: /var/lib/docker is the folder appdata/docker of the btrfs pool frunk
HOST_MOUNTS = """51 44 0:44 / /mnt/frunk rw,noatime shared:6 - btrfs /dev/nvme1n1p4 rw,ssd
53 1 0:44 /appdata/docker /var/lib/docker rw,noatime shared:6 - btrfs /dev/nvme1n1p4 rw,ssd
88 53 0:54 / /var/lib/docker/overlay2/84b9/merged rw,relatime shared:10 - overlay overlay rw
60 44 0:60 / /mnt/user rw shared:9 - fuse.shfs shfs rw
61 53 0:44 /appdata /var/lib/docker/volumes/appdata/_data rw shared:6 - btrfs /dev/nvme1n1p4 rw
62 51 0:44 /appdata /mnt/frunk/appdata/docker/volumes/appdata/_data rw shared:6 - btrfs /dev/nvme1n1p4 rw
"""


def test_docker_root_seen_under_mnt(sd, monkeypatch):
    (sd._tmp / "mountinfo-host").write_text(HOST_MOUNTS)
    monkeypatch.setattr(sd, "HOST_MOUNTINFO", str(sd._tmp / "mountinfo-host"))
    assert sd.host_path("/var/lib/docker") == "/mnt/frunk/appdata/docker"
    assert sd.host_path("/var/lib/docker/containers") == "/mnt/frunk/appdata/docker/containers"
    assert sd.host_path("/mnt/trunk/x") == "/mnt/trunk/x"
    assert sd.host_path("/etc") is None


def test_docker_root_in_a_docker_img_or_a_zfs_dataset(sd, monkeypatch):
    host = sd._tmp / "mountinfo-host"
    monkeypatch.setattr(sd, "HOST_MOUNTINFO", str(host))
    host.write_text("70 1 7:2 / /var/lib/docker rw - btrfs /dev/loop2 rw\n")
    os.makedirs(sd._tmp / "sys" / "loop2" / "loop")
    (sd._tmp / "sys" / "loop2" / "loop" / "backing_file").write_text("/mnt/cache/system/docker/docker.img\n")
    assert sd.host_path("/var/lib/docker") == "/mnt/cache/system/docker/docker.img"
    host.write_text("71 1 0:80 / /var/lib/docker rw - zfs fast/docker rw\n")
    assert sd.host_path("/var/lib/docker") == "/mnt/fast/"


def test_containers_json_says_where_docker_keeps_the_containers(unraid, monkeypatch):
    (unraid._tmp / "mountinfo-host").write_text(HOST_MOUNTS.replace("frunk", "cache"))
    monkeypatch.setattr(unraid, "HOST_MOUNTINFO", str(unraid._tmp / "mountinfo-host"))
    fake = FakeDocker({"/containers/json": [{"Names": ["/wud"], "State": "running", "Labels": {}, "Mounts": []}],
                       "/info": {"DockerRootDir": "/var/lib/docker"}})
    try:
        monkeypatch.setattr(unraid, "DOCKER_SOCK", fake.path)
        body = unraid.containers_info(now=1000)
    finally:
        fake.close()
    assert body["docker_root"] == {"path": "/mnt/cache/appdata/docker", "where": {"kind": "pool", "name": "cache", "hdd": False}}


def test_ctr_line_for_an_oom_kill():
    sd = __import__("spindash")
    assert sd.ctr_line(ev("oom")) == "1791496800,ctr,radarr,oom"


def test_docker_events_resume_after_a_reconnect(sd, monkeypatch):
    """Events during a Docker restart are not lost: the next connection asks for what came after the last one."""
    first = {**ev("start"), "timeNano": 1791496800123456789}
    fake = FakeDocker({"/events": (json.dumps(first),)})
    try:
        monkeypatch.setattr(sd, "DOCKER_SOCK", fake.path)
        sd.docker_events_once()
        sd.docker_events_once()
    finally:
        fake.close()
    assert "since" not in fake.requests[0]
    assert fake.requests[1].endswith("&since=1791496800.123456790")


def test_dockerd_writing_a_container_log_counts_for_that_container(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sd, "classify", lambda pid, comm: ("unraid", comm))
    cid = "f" * 64
    monkeypatch.setattr(sd, "docker_get", lambda path, timeout=2: [{"Id": cid, "Names": ["/plex"]}])
    sd.handle(f"1.0 dockerd(3): W   /mnt/frunk/appdata/docker/containers/{cid}/{cid}-json.log\n")
    sd.handle("1.0 dockerd(3): W   /mnt/frunk/appdata/docker/image/overlay2/repositories.json\n")   # not one container
    assert [(c[1], c[2]) for c in calls] == [("container", "plex"), ("unraid", "dockerd")]


def test_a_path_fatrace_could_not_resolve_is_skipped(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sd, "classify", lambda pid, comm: ("container", "prowlarr"))
    sd.handle("1.0 Prowlarr(9): W   /\n")
    sd.handle("1.0 Prowlarr(9): W   / (deleted)\n")
    assert calls == []


def test_deleted_suffix_is_stripped(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sd, "classify", lambda pid, comm: ("container", "swag"))
    sd.handle("1.0 nginx(9): W   /mnt/frunk/appdata/swag/tmp/0000000001 (deleted)\n")
    assert calls[0][5] == "/mnt/frunk/appdata/swag/tmp/0000000001"
