"""Runs the test cases in ../../tests/run with the reference runner.

tests/run/<case>/   workflow.yaml and expected.yaml, plus any of:
  inputs.yaml       the workflow's inputs
  secrets.yaml      the caller's secrets, by name
  options.yaml      runs (how many times to run), retries, sandbox, macos_only, lock, replay
  model.yaml        scripted replies for agent steps, served as an OpenAI-compatible API
  catalog/          steps only this case uses; tests/run-catalog/ holds the shared ones

expected.yaml says what the last run must give: status, error (the run's error code), outputs,
and for chosen steps their status, error code, cacheHit, attempts and reason. It can also list
calls (step id to the steps it called), model_saw_tools, dry_run_differences, and logs_hide
(values no stored log may contain). Every run record must fit schemas/run.schema.json.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from wfspec import load_catalog, load_document
from wfspec.documents import schema_issues
from wfspec.run import Options, Runner, compare_dry_run
from wfspec.runtimes import SANDBOX, Model
from wfspec.store import Store

ROOT = Path(__file__).resolve().parents[2]
CASES = sorted(p for p in (ROOT / "tests" / "run").iterdir() if p.is_dir())


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
    dirs = [ROOT / "tests" / "run-catalog"] + ([case / "catalog"] if (case / "catalog").is_dir() else [])
    catalog = load_catalog(dirs)
    assert catalog.problems == []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    places = {"{tmp}": str(tmp_path), "{port}": listener.getsockname()[1]}
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
            lock=optional(case, opts["lock"], None) if "lock" in opts else None,
        )
        for _ in range(opts.get("runs", 1)):
            record = Runner(catalog, options).run(workflow, inputs)
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
        got = record["steps"][sid]
        for key, value in want.items():
            actual = got.get("error", {}).get("code") if key == "error" else got.get(key)
            assert actual == value, f"{sid}.{key}: {actual!r} != {value!r} ({got.get('error')})"
    if expected.get("steps") == {}:
        assert record["steps"] == {}
    for sid, uses in (expected.get("calls") or {}).items():
        assert [c["uses"] for c in record["steps"][sid].get("calls", [])] == uses
    if "model_saw_tools" in expected:
        assert [t["function"]["name"] for t in model.requests[0]["tools"]] == expected["model_saw_tools"]
    if "dry_run_differences" in expected:
        assert compare_dry_run(replay, record, catalog) == expected["dry_run_differences"]
    for hidden in expected.get("logs_hide", []):
        for log in (tmp_path / "store" / "files").iterdir():
            assert hidden.encode() not in log.read_bytes(), f"{hidden} reached a stored file"
