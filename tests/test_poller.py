import spindash
from conftest import make_disk


def test_parse_hdparm():
    p = spindash.parse_hdparm
    assert p("\n/dev/sdb:\n drive state is:  standby\n") == "standby"
    assert p(" drive state is:  sleeping") == "standby"
    assert p(" drive state is:  active/idle") == "active"
    assert p(" drive state is:  idle_a") == "active"
    assert p(" drive state is:  unknown") is None
    assert p("SG_IO: bad/missing sense data") is None


def test_poll_once_writes_only_changes(sd, monkeypatch):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["disk1"]\ndevice="sdc"\n["trunk"]\ndevice="sdd"\n')
    make_disk(sd._tmp, "sdc")
    make_disk(sd._tmp, "sdd")
    states = {"sdc": "active", "sdd": "standby"}
    monkeypatch.setattr(sd, "drive_state", lambda dev: states[dev])

    last = {}
    assert sd.poll_once(last) == 2
    assert sd.poll_once(last) == 0          # niets veranderd, niets geschreven
    states["sdd"] = "active"
    assert sd.poll_once(last) == 1

    lines = open(sd.SPIN).read().splitlines()
    assert [l.split(",", 1)[1] for l in lines] == ["/dev/sdc,active", "/dev/sdd,standby", "/dev/sdd,active"]
    assert open(sd.DISKS).read() == "sdc,disk1\nsdd,trunk\n"


def test_poll_once_ignores_unknown_state(sd, monkeypatch):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["disk1"]\ndevice="sdc"\n')
    make_disk(sd._tmp, "sdc")
    monkeypatch.setattr(sd, "drive_state", lambda dev: None)
    assert sd.poll_once({}) == 0


def test_write_if_changed_keeps_mtime_when_equal(sd, tmp_path):
    f = tmp_path / "x.csv"
    sd.write_if_changed(str(f), "a\n")
    m1 = f.stat().st_mtime_ns
    sd.write_if_changed(str(f), "a\n")
    assert f.stat().st_mtime_ns == m1
    sd.write_if_changed(str(f), "b\n")
    assert f.read_text() == "b\n"
