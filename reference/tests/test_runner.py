"""Runs the test cases in ../../tests/run with the reference runner.

tests/README.md says what a case holds. The reference runner enforces effects only with the macOS
sandbox, so it skips a case marked macos_only elsewhere.
"""

import json
import os
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from wfspec import load_catalog, load_document
from wfspec.documents import schema_issues
from wfspec.publish import lock
from wfspec.run import Options, Runner, compare_dry_run
from wfspec.runtimes import SANDBOX, Model, home_installation
from wfspec.store import Store

ROOT = Path(__file__).resolve().parents[2]
CASES = sorted(p for p in (ROOT / "tests" / "run").iterdir() if p.is_dir())
# The test programs run python3. When it's installed under a home folder, as on GitHub's macOS
# runners, the sandbox lets programs read it only when granted, as a person would grant it.
PYTHON = [p for p in [home_installation(["python3"], os.environ.get("PATH", ""))] if p]


class FakeModel:
    """An OpenAI-compatible chat completions endpoint that gives scripted replies in order."""

    def __init__(self, replies: list[dict]):
        self.replies, self.requests = list(replies), []
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model.requests.append(body)
                reply = model.replies.pop(0) if model.replies else {"content": ""}
                message = {"role": "assistant", "content": reply.get("content", "")}
                if "tool_calls" in reply:
                    message["tool_calls"] = [
                        {"id": f"call_{i}", "type": "function",
                         "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                        for i, c in enumerate(reply["tool_calls"])
                    ]
                data = json.dumps({"choices": [{"message": message}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def matches(actual, expected) -> bool:
    """Whether a record matches an expectation: every field given must match, recursively; an error
    may be given by its code alone."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(matches(actual.get("error", {}).get("code") if k == "error" and isinstance(v, str) else actual.get(k), v)
                   for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(matches(a, e) for a, e in zip(actual, expected))
    return actual == expected


def optional(case: Path, name: str, default):
    path = case / name
    return load_document(path) if path.exists() else default


def substitute(value, places: dict):
    """Replace {tmp} and {port} in input values; a value that is only {port} becomes a number."""
    if isinstance(value, str):
        if value in places and isinstance(places[value], int):
            return places[value]
        for key, place in places.items():
            value = value.replace(key, str(place))
        return value
    if isinstance(value, dict):
        return {k: substitute(v, places) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, places) for v in value]
    return value


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_run_case(case: Path, tmp_path: Path):
    opts = optional(case, "options.yaml", {})
    if opts.get("macos_only") and not SANDBOX:
        pytest.skip("needs the macOS sandbox")
    if SANDBOX is None and opts.get("sandbox", True):
        opts["sandbox"] = False  # without a sandbox the runner refuses programs that don't write
    dirs = [ROOT / "tests" / "run-catalog"] + ([case / "catalog"] if (case / "catalog").is_dir() else [])
    catalog = load_catalog(dirs)
    assert catalog.problems == []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    (tmp_path / "outside.txt").write_text("data no step is given")
    places = {"{tmp}": str(tmp_path), "{port}": listener.getsockname()[1], "{case}": str(case)}
    workflow = substitute(load_document(case / "workflow.yaml"), places)
    inputs = substitute(optional(case, "inputs.yaml", {}), places)
    replay = optional(case, opts["replay"], None) if "replay" in opts else None
    model = FakeModel(optional(case, "model.yaml", {}).get("replies", []))
    store = Store(tmp_path / "store")
    try:
        options = Options(
            store=store,
            policy=optional(case, "policy.yaml", None),
            secrets=optional(case, "secrets.yaml", {}),
            retries=opts.get("retries", 0),
            sandbox=opts.get("sandbox", True),
            model=Model(url=model.url),
            replay=replay,
            lock=(lock(workflow, catalog) if opts["lock"] == "current" else optional(case, opts["lock"], None)) if "lock" in opts else None,
            readable=PYTHON,
        )
        runs = opts.get("runs", 1)
        for run_inputs in (substitute(runs, places) if isinstance(runs, list) else [inputs] * runs):
            record = Runner(catalog, options).run(workflow, run_inputs)
    finally:
        model.close()
        listener.close()

    expected = load_document(case / "expected.yaml")
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == [], "the run record doesn't fit run.schema.json"
    assert record["status"] == expected["status"], record.get("error") or {s: e.get("error") for s, e in record["steps"].items()}
    if "error" in expected:
        assert record["error"]["code"] == expected["error"], record["error"]
    if "outputs" in expected:
        assert record.get("outputs", {}) == expected["outputs"]
    for sid, want in (expected.get("steps") or {}).items():
        assert matches(record["steps"][sid], want), f"{sid}: {json.dumps(record['steps'][sid])[:600]}"
    if expected.get("steps") == {}:
        assert record["steps"] == {}
    for sid, calls in (expected.get("calls") or {}).items():
        got = record["steps"][sid].get("calls", [])
        want = [{"uses": c} if isinstance(c, str) else c for c in calls]
        assert matches(got, want), f"{sid} calls: {json.dumps(got)[:600]}"
    if "model_saw_tools" in expected:
        assert [t["function"]["name"] for t in model.requests[0]["tools"]] == expected["model_saw_tools"]
    if "dry_run_differences" in expected:
        assert compare_dry_run(replay, record, catalog, workflow) == expected["dry_run_differences"]
    for hidden in expected.get("logs_hide", []):
        for stored in (tmp_path / "store").rglob("*"):
            if stored.is_file():
                assert hidden.encode() not in stored.read_bytes(), f"{hidden} reached {stored.relative_to(tmp_path)}"


def test_a_workflow_file_changed_after_loading_fails_its_step(tmp_path: Path):
    """A workflow step's file that changes after the catalog is loaded, here into text that no
    longer parses, fails that step with pinned_mismatch, and the run still gets its record
    (SPEC.md, [run.pinned], [run.record])."""
    case = ROOT / "tests" / "run" / "workflow-step"
    shutil.copytree(case / "catalog", tmp_path / "catalog")
    catalog = load_catalog([ROOT / "tests" / "run-catalog", tmp_path / "catalog"])
    assert catalog.problems == []
    (tmp_path / "catalog" / "workflows" / "shout.yaml").write_text("steps: [\n")
    store = Store(tmp_path / "store")
    options = Options(store=store, sandbox=SANDBOX is not None, readable=PYTHON)
    record = Runner(catalog, options).run(load_document(case / "workflow.yaml"), {})
    assert record["status"] == "failed"
    assert record["steps"]["shout"]["error"]["code"] == "pinned_mismatch", record["steps"]["shout"]
    assert record["error"]["code"] == "pinned_mismatch"
    assert (tmp_path / "store" / "runs" / f"{record['id']}.json").is_file()


def test_a_write_that_isnt_approved_is_left_out(tmp_path: Path):
    """Given `approve`, the runner asks before a step that writes (SPEC.md, [run.effects]). A refusal
    skips that step and the steps that depend on it, the run still succeeds, and `watch` hears of
    each step as it starts and ends."""
    catalog = load_catalog([ROOT / "tests" / "run-catalog"])
    workflow = {
        "outputs": {"text": {"from": "steps.shout.outputs.text"}, "saved": {"from": "steps.after.outputs.text"}},
        "steps": {
            "shout": {"uses": "test/upper@1.0.0", "with": {"text": "hi"}},
            "save": {"uses": "test/save-text@1.0.0", "with": {"text": {"from": "steps.shout.outputs.text"}, "folder": str(tmp_path), "name": "saved.txt"}},
            "after": {"uses": "test/echo@1.0.0", "with": {"text": {"from": "steps.save.outputs.path"}}},
        },
    }
    asked, heard = [], []

    def run(answer: bool) -> dict:
        options = Options(store=Store(tmp_path / "store"), sandbox=SANDBOX is not None, readable=PYTHON,
                          approve=lambda ref, inputs: asked.append((ref, inputs["text"])) or answer,
                          watch=lambda sid, entry: heard.append((sid, entry["status"])))
        return Runner(catalog, options).run(workflow, {})

    record = run(False)
    assert asked == [("test/save-text@1.0.0", "HI")]  # only the step that writes is asked about
    assert record["status"] == "succeeded" and record["outputs"] == {"text": "HI"}
    assert record["steps"]["save"]["status"] == "skipped" and record["steps"]["save"]["reason"] == "not approved"
    assert record["steps"]["after"]["status"] == "skipped"
    assert not (tmp_path / "saved.txt").exists()
    assert heard == [("shout", "running"), ("shout", "succeeded"), ("save", "running"), ("save", "skipped"), ("after", "skipped")]
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == []

    record = run(True)
    assert record["steps"]["save"]["status"] == "succeeded" and (tmp_path / "saved.txt").read_text() == "HI"


# ---- a runner that another program drives: an id of its choice, the record so far, cancelling ----

SLOW = """\
name: test/slow
version: 1.0.0
description: Say it has started, then wait for a minute.
inputSchema: { type: object, properties: { started: { type: string } }, required: [started] }
outputSchema: { type: object, properties: { text: { type: string } }, required: [text] }
effects: writes
runtime: { process: { command: [python3, slow.py] } }
"""
SLOW_PROGRAM = """\
import json, os, time
from pathlib import Path
Path(json.loads(Path(os.environ["WF_INPUTS"]).read_text())["started"]).write_text(str(os.getpid()))
time.sleep(60)
"""


def test_the_record_is_saved_as_the_run_goes(tmp_path: Path):
    """The store holds the run's record from its start, under the id the caller chose, and each
    step's record changes there as the step starts and ends. Every state fits run.schema.json."""
    catalog = load_catalog([ROOT / "tests" / "run-catalog"])
    workflow = {
        "outputs": {"text": {"from": "steps.shout.outputs.text"}},
        "steps": {
            "first": {"uses": "test/echo@1.0.0", "with": {"text": "hi"}},
            "shout": {"uses": "test/upper@1.0.0", "with": {"text": {"from": "steps.first.outputs.text"}}},
            "spare": {"uses": "test/echo@1.0.0", "with": {"text": "nobody reads this"}},
        },
    }
    saved = tmp_path / "store" / "runs" / "run_mine.json"
    seen = []

    def watch(sid: str, entry: dict) -> None:
        record = json.loads(saved.read_text())
        assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == []
        seen.append((sid, entry["status"], record["status"], {s: e["status"] for s, e in record["steps"].items()}))

    options = Options(store=Store(tmp_path / "store"), sandbox=SANDBOX is not None, readable=PYTHON, watch=watch)
    record = Runner(catalog, options).run(workflow, {}, run_id="run_mine")
    assert record["id"] == "run_mine" and record["status"] == "succeeded"
    assert seen == [
        ("first", "running", "running", {"first": "running", "shout": "pending", "spare": "not_needed"}),
        ("first", "succeeded", "running", {"first": "succeeded", "shout": "pending", "spare": "not_needed"}),
        ("shout", "running", "running", {"first": "succeeded", "shout": "running", "spare": "not_needed"}),
        ("shout", "succeeded", "running", {"first": "succeeded", "shout": "succeeded", "spare": "not_needed"}),
    ]
    assert json.loads(saved.read_text()) == record


def slow_catalog(tmp_path: Path):
    folder = tmp_path / "slow"
    folder.mkdir()
    (folder / "slow.yaml").write_text(SLOW)
    (folder / "slow.py").write_text(SLOW_PROGRAM)
    catalog = load_catalog([ROOT / "tests" / "run-catalog", folder])
    assert catalog.problems == []
    return catalog


SLOW_WORKFLOW = {
    "outputs": {"first": {"from": "steps.first.outputs.text"}, "last": {"from": "steps.after.outputs.text"}},
    "steps": {
        "first": {"uses": "test/echo@1.0.0", "with": {"text": "hi"}},
        "wait": {"uses": "test/slow@1.0.0", "with": {"started": "{started}"}},
        "after": {"uses": "test/echo@1.0.0", "with": {"text": {"from": "steps.wait.outputs.text"}}},
    },
}


def test_cancelling_while_a_step_runs(tmp_path: Path):
    """RunCancelled, raised here by a signal as a service would, ends the program that is running.
    That step fails with the runner's own code x-cancelled, the steps after it are skipped, what
    finished stays in the record, and the run's status is `cancelled`."""
    import signal
    import time

    from wfspec.run import RunCancelled

    catalog = slow_catalog(tmp_path)
    started = tmp_path / "started.txt"

    def cancel_once_started():
        while not started.exists() or not started.read_text():
            time.sleep(0.02)
        os.kill(os.getpid(), signal.SIGUSR1)

    def cancel(*_):
        raise RunCancelled

    before = signal.signal(signal.SIGUSR1, cancel)
    threading.Thread(target=cancel_once_started, daemon=True).start()
    try:
        options = Options(store=Store(tmp_path / "store"), sandbox=SANDBOX is not None, readable=PYTHON)
        record = Runner(catalog, options).run(substitute(SLOW_WORKFLOW, {"{started}": str(started)}), {})
    finally:
        signal.signal(signal.SIGUSR1, before)
    assert record["status"] == "cancelled" and "error" not in record
    assert record["outputs"] == {"first": "hi"}
    assert record["steps"]["first"]["status"] == "succeeded"
    assert record["steps"]["wait"]["status"] == "failed" and record["steps"]["wait"]["error"]["code"] == "x-cancelled"
    assert record["steps"]["after"] == {"uses": "test/echo@1.0.0", "status": "skipped", "reason": "the run was cancelled"}
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == []
    with pytest.raises(ProcessLookupError):  # the program was ended with its step
        os.kill(int(started.read_text()), 0)


@pytest.mark.parametrize("when", ["asked", "between steps"])
def test_cancelling_before_a_step_starts(tmp_path: Path, when: str):
    """A run cancelled while it waits for an answer about a step that writes, or between two steps,
    records that step as skipped, or as cut short, and never starts its program."""
    from wfspec.run import RunCancelled

    catalog = slow_catalog(tmp_path)
    started = tmp_path / "started.txt"

    def cancel(*_):
        raise RunCancelled

    options = Options(store=Store(tmp_path / "store"), sandbox=SANDBOX is not None, readable=PYTHON,
                      approve=cancel if when == "asked" else None,
                      watch=(lambda sid, entry: cancel() if (sid, entry["status"]) == ("wait", "running") else None) if when != "asked" else None)
    record = Runner(catalog, options).run(substitute(SLOW_WORKFLOW, {"{started}": str(started)}), {})
    assert record["status"] == "cancelled" and not started.exists()
    assert record["steps"]["first"]["status"] == "succeeded" and record["outputs"] == {"first": "hi"}
    if when == "asked":
        assert record["steps"]["wait"]["status"] == "skipped" and record["steps"]["wait"]["reason"] == "the run was cancelled"
    else:
        assert record["steps"]["wait"]["error"]["code"] == "x-cancelled"
    assert record["steps"]["after"]["status"] == "skipped"
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == []
    assert json.loads((tmp_path / "store" / "runs" / f"{record['id']}.json").read_text()) == record


def test_a_catalog_can_name_single_manifests(tmp_path: Path):
    """load_catalog reads a path that is a file as one manifest, so a registry that keeps each
    step version in its own folder can load them without reading the other files there."""
    catalog = load_catalog([ROOT / "tests" / "run-catalog" / "echo.yaml", ROOT / "tests" / "run-catalog" / "upper.yaml"])
    assert catalog.problems == [] and sorted(catalog.steps) == ["test/echo@1.0.0", "test/upper@1.0.0"]
