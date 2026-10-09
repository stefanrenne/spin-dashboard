import os

from conftest import make_proc

HEX = "b" * 64
D9 = 1791496800                     # 2026-10-09 00:00 in Amsterdam
D10 = D9 + 86400


def test_epoch_of(sd):
    assert sd.epoch_of("1790680753,container,x,1,geopend,/p") == 1790680753
    assert sd.epoch_of("2026-10-01T04:15:00+02:00,/dev/sdd,active") == 1790820900
    assert sd.epoch_of("garbage,line") is None


def test_day_of_uses_local_time(sd):
    assert sd.day_of(D9 - 1) == "2026-10-08"
    assert sd.day_of(D9) == "2026-10-09"
    assert sd.day_start("2026-10-09") == D9


def test_prune_days_removes_whole_old_days(sd, monkeypatch):
    monkeypatch.setattr(sd, "RETENTION_DAYS", 2)
    os.makedirs(sd.DAYS)
    for d in ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"):
        open(sd.day_path(d), "w").write("x\n")
    sd.prune_days(now=D9 + 3600)
    assert sd.list_days() == ["2026-10-07", "2026-10-08", "2026-10-09"]


def test_relabel_existing(sd, monkeypatch):
    make_proc(sd._tmp, 42, "python3", cgroup=f"0::/../{HEX}\n")
    make_proc(sd._tmp, 43, "bash")
    monkeypatch.setattr(sd, "container_name", lambda cid: "bazarr")
    os.makedirs(sd.DAYS)
    t = D9 + 60
    open(sd.day_path("2026-10-09"), "w").write(
        f"{t},who,unraid,python3,42,geopend,/mnt/trunk/a.mkv\n"     # wordt bazarr
        f"{t},who,unraid,webGUI,42,geopend,/mnt/trunk/\n"            # naam klopt niet met proces
        f"{t},who,unraid,bash,43,geopend,/mnt/trunk/b\n"             # geen container
        f"{t},who,unraid,python3,999,geopend,/mnt/trunk/c\n"         # proces bestaat niet meer
        f"{t},spin,sdd,active\n"
    )
    sd.relabel_existing(now=t)
    lines = open(sd.day_path("2026-10-09")).read().splitlines()
    assert lines[0] == f"{t},who,container,bazarr,42,geopend,/mnt/trunk/a.mkv"
    assert lines[1:] == [
        f"{t},who,unraid,webGUI,42,geopend,/mnt/trunk/",
        f"{t},who,unraid,bash,43,geopend,/mnt/trunk/b",
        f"{t},who,unraid,python3,999,geopend,/mnt/trunk/c",
        f"{t},spin,sdd,active",
    ]


def write_legacy(sd):
    open(sd.DISKS, "w").write("sdd,trunk\nsde,trunk2\n")
    open(sd.SPIN, "w").write(
        "2026-10-09T08:57:22+02:00,/dev/sdd,active\n"
        "2026-10-09T09:27:26+02:00,/dev/sdd,standby\n"
        "2026-10-10T02:04:19+02:00,/dev/sdd,active\n"
        "Oct  9 10:00:00 Tower emhttpd: spinning down /dev/sde\n"       # syslog: niet over te zetten
    )
    open(sd.WHO, "w").write(
        f"{D9 + 8 * 3600 + 3432},container,sonarr,16690,geopend,/mnt/trunk/Media/series/\n"
        f"{D10 + 7000},onbekend,find (al gestopt),9,geopend,/mnt/trunk/a, b.mkv\n"
    )
    open(sd.ACTIVITY, "w").write(f"{D9 + 100},trunk,OPEN+ISDIR,/mnt/trunk/\n")


def test_migrate_splits_old_files_into_days(sd):
    write_legacy(sd)
    sd.migrate()
    assert sd.list_days() == ["2026-10-09", "2026-10-10"]
    assert open(sd.day_path("2026-10-09")).read().splitlines() == [
        f"{D9},disk,sdd,trunk", f"{D9},disk,sde,trunk2",
        f"{D9 + 100},activity,trunk,OPEN+ISDIR,/mnt/trunk/",
        f"{D9 + 8 * 3600 + 3432},who,container,sonarr,16690,geopend,/mnt/trunk/Media/series/",
        f"{D9 + 8 * 3600 + 3442},spin,sdd,active",
        f"{D9 + 9 * 3600 + 1646},spin,sdd,standby",
    ]
    # tweede dag begint met de namen en de status van gisteravond
    assert open(sd.day_path("2026-10-10")).read().splitlines() == [
        f"{D10},disk,sdd,trunk", f"{D10},disk,sde,trunk2",
        f"{D10},state,sdd,standby",
        f"{D10 + 7000},who,onbekend,find (al gestopt),9,geopend,/mnt/trunk/a, b.mkv",
        f"{D10 + 7459},spin,sdd,active",
    ]
    for f in ("spin.csv", "disks.csv", "who.csv", "activity.csv"):
        assert not os.path.exists(os.path.join(sd.DATA, f))
        assert os.path.exists(os.path.join(sd.LEGACY, f))


def test_migrate_is_safe_to_repeat_and_keeps_new_data(sd):
    write_legacy(sd)
    os.makedirs(sd.DAYS)
    open(sd.day_path("2026-10-10"), "w").write(f"{D10 + 9000},spin,sdd,standby\n")   # al nieuw geschreven
    sd.migrate()
    first = {d: open(sd.day_path(d)).read() for d in sd.list_days()}
    write_legacy(sd)                                  # bv. migratie halverwege afgebroken
    sd.migrate()
    assert {d: open(sd.day_path(d)).read() for d in sd.list_days()} == first
    assert first["2026-10-10"].splitlines()[-1] == f"{D10 + 9000},spin,sdd,standby"
    kept = sorted(os.listdir(sd.LEGACY))              # beide kopieën van de oude bestanden blijven bewaard
    assert [k.split(".csv")[0] for k in kept].count("who") == 2 and len(kept) == 8


def test_migrate_without_old_files_does_nothing(sd):
    sd.migrate()
    assert sd.list_days() == [] and not os.path.exists(sd.LEGACY)
