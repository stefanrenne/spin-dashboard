import spindash
from conftest import make_disk, set_io

T = 1791496800 + 9 * 3600          # 2026-10-09 09:00 in Amsterdam
REAL_SSD_WEAR = spindash.ssd_wear   # the sd fixture replaces it: no smartctl in tests


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
    assert sd.poll_once(last, now=T) == 2           # first poll: current state
    assert sd.poll_once(last, now=T + 60) == 0      # nothing changed, nothing written
    states["sdd"] = "active"
    assert sd.poll_once(last, now=T + 120) == 1
    assert open(sd.day_path("2026-10-09")).read().splitlines() == [
        f"{T},disk,sdc,disk1,hdd", f"{T},disk,sdd,trunk,hdd",
        f"{T},state,sdc,active", f"{T},state,sdd,standby",
        f"{T + 120},spin,sdd,active",
    ]


def test_poll_once_starts_each_day_with_names_and_state(sd, monkeypatch):
    states = setup_disks(sd, monkeypatch)
    last = {}
    sd.poll_once(last, now=T)
    midnight = 1791496800 + 86400 + 30               # 2026-10-10 00:00:30
    states["sdc"] = "standby"                        # changes exactly at the day change
    assert sd.poll_once(last, now=midnight) == 2
    assert open(sd.day_path("2026-10-10")).read().splitlines() == [
        f"{midnight},disk,sdc,disk1,hdd", f"{midnight},disk,sdd,trunk,hdd",
        f"{midnight},spin,sdc,standby", f"{midnight},state,sdd,standby",
    ]


def test_poll_once_logs_renamed_disks(sd, monkeypatch):
    setup_disks(sd, monkeypatch)
    last = {}
    sd.poll_once(last, now=T)
    (sd._tmp / "emhttp" / "disks.ini").write_text('["disk2"]\ndevice="sdc"\n["trunk"]\ndevice="sdd"\n')
    sd.poll_once(last, now=T + 60)
    assert open(sd.day_path("2026-10-09")).read().splitlines()[-2:] == [
        f"{T + 60},disk,sdc,disk2,hdd", f"{T + 60},disk,sdd,trunk,hdd"]


def test_poll_once_ignores_unknown_state(sd, monkeypatch):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["disk1"]\ndevice="sdc"\n')
    make_disk(sd._tmp, "sdc")
    monkeypatch.setattr(sd, "drive_state", lambda dev: None)
    assert sd.poll_once({}, now=T) == 0


def setup_mixed(sd, monkeypatch):
    """An HDD (trunk) and an NVMe cache pool."""
    (sd._tmp / "emhttp" / "disks.ini").write_text('["trunk"]\ndevice="sdd"\n["cache"]\ndevice="nvme0n1"\n')
    make_disk(sd._tmp, "sdd")
    make_disk(sd._tmp, "nvme0n1", rotational=False)
    states = {"sdd": "standby"}
    asked = []
    monkeypatch.setattr(sd, "drive_state", lambda dev: asked.append(dev) or states[dev])
    return states, asked


def test_poll_once_names_ssds_but_never_asks_their_state(sd, monkeypatch):
    _, asked = setup_mixed(sd, monkeypatch)
    sd.poll_once({}, now=T)
    assert asked == ["sdd"]
    assert open(sd.day_path("2026-10-09")).read().splitlines() == [
        f"{T},disk,nvme0n1,cache,ssd", f"{T},disk,sdd,trunk,hdd", f"{T},state,sdd,standby"]


def test_poll_once_writes_io_deltas_and_skips_quiet_drives_and_resets(sd, monkeypatch):
    states, _ = setup_mixed(sd, monkeypatch)
    states["sdd"] = "active"                       # an HDD spins: io lines are written right away
    set_io(sd._tmp, "nvme0n1", 1000, 2000, 10, 20)
    set_io(sd._tmp, "sdd", 50, 50, 5, 5)
    last = {}
    sd.poll_once(last, now=T)                      # first poll: no previous counters, no io
    set_io(sd._tmp, "nvme0n1", 1400, 2000, 14, 20)  # 400 sectors = 200 KiB read
    sd.poll_once(last, now=T + 60)                 # sdd unchanged: no line
    set_io(sd._tmp, "nvme0n1", 10, 10, 1, 1)        # counters reset: skipped, counting starts over
    sd.poll_once(last, now=T + 120)
    set_io(sd._tmp, "nvme0n1", 10, 30, 1, 3)
    sd.poll_once(last, now=T + 180)
    io = [ln for ln in open(sd.day_path("2026-10-09")).read().splitlines() if ",io," in ln]
    assert io == [f"{T + 60},io,nvme0n1,200,0,4,0", f"{T + 180},io,nvme0n1,0,10,0,2"]


def test_io_lines_wait_while_every_hdd_sleeps(sd, monkeypatch):
    """Writing them every minute would keep the drive with DATA_DIR awake."""
    states, _ = setup_mixed(sd, monkeypatch)
    set_io(sd._tmp, "nvme0n1", 0, 0, 0, 0)
    last = {}
    sd.poll_once(last, now=T)
    set_io(sd._tmp, "nvme0n1", 0, 8, 0, 1)
    sd.poll_once(last, now=T + 60)
    path = sd.day_path("2026-10-09")
    assert ",io," not in open(path).read()
    assert sd.pending_io("2026-10-09") == [f"{T + 60},io,nvme0n1,0,4,0,1"]
    assert sd.pending_io("2026-10-10") == []
    states["sdd"] = "active"                        # the HDD spins up: everything is written
    sd.poll_once(last, now=T + 120)
    assert f"{T + 60},io,nvme0n1,0,4,0,1" in open(path).read().splitlines()
    assert sd.pending_io("2026-10-09") == []


def test_io_lines_are_written_without_hdds(sd, monkeypatch):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["cache"]\ndevice="nvme0n1"\n')
    make_disk(sd._tmp, "nvme0n1", rotational=False)
    set_io(sd._tmp, "nvme0n1", 0, 0, 0, 0)
    last = {}
    sd.poll_once(last, now=T)
    set_io(sd._tmp, "nvme0n1", 2, 0, 1, 0)
    sd.poll_once(last, now=T + 60)
    assert f"{T + 60},io,nvme0n1,1,0,1,0" in open(sd.day_path("2026-10-09")).read().splitlines()


def test_stop_writes_pending_io_lines(sd, monkeypatch):
    sd.io_pending.append(f"{T},io,nvme0n1,1,0,1,0")
    import pytest
    with pytest.raises(SystemExit):
        sd.stop()
    assert open(sd.day_path("2026-10-09")).read() == f"{T},io,nvme0n1,1,0,1,0\n"


NVME_JSON = '{"nvme_smart_health_information_log": {"percentage_used": 3, "data_units_written": 2000000}}'
SATA_JSON = ('{"logical_block_size": 512, "ata_smart_attributes": {"table": ['
             '{"id": 9, "value": 99, "raw": {"value": 1234}}, {"id": 177, "value": 97, "raw": {"value": 40}},'
             '{"id": 241, "value": 99, "raw": {"value": 4000000000}}]}}')


def test_parse_wear_nvme_sata_and_unknown():
    p = spindash.parse_wear
    assert p(NVME_JSON) == (3, 1_000_000_000)                  # 2e6 units of 512000 bytes = 1e9 KiB
    assert p(SATA_JSON) == (3, 2_000_000_000)                  # 100 - 97; 4e9 LBAs of 512 bytes
    assert p('{"ata_smart_attributes": {"table": [{"id": 231, "value": 88}]}}') == (12, None)
    assert p('{"smartctl": {"exit_status": 2}}') == (None, None)   # e.g. standby: nothing read
    assert p("not json") == (None, None)


def test_wear_is_asked_only_of_ssds_once_an_hour_and_each_day(sd, monkeypatch):
    states, _ = setup_mixed(sd, monkeypatch)
    states["sdd"] = "active"                         # so the lines are written right away
    asked = []
    monkeypatch.setattr(sd, "ssd_wear", lambda dev: asked.append(dev) or (4, 123456))
    last = {}
    sd.poll_once(last, now=T)
    sd.poll_once(last, now=T + 60)                   # within the hour: not again
    sd.poll_once(last, now=T + 3600)
    assert asked == ["nvme0n1", "nvme0n1"]           # never sdd, the HDD
    lines = open(sd.day_path("2026-10-09")).read().splitlines()
    assert [ln for ln in lines if ",wear," in ln] == [f"{T},wear,nvme0n1,4,123456", f"{T + 3600},wear,nvme0n1,4,123456"]
    midnight = 1791496800 + 86400 + 30
    sd.poll_once(last, now=midnight)                 # a new day: every day file has one
    assert f"{midnight},wear,nvme0n1,4,123456" in open(sd.day_path("2026-10-10")).read().splitlines()


def test_unknown_wear_writes_no_line_and_a_partial_one_keeps_the_empty_field(sd, monkeypatch):
    states, _ = setup_mixed(sd, monkeypatch)
    states["sdd"] = "active"
    monkeypatch.setattr(sd, "ssd_wear", lambda dev: (None, None))
    sd.poll_once({}, now=T)
    assert ",wear," not in open(sd.day_path("2026-10-09")).read()
    monkeypatch.setattr(sd, "ssd_wear", lambda dev: (None, 77))
    sd.poll_once({}, now=T + 60)
    assert f"{T + 60},wear,nvme0n1,,77" in open(sd.day_path("2026-10-09")).read().splitlines()


def test_ssd_wear_runs_smartctl_with_the_standby_guard(sd, monkeypatch):
    calls = []

    class R:
        stdout = NVME_JSON
    monkeypatch.setattr(sd, "run_own", lambda cmd, timeout: calls.append(cmd) or R())
    assert REAL_SSD_WEAR("nvme0n1") == (3, 1_000_000_000)
    assert calls == [["smartctl", "-n", "standby", "-j", "-A", "/dev/nvme0n1"]]


def mountinfo(sd, monkeypatch, line):
    """DATA_DIR is /data, mounted as `line` describes (fields after the mount point: options - type source)."""
    monkeypatch.setattr(sd, "DATA", "/data")
    (sd._tmp / "mountinfo").write_text("22 1 0:20 / / rw - overlay overlay rw\n" + line + "\n")


POOLS = {"nvme0n1": ("cache", False), "nvme1n1": ("cache2", False), "sdc": ("disk1", True),
         "sdd": ("trunk", True), "sde": ("trunk2", True), "sdf": ("disk2", True)}


def test_data_dir_devices_from_mountinfo(sd, monkeypatch):
    cases = [
        ("36 22 0:31 /appdata/spin-dashboard/data /data rw - zfs trunk/appdata rw", ["sdd", "sde"]),
        ("36 22 9:1 / /data rw - xfs /dev/md1p1 rw", ["sdc"]),
        ("36 22 9:2 / /data rw - xfs /dev/mapper/md2p1 rw", ["sdf"]),                    # encrypted array disk
        ("36 22 259:1 /appdata /data rw - btrfs /dev/nvme0n1p1 rw", ["nvme0n1", "nvme1n1"]),   # one member names the pool
        ("36 22 0:44 / /data rw - ext4 /dev/sdz1 rw", None),                             # a drive we do not know
        ("36 22 0:50 / /data rw - nfs server:/x rw", None),
    ]
    for line, want in cases:
        mountinfo(sd, monkeypatch, line)
        assert sd.data_dir_devices(POOLS) == want, line
    (sd._tmp / "mountinfo").unlink()
    assert sd.data_dir_devices(POOLS) is None


def test_data_dir_on_a_user_share_counts_only_when_it_lives_on_one_pool(sd, monkeypatch):
    mountinfo(sd, monkeypatch, "36 22 0:40 /appdata/spin-dashboard /data rw - fuse.shfs shfs rw")
    (sd._tmp / "emhttp" / "shares.ini").write_text('["appdata"]\nuseCache="only"\ncachePool="cache"\n')
    assert sd.data_dir_devices(POOLS) == ["nvme0n1", "nvme1n1"]
    sd._shares_cache.update(body=None)
    (sd._tmp / "emhttp" / "shares.ini").write_text('["appdata"]\nuseCache="yes"\ncachePool="cache"\n')
    assert sd.data_dir_devices(POOLS) is None                  # the mover can put it on any array disk


def test_waiting_lines_only_wake_the_drive_of_data_dir_when_it_spins(sd, monkeypatch):
    """DATA_DIR on HDD pool tank (asleep); another HDD spinning is no reason to write."""
    (sd._tmp / "emhttp" / "disks.ini").write_text('["trunk"]\ndevice="sdd"\n["tank"]\ndevice="sdg"\n["cache"]\ndevice="nvme0n1"\n')
    for dev, rot in (("sdd", True), ("sdg", True), ("nvme0n1", False)):
        make_disk(sd._tmp, dev, rotational=rot)
    states = {"sdd": "active", "sdg": "standby"}
    monkeypatch.setattr(sd, "drive_state", lambda dev: states[dev])
    mountinfo(sd, monkeypatch, "36 22 0:31 / /data rw - zfs tank/spin rw")
    set_io(sd._tmp, "nvme0n1", 0, 0, 0, 0)
    last = {}
    sd.poll_once(last, now=T)
    set_io(sd._tmp, "nvme0n1", 0, 8, 0, 1)
    sd.poll_once(last, now=T + 60)
    assert sd.pending_io("2026-10-09") == [f"{T + 60},io,nvme0n1,0,4,0,1"]       # trunk spins, tank sleeps
    states["sdg"] = "active"
    sd.poll_once(last, now=T + 120)
    assert sd.pending_io("2026-10-09") == []
    # DATA_DIR on the SSD pool: written right away, whatever the HDDs do
    states.update(sdd="standby", sdg="standby")
    sd.poll_once(last, now=T + 180)
    mountinfo(sd, monkeypatch, "36 22 259:1 / /data rw - btrfs /dev/nvme0n1p1 rw")
    set_io(sd._tmp, "nvme0n1", 0, 16, 0, 2)
    sd.poll_once(last, now=T + 240)
    assert sd.pending_io("2026-10-09") == []


def test_poll_keeps_what_the_watcher_needs(sd, monkeypatch):
    setup_mixed(sd, monkeypatch)
    sd.poll_once({}, now=T)
    assert sd.drive_states == {"sdd": "standby"}
    assert sd.known_devices == {"sdd": ("trunk", True), "nvme0n1": ("cache", False)}


def test_written_units_come_from_the_attribute_name():
    w = spindash.written_kib
    assert w("Total_LBAs_Written", 2_000_000, 512) == 1_000_000           # Samsung: logical blocks
    assert w("Total_LBAs_Written", 1000, 4096) == 4000
    assert w("Host_Writes_32MiB", 10, 512) == 10 * 32 * 1024              # Intel
    assert w("Lifetime_Writes_GiB", 3, 512) == 3 * 1024 * 1024           # Kingston
    assert w("Host_Writes_GiB", 3, 512) == 3 * 1024 * 1024
    assert w("Host_Writes_MiB", 5, 512) == 5 * 1024
    assert w(None, 4, 512) == 2
    intel = ('{"ata_smart_attributes": {"table": [{"id": 233, "value": 98},'
             '{"id": 241, "name": "Host_Writes_32MiB", "raw": {"value": 100}}]}}')
    assert spindash.parse_wear(intel) == (2, 100 * 32 * 1024)
