import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import spindash  # noqa: E402


@pytest.fixture
def sd(tmp_path, monkeypatch):
    """spindash met alle host-paden en uitvoerbestanden in een tijdelijke map."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(spindash, "DATA", str(data))
    monkeypatch.setattr(spindash, "SPIN", str(data / "spin.csv"))
    monkeypatch.setattr(spindash, "DISKS", str(data / "disks.csv"))
    monkeypatch.setattr(spindash, "WHO", str(data / "who.csv"))
    monkeypatch.setattr(spindash, "EMHTTP", str(tmp_path / "emhttp"))
    monkeypatch.setattr(spindash, "SYS_BLOCK", str(tmp_path / "sys"))
    monkeypatch.setattr(spindash, "PROC", str(tmp_path / "proc"))
    monkeypatch.setattr(spindash, "MOUNTS_FILE", str(tmp_path / "mounts"))
    monkeypatch.setattr(spindash, "USER_SHARES", str(tmp_path / "user"))
    monkeypatch.setattr(spindash, "WATCH_OVERRIDE", [])
    monkeypatch.setattr(spindash, "recent", {})
    monkeypatch.setattr(spindash, "names", {})
    monkeypatch.setattr(spindash, "log", lambda *a: None)
    (tmp_path / "emhttp").mkdir()
    (tmp_path / "sys").mkdir()
    (tmp_path / "proc").mkdir()
    spindash._tmp = tmp_path
    return spindash


def make_disk(tmp_path, dev, rotational=True):
    q = tmp_path / "sys" / dev / "queue"
    q.mkdir(parents=True)
    (q / "rotational").write_text("1\n" if rotational else "0\n")


def make_proc(tmp_path, pid, comm, ppid=1, cgroup="0::/\n"):
    d = tmp_path / "proc" / str(pid)
    d.mkdir(parents=True)
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0 0\n")
    (d / "cgroup").write_text(cgroup)
    (d / "fd").mkdir()
    return d
