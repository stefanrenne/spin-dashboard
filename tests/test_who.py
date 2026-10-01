import spindash
from conftest import make_proc

HEX = "a" * 64


def test_line_regex():
    m = spindash.LINE.match("1790680753.123456 python3(4567): O   /mnt/trunk/Media/a b, c.mkv")
    assert m.groups() == ("1790680753", "python3", "4567", "O", "/mnt/trunk/Media/a b, c.mkv")
    m = spindash.LINE.match("1790680753.123456 weird (name)(12): +   /mnt/trunk/x exe=/usr/bin/x")
    assert m.group(2) == "weird (name)" and m.group(5) == "/mnt/trunk/x"
    assert spindash.LINE.match("1790680753.1 bash(1): O   (deleted)") is None


def test_container_id_variants(sd):
    for cg in (f"0::/docker/{HEX}\n", f"0::/../{HEX}\n", f"0::/system.slice/docker-{HEX}.scope\n"):
        make_proc(sd._tmp, 10, "x", cgroup=cg)
        assert sd.container_id("10") == HEX
        import shutil
        shutil.rmtree(sd._tmp / "proc" / "10")
    make_proc(sd._tmp, 11, "x", cgroup="0::/user.slice\n")
    assert sd.container_id("11") is None
    assert sd.container_id("999") is None


def test_classify_container(sd, monkeypatch):
    make_proc(sd._tmp, 20, "python3", cgroup=f"0::/../{HEX}\n")
    monkeypatch.setattr(sd, "container_name", lambda cid: "bazarr")
    assert sd.classify("20", "python3") == ("container", "bazarr")


def test_classify_host_processes(sd):
    t = sd._tmp
    make_proc(t, 100, "sshd")
    make_proc(t, 101, "bash", ppid=100)
    make_proc(t, 102, "cat", ppid=101)
    make_proc(t, 200, "smbd")
    make_proc(t, 201, "smbd", ppid=200)
    make_proc(t, 300, "crond")
    make_proc(t, 301, "sh", ppid=300)
    make_proc(t, 302, "find", ppid=301)
    make_proc(t, 400, "php-fpm")
    make_proc(t, 500, "shfs")
    make_proc(t, 600, "btrfs")
    make_proc(t, 700, "mover")
    make_proc(t, 701, "rsync", ppid=700)
    make_proc(t, 800, "ttyd")
    make_proc(t, 801, "ls", ppid=800)
    assert sd.classify("102", "cat") == ("gebruiker", "shell (ssh)")
    assert sd.classify("201", "smbd") == ("gebruiker", "SMB-share")
    assert sd.classify("302", "find") == ("unraid", "cron / User Scripts")
    assert sd.classify("400", "php-fpm") == ("unraid", "webGUI")
    assert sd.classify("500", "shfs") == ("unraid", "shfs (via /mnt/user)")
    assert sd.classify("600", "btrfs") == ("unraid", "btrfs")
    assert sd.classify("701", "rsync") == ("unraid", "mover")
    assert sd.classify("801", "ls") == ("gebruiker", "shell (webterminal)")
    assert sd.classify("9999", "ghost") == ("onbekend", "ghost (al gestopt)")


def test_share_root():
    assert spindash.share_root("/mnt/trunk/Media/films/a.mkv") == "/mnt/trunk/Media/"
    assert spindash.share_root("/mnt/trunk/Media/") == "/mnt/trunk/Media/"
    assert spindash.share_root("/mnt/trunk/") == "/mnt/trunk/"


def test_write_who_dedup_and_smb_collapse(sd):
    sd.write_who("100", "container", "plex", "1", "geopend", "/mnt/trunk/Media/a.mkv", now=1000)
    sd.write_who("101", "container", "plex", "1", "geopend", "/mnt/trunk/Media/a.mkv", now=1100)   # binnen 10 min
    sd.write_who("102", "container", "plex", "1", "geopend", "/mnt/trunk/Media/a.mkv", now=1700)   # erna
    sd.write_who("103", "gebruiker", "SMB-share", "2", "geopend", "/mnt/trunk/Media/x/1.mkv", now=1000)
    sd.write_who("104", "gebruiker", "SMB-share", "2", "geopend", "/mnt/trunk/Media/y/2.mkv", now=1001)
    sd.write_who("105", "container", "a,b", "3", "geopend", "/mnt/trunk/z", now=1000)
    lines = open(sd.WHO).read().splitlines()
    assert lines == [
        "100,container,plex,1,geopend,/mnt/trunk/Media/a.mkv",
        "102,container,plex,1,geopend,/mnt/trunk/Media/a.mkv",
        "103,gebruiker,SMB-share,2,geopend,/mnt/trunk/Media/",
        "105,container,a b,3,geopend,/mnt/trunk/z",
    ]


def test_handle_skips_own_writes_and_marks_dirs(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a: calls.append(a))
    monkeypatch.setattr(sd, "classify", lambda pid, comm: ("container", comm))
    d = sd._tmp / "pool" / "Share"
    d.mkdir(parents=True)
    sd.handle(f"1.0 x({sd.MY_PID}): O   /mnt/trunk/a.mkv\n")
    sd.handle("1.0 x(5): O   /mnt/frunk/appdata/spin-dashboard/who.csv\n")
    sd.handle(f"1.0 plex(5): O   {d}\n")
    sd.handle("1.0 plex(5): C   /mnt/trunk/a.mkv\n")              # alleen close: niet gelogd
    sd.handle("1.0 rm(6): D   /mnt/trunk/gone\n")
    assert calls == [
        ("1", "container", "plex", "5", "geopend", f"{d}/"),
        ("1", "container", "rm", "6", "verwijderd", "/mnt/trunk/gone"),
    ]


def test_handle_shfs_is_resolved_later(sd, monkeypatch):
    later = []
    monkeypatch.setattr(sd, "resolve_shfs_later", lambda *a: later.append(a))
    sd.handle("1.0 shfs(77): O   /mnt/trunk/Media/a.mkv\n")
    assert later == [("1", "77", "geopend", "/mnt/trunk/Media/a.mkv")]


def test_via_user_share_finds_real_opener(sd):
    t = sd._tmp
    f = t / "user" / "Media" / "a.mkv"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    p = make_proc(t, 300, "Plex Media Serv")
    (p / "fd" / "5").symlink_to(f)
    s = make_proc(t, 301, "shfs")
    (s / "fd" / "9").symlink_to(f)
    assert sd.via_user_share("/mnt/trunk/Media/a.mkv") == ("300", "Plex Media Serv")
    assert sd.via_user_share("/mnt/trunk/Media/missing.mkv") is None
    assert sd.via_user_share("/mnt/trunk") is None
