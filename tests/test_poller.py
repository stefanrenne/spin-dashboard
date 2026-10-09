import spindash
from conftest import make_disk

T = 1791496800 + 9 * 3600          # 2026-10-09 09:00 in Amsterdam


def test_parse_hdparm():
    p = spindash.parse_hdparm
    assert p("\n/dev/sdb:\n drive state is:  standby\n") == "standby"
    assert p(" drive state is:  sleeping") == "standby"
    assert p(" drive state is:  active/idle") == "active"
    assert p(" drive state is:  idle_a") == "active"
    assert p(" drive state is:  unknown") is None
    assert p("SG_IO: bad/missing sense data") is None


def setup_disks(sd, monkeypatch):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["disk1"]\ndevice="sdc"\n["trunk"]\ndevice="sdd"\n')
    make_disk(sd._tmp, "sdc")
    make_disk(sd._tmp, "sdd")
    states = {"sdc": "active", "sdd": "standby"}
    monkeypatch.setattr(sd, "drive_state", lambda dev: states[dev])
    return states


def test_poll_once_writes_state_then_only_changes(sd, monkeypatch):
    states = setup_disks(sd, monkeypatch)
    last = {}
    assert sd.poll_once(last, now=T) == 2           # eerste peiling: huidige status
    assert sd.poll_once(last, now=T + 60) == 0      # niets veranderd, niets geschreven
    states["sdd"] = "active"
    assert sd.poll_once(last, now=T + 120) == 1
    assert open(sd.day_path("2026-10-09")).read().splitlines() == [
        f"{T},disk,sdc,disk1", f"{T},disk,sdd,trunk",
        f"{T},state,sdc,active", f"{T},state,sdd,standby",
        f"{T + 120},spin,sdd,active",
    ]


def test_poll_once_starts_each_day_with_names_and_state(sd, monkeypatch):
    states = setup_disks(sd, monkeypatch)
    last = {}
    sd.poll_once(last, now=T)
    midnight = 1791496800 + 86400 + 30               # 2026-10-10 00:00:30
    states["sdc"] = "standby"                        # verandert precies bij de dagwissel
    assert sd.poll_once(last, now=midnight) == 2
    assert open(sd.day_path("2026-10-10")).read().splitlines() == [
        f"{midnight},disk,sdc,disk1", f"{midnight},disk,sdd,trunk",
        f"{midnight},spin,sdc,standby", f"{midnight},state,sdd,standby",
    ]


def test_poll_once_logs_renamed_disks(sd, monkeypatch):
    setup_disks(sd, monkeypatch)
    last = {}
    sd.poll_once(last, now=T)
    (sd._tmp / "emhttp" / "disks.ini").write_text('["disk2"]\ndevice="sdc"\n["trunk"]\ndevice="sdd"\n')
    sd.poll_once(last, now=T + 60)
    assert open(sd.day_path("2026-10-09")).read().splitlines()[-2:] == [
        f"{T + 60},disk,sdc,disk2", f"{T + 60},disk,sdd,trunk"]


def test_poll_once_ignores_unknown_state(sd, monkeypatch):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["disk1"]\ndevice="sdc"\n')
    make_disk(sd._tmp, "sdc")
    monkeypatch.setattr(sd, "drive_state", lambda dev: None)
    assert sd.poll_once({}, now=T) == 0
