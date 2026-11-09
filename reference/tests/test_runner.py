"""Runs the test cases in ../../tests/run with the reference runner.

tests/run/<case>/   workflow.yaml and expected.yaml, plus any of:
  inputs.yaml       the workflow's inputs
  secrets.yaml      the caller's secrets, by name
  options.yaml      runs (a count, or one inputs object per run), retries, sandbox, macos_only,
                    allow_read (folders programs may read), lock (a file, or "current"), replay
  model.yaml        scripted replies for agent steps, served as an OpenAI-compatible API
  catalog/          steps only this case uses; tests/run-catalog/ holds the shared ones

expected.yaml says what the last run must give: status, error (the run's error code), outputs,
and for chosen steps the fields of their records, such as status, error code, cacheHit, attempts,
reason or outputs. It can also give calls (step id to the records of the calls it made),
model_saw_tools, dry_run_differences, and logs_hide (values nothing in the store may contain).
Records match when every field the expectation gives matches; other fields are free. Placeholders
in inputs and options: {tmp} (a scratch folder), {port} (a listening port), {case} (the case folder).
Every run record must fit schemas/run.schema.json.
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
            readable=[Path(p) for p in substitute(opts.get("allow_read", []), places)] + PYTHON,
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
