import spindash
from conftest import make_proc

HEX = "a" * 64


def test_line_regex():
    m = spindash.LINE.match("1790680753.123456 python3(4567): O   /mnt/trunk/Media/a b, c.mkv")
    assert m.groups() == ("1790680753", "python3", "4567", "O", "/mnt/trunk/Media/a b, c.mkv")
    m = spindash.LINE.match("1790680753.123456 weird (name)(12): +   /mnt/trunk/x exe=/usr/bin/x")
    assert m.group(2) == "weird (name)" and m.group(5) == "/mnt/trunk/x"
    assert spindash.LINE.match("1790680753.1 bash(1): O   (deleted)") is None


def test_line_regex_write_flags():
    m = spindash.LINE.match("1790680753.123456 plex(4567): W   /mnt/trunk/Media/a.mkv")
    assert m.groups() == ("1790680753", "plex", "4567", "W", "/mnt/trunk/Media/a.mkv")
    m = spindash.LINE.match("1790680753.123456 plex(4567): OW  /mnt/trunk/Media/a.mkv")
    assert m.groups() == ("1790680753", "plex", "4567", "OW", "/mnt/trunk/Media/a.mkv")
    m = spindash.LINE.match("1790680753.123456 plex(4567): ORW /mnt/trunk/Media/a.mkv")
    assert m.groups() == ("1790680753", "plex", "4567", "ORW", "/mnt/trunk/Media/a.mkv")


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


def test_classify_ignores_cache_dirs_and_children(sd, monkeypatch):
    t = sd._tmp
    make_proc(t, 900, "cache_dirs")
    make_proc(t, 901, "timeout", ppid=900)
    make_proc(t, 902, "find", ppid=901)
    make_proc(t, 903, "find")
    assert sd.classify_now("900", "cache_dirs") is None
    assert sd.classify_now("902", "find") is None
    assert sd.classify_now("903", "find") == ("unraid", "find")
    monkeypatch.setattr(sd, "IGNORE_PROCS", set())
    assert sd.classify_now("902", "find") == ("unraid", "find")


def test_classify_remembers_pid_after_process_stopped(sd):
    import shutil
    t = sd._tmp
    make_proc(t, 900, "cache_dirs")
    make_proc(t, 902, "find", ppid=900)
    make_proc(t, 950, "du")
    assert sd.classify("902", "find", now=1000) is None
    assert sd.classify("950", "du", now=1000) == ("unraid", "du")
    shutil.rmtree(t / "proc" / "902")
    shutil.rmtree(t / "proc" / "950")
    assert sd.classify("902", "find", now=1010) is None                   # stopped, still ignored
    assert sd.classify("950", "du", now=1010) == ("unraid", "du")
    assert sd.classify("950", "du", now=1040) == ("onbekend", "du (al gestopt)")       # after PID_TTL
    assert sd.classify("950", "ls", now=1010) == ("onbekend", "ls (al gestopt)")       # PID reused
    assert sd.classify("951", "x", now=1010) == ("onbekend", "x (al gestopt)")
    assert "951" not in sd.seen                                            # unknown is not remembered


def test_classify_ignores_script_started_via_bash(sd):
    t = sd._tmp
    d = make_proc(t, 900, "bash")
    (d / "cmdline").write_bytes(b"/bin/bash\0/usr/local/emhttp/plugins/dynamix.cache.dirs/scripts/cache_dirs\0-i\0Media\0")
    make_proc(t, 901, "timeout", ppid=900)
    make_proc(t, 902, "find", ppid=901)
    assert sd.classify_now("902", "find") is None


def test_classify_safety_net_for_orphaned_and_stopped(sd):
    t = sd._tmp
    make_proc(t, 900, "cache_dirs")
    make_proc(t, 901, "timeout", ppid=900)
    make_proc(t, 902, "find", ppid=901)
    make_proc(t, 911, "timeout")                       # parent already gone: orphaned
    make_proc(t, 912, "find", ppid=911)
    make_proc(t, 800, "crond")
    make_proc(t, 801, "find", ppid=800)
    assert sd.classify("912", "find", now=1000) == ("unraid", "find")       # nothing ignored yet
    sd.seen.clear()
    assert sd.classify("902", "find", now=1000) is None
    assert sd.classify("912", "find", now=1001) is None                     # orphaned
    assert sd.classify("999", "find", now=1002) is None                     # already stopped
    assert sd.classify("801", "find", now=1003) == ("unraid", "cron / User Scripts")
    assert sd.classify("998", "ls", now=1004) == ("onbekend", "ls (al gestopt)")
    assert sd.classify("997", "find", now=1400) == ("onbekend", "find (al gestopt)")  # after IGNORE_TTL


def test_classify_early_then_handle_after_process_stopped(sd, monkeypatch):
    import shutil
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    make_proc(sd._tmp, 900, "cache_dirs")
    make_proc(sd._tmp, 901, "timeout", ppid=900)
    make_proc(sd._tmp, 902, "find", ppid=901)
    line = "1.0 find(902): O   /mnt/trunk/Media/series/\n"
    sd.classify_early(line)
    shutil.rmtree(sd._tmp / "proc" / "902")
    shutil.rmtree(sd._tmp / "proc" / "901")
    sd.handle(line)
    sd.handle("1.0 find(902): O   /mnt/trunk/Media/films/\n")
    assert calls == []


def test_handle_skips_ignored_processes(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    make_proc(sd._tmp, 900, "cache_dirs")
    make_proc(sd._tmp, 902, "find", ppid=900)
    sd.handle("1.0 find(902): O   /mnt/trunk/Media/series/\n")
    assert calls == []


def test_share_root():
    assert spindash.share_root("/mnt/trunk/Media/films/a.mkv") == "/mnt/trunk/Media/"
    assert spindash.share_root("/mnt/trunk/Media/") == "/mnt/trunk/Media/"
    assert spindash.share_root("/mnt/trunk/") == "/mnt/trunk/"


def test_write_who_dedup_and_smb_collapse(sd):
    sd.write_who("100", "container", "plex", "1", "geopend", "/mnt/trunk/Media/a.mkv", now=1000)
    sd.write_who("101", "container", "plex", "1", "geopend", "/mnt/trunk/Media/a.mkv", now=1100)   # within 10 min
    sd.write_who("102", "container", "plex", "1", "geopend", "/mnt/trunk/Media/a.mkv", now=1700)   # after it
    sd.write_who("103", "gebruiker", "SMB-share", "2", "geopend", "/mnt/trunk/Media/x/1.mkv", now=1000)
    sd.write_who("104", "gebruiker", "SMB-share", "2", "geopend", "/mnt/trunk/Media/y/2.mkv", now=1001)
    sd.write_who("105", "container", "a,b", "3", "geopend", "/mnt/trunk/z", now=1000)
    lines = open(sd.day_path("1970-01-01")).read().splitlines()
    assert lines == [
        "100,who,container,plex,1,geopend,/mnt/trunk/Media/a.mkv",
        "102,who,container,plex,1,geopend,/mnt/trunk/Media/a.mkv",
        "103,who,gebruiker,SMB-share,2,geopend,/mnt/trunk/Media/",
        "105,who,container,a b,3,geopend,/mnt/trunk/z",
    ]


def test_write_who_dedup_keeps_read_and_write_apart(sd):
    """Reading and writing the same path within 10 minutes each count once."""
    sd.write_who("100", "container", "plex", "1", "geopend", "/mnt/trunk/Media/a.mkv", now=1000)
    sd.write_who("101", "container", "plex", "1", "geschreven", "/mnt/trunk/Media/a.mkv", now=1100)
    sd.write_who("102", "container", "plex", "1", "geschreven", "/mnt/trunk/Media/a.mkv", now=1200)  # same op: suppressed
    lines = open(sd.day_path("1970-01-01")).read().splitlines()
    assert lines == [
        "100,who,container,plex,1,geopend,/mnt/trunk/Media/a.mkv",
        "101,who,container,plex,1,geschreven,/mnt/trunk/Media/a.mkv",
    ]


def test_handle_write_events(sd, monkeypatch):
    """A W event and an open with write intent (OW) are both 'geschreven'."""
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sd, "classify", lambda pid, comm: ("container", comm))
    sd.handle("1.0 plex(5): W   /mnt/trunk/Media/a.mkv\n")
    sd.handle("1.0 plex(5): OW  /mnt/trunk/Media/b.mkv\n")
    sd.handle("1.0 plex(5): ORW /mnt/trunk/Media/c.mkv\n")
    sd.handle("1.0 plex(5): O   /mnt/trunk/Media/d.mkv\n")   # read only
    assert calls == [
        ("1", "container", "plex", "5", "geschreven", "/mnt/trunk/Media/a.mkv"),
        ("1", "container", "plex", "5", "geschreven", "/mnt/trunk/Media/b.mkv"),
        ("1", "container", "plex", "5", "geschreven", "/mnt/trunk/Media/c.mkv"),
        ("1", "container", "plex", "5", "geopend", "/mnt/trunk/Media/d.mkv"),
    ]


def test_own_day_files_are_not_logged(sd):
    assert sd.is_own_file("/mnt/cache/appdata/spin-dashboard/days/2026-10-09.csv")
    assert sd.is_own_file("/mnt/cache/appdata/spin-dashboard/days/2026-10-09.csv.tmp")
    assert sd.is_own_file("/mnt/cache/appdata/spin-dashboard/who.csv")
    assert sd.is_own_file("/mnt/cache/appdata/spin-dashboard/spindash-settings.json")
    assert sd.is_own_file("/mnt/cache/appdata/spin-dashboard/spindash-settings.json.tmp")
    assert not sd.is_own_file("/mnt/trunk/Media/2026-10-09.mkv")


def test_handle_skips_own_writes_and_marks_dirs(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sd, "classify", lambda pid, comm: ("container", comm))
    d = sd._tmp / "pool" / "Share"
    d.mkdir(parents=True)
    sd.handle(f"1.0 x({sd.MY_PID}): O   /mnt/trunk/a.mkv\n")
    sd.handle("1.0 x(5): O   /mnt/frunk/appdata/spin-dashboard/who.csv\n")
    sd.handle(f"1.0 plex(5): O   {d}\n")
    sd.handle("1.0 plex(5): C   /mnt/trunk/a.mkv\n")              # close only: not logged
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


def test_stopped_dir_scans_are_dropped_while_cache_dirs_runs(sd, monkeypatch):
    calls = []
    monkeypatch.setattr(sd, "write_who", lambda *a, **k: calls.append(a))
    d = sd._tmp / "pool" / "fotos"
    d.mkdir(parents=True)
    sd.handle(f"1.0 unknown(4242): O   {d}\n")                   # no cache_dirs yet: log it
    assert [c[2] for c in calls] == ["unknown (al gestopt)"]
    make_proc(sd._tmp, 900, "cache_dirs")
    monkeypatch.setattr(sd, "_ignore_seen", {"t": None, "on": False})
    calls.clear()
    sd.handle(f"1.0 unknown(4243): O   {d}\n")                   # folder, process gone: do not log
    sd.handle("1.0 unknown(4244): O   /mnt/trunk/Media/fotos/a.jpg\n")   # file: log it
    make_proc(sd._tmp, 950, "ls")
    sd.handle(f"1.0 ls(950): O   {d}\n")                         # running process with a source: log it
    assert [(c[2], c[5]) for c in calls] == [("unknown (al gestopt)", "/mnt/trunk/Media/fotos/a.jpg"),
                                            ("ls", f"{d}/")]


def test_ignore_active_is_cached_and_reads_command_lines(sd):
    assert sd.ignore_active(now=1000) is False
    p = make_proc(sd._tmp, 900, "bash")
    (p / "cmdline").write_bytes(b"/bin/bash\0/usr/local/emhttp/plugins/dynamix.cache.dirs/scripts/cache_dirs\0")
    assert sd.ignore_active(now=1010) is False                  # still within IGNORE_SCAN: remembered
    assert sd.ignore_active(now=1031) is True


def test_via_user_share_reuses_one_fd_scan_within_a_second(sd, monkeypatch):
    """A scan through /mnt/user gives thousands of shfs events; /proc/*/fd is read only once per second."""
    t = sd._tmp
    for name in ("a.mkv", "b.mkv"):
        f = t / "user" / "Media" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    p = make_proc(t, 300, "Plex Media Serv")
    (p / "fd" / "5").symlink_to(t / "user" / "Media" / "a.mkv")
    scans = []
    real = sd.os.listdir
    monkeypatch.setattr(sd.os, "listdir", lambda d: (scans.append(d) if d == sd.PROC else None) or real(d))
    clock = [1000.0]
    monkeypatch.setattr(sd.time, "time", lambda: clock[0])
    assert sd.via_user_share("/mnt/trunk/Media/a.mkv", since=1000.0) == ("300", "Plex Media Serv")
    clock[0] = 1000.5
    assert sd.via_user_share("/mnt/trunk/Media/a.mkv", since=999.9) == ("300", "Plex Media Serv")
    assert len(scans) == 1                                   # second time from the snapshot
    # a file opened after the snapshot: the snapshot is too old for this event and is renewed
    (p / "fd" / "6").symlink_to(t / "user" / "Media" / "b.mkv")
    assert sd.via_user_share("/mnt/trunk/Media/b.mkv", since=1000.4) == ("300", "Plex Media Serv")
    assert len(scans) == 2
    clock[0] = 1002.0                                        # older than a second: again
    sd.via_user_share("/mnt/trunk/Media/a.mkv", since=990.0)
    assert len(scans) == 3
