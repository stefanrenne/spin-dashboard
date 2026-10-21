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


def test_all_devices_includes_ssds_and_skips_unknown(sd):
    write_ini(sd)
    for dev in ("sdb", "sdc", "sdd"):
        make_disk(sd._tmp, dev)
    make_disk(sd._tmp, "nvme0n1", rotational=False)        # sde is not in /sys/block
    assert sd.all_devices() == {"sdb": ("parity", True), "sdc": ("disk1", True),
                                "sdd": ("trunk", True), "nvme0n1": ("frunk", False)}


def test_all_devices_fallback_without_disks_ini(sd):
    make_disk(sd._tmp, "sdx")
    make_disk(sd._tmp, "nvme1n1", rotational=False)
    make_disk(sd._tmp, "loop0", rotational=False)
    assert sd.all_devices() == {"nvme1n1": ("nvme1n1", False), "sdx": ("sdx", True)}


def test_target_mounts_adds_only_the_chosen_ssd_pools(sd):
    (sd._tmp / "emhttp" / "disks.ini").write_text(
        '["trunk"]\ndevice="sdd"\n["cache"]\ndevice="nvme0n1"\n["cache2"]\ndevice="nvme1n1"\n["fast"]\ndevice="sdf"\n')
    make_disk(sd._tmp, "sdd")
    for dev in ("nvme0n1", "nvme1n1", "sdf"):
        make_disk(sd._tmp, dev, rotational=False)
    (sd._tmp / "mounts").write_text(
        "trunk /mnt/trunk zfs rw 0 0\ncache /mnt/cache btrfs rw 0 0\nfast /mnt/fast xfs rw 0 0\n")
    assert sd.target_mounts() == ["/mnt/trunk"]
    sd.save_settings({"ssd_watch": ["cache"]})
    assert sd.target_mounts() == ["/mnt/cache", "/mnt/trunk"]


def test_ssd_watch_setting_is_checked(sd):
    import pytest
    assert sd.clean_ssd_watch(["cache", "cache", "array"]) == ["cache", "array"]
    assert sd.clean_ssd_watch(None) == []
    for bad in ("cache", ["a/b"], [1], ["x"] * 65):
        with pytest.raises(ValueError):
            sd.clean_ssd_watch(bad)


def test_old_disk_lines_without_kind_still_load(sd):
    """The kind field is only added: old disk lines (HDDs only) need no conversion."""
    import os
    os.makedirs(sd.DAYS)
    open(sd.day_path("2026-10-08"), "w").write("1791410400,disk,sdd,trunk\n1791410400,state,sdd,standby\n")
    assert sd.spins_body()[0].decode().splitlines() == ["1791410400,disk,sdd,trunk", "1791410400,state,sdd,standby"]


def test_wear_warn_setting_is_checked(sd):
    import pytest
    assert sd.clean_wear_warn(None) is None
    assert sd.clean_wear_warn(100) == 100
    for bad in (0, -5, 1.5, "100", True, 2_000_000):
        with pytest.raises(ValueError):
            sd.clean_wear_warn(bad)
    assert sd.save_settings({"wear_warn": None}) == {"wear_warn": None}


def test_target_mounts_skip_bind_mounts_and_container_filesystems(sd):
    """Docker on an SSD pool: overlay root filesystems and volumes that bind a folder of a pool are not
    watched again; fatrace already sees the whole filesystem through its real mount."""
    (sd._tmp / "emhttp" / "disks.ini").write_text('["trunk"]\ndevice="sdd"\n["frunk"]\ndevice="nvme0n1"\n')
    make_disk(sd._tmp, "sdd")
    make_disk(sd._tmp, "nvme0n1", rotational=False)
    (sd._tmp / "mounts").write_text(
        "frunk /mnt/frunk zfs rw 0 0\n"
        "frunk/appdata /mnt/frunk/appdata zfs rw 0 0\n"
        "trunk /mnt/trunk zfs rw 0 0\n"
        "trunk/Media /mnt/trunk/Media zfs rw 0 0\n"
        "frunk/appdata /data zfs rw 0 0\n"                                     # our own /data: not under /mnt
        "overlay /mnt/frunk/appdata/docker/overlay2/abc/merged overlay rw 0 0\n"
        "frunk/appdata /mnt/frunk/appdata/docker/volumes/appdata/_data zfs rw 0 0\n"   # bind of appdata
        "trunk/Media /mnt/frunk/appdata/docker/volumes/media/_data zfs rw 0 0\n"     # bind of an HDD dataset
        "tmpfs /mnt/frunk/appdata/docker/containers/x/shm tmpfs rw 0 0\n"
        "shfs /mnt/user fuse.shfs rw 0 0\n")
    assert sd.target_mounts() == ["/mnt/trunk", "/mnt/trunk/Media"]
    sd.save_settings({"ssd_watch": ["frunk"]})
    assert sd.target_mounts() == ["/mnt/frunk", "/mnt/frunk/appdata", "/mnt/trunk", "/mnt/trunk/Media"]


def test_mount_points_with_spaces(sd):
    (sd._tmp / "mounts").write_text("tank/My\\040Files /mnt/tank/My\\040Files zfs rw 0 0\n")
    assert sd.mounted() == [("tank/My Files", "/mnt/tank/My Files", "zfs")]


def test_watch_override_outside_mnt_and_on_tmpfs(sd, monkeypatch):
    """docs/development.md runs it with WATCH=/tmp: a root given in WATCH counts, even as tmpfs."""
    monkeypatch.setattr(sd, "WATCH_OVERRIDE", ["/tmp"])
    (sd._tmp / "mounts").write_text("tmpfs /tmp tmpfs rw 0 0\noverlay /tmp/c/merged overlay rw 0 0\n"
                                    "tmpfs /run tmpfs rw 0 0\n")
    assert sd.target_mounts() == ["/tmp"]


UD_MOUNTS = ("trunk /mnt/trunk zfs rw 0 0\n"
             "tmpfs /mnt/disks tmpfs ro 0 0\n"                         # Unraid's own placeholder: no drive
             "/dev/sdf1 /mnt/disks/backup xfs rw 0 0\n"                 # an HDD of Unassigned Devices
             "/dev/nvme2n1p1 /mnt/disks/scratch btrfs rw 0 0\n"         # an SSD of Unassigned Devices
             "//nas/media /mnt/remotes/nas_media cifs rw 0 0\n"         # a network share: no drive
             "/dev/sdd1 /mnt/disks/oops xfs rw 0 0\n")                  # already in disks.ini: not again


def ud_setup(sd):
    (sd._tmp / "emhttp" / "disks.ini").write_text('["trunk"]\ndevice="sdd"\n')
    make_disk(sd._tmp, "sdd")
    make_disk(sd._tmp, "sdf")
    make_disk(sd._tmp, "nvme2n1", rotational=False)
    (sd._tmp / "mounts").write_text(UD_MOUNTS)


def test_unassigned_devices_are_found_from_the_mounts(sd):
    ud_setup(sd)
    assert sd.all_devices() == {"sdd": ("trunk", True), "sdf": ("disks/backup", True), "nvme2n1": ("disks/scratch", False)}
    assert sd.hdd_devices() == {"sdd": "trunk", "sdf": "disks/backup"}


def test_unassigned_devices_are_their_own_lane_and_mount(sd):
    assert sd.mount_base("disks/backup2") == "disks/backup2"           # never a pool member
    assert sd.lane_key("disks/backup2") == "disks/backup2"
    assert sd.mount_key("/mnt/disks/backup/a/b.mkv") == "disks/backup"
    assert sd.mount_key("/mnt/trunk/a.mkv") == "trunk"
    assert sd.clean_quiet({"from": "23:00", "to": "07:00", "drives": ["disks/backup"]})["drives"] == ["disks/backup"]


def test_unassigned_hdds_are_watched(sd):
    ud_setup(sd)
    assert sd.target_mounts() == ["/mnt/disks/backup", "/mnt/trunk"]      # the SSD only when chosen
    sd.save_settings({"ssd_watch": ["disks/scratch"]})
    assert "/mnt/disks/scratch" in sd.target_mounts()


def test_paths_on_unassigned_devices_and_remotes(sd):
    shares, bases = {}, {"trunk", "disks/backup"}
    assert sd.locate("/mnt/disks/backup/photos", shares, bases) == {"kind": "pool", "name": "disks/backup", "hdd": True}
    assert sd.locate("/mnt/disks/scratch", shares, bases) == {"kind": "pool", "name": "disks/scratch", "hdd": False}
    assert sd.locate("/mnt/remotes/nas_media/films", shares, bases) == {"kind": "remote", "name": "nas_media", "hdd": False}
