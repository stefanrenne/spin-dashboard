#!/usr/bin/env python3
"""Spin Dashboard voor Unraid.

Eén proces met drie onderdelen:

  poller   peilt elke POLL_INTERVAL seconden de status van alle HDD's (hdparm -C, met
           smartctl als terugval) en schrijft alleen wijzigingen naar spin.csv.
           De namen (parity, disk1, pool, pool2) komen uit Unraid's disks.ini.
  watcher  draait per HDD-mount een `fatrace -c` (fanotify) en legt vast welk proces
           welk bestand opent: een container, Unraid zelf of een gebruiker (SMB/shell).
  web      serveert het dashboard en de CSV's.

Alles staat in DATA_DIR/days: één CSV per dag (YYYY-MM-DD.csv, lokale datum) met regels
`epoch,soort,...` (disk, state, spin, who, activity). Oudere losse bestanden (spin.csv, disks.csv,
who.csv, activity.csv) worden bij het starten eenmalig naar dagbestanden gemigreerd.
"""
import http.client
import http.server
import json
import os
import queue
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from functools import partial

# ---------- instellingen ----------
PORT = int(os.environ.get("PORT", "8089"))
DATA = os.environ.get("DATA_DIR", "/data")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
EMHTTP = os.environ.get("EMHTTP_DIR", "/emhttp")          # /var/local/emhttp van de host
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DEDUP_S = int(os.environ.get("DEDUP_SECONDS", "600"))   # zelfde pad + bron maar 1x per 10 min
WATCH_OVERRIDE = os.environ.get("WATCH", "").split()   # optioneel: vaste lijst mounts
# processen (en hun kinderen) die niet gelogd worden; cache_dirs opent continu alle mappen
IGNORE_PROCS = set(os.environ.get("IGNORE_PROCS", "cache_dirs").split())
SHFS_DELAY = 0.4
PID_TTL = 30          # uitkomst van classify() per PID zo lang onthouden
IGNORE_TTL = 300      # zo lang na een genegeerd proces dezelfde naam zonder bron ook negeren

# Paden naar het hostsysteem; in tests te vervangen
PROC = "/proc"
SYS_BLOCK = "/sys/block"
MOUNTS_FILE = "/proc/self/mounts"
USER_SHARES = "/mnt/user"
DOCKER_SOCK = "/var/run/docker.sock"

DAYS = os.path.join(DATA, "days")
LEGACY = os.path.join(DATA, "legacy")
# oude losse bestanden; alleen nog gelezen door migrate()
SPIN = os.path.join(DATA, "spin.csv")
DISKS = os.path.join(DATA, "disks.csv")
WHO = os.path.join(DATA, "who.csv")
ACTIVITY = os.path.join(DATA, "activity.csv")
OWN_FILES = {"spin.csv", "disks.csv", "who.csv", "activity.csv"}
DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv(?:\.tmp)?$")
TYPES = ("disk", "state", "spin", "who", "activity")     # volgorde binnen dezelfde seconde

write_lock = threading.Lock()


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


# =====================================================================================
# Dagbestanden
# =====================================================================================
def day_of(ts):
    return time.strftime("%Y-%m-%d", time.localtime(float(ts)))


def day_path(day):
    return os.path.join(DAYS, f"{day}.csv")


def day_start(day):
    return int(time.mktime(time.strptime(day, "%Y-%m-%d")))


def append_lines(lines):
    """Regels `epoch,soort,...` toevoegen aan het dagbestand van hun eigen tijdstip."""
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


def is_own_file(path):
    name = os.path.basename(path.rstrip("/"))
    return name in OWN_FILES or bool(DAY_FILE.match(name))


# =====================================================================================
# Schijven en mounts
# =====================================================================================
def read_disks_ini():
    """[(device, naam)] uit disks.ini; flash en lege slots overgeslagen."""
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


def is_rotational(dev):
    try:
        return open(f"{SYS_BLOCK}/{dev}/queue/rotational").read().strip() == "1"
    except OSError:
        return False


def mount_base(name):
    """disk1 -> disk1, trunk2 -> trunk (poolleden), parity -> None."""
    if name.startswith("parity"):
        return None
    return name if re.fullmatch(r"disk\d+", name) else re.sub(r"\d+$", "", name)


def hdd_devices():
    """{device: naam} voor alle draaiende schijven. Zonder disks.ini: alle sdX met rotational=1."""
    mapped = {d: n for d, n in read_disks_ini() if is_rotational(d)}
    if mapped:
        return mapped
    try:
        return {d: d for d in os.listdir(SYS_BLOCK) if d.startswith("sd") and is_rotational(d)}
    except OSError:
        return {}


def mounted():
    out = []
    for line in open(MOUNTS_FILE):
        out.append(line.split()[1].replace("\\040", " "))
    return out


def target_mounts():
    """Mounts van HDD's onder /mnt, inclusief ZFS-datasets daaronder."""
    if WATCH_OVERRIDE:
        roots = [r.rstrip("/") for r in WATCH_OVERRIDE]
    else:
        roots = sorted({f"/mnt/{b}" for b in map(mount_base, hdd_devices().values()) if b})
    mps = mounted()
    return sorted({mp for mp in mps for r in roots if mp == r or mp.startswith(r + "/")})


# =====================================================================================
# Poller: spin-status
# =====================================================================================
def parse_hdparm(out):
    """Uitvoer van `hdparm -C` -> 'active', 'standby' of None."""
    m = re.search(r"drive state is:\s+(\S+)", out)
    if not m:
        return None
    s = m.group(1)
    if s in ("standby", "sleeping"):
        return "standby"
    if s.startswith("active") or s.startswith("idle"):
        return "active"
    return None


def drive_state(dev):
    """'active', 'standby' of None. hdparm maakt de schijf niet wakker; smartctl -n standby ook niet."""
    try:
        out = subprocess.run(["hdparm", "-C", f"/dev/{dev}"], capture_output=True, text=True, timeout=15).stdout
        st = parse_hdparm(out)
        if st:
            return st
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:   # SAS en controllers waar hdparm niets zegt
        r = subprocess.run(["smartctl", "-n", "standby", "-i", f"/dev/{dev}"],
                           capture_output=True, text=True, timeout=15)
        if "STANDBY" in r.stdout.upper():
            return "standby"
        if r.returncode == 0:
            return "active"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def poll_once(last, now=None):
    """Eén peiling. Schrijft naar het dagbestand:
    - `disk`-regels bij de eerste peiling van een dag of als de schijfnamen veranderen;
    - `spin` bij een statuswijziging;
    - `state` met de huidige status bij de eerste peiling van een dag of na het starten,
      zodat elk dagbestand op zichzelf leesbaar is. Een `state` is geen spin-up.
    `last` houdt de vorige status bij. Geeft het aantal spin- en state-regels terug."""
    now = int(time.time() if now is None else now)
    day, devs = day_of(now), hdd_devices()
    known = last.setdefault("st", {})
    new_day = last.get("day") != day
    lines = []
    if new_day or devs != last.get("disks"):
        lines += [f"{now},disk,{d},{n}" for d, n in sorted(devs.items())]
    n = 0
    for dev in sorted(devs):
        st = drive_state(dev)
        if not st:
            continue
        if dev in known and st != known[dev]:
            lines.append(f"{now},spin,{dev},{st}")
            n += 1
        elif dev not in known or new_day:
            lines.append(f"{now},state,{dev},{st}")
            n += 1
        known[dev] = st
    last["day"], last["disks"] = day, dict(devs)
    if lines:
        append_lines(lines)
    if not devs:
        log("Geen HDD's gevonden. Is /var/local/emhttp gemount en draait de container privileged?")
    return n


def poller():
    last = {}
    while True:
        try:
            poll_once(last)
        except Exception as e:
            log("poller-fout:", repr(e))
        time.sleep(POLL_INTERVAL)


# =====================================================================================
# Watcher: wie opent wat (fatrace)
# =====================================================================================
LINE = re.compile(r"^(\d+)\.\d+ (.*)\((\d+)\): ([RWOCD+<>]+)\s+(/.*?)(?: exe=.*)?$")
OPS = [("D", "verwijderd"), ("+", "aangemaakt"), ("<", "verplaatst"), (">", "verplaatst"), ("O", "geopend")]
SHELLS = {"sshd": "ssh", "sshd-session": "ssh", "ttyd": "webterminal", "login": "console",
          "agetty": "console", "tmux: server": "tmux", "screen": "screen"}

events = queue.Queue(maxsize=20_000)
procs = []
recent = {}
names = {}
seen = {}             # pid -> (tijd, comm, uitkomst van classify)
ignored_comms = {}    # comm -> laatste keer dat een proces met die naam genegeerd werd
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


def is_ignored(pid, limit=15):
    """Hoort dit proces (of een voorouder) bij IGNORE_PROCS? Kijkt naar de procesnaam en naar de
    opdrachtregel, voor scripts die als `bash /pad/naar/cache_dirs` gestart zijn."""
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
    # met een eigen cgroup-namespace ziet dit eruit als "0::/../<id>", zonder "docker/"
    m = re.search(r"(?:^|[/-])([0-9a-f]{64})(?:\.scope)?\s*$", cg, re.M)
    return m.group(1) if m else None


def container_name(cid):
    if cid in names:
        return names[cid]
    name = cid[:12]
    try:
        c = http.client.HTTPConnection("localhost", timeout=2)
        c.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.sock.settimeout(2)
        c.sock.connect(DOCKER_SOCK)
        c.request("GET", f"/containers/{cid}/json")
        r = c.getresponse()
        if r.status == 200:
            name = json.loads(r.read()).get("Name", "").lstrip("/") or name
    except Exception:
        pass
    names[cid] = name
    return name


def classify(pid, comm, now=None):
    """Als classify_now(), maar onthoudt de uitkomst per PID. Een kortlevend proces (find van
    cache_dirs) is vaak al gestopt voordat al zijn events verwerkt zijn; de latere events krijgen
    zo dezelfde uitkomst als het eerste, toen het proces nog draaide.

    Vangnet: is kort geleden een proces met dezelfde naam genegeerd, dan wordt een proces met die
    naam zonder herkenbare bron (gestopt, of verweesd zodat de keten niet meer te volgen is) ook
    genegeerd. Een find vanuit een shell, SMB of cron heeft wel een bron en wordt gewoon gelogd."""
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
    """(soort, naam) van een proces, of None als het genegeerd wordt (IGNORE_PROCS)."""
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


def via_user_share(path):
    """Wie heeft /mnt/pool/Share/x via /mnt/user/Share/x open? Vergelijkt inodes."""
    parts = path.split("/")
    if len(parts) < 5:
        return None
    try:
        want = os.stat(f"{USER_SHARES}/" + "/".join(parts[3:]))
    except OSError:
        return None
    key, tail = (want.st_dev, want.st_ino), "/" + parts[-1]
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
                if not os.readlink(f"{fddir}/{fd}").endswith(tail):
                    continue
                st = os.stat(f"{fddir}/{fd}")
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
    """/mnt/pool/Share/a/b.mkv -> /mnt/pool/Share/ (ongewijzigd als er geen share in zit)."""
    parts = path.split("/")
    return "/".join(parts[:4]) + "/" if len(parts) > 4 else path


def write_who(ts, kind, name, pid, op, path, now=None):
    if kind == "gebruiker" and name in ("SMB-share", "NFS-share"):
        path = share_root(path)              # één regel per share
    now = time.time() if now is None else now
    k = (path, kind, name)
    with write_lock:
        if now - recent.get(k, 0) < DEDUP_S:
            return
        recent[k] = now
        if len(recent) > 50_000:
            for kk in [kk for kk, t in recent.items() if now - t > DEDUP_S]:
                del recent[kk]
    append_lines([f"{ts},who,{kind},{name.replace(',', ' ')},{pid},{op},{path}"])
    stats["written"] += 1


def resolve_shfs_later(ts, pid, op, path):
    def run():
        time.sleep(SHFS_DELAY)
        hit = via_user_share(path)
        if hit:
            src = classify(*hit)
            if src:
                write_who(ts, *src, hit[0], op, path)
        else:
            write_who(ts, "unraid", "shfs (via /mnt/user)", pid, op, path)
    threading.Thread(target=run, daemon=True).start()


def classify_early(line):
    """Direct bij binnenkomst (in de leesthread) de bron van een nieuwe PID bepalen, zolang het
    proces nog draait. De consumer kan achterlopen; classify() geeft dan de onthouden uitkomst."""
    m = LINE.match(line)
    if m and m.group(3) not in seen and m.group(3) != MY_PID:
        classify(m.group(3), m.group(2))


def handle(line):
    m = LINE.match(line.rstrip("\n"))
    if not m:
        return
    ts, comm, pid, types, path = m.groups()
    if pid == MY_PID or is_own_file(path):
        return                               # eigen schrijfacties niet loggen
    if not path.endswith("/") and "D" not in types and os.path.isdir(path):
        path += "/"
    op = next((label for c, label in OPS if c in types), None)
    if not op:
        return
    if comm == "shfs" and op == "geopend" and not path.endswith("/"):
        resolve_shfs_later(ts, pid, op, path)
        return
    src = classify(pid, comm)
    if src:
        write_who(ts, *src, pid, op, path)


def consumer():
    while True:
        try:
            handle(events.get())
        except Exception as e:
            log("fout bij event:", repr(e))


def start_fatrace(mp):
    try:
        p = subprocess.Popen(["fatrace", "-c", "-tt", "-f", "O+D<"], cwd=mp,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, errors="replace", bufsize=1)
    except OSError as e:
        log("fatrace start mislukt voor", mp, e)
        return
    procs.append(p)

    def pump_out():
        for line in p.stdout:
            try:
                classify_early(line)
            except Exception as e:
                log("fout bij vroege classificatie:", repr(e))
            try:
                events.put_nowait(line)
            except queue.Full:
                stats["dropped"] += 1
        log("fatrace gestopt voor", mp, "exitcode", p.wait())

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
                    log("Bewaakt:", " ".join(mounts))
                    for mp in mounts:
                        start_fatrace(mp)
                else:
                    log("Geen HDD-mounts onder /mnt gevonden; nieuwe poging over een minuut")
                current = mounts
        except Exception as e:
            log("watcher-fout:", repr(e))
        beat += 1
        if beat % 10 == 0 and current:
            log(f"actief: {len(procs)} fatrace, {stats['written']} regels, "
                f"wachtrij {events.qsize()}, gemist {stats['dropped']}")
        time.sleep(60)


# =====================================================================================
# Onderhoud: oude regels opruimen, oude labels herstellen
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
    """Dagbestanden ouder dan RETENTION_DAYS verwijderen (hele dagen)."""
    cutoff = day_of((time.time() if now is None else now) - RETENTION_DAYS * 86400)
    old = [d for d in list_days() if d < cutoff]
    with write_lock:
        for d in old:
            try:
                os.remove(day_path(d))
            except OSError:
                pass
    if old:
        log(f"{len(old)} dagbestand(en) ouder dan {RETENTION_DAYS} dagen verwijderd")


def relabel_existing(now=None):
    """who-regels met 'unraid'/'onbekend' van vandaag en gisteren alsnog aan een container
    koppelen als het proces nog draait."""
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
                log(f"{day}: {changed} oudere regels alsnog aan een container gekoppeld")


def housekeeping():
    while True:
        try:
            prune_days()
        except Exception as e:
            log("opruimen mislukt:", repr(e))
        time.sleep(6 * 3600)


# =====================================================================================
# Migratie van de losse bestanden (spin.csv, disks.csv, who.csv, activity.csv)
# =====================================================================================
def _legacy_records():
    """(epoch, regel) in het nieuwe formaat uit de oude bestanden, de statuswijzigingen
    [(epoch, device, status)] en de schijfnamen."""
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
    for line in _read(WHO):                  # epoch,soort,naam,pid,actie,pad
        p = line.split(",", 5)
        if len(p) == 6 and p[0].isdigit() and p[5].startswith("/"):
            recs.append((int(p[0]), f"{p[0]},who,{line.split(',', 1)[1]}"))
        else:
            skipped += 1
    for line in _read(ACTIVITY):             # epoch,disk,EV,pad
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
    """Losse bestanden eenmalig omzetten naar dagbestanden. Veilig om te herhalen: bestaande
    dagregels blijven staan en dubbele regels worden samengevoegd. De oude bestanden gaan naar
    DATA_DIR/legacy, zodat er niets verloren gaat."""
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
            for ts, dev, st in spins:        # status van elke schijf bij het begin van de dag
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
    log(f"Migratie: {len(recs)} regels uit {', '.join(os.path.basename(f) for f in sources)} "
        f"verdeeld over {len(by_day)} dagbestanden; oude bestanden staan in {LEGACY}"
        + (f"; {skipped} onleesbare regels overgeslagen" if skipped else ""))


# =====================================================================================
# Web
# =====================================================================================
class Handler(http.server.SimpleHTTPRequestHandler):
    """/ → dashboard, /data/days.json → lijst van dagen, /data/days/YYYY-MM-DD.csv → dagbestand."""
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/data/days.json":
            body = json.dumps({"days": list_days()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

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


def web():
    srv = http.server.ThreadingHTTPServer(("", PORT), partial(Handler, directory=STATIC))
    log(f"Dashboard op poort {PORT}")
    srv.serve_forever()


# =====================================================================================
def main():
    os.makedirs(DAYS, exist_ok=True)
    try:
        migrate()
    except Exception as e:
        log("migratie mislukt, oude bestanden blijven staan:", repr(e))
    try:
        ver = subprocess.run(["fatrace", "--help"], capture_output=True, text=True).stdout.split("\n")[0]
    except OSError:
        ver = "niet gevonden"
    log(f"Spin Dashboard start (data: {DATA}, peiling elke {POLL_INTERVAL}s, bewaren {RETENTION_DAYS} dagen, fatrace: {ver})")
    relabel_existing()
    threading.excepthook = lambda a: log("thread-fout:", repr(a.exc_value))
    for target in (poller, watcher, housekeeping):
        threading.Thread(target=target, daemon=True).start()
    web()


if __name__ == "__main__":
    main()
