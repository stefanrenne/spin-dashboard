#!/usr/bin/env python3
"""Spin Dashboard voor Unraid.

Eén proces met drie onderdelen:

  poller   peilt elke POLL_INTERVAL seconden de status van alle HDD's (hdparm -C, met
           smartctl als terugval) en schrijft alleen wijzigingen naar spin.csv.
           De namen (parity, disk1, pool, pool2) komen uit Unraid's disks.ini.
  watcher  draait per HDD-mount een `fatrace -c` (fanotify) en legt vast welk proces
           welk bestand opent: een container, Unraid zelf of een gebruiker (SMB/shell).
  web      serveert het dashboard en de CSV's.

Alles staat in DATA_DIR: spin.csv, disks.csv en who.csv.
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

# Paden naar het hostsysteem; in tests te vervangen
PROC = "/proc"
SYS_BLOCK = "/sys/block"
MOUNTS_FILE = "/proc/self/mounts"
USER_SHARES = "/mnt/user"
DOCKER_SOCK = "/var/run/docker.sock"

SPIN = os.path.join(DATA, "spin.csv")
DISKS = os.path.join(DATA, "disks.csv")
WHO = os.path.join(DATA, "who.csv")
OWN_FILES = {"spin.csv", "disks.csv", "who.csv", "who.csv.tmp", "spin.csv.tmp"}

write_lock = threading.Lock()


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


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


def write_if_changed(path, content):
    try:
        if open(path).read() == content:
            return
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def poll_once(last):
    """Eén peiling: disks.csv bijwerken en statuswijzigingen aan spin.csv toevoegen.
    `last` ({device: status}) wordt bijgewerkt. Geeft het aantal geschreven regels terug."""
    devs = hdd_devices()
    write_if_changed(DISKS, "".join(f"{d},{n}\n" for d, n in sorted(devs.items())))
    lines = []
    for dev in sorted(devs):
        st = drive_state(dev)
        if st and st != last.get(dev):
            lines.append(f"{datetime.now().astimezone().isoformat(timespec='seconds')},/dev/{dev},{st}\n")
            last[dev] = st
    if lines:
        with write_lock, open(SPIN, "a") as f:
            f.writelines(lines)
    if not devs:
        log("Geen HDD's gevonden. Is /var/local/emhttp gemount en draait de container privileged?")
    return len(lines)


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


def classify(pid, comm):
    """(soort, naam) van een proces, of None als het genegeerd wordt (IGNORE_PROCS)."""
    cid = container_id(pid)
    if cid:
        return "container", container_name(cid)
    if not os.path.exists(f"{PROC}/{pid}"):
        return "onbekend", f"{comm} (al gestopt)"
    chain = [comm] + (chain_of(pid) or [comm])[1:]
    if IGNORE_PROCS.intersection(chain):
        return None
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
        with open(WHO, "a") as f:
            f.write(f"{ts},{kind},{name.replace(',', ' ')},{pid},{op},{path}\n")
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


def handle(line):
    m = LINE.match(line.rstrip("\n"))
    if not m:
        return
    ts, comm, pid, types, path = m.groups()
    if pid == MY_PID or os.path.basename(path.rstrip("/")) in OWN_FILES:
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


def trim(path, now=None):
    cutoff = (time.time() if now is None else now) - RETENTION_DAYS * 86400
    with write_lock:
        try:
            lines = open(path).readlines()
        except OSError:
            return
        keep = [l for l in lines if (epoch_of(l) or cutoff) >= cutoff]
        if len(keep) != len(lines):
            tmp = path + ".tmp"
            open(tmp, "w").writelines(keep)
            os.replace(tmp, path)
            log(f"{os.path.basename(path)}: {len(lines) - len(keep)} regels ouder dan {RETENTION_DAYS} dagen verwijderd")


def relabel_existing():
    """Regels met 'unraid'/'onbekend' alsnog aan een container koppelen als het proces nog draait."""
    with write_lock:
        try:
            lines = open(WHO).readlines()
        except OSError:
            return
        changed = 0
        for i, line in enumerate(lines):
            p = line.rstrip("\n").split(",", 5)
            if len(p) < 6 or p[1] not in ("unraid", "onbekend"):
                continue
            try:
                comm, _ = proc_comm_ppid(p[3])
            except (OSError, ValueError, IndexError):
                continue
            cid = container_id(p[3])
            if not cid or comm != p[2].replace(" (al gestopt)", ""):
                continue
            p[1], p[2] = "container", container_name(cid).replace(",", " ")
            lines[i] = ",".join(p) + "\n"
            changed += 1
        if changed:
            open(WHO + ".tmp", "w").writelines(lines)
            os.replace(WHO + ".tmp", WHO)
            log(f"{changed} oudere regels alsnog aan een container gekoppeld")


def housekeeping():
    while True:
        for f in (SPIN, WHO):
            try:
                trim(f)
            except Exception as e:
                log("opruimen mislukt:", repr(e))
        time.sleep(6 * 3600)


# =====================================================================================
# Web
# =====================================================================================
class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            name = os.path.basename(clean)
            return os.path.join(DATA, name) if name.endswith(".csv") else os.path.join(DATA, "_")
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
    os.makedirs(DATA, exist_ok=True)
    for f in (SPIN, WHO):
        open(f, "a").close()
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
