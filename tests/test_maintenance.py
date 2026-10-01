from conftest import make_proc

HEX = "b" * 64


def test_epoch_of(sd):
    assert sd.epoch_of("1790680753,container,x,1,geopend,/p") == 1790680753
    assert sd.epoch_of("2026-10-01T04:15:00+02:00,/dev/sdd,active") == 1790820900
    assert sd.epoch_of("garbage,line") is None


def test_trim_keeps_recent_and_unparseable(sd, monkeypatch):
    monkeypatch.setattr(sd, "RETENTION_DAYS", 1)
    now = 1_790_000_000
    open(sd.WHO, "w").write(
        f"{now - 3 * 86400},container,old,1,geopend,/a\n"
        f"{now - 3600},container,new,1,geopend,/b\n"
        "rommel\n"
    )
    sd.trim(sd.WHO, now=now)
    assert open(sd.WHO).read().splitlines() == [
        f"{now - 3600},container,new,1,geopend,/b",
        "rommel",
    ]


def test_relabel_existing(sd, monkeypatch):
    make_proc(sd._tmp, 42, "python3", cgroup=f"0::/../{HEX}\n")
    make_proc(sd._tmp, 43, "bash")
    monkeypatch.setattr(sd, "container_name", lambda cid: "bazarr")
    open(sd.WHO, "w").write(
        "1,unraid,python3,42,geopend,/mnt/trunk/a.mkv\n"   # wordt bazarr
        "2,unraid,webGUI,42,geopend,/mnt/trunk/\n"          # naam klopt niet met proces
        "3,unraid,bash,43,geopend,/mnt/trunk/b\n"           # geen container
        "4,unraid,python3,999,geopend,/mnt/trunk/c\n"       # proces bestaat niet meer
        "5,container,plex,1,geopend,/mnt/trunk/d\n"
    )
    sd.relabel_existing()
    lines = open(sd.WHO).read().splitlines()
    assert lines[0] == "1,container,bazarr,42,geopend,/mnt/trunk/a.mkv"
    assert lines[1:] == [
        "2,unraid,webGUI,42,geopend,/mnt/trunk/",
        "3,unraid,bash,43,geopend,/mnt/trunk/b",
        "4,unraid,python3,999,geopend,/mnt/trunk/c",
        "5,container,plex,1,geopend,/mnt/trunk/d",
    ]
