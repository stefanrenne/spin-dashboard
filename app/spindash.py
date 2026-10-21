#!/usr/bin/env python3
"""Spin Dashboard for Unraid.

One process with these parts:

  poller   polls the state of every HDD every POLL_INTERVAL seconds (hdparm -C, with
           smartctl as fallback) and writes only changes to the day file, plus what every
           drive (SSDs included) read and wrote, from the kernel counters in /sys/block/<dev>/stat.
           The names (parity, disk1, pool, pool2) come from Unraid's disks.ini.
  watcher  runs one `fatrace -c` (fanotify) per HDD mount (and per SSD pool chosen in Settings)
           and records which process opens which file: a container, Unraid itself or a user (SMB/shell).
  docker   follows start/stop/health of containers (ctr lines) and provides per container the mounts
           with where the share lives (shares.ini) for /data/containers.json.
  web      serves the dashboard and the CSV files, and stores the settings (hidden paths, quiet hours).

Everything lives in DATA_DIR/days: one CSV per day (YYYY-MM-DD.csv, local date) with lines
`epoch,type,...` (disk, state, spin, io, wear, proc, who, activity, ctr). Older separate files (spin.csv, disks.csv,
who.csv, activity.csv) are migrated to day files once at start.
"""
import gzip
import hashlib
import http.client
import http.server
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from functools import partial

# ---------- settings ----------
PORT = int(os.environ.get("PORT", "8089"))
DATA = os.environ.get("DATA_DIR", "/data")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
EMHTTP = os.environ.get("EMHTTP_DIR", "/emhttp")          # /var/local/emhttp of the host
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DEDUP_S = int(os.environ.get("DEDUP_SECONDS", "600"))   # same path + source only once per 10 min
WATCH_OVERRIDE = os.environ.get("WATCH", "").split()   # optional: fixed list of mounts
# processes (and their children) that are not logged; cache_dirs keeps opening every folder
IGNORE_PROCS = set(os.environ.get("IGNORE_PROCS", "cache_dirs").split())
SHFS_DELAY = 0.4
PID_TTL = 30          # remember the result of classify() per PID this long
IGNORE_TTL = 300      # this long after an ignored process, also ignore the same name without a source
IGNORE_SCAN = 30      # how often to check again whether an IGNORE_PROCS process is running (s)

# Paths to the host system; replaceable in tests
PROC = "/proc"
SYS_BLOCK = "/sys/block"
MOUNTS_FILE = "/proc/self/mounts"
MOUNTINFO = "/proc/self/mountinfo"
USER_SHARES = "/mnt/user"
DOCKER_SOCK = "/var/run/docker.sock"

DAYS = os.path.join(DATA, "days")
LEGACY = os.path.join(DATA, "legacy")
# old separate files; only read by migrate()
SPIN = os.path.join(DATA, "spin.csv")
DISKS = os.path.join(DATA, "disks.csv")
WHO = os.path.join(DATA, "who.csv")
ACTIVITY = os.path.join(DATA, "activity.csv")
# settings from the dashboard (hidden paths, quiet hours), shared by every browser
SETTINGS = os.path.join(DATA, "spindash-settings.json")
OWN_FILES = {"spin.csv", "disks.csv", "who.csv", "activity.csv",
             "spindash-settings.json", "spindash-settings.json.tmp"}
DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv(?:\.tmp)?$")
TYPES = ("disk", "state", "spin", "io", "wear", "proc", "who", "activity", "ctr")   # order within the same second

write_lock = threading.Lock()
# health for /health: when the poller last succeeded, what the watcher watches, whether
# Docker events arrive, and the background threads (filled in main)
health = {"started": time.time(), "poll": None, "poll_error": None, "mounts": [], "docker": None}
threads = {}
# live connections (Server-Sent Events): every write_who puts the line in all waiting queues
streams = []
streams_lock = threading.Lock()


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


# =====================================================================================
# Day files
# =====================================================================================
def day_of(ts):
    return time.strftime("%Y-%m-%d", time.localtime(float(ts)))


def day_path(day):
    return os.path.join(DAYS, f"{day}.csv")


def day_start(day):
    return int(time.mktime(time.strptime(day, "%Y-%m-%d")))


def append_lines(lines):
    """Append lines `epoch,type,...` to the day file of their own time."""
    by_day = {}
    for line in lines:
        by_day.setdefault(day_of(line.split(",", 1)[0]), []).append(line + "\n")
    with write_lock:
        os.makedirs(DAYS, exist_ok=True)
        for day, ls in by_day.items():
            with open(day_path(day), "a") as f:
                f.writelines(ls)


def list_days():
    try:
        return sorted(n[:-4] for n in os.listdir(DAYS) if DAY_FILE.match(n) and n.endswith(".csv"))
    except OSError:
        return []


def days_index():
    """Content of /data/days.json: the days, their size and the total (uncompressed), so the
    frontend only fetches the days of the shown period and can show progress while doing so."""
    days, sizes = list_days(), []
    for d in days:
        try:
            sizes.append(os.path.getsize(day_path(d)))
        except OSError:
            sizes.append(0)
    return {"days": days, "bytes": sum(sizes), "sizes": sizes}


_gz_cache = {}        # path -> (mtime_ns, size, gzip bytes); old days no longer change


def day_body(path, st, gz):
    """Content of a day file, gzip-compressed when the browser accepts it."""
    if not gz:
        with open(path, "rb") as f:
            return f.read()
    hit = _gz_cache.get(path)
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    with open(path, "rb") as f:
        body = gzip.compress(f.read(), compresslevel=6, mtime=0)
    if len(_gz_cache) >= 64:
        _gz_cache.clear()
    _gz_cache[path] = (st.st_mtime_ns, st.st_size, body)
    return body


_spin_cache = {}      # path -> (mtime_ns, size, spin lines); only the disk/state/spin/wear lines
SPIN_TYPES = ("disk", "state", "spin", "wear")


def summary_lines(lines):
    """The disk, state, spin and wear lines of `lines` as they are, plus the io lines added up per
    drive per hour: one io line per drive per hour, at the time of the last one it covers."""
    out, hours = [], {}
    for line in lines:
        p = line.rstrip("\n").split(",")
        if len(p) >= 3 and p[1] in SPIN_TYPES:
            out.append(line if line.endswith("\n") else line + "\n")
        elif len(p) == 7 and p[1] == "io":
            try:
                t, vals = int(p[0]), [int(x) for x in p[3:]]
            except ValueError:
                continue
            h = hours.setdefault((p[2], t // 3600), [0, 0, 0, 0, 0])
            h[0] = max(h[0], t)
            for i, v in enumerate(vals):
                h[i + 1] += v
    for (dev, _), h in sorted(hours.items(), key=lambda kv: kv[1][0]):
        out.append(f"{h[0]},io,{dev}," + ",".join(map(str, h[1:])) + "\n")
    return out


def spin_lines(path, st):
    """The summary of one day file (see summary_lines). Small (the who and io lines are most of it),
    so the frontend can show key figures and the trend over all days without fetching everything."""
    hit = _spin_cache.get(path)
    if hit and hit[:2] == (st.st_mtime_ns, st.st_size):
        return hit[2]
    with open(path, encoding="utf-8", errors="replace") as f:
        text = "".join(summary_lines(f))
    _spin_cache[path] = (st.st_mtime_ns, st.st_size, text)
    return text


def spins_body():
    """Content of /data/spins.csv: the spin lines of all days in order, plus an ETag that
    changes as soon as a day file changes."""
    parts, tag, days = [], [], list_days()
    for d in days:
        path = day_path(d)
        try:
            st = os.stat(path)
            text = spin_lines(path, st)
        except OSError:
            continue
        extra = pending_io(d)          # waiting io and wear lines: added up together with the day file
        if extra:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = "".join(summary_lines(list(f) + extra))
            tag.append(f"{len(extra)}:{extra[-1]}")
        parts.append(text)
        tag.append(f"{d}:{st.st_mtime_ns:x}:{st.st_size:x}")
    live = {day_path(d) for d in days}
    for path in [p for p in _spin_cache if p not in live]:      # forget days that were cleaned up
        _spin_cache.pop(path, None)
    etag = '"s-%s"' % hashlib.sha1("|".join(tag).encode()).hexdigest()[:16]
    return "".join(parts).encode(), etag


def is_own_file(path):
    name = os.path.basename(path.rstrip("/"))
    return name in OWN_FILES or bool(DAY_FILE.match(name))


# =====================================================================================
# Drives and mounts
# =====================================================================================
def read_disks_ini():
    """[(device, name)] from disks.ini; flash and empty slots skipped."""
    path = os.path.join(EMHTTP, "disks.ini")
    out, name, seen = [], None, set()
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith("["):
                name = line.strip('[]"')
            elif line.startswith("device=") and name and name != "flash":
                dev = line.split("=", 1)[1].strip('"')
                if dev and dev not in seen:
                    seen.add(dev)
                    out.append((dev, name))
    except OSError:
        pass
    return out


def rotational(dev):
    """True for a spinning drive, False for an SSD/NVMe, None when the kernel does not know the device."""
    try:
        return open(f"{SYS_BLOCK}/{dev}/queue/rotational").read().strip() == "1"
    except OSError:
        return None


def is_rotational(dev):
    return rotational(dev) is True


def mount_base(name):
    """disk1 -> disk1, trunk2 -> trunk (pool members), parity -> None."""
    if name.startswith("parity"):
        return None
    return name if re.fullmatch(r"disk\d+", name) else re.sub(r"\d+$", "", name)


BLOCK_NAME = re.compile(r"^(sd[a-z]+|nvme\d+n\d+)$")     # whole drives, no partitions, loop or md


def all_devices():
    """{device: (name, hdd)} for every drive Unraid knows (disks.ini), SSDs and NVMe included.
    Without disks.ini: every sdX and nvmeXnY in /sys/block, named after the device.
    Devices the kernel does not know (no /sys/block entry) are left out."""
    out = {}
    for d, n in read_disks_ini():
        r = rotational(d)
        if r is not None:
            out[d] = (n, r)
    if out:
        return out
    try:
        names = sorted(os.listdir(SYS_BLOCK))
    except OSError:
        return {}
    for d in names:
        r = rotational(d) if BLOCK_NAME.match(d) else None
        if r is not None:
            out[d] = (d, r)
    return out


def hdd_devices(devs=None):
    """{device: name} for the spinning drives only."""
    devs = all_devices() if devs is None else devs
    return {d: n for d, (n, hdd) in devs.items() if hdd}


# What the poller saw last: the watcher uses it without asking the drives again
drive_states = {}      # device -> 'active' / 'standby'
known_devices = {}     # device -> (name, hdd), the last all_devices()


def mount_devices(base, devs=None):
    """The devices of a mount under /mnt: the members of a pool (trunk, trunk2), or one array disk (diskN)."""
    devs = known_devices if devs is None else devs
    return sorted(d for d, (n, _) in devs.items() if mount_base(n) == base)


# Not a filesystem on a drive: container root filesystems (Docker's data-root can live on a pool), memory
# and kernel filesystems, and the user shares
SKIP_FSTYPES = {"overlay", "tmpfs", "ramfs", "proc", "sysfs", "nsfs", "devtmpfs", "devpts", "mqueue", "cgroup",
                "cgroup2", "autofs", "squashfs", "fuse.shfs", "fuse.lxcfs", "binfmt_misc", "debugfs", "tracefs"}


def mounted():
    """[(source, mount point, filesystem type)] from MOUNTS_FILE."""
    out = []
    for line in open(MOUNTS_FILE):
        p = line.split()
        if len(p) >= 3:
            out.append((p[0].replace("\\040", " "), p[1].replace("\\040", " "), p[2]))
    return out


def target_mounts():
    """Mounts of HDDs (and of the SSD pools chosen in `ssd_watch`) under /mnt, including ZFS datasets below them.
    One per filesystem: fatrace marks the whole filesystem, so a bind mount of one that is already watched
    (a Docker volume that binds a folder of the pool) would report every event twice, under another path.
    Of the mounts of one source the shortest under /mnt is the real one; overlay and other non-drive
    filesystems are skipped."""
    if WATCH_OVERRIDE:
        roots = [r.rstrip("/") for r in WATCH_OVERRIDE]
    else:
        # HDDs always; SSD pools (and SSDs in the array) only when chosen in Settings
        ssd = set(read_settings().get("ssd_watch", []))
        roots = sorted({f"/mnt/{b}" for n, hdd in all_devices().values()
                        if (hdd or lane_key(n) in ssd) and (b := mount_base(n))})
    def in_scope(mp):
        return mp.startswith("/mnt/") or any(mp == r or mp.startswith(r + "/") for r in roots)
    # a root given in WATCH counts whatever its type (/tmp is often tmpfs); below it the usual skips apply
    mps = [(src, mp) for src, mp, fs in mounted()
           if in_scope(mp) and (fs not in SKIP_FSTYPES or (WATCH_OVERRIDE and mp in roots))]
    real = {}
    for src, mp in sorted(mps, key=lambda x: (len(x[1]), x[1])):
        real.setdefault(src, mp)
    return sorted({mp for src, mp in mps if real[src] == mp
                   for r in roots if mp == r or mp.startswith(r + "/")})


# =====================================================================================
# Poller: spin state
# =====================================================================================
def parse_hdparm(out):
    """Output of `hdparm -C` -> 'active', 'standby' or None."""
    m = re.search(r"drive state is:\s+(\S+)", out)
    if not m:
        return None
    s = m.group(1)
    if s in ("standby", "sleeping"):
        return "standby"
    if s.startswith("active") or s.startswith("idle"):
        return "active"
    return None


# Our own tools (hdparm, smartctl, fatrace) are never logged (rule 3): their PIDs are registered as soon as
# they exist, and kept a little after they end, because their events can still be in the queue.
own_pids = {}          # pid -> time it ended, None while it runs
OWN_PID_KEEP = 60


def own_pid(pid, running=True):
    now = time.time()
    own_pids[str(pid)] = None if running else now
    for k, t in list(own_pids.items()):
        if t is not None and now - t > OWN_PID_KEEP:
            own_pids.pop(k, None)


def run_own(cmd, timeout):
    """subprocess.run for one of our own tools, with its PID registered in own_pids."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    own_pid(p.pid)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise
    finally:
        own_pid(p.pid, running=False)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def drive_state(dev):
    """'active', 'standby' or None. hdparm does not wake the drive; neither does smartctl -n standby."""
    try:
        out = run_own(["hdparm", "-C", f"/dev/{dev}"], timeout=15).stdout
        st = parse_hdparm(out)
        if st:
            return st
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:   # SAS and controllers where hdparm says nothing
        r = run_own(["smartctl", "-n", "standby", "-i", f"/dev/{dev}"], timeout=15)
        if "STANDBY" in r.stdout.upper():
            return "standby"
        if r.returncode == 0:
            return "active"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def io_counters(dev):
    """(read KiB, written KiB, reads, writes) since boot from /sys/block/<dev>/stat, or None.
    Kernel counters: reading them never touches the drive, so it does not wake a sleeping HDD."""
    try:
        f = open(f"{SYS_BLOCK}/{dev}/stat").read().split()
        return (int(f[2]) // 2, int(f[6]) // 2, int(f[0]), int(f[4]))      # sectors are 512 bytes
    except (OSError, ValueError, IndexError):
        return None


# Wear of an SSD from SMART: smartctl only ever runs on SSDs (never on an HDD, hard rule 1), with
# `-n standby` as a second guard, and at most once per WEAR_INTERVAL per drive.
WEAR_INTERVAL = 3600
# SATA attributes whose normalised value is the life left in percent (Samsung, Kingston/SandForce, Intel,
# Crucial/Micron, WD), and those whose raw value counts written logical blocks
WEAR_LEFT_IDS = (177, 231, 233, 202, 169)
WRITTEN_LBA_IDS = (241, 246)


def parse_wear(out):
    """(percentage used or None, KiB written or None) from `smartctl -j -A`; (None, None) when unknown."""
    try:
        j = json.loads(out)
    except ValueError:
        return None, None
    nv = j.get("nvme_smart_health_information_log")
    if isinstance(nv, dict):
        pct, units = nv.get("percentage_used"), nv.get("data_units_written")
        return (pct if isinstance(pct, int) else None,
                units * 500 if isinstance(units, int) else None)     # units of 1000 × 512 bytes
    table = {a.get("id"): a for a in (j.get("ata_smart_attributes") or {}).get("table", []) if isinstance(a, dict)}
    pct = kib = None
    for i in WEAR_LEFT_IDS:
        v = (table.get(i) or {}).get("value")
        if isinstance(v, int) and 0 <= v <= 100:
            pct = 100 - v
            break
    lbs = j.get("logical_block_size") if isinstance(j.get("logical_block_size"), int) else 512
    for i in WRITTEN_LBA_IDS:
        a = table.get(i) or {}
        raw = (a.get("raw") or {}).get("value")
        if isinstance(raw, int) and raw > 0:
            kib = written_kib(a.get("name"), raw, lbs)
            break
    return pct, kib


def written_kib(name, raw, lbs):
    """KiB from a 'written' attribute: the unit is in its name (Intel Host_Writes_32MiB, Kingston and others
    Lifetime_Writes_GiB / Host_Writes_GiB, some _MiB); without a unit it counts logical blocks."""
    n = (name or "").lower()
    if "32mib" in n:
        return raw * 32 * 1024
    if "gib" in n or n.endswith("_gb"):
        return raw * 1024 * 1024
    if "mib" in n:
        return raw * 1024
    return raw * lbs // 1024


def ssd_wear(dev):
    """(percentage used, KiB written) of an SSD, each None when unknown."""
    try:
        r = run_own(["smartctl", "-n", "standby", "-j", "-A", f"/dev/{dev}"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return parse_wear(r.stdout)


# io and wear lines wait here while every HDD sleeps: writing them each minute would keep a drive awake
# that holds DATA_DIR (rule 3). The web server sends them along with the day file, so the
# dashboard is live anyway; they are written as soon as something else is written or an HDD spins.
IO_PENDING_MAX = 200_000
io_pending = []
io_lock = threading.Lock()


def flush_io():
    with io_lock:
        lines = io_pending[:]
        io_pending.clear()
    if lines:
        append_lines(lines)


def pending_io(day):
    """The io and wear lines of `day` that are not in the day file yet."""
    with io_lock:
        return [ln for ln in io_pending if day_of(ln.split(",", 1)[0]) == day]


def data_dir_devices(devs=None):
    """The devices that hold DATA_DIR, from mountinfo: a ZFS pool, an array disk (/dev/mdX, also encrypted),
    a pool partition, or a user share that only lives on a pool. None when that cannot be told."""
    devs = (known_devices or all_devices()) if devs is None else devs
    best = None
    try:
        for line in open(MOUNTINFO):
            pre, _, post = line.partition(" - ")
            f, g = pre.split(), post.split()
            if len(f) < 5 or len(g) < 2:
                continue
            mp = f[4].replace("\\040", " ")
            if (DATA == mp or DATA.startswith(mp.rstrip("/") + "/")) and (best is None or len(mp) >= len(best[0])):
                best = (mp, f[3].replace("\\040", " "), g[0], g[1])
    except OSError:
        return None
    if not best:
        return None
    mp, root, fstype, src = best
    if fstype == "zfs":
        return mount_devices(src.split("/")[0], devs) or None
    if fstype == "fuse.shfs":
        inner = root.rstrip("/") + DATA[len(mp.rstrip("/")):]       # the path below /mnt/user
        share = inner.strip("/").split("/")[0]
        st, pool = share_storage(shares_ini().get(share, {}))
        return (mount_devices(pool, devs) or None) if st == "pool" else None
    m = re.fullmatch(r"/dev/(?:mapper/)?md(\d+)(?:p\d+)?", src)
    if m:
        return mount_devices(f"disk{m.group(1)}", devs) or None
    m = re.fullmatch(r"/dev/(?:mapper/)?(sd[a-z]+|nvme\d+n\d+)(?:p?\d+)?", src)
    if m and m.group(1) in devs:
        base = mount_base(devs[m.group(1)][0])
        return (mount_devices(base, devs) if base else [m.group(1)]) or None
    return None


def poll_once(last, now=None):
    """One poll. Writes to the day file:
    - `disk` lines (device, name, hdd/ssd) on the first poll of a day or when the drives change;
    - `spin` on a state change of an HDD;
    - `state` with the current state of every HDD on the first poll of a day or after a start,
      so every day file can be read on its own. A `state` is not a spin-up. SSDs get neither;
    - `io` with what every drive read and wrote since the previous poll (only when not zero);
    - `wear` with the SMART wear of every SSD, once per WEAR_INTERVAL and on the first poll of a day.
      io and wear lines wait in `io_pending` while every HDD sleeps and nothing else is written.
    `last` keeps the previous state. Returns the number of spin and state lines."""
    now = int(time.time() if now is None else now)
    day, alld = day_of(now), all_devices()
    devs = hdd_devices(alld)
    known = last.setdefault("st", {})
    new_day = last.get("day") != day
    lines = []
    if new_day or alld != last.get("all"):
        lines += [f"{now},disk,{d},{n},{'hdd' if hdd else 'ssd'}" for d, (n, hdd) in sorted(alld.items())]
    n, ups = 0, []
    for dev in sorted(devs):
        st = drive_state(dev)
        if not st:
            continue
        if dev in known and st != known[dev]:
            lines.append(f"{now},spin,{dev},{st}")
            n += 1
            if st == "active":
                ups.append((dev, devs[dev]))
        elif dev not in known or new_day:
            lines.append(f"{now},state,{dev},{st}")
            n += 1
        known[dev] = st
    prev, io = last.setdefault("io", {}), []
    for dev in sorted(alld):
        c = io_counters(dev)
        if c is None:
            continue
        p, prev[dev] = prev.get(dev), c
        if p is None:
            continue
        delta = [x - y for x, y in zip(c, p)]
        if any(x < 0 for x in delta):        # counters reset (device replaced): start over
            continue
        if any(delta):
            io.append(f"{now},io,{dev}," + ",".join(map(str, delta)))
    for dev in [d for d in prev if d not in alld]:
        del prev[dev]
    asked = last.setdefault("wear", {})
    for dev in sorted(d for d, (_, hdd) in alld.items() if not hdd):     # SSDs only: never smartctl on an HDD
        if not new_day and now - asked.get(dev, -WEAR_INTERVAL) < WEAR_INTERVAL:
            continue
        asked[dev] = now
        pct, kib = ssd_wear(dev)
        if pct is not None or kib is not None:
            io.append(f"{now},wear,{dev},{'' if pct is None else pct},{'' if kib is None else kib}")
    last["day"], last["disks"], last["all"] = day, dict(devs), dict(alld)
    drive_states.clear()
    drive_states.update(known)
    known_devices.clear()
    known_devices.update(alld)
    with io_lock:
        io_pending.extend(io)
        del io_pending[:-IO_PENDING_MAX]
    # write the waiting lines when something is written anyway, or when it cannot wake a drive: the drive
    # of DATA_DIR is an SSD or spins; when that drive is unknown, when any HDD spins
    data_devs = data_dir_devices(alld)
    if data_devs is not None:
        safe = any(not alld[d][1] or known.get(d) == "active" for d in data_devs if d in alld)
    else:
        safe = any(known.get(d) == "active" for d in devs)
    if lines or not devs or safe:
        flush_io()
    if lines:
        append_lines(lines)
    if ups:
        try:
            notify_spinups(now, ups)
        except Exception as e:                  # a notification must never break the poll
            log("notify failed:", repr(e))
    if not devs:
        log("No HDDs found. Is /var/local/emhttp mounted and is the container privileged?")
    return n


# =====================================================================================
# Quiet hours: the poller reports a spin-up inside the configured window through a webhook
# =====================================================================================
NOTIFY_GAP = 600       # at most one notification per lane per 10 minutes (pool members spin up together)
notified = {}


def lane_key(name):
    """Lane key as in the frontend: parity and diskN belong to the array, pool members to their pool."""
    return "array" if re.fullmatch(r"parity\d*|disk\d+", name) else re.sub(r"\d+$", "", name)


def in_quiet(ts, quiet):
    """Is `ts` (epoch, local time) inside the quiet hours? A window like 23:00-07:00 runs over midnight."""
    if not quiet:
        return False
    lt = time.localtime(ts)
    m = lt.tm_hour * 60 + lt.tm_min
    f, t = (int(x[:2]) * 60 + int(x[3:]) for x in (quiet["from"], quiet["to"]))
    return f <= m < t if f < t else (m >= f or m < t)


def send_webhook(url, payload, timeout=10):
    """JSON to the webhook. `text` (Slack, ntfy, Gotify) and `content` (Discord) hold the same sentence."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "spin-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def notify_spinups(now, ups, send=None):
    """Report spin-ups [(device, name)] of one poll when they fall inside the quiet hours and their
    lane takes part. Sending happens in its own thread, so the poller never waits for the network."""
    st = read_settings()
    quiet, url = st.get("quiet"), st.get("webhook")
    if not ups or not url or not in_quiet(now, quiet):
        return []
    lanes = {}
    for dev, name in ups:
        k = lane_key(name)
        if not quiet["drives"] or k in quiet["drives"]:
            lanes.setdefault(k, []).append(dev)
    sent = []
    for k, devs in sorted(lanes.items()):
        if now - notified.get(k, -NOTIFY_GAP) < NOTIFY_GAP:
            continue
        notified[k] = now
        msg = f"{k} spun up at {time.strftime('%H:%M', time.localtime(now))} during quiet hours ({quiet['from']}-{quiet['to']})"
        payload = {"event": "spin-up", "lane": k, "devices": devs, "epoch": now,
                   "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "text": msg, "content": msg}
        sent.append(payload)
        if send:
            send(url, payload)
        else:
            threading.Thread(target=webhook_safely, args=(url, payload), daemon=True).start()
    return sent


def webhook_safely(url, payload):
    try:
        send_webhook(url, payload)
    except Exception as e:
        log("webhook failed:", repr(e))


def poller():
    last = {}
    while True:
        try:
            poll_once(last)
            health["poll"], health["poll_error"] = time.time(), None
        except Exception as e:
            health["poll_error"] = repr(e)
            log("poller error:", repr(e))
        time.sleep(POLL_INTERVAL)


# =====================================================================================
# Watcher: who opens what (fatrace)
# =====================================================================================
LINE = re.compile(r"^(\d+)\.\d+ (.*)\((\d+)\): ([RWOCD+<>]+)\s+(/.*?)(?: exe=.*)?$")
# W (write) comes before O (open): an open with write intent (OW/ORW) counts as a write
OPS = [("D", "verwijderd"), ("+", "aangemaakt"), ("<", "verplaatst"), (">", "verplaatst"),
       ("W", "geschreven"), ("O", "geopend")]
SHELLS = {"sshd": "ssh", "sshd-session": "ssh", "ttyd": "webterminal", "login": "console",
          "agetty": "console", "tmux: server": "tmux", "screen": "screen"}

events = queue.Queue(maxsize=20_000)
procs = []
recent = {}
names = {}
seen = {}             # pid -> (time, comm, result of classify)
ignored_comms = {}    # comm -> last time a process with that name was ignored
stats = {"written": 0, "dropped": 0}
MY_PID = str(os.getpid())


def proc_comm_ppid(pid):
    st = open(f"{PROC}/{pid}/stat").read()
    return st[st.index("(") + 1:st.rindex(")")], st[st.rindex(")") + 2:].split()[1]


def chain_of(pid, limit=15):
    chain = []
    while pid and pid not in ("0", "1") and len(chain) < limit:
        try:
            comm, pid = proc_comm_ppid(pid)
        except (OSError, ValueError, IndexError):
            break
        chain.append(comm)
    return chain


_ignore_seen = {"t": None, "on": False}


def ignore_active(now=None):
    """Is a process from IGNORE_PROCS (e.g. cache_dirs) running? Checked again at most once per IGNORE_SCAN."""
    now = time.time() if now is None else now
    if _ignore_seen["t"] is not None and now - _ignore_seen["t"] < IGNORE_SCAN:
        return _ignore_seen["on"]
    on = False
    try:
        pids = [p for p in os.listdir(PROC) if p.isdigit()]
    except OSError:
        pids = []
    for p in pids:
        try:
            comm, _ = proc_comm_ppid(p)
        except (OSError, ValueError, IndexError):
            continue
        if comm in IGNORE_PROCS:
            on = True
            break
        try:
            args = open(f"{PROC}/{p}/cmdline", "rb").read().split(b"\0")[:2]
        except OSError:
            continue
        if any(os.path.basename(a.decode(errors="replace")) in IGNORE_PROCS for a in args):
            on = True
            break
    _ignore_seen.update(t=now, on=on)
    return on


def is_ignored(pid, limit=15):
    """Does this process (or an ancestor) belong to IGNORE_PROCS? Looks at the process name and at the
    command line, for scripts started as `bash /path/to/cache_dirs`."""
    while pid and pid not in ("0", "1") and limit:
        limit -= 1
        try:
            comm, ppid = proc_comm_ppid(pid)
        except (OSError, ValueError, IndexError):
            return False
        if comm in IGNORE_PROCS:
            return True
        try:
            args = open(f"{PROC}/{pid}/cmdline", "rb").read().split(b"\0")[:2]
        except OSError:
            args = []
        if any(os.path.basename(a.decode(errors="replace")) in IGNORE_PROCS for a in args):
            return True
        pid = ppid
    return False


def container_id(pid):
    try:
        cg = open(f"{PROC}/{pid}/cgroup").read()
    except OSError:
        return None
    # with its own cgroup namespace this looks like "0::/../<id>", without "docker/"
    m = re.search(r"(?:^|[/-])([0-9a-f]{64})(?:\.scope)?\s*$", cg, re.M)
    return m.group(1) if m else None


def docker_conn(timeout):
    """HTTP over the Docker socket. timeout None: keep waiting (the event stream)."""
    c = http.client.HTTPConnection("localhost", timeout=timeout)
    c.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.sock.settimeout(timeout)
    c.sock.connect(DOCKER_SOCK)
    return c


def docker_get(path, timeout=2):
    """JSON of a GET on the Docker API, or None (no socket, error, other status)."""
    try:
        c = docker_conn(timeout)
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        c.close()
        return json.loads(body) if r.status == 200 else None
    except Exception:
        return None


names_failed = {}      # container id -> when Docker last gave no name; asked again after NAME_RETRY
NAME_RETRY = 60


def container_name(cid, now=None):
    """Name of a container. Only real names are remembered: when Docker does not answer, the short ID is
    used for now and Docker is asked again after NAME_RETRY (not for every event)."""
    if cid in names:
        return names[cid]
    now = time.time() if now is None else now
    if now - names_failed.get(cid, -NAME_RETRY) < NAME_RETRY:
        return cid[:12]
    name = (docker_get(f"/containers/{cid}/json") or {}).get("Name", "").lstrip("/")
    if not name:
        names_failed[cid] = now
        return cid[:12]
    names_failed.pop(cid, None)
    names[cid] = name
    return name


def classify(pid, comm, now=None):
    """Like classify_now(), but remembers the result per PID. A short-lived process (find of
    cache_dirs) has often stopped before all its events are handled; the later events then get
    the same result as the first one, while the process was still running.

    Safety net: if a process with the same name was ignored shortly before, a process with that
    name without a recognisable source (stopped, or orphaned so the chain can no longer be followed)
    is ignored too. A find from a shell, SMB or cron does have a source and is logged as usual."""
    now = time.time() if now is None else now
    hit = seen.get(pid)
    if hit and now - hit[0] < PID_TTL and hit[1] == comm:
        return hit[2]
    src = classify_now(pid, comm)
    if src is None:
        ignored_comms[comm] = now
    elif (src in (("unraid", comm), ("onbekend", f"{comm} (al gestopt)"))
          and now - ignored_comms.get(comm, -IGNORE_TTL) < IGNORE_TTL):
        src = None
    if src is None or src[0] != "onbekend":
        seen[pid] = (now, comm, src)
        if len(seen) > 10_000:
            for k, v in list(seen.items()):
                if now - v[0] >= PID_TTL:
                    seen.pop(k, None)
    return src


def classify_now(pid, comm):
    """(kind, name) of a process, or None when it is ignored (IGNORE_PROCS)."""
    cid = container_id(pid)
    if cid:
        return "container", container_name(cid)
    if not os.path.exists(f"{PROC}/{pid}"):
        return "onbekend", f"{comm} (al gestopt)"
    if is_ignored(pid):
        return None
    chain = [comm] + (chain_of(pid) or [comm])[1:]
    if "smbd" in chain:
        return "gebruiker", "SMB-share"
    if any(c.startswith("nfsd") for c in chain):
        return "gebruiker", "NFS-share"
    for c in chain:
        if c in SHELLS:
            return "gebruiker", f"shell ({SHELLS[c]})"
    if comm == "shfs":
        return "unraid", "shfs (via /mnt/user)"
    if comm.startswith("btrfs"):
        return "unraid", "btrfs"
    for c in chain:
        if c in ("mover", "move", "age_mover"):
            return "unraid", "mover"
        if c in ("crond", "cron"):
            return "unraid", "cron / User Scripts"
        if c.startswith("php") or c in ("emhttpd", "emhttp", "nginx"):
            return "unraid", "webGUI"
    return "unraid", comm


FD_SNAP_TTL = 1.0                      # a snapshot of all open files is reused this long (s)
_fd_snap = {"t": 0.0, "by_name": {}}
_fd_lock = threading.Lock()


def fd_snapshot(since):
    """{file name: [(pid, /proc/pid/fd/n)]} of all open files. A scan of thousands of files through
    /mnt/user gives thousands of shfs events per second; without a snapshot every event read all of
    /proc/*/fd again. Reused only when the snapshot was taken after `since` (the event), so a file
    that was just opened is always in it, and at most FD_SNAP_TTL seconds old."""
    with _fd_lock:
        now = time.time()
        if _fd_snap["t"] >= since and now - _fd_snap["t"] < FD_SNAP_TTL:
            return _fd_snap["by_name"]
        by = {}
        for pid in os.listdir(PROC):
            if not pid.isdigit():
                continue
            fddir = f"{PROC}/{pid}/fd"
            try:
                fds = os.listdir(fddir)
            except OSError:
                continue
            for fd in fds:
                try:
                    link = os.readlink(f"{fddir}/{fd}")
                except OSError:
                    continue
                by.setdefault(link.rsplit("/", 1)[-1], []).append((pid, f"{fddir}/{fd}"))
        _fd_snap.update(t=now, by_name=by)
        return by


_shares_cache = {"t": 0.0, "body": None}


def shares_ini(now=None):
    """read_shares_ini(), read again at most every 30 s (shfs events can come thousands a second)."""
    now = time.time() if now is None else now
    if _shares_cache["body"] is None or now - _shares_cache["t"] >= 30:
        _shares_cache.update(t=now, body=read_shares_ini())
    return _shares_cache["body"]


def share_locations(share, shares=None):
    """The mounts (pools, diskN) where a user share can have files, from its settings; None when unknown."""
    sh = (shares_ini() if shares is None else shares).get(share)
    if not sh:
        return None
    st, pool = share_storage(sh)
    if st == "unknown":
        return None
    pools = [p for p in dict.fromkeys((pool, sh.get("cachePool2"))) if p]
    inc = [x for x in (sh.get("include") or "").split(",") if x]
    exc = {x for x in (sh.get("exclude") or "").split(",") if x}
    disks = sorted({n for n, _ in known_devices.values() if re.fullmatch(r"disk\d+", n)
                    and (not inc or n in inc) and n not in exc}, key=lambda n: int(n[4:]))
    return {"pool": pools, "array": disks}.get(st, pools + disks)


def user_share_lookup_safe(path):
    """Rule 1: a lookup through /mnt/user lets shfs look on every disk or pool the share may use. Only safe
    when none of them, apart from the mount of the event itself (spinning), is an HDD that is not spinning
    or whose state is unknown. Unknown share settings or drives: not safe."""
    parts = path.split("/")
    if len(parts) < 5 or not known_devices:
        return False
    locs = share_locations(parts[3])
    if locs is None:
        return False
    for loc in locs:
        if loc == parts[2]:
            continue
        devs = mount_devices(loc)
        if not devs:
            return False
        if any(known_devices[d][1] and drive_states.get(d) != "active" for d in devs):
            return False
    return True


def via_user_share(path, since=None):
    """Who has /mnt/pool/Share/x open through /mnt/user/Share/x? Compares inodes. Only when the lookup
    through /mnt/user cannot touch a sleeping drive (user_share_lookup_safe)."""
    parts = path.split("/")
    if len(parts) < 5 or not user_share_lookup_safe(path):
        return None
    try:
        want = os.stat(f"{USER_SHARES}/" + "/".join(parts[3:]))
    except OSError:
        return None
    key = (want.st_dev, want.st_ino)
    for pid, fdpath in fd_snapshot(time.time() if since is None else since).get(parts[-1], []):
        try:
            st = os.stat(fdpath)
        except OSError:
            continue
        if (st.st_dev, st.st_ino) == key:
            try:
                comm, _ = proc_comm_ppid(pid)
            except (OSError, ValueError, IndexError):
                continue
            if comm != "shfs":
                return pid, comm
    return None


def share_root(path):
    """/mnt/pool/Share/a/b.mkv -> /mnt/pool/Share/ (unchanged when there is no share in it)."""
    parts = path.split("/")
    return "/".join(parts[:4]) + "/" if len(parts) > 4 else path


procs_seen = {}       # pid -> (comm, day) of the last proc line


def proc_line(ts, pid, comm):
    """`proc` line when this PID has not been logged with this process name today, otherwise None.
    That way the frontend knows which process inside a container (Plex Media Scanner, Plex Transcoder)
    opened a file, without changing the who line."""
    day = day_of(ts)
    if procs_seen.get(pid) == (comm, day):
        return None
    if len(procs_seen) > 50_000:
        procs_seen.clear()
    procs_seen[pid] = (comm, day)
    return f"{ts},proc,{pid},{comm.replace(',', ' ')}"


def write_who(ts, kind, name, pid, op, path, now=None, comm=None):
    if kind == "gebruiker" and name in ("SMB-share", "NFS-share"):
        path = share_root(path)              # one line per share
    now = time.time() if now is None else now
    # op is part of the key: a read and a write of the same path each count once
    k = (path, kind, name, op)
    with write_lock:
        if now - recent.get(k, 0) < DEDUP_S:
            return
        recent[k] = now
        if len(recent) > 50_000:
            for kk in [kk for kk, t in recent.items() if now - t > DEDUP_S]:
                del recent[kk]
    line = f"{ts},who,{kind},{name.replace(',', ' ')},{pid},{op},{path}"
    proc = proc_line(ts, pid, comm) if kind == "container" and comm else None
    append_lines([proc, line] if proc else [line])
    stats["written"] += 1
    if proc:
        broadcast(proc)                      # before the who line, so the live feed already knows the name
    broadcast(line)


def broadcast(line):
    """Put a who line on every open SSE connection. Full or closed connections are skipped;
    the periodic CSV refresh stays the source of truth."""
    with streams_lock:
        for q in list(streams):
            try:
                q.put_nowait(line)
            except queue.Full:
                pass


def resolve_shfs_later(ts, pid, op, path):
    since = time.time()                      # the file was opened now at the latest
    def run():
        time.sleep(SHFS_DELAY)
        hit = via_user_share(path, since)
        if hit:
            src = classify(*hit)
            if src:
                write_who(ts, *src, hit[0], op, path, comm=hit[1])
        else:
            write_who(ts, "unraid", "shfs (via /mnt/user)", pid, op, path)
    threading.Thread(target=run, daemon=True).start()


# Docker's own storage (its data-root on a pool): image layers, the writable layer of every container,
# and per container its config and logs. Every program a container starts opens its binaries and
# libraries here, thousands of opens a minute, so opens are not logged; writes to a layer are logged as
# that layer's folder (an image pull writes thousands of files), writes in containers/<id>/ (the logs) as they are.
DOCKER_STORE = re.compile(r"^(.*?/docker/(overlay2|btrfs|zfs|vfs|image|buildkit|containerd|containers|network|plugins|runtimes|tmp)/)(.*)$")
LAYER_ID = re.compile(r"^(?:subvolumes/|graph/)?[0-9a-f]{25,64}(?:-init)?(?=/|$)")


def docker_store_path(path, op):
    """None to skip the event, otherwise the path to log (see DOCKER_STORE)."""
    m = DOCKER_STORE.match(path)
    if not m:
        return path
    if op == "geopend":
        return None
    if m.group(2) == "containers":
        return path
    layer = LAYER_ID.match(m.group(3))
    return m.group(1) + (layer.group(0) + "/" if layer else "")


def classify_early(line):
    """Determine the source of a new PID right away (in the reading thread), while the process
    is still running. The consumer can lag behind; classify() then returns the remembered result."""
    m = LINE.match(line)
    if m and m.group(3) not in seen and m.group(3) != MY_PID and m.group(3) not in own_pids:
        classify(m.group(3), m.group(2))


def handle(line):
    m = LINE.match(line.rstrip("\n"))
    if not m:
        return
    ts, comm, pid, types, path = m.groups()
    if pid == MY_PID or pid in own_pids or is_own_file(path):
        return                               # do not log ourselves (rule 3)
    op = next((label for c, label in OPS if c in types), None)
    if not op:
        return
    store = docker_store_path(path, op)
    if store is None:
        return
    if store != path:
        path = store                         # a layer of Docker's storage, as one folder
    elif not path.endswith("/") and "D" not in types and os.path.isdir(path):
        path += "/"
    if comm == "shfs" and op == "geopend" and not path.endswith("/"):
        resolve_shfs_later(ts, pid, op, path)
        return
    src = classify(pid, comm)
    # Cache Dirs runs find so briefly that fatrace sometimes only sees "unknown" and the parents are gone.
    # find only opens folders: a folder open by an already stopped process without a source does not count then.
    if src and src[0] == "onbekend" and op == "geopend" and path.endswith("/") and ignore_active():
        return
    if src:
        write_who(ts, *src, pid, op, path, comm=comm)


def consumer():
    while True:
        try:
            handle(events.get())
        except Exception as e:
            log("error handling event:", repr(e))


def start_fatrace(mp):
    try:
        p = subprocess.Popen(["fatrace", "-c", "-tt", "-f", "O+WD<"], cwd=mp,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, errors="replace", bufsize=1)
    except OSError as e:
        log("could not start fatrace for", mp, e)
        return
    procs.append(p)
    own_pid(p.pid)

    def pump_out():
        for line in p.stdout:
            try:
                classify_early(line)
            except Exception as e:
                log("error in early classification:", repr(e))
            try:
                events.put_nowait(line)
            except queue.Full:
                stats["dropped"] += 1
        log("fatrace stopped for", mp, "exit code", p.wait())
        own_pid(p.pid, running=False)

    def pump_err():
        for line in p.stderr:
            log(f"fatrace {mp}:", line.rstrip())

    threading.Thread(target=pump_out, daemon=True).start()
    threading.Thread(target=pump_err, daemon=True).start()


def watcher():
    threading.Thread(target=consumer, daemon=True).start()
    current, beat = None, 0
    while True:
        try:
            mounts = target_mounts()
            dead = any(p.poll() is not None for p in procs)
            if mounts != current or dead:
                for p in procs:
                    p.terminate()
                procs.clear()
                if mounts:
                    log("Watching:", " ".join(mounts))
                    for mp in mounts:
                        start_fatrace(mp)
                else:
                    log("No HDD mounts found under /mnt; retrying in a minute")
                current = mounts
                health["mounts"] = list(mounts)
        except Exception as e:
            log("watcher error:", repr(e))
        beat += 1
        if beat % 10 == 0 and current:
            log(f"active: {len(procs)} fatrace, {stats['written']} lines, "
                f"queue {events.qsize()}, dropped {stats['dropped']}")
        time.sleep(60)


# =====================================================================================
# Docker: lifecycle (ctr lines) and mounts per container
# =====================================================================================
CTR_ACTIONS = {"start": "start", "die": "die",
               "health_status: healthy": "healthy", "health_status: unhealthy": "unhealthy"}


def ctr_line(ev, now=None):
    """`ctr` line for a Docker event (start, die with exit code, healthy, unhealthy), otherwise None."""
    if ev.get("Type") != "container":
        return None
    act = CTR_ACTIONS.get(ev.get("Action") or ev.get("status") or "")
    attrs = (ev.get("Actor") or {}).get("Attributes") or {}
    name = (attrs.get("name") or "").replace(",", " ")
    if not act or not name:
        return None
    ts = int(ev.get("time") or (time.time() if now is None else now))
    line = f"{ts},ctr,{name},{act}"
    code = str(attrs.get("exitCode", ""))
    if act == "die" and code.lstrip("-").isdigit():
        line += f",{code}"
    return line


def docker_events_once():
    """One connection to the event stream; returns when Docker closes the connection."""
    flt = urllib.parse.quote(json.dumps({"type": ["container"]}))
    c = docker_conn(None)
    c.request("GET", f"/events?filters={flt}")
    r = c.getresponse()
    if r.status != 200:
        raise OSError(f"docker events: HTTP {r.status}")
    health["docker"] = "connected"
    while True:
        raw = r.readline()
        if not raw:
            health["docker"] = "disconnected"
            return
        try:
            line = ctr_line(json.loads(raw))
        except ValueError:
            continue
        if line:
            append_lines([line])


def docker_events():
    if not os.path.exists(DOCKER_SOCK):
        health["docker"] = "no socket"
        log(f"No Docker socket at {DOCKER_SOCK}: container start/stop is not logged")
        return
    while True:
        try:
            docker_events_once()
        except Exception as e:
            health["docker"] = "disconnected"
            log("docker events:", repr(e))
        time.sleep(30)


def read_shares_ini():
    """{share: {key: value}} from Unraid's shares.ini (tmpfs under /emhttp, wakes nothing)."""
    out, cur = {}, None
    try:
        for line in open(os.path.join(EMHTTP, "shares.ini")):
            line = line.strip()
            if line.startswith("["):
                cur = out.setdefault(line.strip('[]"'), {})
            elif "=" in line and cur is not None:
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return out


def share_storage(sh):
    """(storage, pool) according to the share settings. Storage: pool (pool only), array (array only),
    pool>array (first the pool, the mover moves it to the array), array>pool (the mover moves it to
    the pool) or unknown. The field names differ per Unraid version; unknown means unknown."""
    use = (sh.get("useCache") or sh.get("shareUseCache") or "").lower()
    pool = sh.get("cachePool") or sh.get("shareCachePool") or "cache"
    return {"only": "pool", "no": "array", "yes": "pool>array", "prefer": "array>pool"}.get(use, "unknown"), pool


def locate(src, shares, hdd_bases):
    """Where a host path under /mnt lives: a share (with its storage), an array disk or a pool,
    and whether that is (possibly) an HDD. Only settings and names: no stat on the drive (rule 1)."""
    p = [x for x in src.split("/") if x]
    if len(p) < 2 or p[0] != "mnt":
        return None
    array_hdd = any(re.fullmatch(r"disk\d+", b) for b in hdd_bases)
    top = p[1]
    if top in ("user", "user0"):
        if len(p) < 3:
            return {"kind": "user", "hdd": array_hdd or bool(hdd_bases)}
        st, pool = share_storage(shares.get(p[2], {}))
        if top == "user0":
            st = "array"
        hdd = {"pool": pool in hdd_bases, "array": array_hdd, "pool>array": array_hdd,
               "array>pool": pool in hdd_bases}.get(st)
        return {"kind": "share", "share": p[2], "storage": st, "pool": pool, "hdd": hdd}
    kind = "disk" if re.fullmatch(r"disk\d+", top) else "pool"
    return {"kind": kind, "name": top, "hdd": top in hdd_bases}


def volume_devices():
    """{volume name: folder} for local volumes that bind a folder (driver_opts o=bind, device=/mnt/...).
    For such a volume Docker shows /var/lib/docker/volumes/<name>/_data as source; the real folder is
    in the volume's options."""
    out = {}
    for v in (docker_get("/volumes") or {}).get("Volumes") or []:
        opts = v.get("Options") or {}
        if v.get("Driver") == "local" and "bind" in (opts.get("o") or "").split(",") and opts.get("device"):
            out[v.get("Name")] = opts["device"]
    return out


_containers_cache = {"t": 0.0, "body": None}


def containers_info(now=None):
    """Content of /data/containers.json: per container the mounts under /mnt and where they live.
    Asked again at most every 30 s; without a Docker socket an empty list."""
    now = time.time() if now is None else now
    if _containers_cache["body"] is not None and now - _containers_cache["t"] < 30:
        return _containers_cache["body"]
    shares = read_shares_ini()
    hdd_bases = {b for b in map(mount_base, hdd_devices().values()) if b}
    vols = volume_devices()
    out = []
    for c in docker_get("/containers/json?all=1") or []:
        name = ((c.get("Names") or [""])[0]).lstrip("/")
        if not name:
            continue
        mounts = []
        for m in c.get("Mounts") or []:
            src = m.get("Source") or ""
            if m.get("Type") == "volume" and m.get("Name") in vols:
                src = vols[m["Name"]]            # named volume that points to a folder under /mnt
            where = locate(src, shares, hdd_bases)
            if where:
                entry = {"source": src, "target": m.get("Destination"), "rw": bool(m.get("RW", True)), "where": where}
                if m.get("Type") == "volume":
                    entry["volume"] = m.get("Name")
                mounts.append(entry)
        out.append({"name": name, "state": c.get("State") or "",
                    "project": (c.get("Labels") or {}).get("com.docker.compose.project") or "",
                    "mounts": mounts})
    body = {"docker": os.path.exists(DOCKER_SOCK), "containers": sorted(out, key=lambda x: x["name"].lower())}
    _containers_cache.update(t=now, body=body)
    return body


# =====================================================================================
# Housekeeping: clean up old lines, restore old labels
# =====================================================================================
def epoch_of(line):
    first = line.split(",", 1)[0]
    if first.isdigit():
        return int(first)
    try:
        return datetime.fromisoformat(first).timestamp()
    except ValueError:
        return None


def prune_days(now=None):
    """Remove day files older than RETENTION_DAYS (whole days)."""
    cutoff = day_of((time.time() if now is None else now) - RETENTION_DAYS * 86400)
    old = [d for d in list_days() if d < cutoff]
    with write_lock:
        for d in old:
            try:
                os.remove(day_path(d))
            except OSError:
                pass
    if old:
        log(f"removed {len(old)} day file(s) older than {RETENTION_DAYS} days")


def relabel_existing(now=None):
    """Link who lines with 'unraid'/'onbekend' from today and yesterday to a container after all
    when the process is still running."""
    now = time.time() if now is None else now
    for day in {day_of(now - 86400), day_of(now)}:
        path = day_path(day)
        with write_lock:
            try:
                lines = open(path).readlines()
            except OSError:
                continue
            changed = 0
            for i, line in enumerate(lines):
                p = line.rstrip("\n").split(",", 6)
                if len(p) < 7 or p[1] != "who" or p[2] not in ("unraid", "onbekend"):
                    continue
                try:
                    comm, _ = proc_comm_ppid(p[4])
                except (OSError, ValueError, IndexError):
                    continue
                cid = container_id(p[4])
                if not cid or comm != p[3].replace(" (al gestopt)", ""):
                    continue
                p[2], p[3] = "container", container_name(cid).replace(",", " ")
                lines[i] = ",".join(p) + "\n"
                changed += 1
            if changed:
                open(path + ".tmp", "w").writelines(lines)
                os.replace(path + ".tmp", path)
                log(f"{day}: linked {changed} older line(s) to a container")


def housekeeping():
    while True:
        try:
            prune_days()
        except Exception as e:
            log("cleanup failed:", repr(e))
        time.sleep(6 * 3600)


# =====================================================================================
# Migration of the separate files (spin.csv, disks.csv, who.csv, activity.csv)
# =====================================================================================
def _legacy_records():
    """(epoch, line) in the new format from the old files, the state changes
    [(epoch, device, state)] and the drive names."""
    recs, spins, disks, skipped = [], [], [], 0
    for line in _read(DISKS):
        p = line.split(",")
        if len(p) == 2 and p[0] and p[1]:
            disks.append((p[0], p[1]))
    for line in _read(SPIN):                 # 2026-10-01T04:15:07+02:00,/dev/sdd,active
        p = line.split(",")
        ts = epoch_of(line)
        if len(p) == 3 and ts is not None and p[2] in ("active", "standby"):
            ts, dev = int(ts), p[1].replace("/dev/", "")
            recs.append((ts, f"{ts},spin,{dev},{p[2]}"))
            spins.append((ts, dev, p[2]))
        else:
            skipped += 1
    for line in _read(WHO):                  # epoch,kind,name,pid,action,path
        p = line.split(",", 5)
        if len(p) == 6 and p[0].isdigit() and p[5].startswith("/"):
            recs.append((int(p[0]), f"{p[0]},who,{line.split(',', 1)[1]}"))
        else:
            skipped += 1
    for line in _read(ACTIVITY):             # epoch,disk,EV,path
        p = line.split(",", 3)
        if len(p) == 4 and p[0].isdigit() and p[3].startswith("/"):
            recs.append((int(p[0]), f"{p[0]},activity,{line.split(',', 1)[1]}"))
        else:
            skipped += 1
    return recs, sorted(spins), disks, skipped


def _read(path):
    try:
        return [l.rstrip("\n") for l in open(path) if l.strip()]
    except OSError:
        return []


def _sort_key(line):
    p = line.split(",", 2)
    try:
        return int(p[0]), TYPES.index(p[1]), line
    except (ValueError, IndexError):
        return 0, 0, line


def migrate():
    """Convert the separate files to day files once. Safe to repeat: existing day lines stay
    and duplicate lines are merged. The old files go to DATA_DIR/legacy, so nothing is lost."""
    sources = [f for f in (SPIN, DISKS, WHO, ACTIVITY) if os.path.exists(f)]
    if not sources:
        return
    recs, spins, disks, skipped = _legacy_records()
    by_day = {}
    for ts, line in recs:
        by_day.setdefault(day_of(ts), []).append(line)
    with write_lock:
        os.makedirs(DAYS, exist_ok=True)
        for day, lines in by_day.items():
            t0 = day_start(day)
            head = [f"{t0},disk,{d},{n}" for d, n in disks]
            state = {}
            for ts, dev, st in spins:        # state of each drive at the start of the day
                if ts >= t0:
                    break
                state[dev] = st
            head += [f"{t0},state,{d},{st}" for d, st in sorted(state.items())]
            path = day_path(day)
            merged = set(_read(path)) | set(head) | set(lines)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.writelines(l + "\n" for l in sorted(merged, key=_sort_key))
            os.replace(tmp, path)
        os.makedirs(LEGACY, exist_ok=True)
        for f in sources:
            dest = os.path.join(LEGACY, os.path.basename(f))
            if os.path.exists(dest):
                dest += f".{int(time.time())}"
            os.replace(f, dest)
    log(f"Migration: {len(recs)} lines from {', '.join(os.path.basename(f) for f in sources)} "
        f"split into {len(by_day)} day files; old files moved to {LEGACY}"
        + (f"; skipped {skipped} unreadable line(s)" if skipped else ""))


# =====================================================================================
# Settings
# =====================================================================================
HIDE_MAX = 200          # patterns
HIDE_LEN = 500          # characters per pattern
SETTINGS_BODY_MAX = 64 * 1024
settings_lock = threading.Lock()


def read_settings():
    """Saved settings; {} when there is nothing (yet) or the file cannot be read.
    Without a 'hide' key the browser knows it may move its own list over once."""
    try:
        with open(SETTINGS) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(s, dict):
        return {}
    out = {}
    for key, clean in SETTINGS_KEYS.items():       # an invalid key is dropped, the rest stays
        if key in s:
            try:
                out[key] = clean(s[key])
            except ValueError:
                pass
    return out


def clean_hide(value):
    """Check a list of patterns: text only, without control characters, within the limits.
    Empty lines are dropped; ValueError for anything that is not a valid list."""
    if not isinstance(value, list) or len(value) > HIDE_MAX:
        raise ValueError(f"hide must be a list of at most {HIDE_MAX} patterns")
    out = []
    for p in value:
        if not isinstance(p, str):
            raise ValueError("every pattern must be a string")
        p = p.strip()
        if len(p) > HIDE_LEN or any(ord(c) < 32 or ord(c) == 127 for c in p):
            raise ValueError(f"patterns are at most {HIDE_LEN} characters, without control characters")
        if p and p not in out:
            out.append(p)
    return out


HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
LANE_NAME = re.compile(r"^[\w.-]{1,64}$")


def clean_quiet(value):
    """Quiet hours: None (off) or {from, to, drives}. from/to as HH:MM (may run over midnight),
    drives a list of lane keys (array, trunk, ...); empty = all drives."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("quiet must be an object or null")
    f, t, drives = value.get("from"), value.get("to"), value.get("drives", [])
    if not (isinstance(f, str) and HHMM.match(f) and isinstance(t, str) and HHMM.match(t)) or f == t:
        raise ValueError("quiet.from and quiet.to are two different times as HH:MM")
    if not isinstance(drives, list) or len(drives) > 64 or not all(isinstance(d, str) and LANE_NAME.match(d) for d in drives):
        raise ValueError("quiet.drives is a list of at most 64 drive or pool names")
    return {"from": f, "to": t, "drives": list(dict.fromkeys(drives))}


def clean_webhook(value):
    """Webhook URL for notifications: empty (off) or an http(s) address without spaces, at most 500 characters."""
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError("webhook must be a string")
    v = value.strip()
    parts = urllib.parse.urlsplit(v)
    if (len(v) > 500 or parts.scheme not in ("http", "https") or not parts.netloc
            or any(ord(c) <= 32 or ord(c) == 127 for c in v)):
        raise ValueError("webhook is an http(s) URL of at most 500 characters")
    return v


def clean_ssd_watch(value):
    """SSD pools (lane keys: cache, array, ...) whose file access the watcher follows; empty = none."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64 or not all(isinstance(d, str) and LANE_NAME.match(d) for d in value):
        raise ValueError("ssd_watch is a list of at most 64 pool names")
    return list(dict.fromkeys(value))


def clean_wear_warn(value):
    """Warn when an SSD writes more than this many GB per day: an integer 1-1000000, or None (never)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000:
        raise ValueError("wear_warn is a whole number of GB per day (1-1000000) or null")
    return value


SETTINGS_KEYS = {"hide": clean_hide, "quiet": clean_quiet, "webhook": clean_webhook, "ssd_watch": clean_ssd_watch,
                 "wear_warn": clean_wear_warn}


def save_settings(new):
    """Check the known keys of `new` and write them atomically (only in DATA_DIR).
    Returns the saved settings; ValueError on invalid input."""
    if not isinstance(new, dict):
        raise ValueError("settings must be a JSON object")
    with settings_lock:
        s = read_settings()
        cleaned = {k: clean(new[k]) for k, clean in SETTINGS_KEYS.items() if k in new}   # check everything first
        s.update(cleaned)
        tmp = SETTINGS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=1)
        os.replace(tmp, SETTINGS)
    return s


# =====================================================================================
# Web
# =====================================================================================
class Handler(http.server.SimpleHTTPRequestHandler):
    """/ → dashboard, /data/days.json → list of days, /data/days/YYYY-MM-DD.csv → day file,
    /data/spins.csv → only the spin lines of all days, /data/containers.json → mounts per
    container, /data/settings.json → settings (GET reads, POST saves), /health → health."""
    def do_GET(self):
        clean = self.path.split("?", 1)[0]
        if clean == "/data/days.json":
            self.send_json(days_index())
            return
        if clean == "/data/settings.json":
            self.send_json(read_settings())
            return
        if clean == "/data/spins.csv":
            self.send_spins()
            return
        if clean == "/data/containers.json":
            self.send_json(containers_info())
            return
        if clean == "/health":
            ok, body = health_status()
            self.send_json(body, 200 if ok else 503)
            return
        if clean == "/data/stream":
            self.stream_events()
            return
        m = re.fullmatch(r"/data/days/(\d{4}-\d{2}-\d{2}\.csv)", clean)
        if m:
            self.send_day(os.path.join(DAYS, m.group(1)))
            return
        super().do_GET()

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route not in ("/data/settings.json", "/data/notify-test"):
            self.send_error(404)
            return
        # JSON only: a plain form on another site then cannot save anything
        # (a cross-site JSON request asks for permission first, which we never give)
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self.send_error(415)
            return
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(411)
            return
        if not 0 <= size <= SETTINGS_BODY_MAX:
            self.send_error(413)
            return
        if route == "/data/notify-test":
            self.rfile.read(size)
            self.notify_test()
            return
        try:
            saved = save_settings(json.loads(self.rfile.read(size) or b"null"))
        except ValueError as e:      # also unreadable JSON
            self.send_json({"error": str(e)}, 400)
            return
        except OSError as e:
            log("could not save settings:", repr(e))
            self.send_json({"error": "could not save settings"}, 500)
            return
        self.send_json(saved)

    def notify_test(self):
        """Test message to the saved webhook (never to an address from the request)."""
        url = read_settings().get("webhook")
        if not url:
            self.send_json({"ok": False, "error": "no webhook saved"}, 400)
            return
        msg = "Test from Spin Dashboard: notifications work"
        try:
            status = send_webhook(url, {"event": "test", "epoch": int(time.time()), "text": msg, "content": msg})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)[:200]}, 502)
            return
        self.send_json({"ok": True, "status": status})

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_spins(self):
        body, etag = spins_body()
        gz = "gzip" in self.headers.get("Accept-Encoding", "")
        if gz:
            etag = etag[:-1] + '-gz"'
        if etag in self.headers.get("If-None-Match", ""):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        if gz:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        self.wfile.write(body)

    def send_day(self, path):
        """Day file with gzip (when the browser accepts it) and an ETag, so an unchanged
        day gives a 304 without content."""
        try:
            st = os.stat(path)
        except OSError:
            self.send_error(404)
            return
        gz = "gzip" in self.headers.get("Accept-Encoding", "")
        extra = pending_io(os.path.basename(path)[:-4])         # io lines not written yet (see io_pending)
        tail = f"-{hashlib.sha1(extra[-1].encode()).hexdigest()[:8]}{len(extra):x}" if extra else ""
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}{tail}{"-gz" if gz else ""}"'
        if etag in self.headers.get("If-None-Match", ""):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        try:
            if extra:
                with open(path, "rb") as f:
                    body = f.read()
                if body and not body.endswith(b"\n"):
                    body += b"\n"
                body += "".join(ln + "\n" for ln in extra).encode()
                if gz:
                    body = gzip.compress(body, compresslevel=6, mtime=0)
            else:
                body = day_body(path, st, gz)
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Last-Modified", self.date_time_string(st.st_mtime))
        self.end_headers()
        self.wfile.write(body)

    def stream_events(self):
        """Server-Sent Events: send every new who line to the browser right away,
        so the live feed streams instead of waiting a minute for the CSV."""
        q = queue.Queue(maxsize=500)
        with streams_lock:
            streams.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")   # no proxy buffering
            self.end_headers()
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
            while True:
                try:
                    line = q.get(timeout=30)
                    self.wfile.write(f"data: {line}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")        # keep the connection alive
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with streams_lock:
                if q in streams:
                    streams.remove(q)

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            m = re.fullmatch(r"/data/days/(\d{4}-\d{2}-\d{2}\.csv)", clean)
            return os.path.join(DAYS, m.group(1)) if m else os.path.join(DAYS, "_")
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


def health_status(now=None):
    """(healthy, details) for /health. Unhealthy only when the poller has not succeeded for too long
    or a background thread stopped: a restart fixes that. No fatrace processes or no Docker socket
    are in the details, but do not make it unhealthy; otherwise an auto-heal container restarts it
    forever on a system without HDDs or without a socket."""
    now = time.time() if now is None else now
    since = health["poll"] or health["started"]
    stale = now - since > 3 * POLL_INTERVAL + 60
    alive = {name: t.is_alive() for name, t in threads.items()}
    docker = health["docker"]
    if docker is None and "docker_events" in alive:
        docker = "connecting"
    # docker_events stops on purpose without a socket; only the threads that must always run count
    ok = not stale and all(v for name, v in alive.items() if name != "docker_events")
    return ok, {
        "ok": ok,
        "uptime": int(now - health["started"]),
        "poller": {"last_success": None if health["poll"] is None else int(now - health["poll"]),
                   "error": health["poll_error"]},
        "watcher": {"mounts": health["mounts"], "fatrace": sum(p.poll() is None for p in procs)},
        "docker": docker,
        "threads": alive,
    }


class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        """A client that hangs up (healthcheck, browser navigating away) is not an error:
        no traceback in the log. Other errors are."""
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def web():
    srv = Server(("", PORT), partial(Handler, directory=STATIC))
    log(f"Dashboard on port {PORT}")
    srv.serve_forever()


# =====================================================================================
def fatrace_version():
    """fatrace has no --version; the package version from dpkg, otherwise only whether it is there."""
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "fatrace"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except OSError:
        pass
    return "found" if shutil.which("fatrace") else "not found"


def stop(signum=None, frame=None):
    """docker stop: write the io lines that are still waiting, then exit."""
    try:
        flush_io()
    finally:
        sys.exit(0)


def main():
    os.makedirs(DAYS, exist_ok=True)
    try:
        migrate()
    except Exception as e:
        log("migration failed, old files left in place:", repr(e))
    log(f"Spin Dashboard starting (data: {DATA}, polling every {POLL_INTERVAL}s, "
        f"keeping {RETENTION_DAYS} days, fatrace: {fatrace_version()})")
    relabel_existing()
    devs = all_devices()
    where = data_dir_devices(devs)
    log("Data on " + (", ".join(f"{d} ({'hdd' if devs[d][1] else 'ssd'})" for d in where) if where else
                      "an unknown drive: waiting lines are written while any HDD spins"))
    threading.excepthook = lambda a: log("thread error:", repr(a.exc_value))
    signal.signal(signal.SIGTERM, stop)
    for target in (poller, watcher, housekeeping, docker_events):
        threads[target.__name__] = threading.Thread(target=target, daemon=True)
        threads[target.__name__].start()
    web()


if __name__ == "__main__":
    main()
