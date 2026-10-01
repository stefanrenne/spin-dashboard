from conftest import make_disk

DISKS_INI = '''["parity"]
idx="0"
device="sdb"
["disk1"]
device="sdc"
["disk2"]
device=""
["trunk"]
device="sdd"
["trunk2"]
device="sde"
["flash"]
device="sda"
["frunk"]
device="nvme0n1"
["cache"]
device="sdd"
'''


def write_ini(sd):
    (sd._tmp / "emhttp" / "disks.ini").write_text(DISKS_INI)


def test_read_disks_ini_skips_flash_empty_and_duplicates(sd):
    write_ini(sd)
    assert sd.read_disks_ini() == [
        ("sdb", "parity"), ("sdc", "disk1"), ("sdd", "trunk"), ("sde", "trunk2"), ("nvme0n1", "frunk"),
    ]


def test_read_disks_ini_missing_file(sd):
    assert sd.read_disks_ini() == []


def test_mount_base():
    import spindash
    assert spindash.mount_base("disk1") == "disk1"
    assert spindash.mount_base("disk12") == "disk12"
    assert spindash.mount_base("trunk") == "trunk"
    assert spindash.mount_base("trunk2") == "trunk"
    assert spindash.mount_base("parity") is None
    assert spindash.mount_base("parity2") is None


def test_hdd_devices_only_rotational(sd):
    write_ini(sd)
    for dev in ("sdb", "sdc", "sdd", "sde"):
        make_disk(sd._tmp, dev)
    make_disk(sd._tmp, "nvme0n1", rotational=False)
    assert sd.hdd_devices() == {"sdb": "parity", "sdc": "disk1", "sdd": "trunk", "sde": "trunk2"}


def test_hdd_devices_fallback_without_disks_ini(sd):
    make_disk(sd._tmp, "sdx")
    make_disk(sd._tmp, "sdy", rotational=False)
    assert sd.hdd_devices() == {"sdx": "sdx"}


def test_target_mounts_includes_datasets_and_excludes_others(sd):
    write_ini(sd)
    for dev in ("sdc", "sdd", "sde"):
        make_disk(sd._tmp, dev)
    make_disk(sd._tmp, "nvme0n1", rotational=False)
    (sd._tmp / "mounts").write_text(
        "rootfs / rootfs rw 0 0\n"
        "/dev/md1p1 /mnt/disk1 xfs rw 0 0\n"
        "trunk /mnt/trunk zfs rw 0 0\n"
        "trunk/Media /mnt/trunk/Media zfs rw 0 0\n"
        "trunk2x /mnt/trunk2x zfs rw 0 0\n"
        "frunk /mnt/frunk zfs rw 0 0\n"
        "shfs /mnt/user fuse.shfs rw 0 0\n"
    )
    assert sd.target_mounts() == ["/mnt/disk1", "/mnt/trunk", "/mnt/trunk/Media"]


def test_target_mounts_override(sd, monkeypatch):
    monkeypatch.setattr(sd, "WATCH_OVERRIDE", ["/mnt/tank/"])
    (sd._tmp / "mounts").write_text("tank /mnt/tank zfs rw 0 0\nx /mnt/tankx zfs rw 0 0\n")
    assert sd.target_mounts() == ["/mnt/tank"]


def test_mounts_with_spaces(sd, monkeypatch):
    monkeypatch.setattr(sd, "WATCH_OVERRIDE", ["/mnt/my pool"])
    (sd._tmp / "mounts").write_text("p /mnt/my\\040pool zfs rw 0 0\n")
    assert sd.target_mounts() == ["/mnt/my pool"]
