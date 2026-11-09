"""How the reference runner keeps a program step to itself (drafts/runtime.md, section 1): what it
leaves running, the files the runner reads after it, other processes, and the runner's own
environment. These need processes the test cases in tests/run can't describe."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from wfspec import load_catalog
from wfspec.run import Options, Runner
from wfspec.runtimes import SANDBOX, home_installation, read_inside
from wfspec.store import Store

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "tests" / "run-catalog"
PYTHON = [p for p in [home_installation(["python3"], os.environ.get("PATH", ""))] if p]  # see test_runner.py


def run(workflow: dict, tmp_path: Path, readable: tuple[Path, ...] = ()) -> dict:
    options = Options(store=Store(tmp_path / "store"), sandbox=SANDBOX is not None, readable=[*readable, *PYTHON])
    return Runner(load_catalog([CATALOG]), options).run(workflow, {})


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_read_inside_follows_no_links(tmp_path):
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    (work / "sub" / "real.txt").write_text("mine")
    (tmp_path / "outside.txt").write_text("not mine")
    (work / "link.txt").symlink_to(tmp_path / "outside.txt")
    (work / "inner-link.txt").symlink_to(work / "sub" / "real.txt")
    (work / "folder-link").symlink_to(tmp_path)
    os.mkfifo(work / "pipe")
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert read_inside(fd, "sub/real.txt") == b"mine"
        assert read_inside(fd, "missing.txt") is None
        for path in ("link.txt", "inner-link.txt", "folder-link/outside.txt", "pipe", "../outside.txt", "/etc/hosts", ""):
            with pytest.raises(ValueError):
                read_inside(fd, path)
    finally:
        os.close(fd)


def test_a_step_leaves_no_process_running(tmp_path):
    record = run({"steps": {"left": {"uses": "test/leave-child@1.0.0"}},
                  "outputs": {"pid": {"from": "steps.left.outputs.pid"}}}, tmp_path)
    assert record["status"] == "succeeded", record
    pid = record["outputs"]["pid"]
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid), "a process the step started is still running after it"


@pytest.mark.skipif(SANDBOX is None, reason="needs the macOS sandbox")
def test_a_sandboxed_step_cant_signal_other_processes(tmp_path):
    other = subprocess.Popen(["/bin/sleep", "30"])
    try:
        record = run({"steps": {"sent": {"uses": "test/signal@1.0.0", "with": {"pid": other.pid}}},
                      "outputs": {"sent": {"from": "steps.sent.outputs.sent"}}}, tmp_path)
        assert record["outputs"] == {"sent": False}, record
    finally:
        other.kill()
        other.wait()


def test_the_runner_keeps_secrets_out_of_its_environment(tmp_path):
    """Any program of the same user can read the environment another program started with, and the
    sandbox can't stop it, so the runner mustn't hold secrets there (SPEC.md, [run.secrets-named])."""
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(json.dumps({"steps": {"looked": {"uses": "test/parent-env@1.0.0", "with": {"name": "TOKEN_A"}}},
                                    "outputs": {"seen": {"from": "steps.looked.outputs.seen"}}}))
    command = [sys.executable, "-m", "wfspec.cli", "run", str(workflow), "--catalog", str(CATALOG),
               "--secret", "TOKEN_A", "--store", str(tmp_path / "store"), "--json"]
    command += [arg for p in PYTHON for arg in ("--allow-read", str(p))] + ([] if SANDBOX else ["--no-sandbox"])
    done = subprocess.run(command, capture_output=True, text=True,
                          env={**os.environ, "TOKEN_A": "alpha-secret-1"})
    record = json.loads(done.stdout)
    assert record["outputs"] == {"seen": False}, done.stdout + done.stderr
    assert "alpha-secret-1" not in done.stdout + done.stderr


@pytest.mark.skipif(SANDBOX is None, reason="needs the macOS sandbox")
def test_programs_read_the_home_folder_only_where_granted(tmp_path):
    """The reference runner's own policy, beyond SPEC.md: a program that doesn't write reads nothing
    under the home folder but what the caller grants with --allow-read."""
    home = Path(tempfile.mkdtemp(prefix=".wfspec-test-", dir=Path.home()))
    try:
        (home / "private.txt").write_text("private")
        workflow = {"steps": {"read": {"uses": "test/try-read-reads@1.0.0", "with": {"path": str(home / "private.txt")}}},
                    "outputs": {"read": {"from": "steps.read.outputs.read"}}}
        assert run(workflow, tmp_path / "ungranted")["outputs"] == {"read": False}
        assert run(workflow, tmp_path / "granted", readable=(home,))["outputs"] == {"read": True}
    finally:
        shutil.rmtree(home)
