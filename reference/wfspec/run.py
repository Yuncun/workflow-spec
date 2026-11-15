"""The reference runner: the running rules in SPEC.md, section 9, with the step runtimes in
drafts/runtime.md (`process`, `agent`) and SPEC.md's `workflow` kind.

It runs on one machine, one step at a time. On macOS it enforces `effects` for programs with the
system sandbox: a `none` step gets no network, and neither a `none` nor a `reads` step may write
outside its work folder.

A program that starts runs for other programs can give each run its id, read the run's record from
the store while it runs, and cancel it by raising RunCancelled.
"""

from __future__ import annotations

import getpass
import json
import secrets as tokens
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .check import INPUT_REF, OUTPUT_REF, check, split_value
from .documents import Catalog, json_problem, load_document, media_type_problems, value_problem
from .publish import digest, pinned_workflow
from .runtimes import Model, Outcome, StepFailed, inside, readable, run_agent, run_process
from .store import Store

CALL_FIELDS = ("uses", "digest", "inputs", "outputs", "status", "reason", "started", "ended", "error", "logs", "calls", "steps")
MAX_DEPTH = 32  # how deeply steps may nest, through calls and workflow steps


class RunCancelled(BaseException):
    """Raise this in the thread that runs a run to cancel it, for example from a signal handler.

    The run ends with the status `cancelled`. A step that was running fails with the runner's own
    code `x-cancelled` (SPEC.md, [run.error-codes]), since nothing says how far it got, and the
    steps that hadn't started are skipped. It isn't an Exception, so no step runtime catches it."""


CANCELLED = "the run was cancelled"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def with_defaults(schema: dict, values: dict) -> dict:
    """The input values with each missing input's default filled in."""
    filled = dict(values)
    for name, prop in schema.get("properties", {}).items():
        if name not in filled and isinstance(prop, dict) and "default" in prop:
            filled[name] = prop["default"]
    return filled


@dataclass
class Options:
    store: Store
    policy: dict | None = None
    secrets: dict[str, str] = field(default_factory=dict)  # the caller's secrets, by name
    timeout: float = 300  # seconds, per program
    model: Model = field(default_factory=Model)
    sandbox: bool = True
    caller: str = field(default_factory=getpass.getuser)
    readable: list[Path] = field(default_factory=list)  # folders programs may read under the home folder
    # Asked with a step's name@version and input values before it writes; False leaves the step out ([run.effects]).
    approve: Callable[[str, dict], bool] | None = None
    # Told the step id and the record so far when each step of the workflow starts, and again when it ends.
    watch: Callable[[str, dict], None] | None = None


class Runner:
    def __init__(self, catalog: Catalog, options: Options):
        self.catalog, self.options, self.store = catalog, options, options.store
        self._live: dict | None = None  # the record of the run in progress
        self._cancelled = False

    # ---- a whole run ----------------------------------------------------------------------

    def run(self, workflow: dict, inputs: dict, run_id: str | None = None) -> dict:
        """Run a workflow and return its run record.

        The record is saved in the store when the run starts, each time one of the workflow's steps
        starts or ends, and when the run ends, so another program can read how far the run has got.
        `run_id` gives the record's id, for a caller that has to name the run before it starts."""
        record = {
            "id": run_id or "run_" + uuid.uuid4().hex[:12],
            "workflow": {k: workflow[k] for k in ("name", "version") if isinstance(workflow, dict) and k in workflow} | {"digest": digest(workflow)},
            "inputs": inputs,
            "status": "running",
            "caller": self.options.caller,
            "created": now(),
            "traceId": tokens.token_hex(16),
            "steps": {},
        }
        result = check(workflow, self.catalog, self.options.policy)
        if not result.ok:  # SPEC.md, [run.check-first]
            problems = "; ".join(f"{e.code} at {e.path or '/'}" for e in result.errors)
            return self._refuse(record, "check_failed", f"the workflow failed checking: {problems}")
        schema = workflow.get("inputSchema", {"type": "object", "properties": {}})
        values = with_defaults(schema, inputs)
        problem = json_problem(values) or value_problem(schema, values) or next(iter(media_type_problems(values, schema)), None)
        if problem:  # a run record is a JSON document, so NaN or a number beyond 2**53 can't go in ([doc.numbers])
            return self._refuse(record, "invalid_input", f"the inputs don't fit the workflow's inputSchema: {problem}")
        record["inputs"] = values
        record["started"] = now()
        record["steps"] = {sid: {"uses": step["uses"], "status": "pending" if sid in result.order else "not_needed"}
                           for sid, step in workflow["steps"].items()}
        self._live, self._cancelled = record, False
        try:
            self._save(record)
            steps, outputs, failed = self._steps(workflow, result, values)
        except RunCancelled:  # between two steps; a step that is cancelled while it runs records that itself
            self._cancelled = True
            steps, failed = {sid: _cut_short(entry) for sid, entry in record["steps"].items()}, []
            outputs = self._outputs(result, values, steps)
        finally:
            self._live = None
        record["steps"], record["outputs"] = steps, outputs
        record["status"] = "cancelled" if self._cancelled else "failed" if failed else "succeeded"
        if failed and not self._cancelled:
            first = steps[failed[0]]["error"]
            record["error"] = {"code": first["code"], "message": f"step {failed[0]!r}: {first['message']}"}
        record["ended"] = now()
        return self._save(record)

    def _refuse(self, record: dict, code: str, message: str) -> dict:
        record.update(status="failed", error={"code": code, "message": message}, ended=now())
        return self._save(record)

    def _save(self, record: dict) -> dict:
        """Save the record with every secret value replaced, wherever it ended up ([run.secrets-hidden])."""
        record = self._scrub(record)
        self.store.save_run(record)
        return record

    def unreadable(self, raw: bytes, message: str, run_id: str | None = None) -> dict:
        """The record of a run refused because its workflow can't be read ([run.check-first])."""
        import hashlib

        record = {"id": run_id or "run_" + uuid.uuid4().hex[:12], "workflow": {"digest": "sha256:" + hashlib.sha256(raw).hexdigest()},
                  "inputs": {}, "status": "running", "caller": self.options.caller,
                  "created": now(), "traceId": tokens.token_hex(16), "steps": {}}
        return self._refuse(record, "check_failed", f"the workflow can't be read: {message}")

    def _steps(self, workflow: dict, result, values: dict, depth: int = 0) -> tuple[dict, dict, list[str]]:
        """Run the needed steps in order (SPEC.md, sections 9.2 to 9.4). Returns the step records, the
        workflow's outputs, and the ids of the steps that failed."""
        done: dict[str, dict] = {}
        for sid in result.order:
            step = workflow["steps"][sid]
            if self._cancelled:
                done[sid] = {"uses": step["uses"], "status": "skipped", "reason": CANCELLED}
                self._tell(sid, done[sid], depth)
                continue
            blocked = [d for d in sorted(result.deps[sid]) if done[d]["status"] in ("failed", "skipped")]
            if blocked:  # [run.failure], [run.skip]
                upstream = done[blocked[0]]
                why = upstream.get("reason") if upstream["status"] == "skipped" else f"step {blocked[0]!r} failed"
                done[sid] = {"uses": step["uses"], "status": "skipped", "reason": why or f"step {blocked[0]!r} skipped"}
                self._tell(sid, done[sid], depth)
                continue
            given = {}
            for name, value in (step.get("with") or {}).items():
                kind, x = split_value(value)
                if kind == "literal":
                    given[name] = x
                    continue
                found, v = self._resolve(x, values, done)
                if found:  # a missing value leaves the input unset, so its default applies ([run.missing-value])
                    given[name] = v
            self._tell(sid, {"uses": step["uses"], "status": "running"}, depth)
            done[sid] = self.run_step(step["uses"], given, depth)
            self._tell(sid, done[sid], depth)
        steps = {sid: done.get(sid, {"uses": workflow["steps"][sid]["uses"], "status": "not_needed"}) for sid in workflow["steps"]}
        return steps, self._outputs(result, values, done), [sid for sid in result.order if done[sid]["status"] == "failed"]

    def _outputs(self, result, values: dict, done: dict) -> dict:
        """The workflow's outputs, leaving out those that depend on a step that didn't succeed."""
        outputs = {}
        for name, source in result.outputs.items():
            ref = f"inputs.{source.name}" if source.step is None else f"steps.{source.step}.outputs.{source.name}"
            found, v = self._resolve(ref, values, done)
            if found:
                outputs[name] = v
        return outputs

    def _tell(self, sid: str, entry: dict, depth: int) -> None:
        if depth != 0:  # only the steps of the workflow itself, not of one used as a step
            return
        if self._live is not None:
            self._live["steps"][sid] = entry
            self._save(self._live)
        if self.options.watch is not None:
            self.options.watch(sid, self._scrub(entry))

    @staticmethod
    def _resolve(ref: str, values: dict, done: dict) -> tuple[bool, object]:
        if m := INPUT_REF.match(ref):
            return (m.group(1) in values), values.get(m.group(1))
        m = OUTPUT_REF.match(ref)
        entry = done.get(m.group(1), {})
        outputs = entry.get("outputs", {}) if entry.get("status") == "succeeded" else {}
        return (m.group(2) in outputs), outputs.get(m.group(2))

    # ---- one step -------------------------------------------------------------------------

    def run_step(self, ref: str, given, depth: int = 0) -> dict:
        """Run one step version with the given input values, and return its record (SPEC.md, sections 9.3 to 9.7)."""
        manifest = self.catalog.steps[ref]
        entry: dict = {"uses": ref, "digest": digest(manifest), "status": "running", "started": now(),
                       "inputs": given if isinstance(given, dict) else {}}
        began = False  # whether the step itself has started, so cancelling can't have left it half done
        try:
            if not isinstance(given, dict):  # a model can send anything as a call's arguments
                raise StepFailed("invalid_input", f"the input values aren't a JSON object: {json.dumps(given)[:200]}")
            if depth > MAX_DEPTH:
                raise StepFailed("failed", f"steps nest more than {MAX_DEPTH} levels deep")
            inputs = with_defaults(manifest["inputSchema"], given)
            entry["inputs"] = inputs
            problem = (json_problem(inputs) or value_problem(manifest["inputSchema"], inputs)
                       or next(iter(media_type_problems(inputs, manifest["inputSchema"])), None))
            if problem:  # [run.input-check], [types.media-type], [doc.numbers]
                raise StepFailed("invalid_input", problem)
            # An agent or a workflow writes only through the steps it runs, and each of those is asked about in turn.
            asks = manifest["effects"] == "writes" and next(iter(manifest["runtime"])) not in ("agent", "workflow")
            if asks and self.options.approve is not None and not self.options.approve(ref, self._scrub(inputs)):
                entry.update(status="skipped", reason="not approved", ended=now())
                return entry
            began = True
            outcome = self._once(ref, manifest, inputs, entry, depth)
            if outcome.skipped is not None:
                entry.update(status="skipped", reason=self._redact(outcome.skipped))
            else:
                entry.update(status="succeeded", outputs=outcome.outputs)
        except RunCancelled:  # whoever started this step sees _cancelled and stops too
            self._cancelled = True
            if began:
                entry.update(status="failed", error={"code": "x-cancelled", "message": f"{CANCELLED} while this step was running"})
            else:
                entry.update(status="skipped", reason=CANCELLED)
        except StepFailed as exc:
            entry.update(status="failed", error={"code": exc.code, "message": self._redact(exc.message)})
            if exc.logs:
                entry["logs"] = self.store.put_bytes(self._redact(exc.logs), "text/plain", f"{ref.split('/')[1]}.log")
        except Exception as exc:  # a bug in a runtime or the runner still leaves a complete record
            entry.update(status="failed", error={"code": "failed", "message": self._redact(f"the runner failed: {type(exc).__name__}: {exc}")})
        entry["ended"] = now()
        return entry

    def _once(self, ref: str, manifest: dict, inputs: dict, entry: dict, depth: int) -> Outcome:
        """Run a step once, and check its outputs. The runner retries no step ([run.writes-once])."""
        outcome = self._execute(ref, manifest, inputs, entry, depth)
        if outcome.logs:
            entry["logs"] = self.store.put_bytes(self._redact(outcome.logs), "text/plain", f"{ref.split('/')[1]}.log")
        if outcome.skipped is None:
            problem = json_problem(outcome.outputs) or value_problem(manifest["outputSchema"], outcome.outputs) or next(
                iter(media_type_problems(outcome.outputs, manifest["outputSchema"])), None)
            if problem:  # [run.output-check], [types.media-type]
                raise StepFailed("invalid_output", problem, outcome.logs)
            if self._holds_secret(outcome.outputs):  # [run.secrets-hidden]
                raise StepFailed("failed", "the outputs contain a secret's value", outcome.logs)
        return outcome

    def _execute(self, ref: str, manifest: dict, inputs: dict, entry: dict, depth: int) -> Outcome:
        kind = next(iter(manifest["runtime"]))
        folder = Path(self.catalog.files[ref]).parent
        missing = [s for s in manifest.get("secrets", []) if s not in self.options.secrets]
        if missing:
            raise StepFailed("failed", f"secrets this run doesn't have: {', '.join(missing)}")
        named = {s: self.options.secrets[s] for s in manifest.get("secrets", [])}  # [run.secrets-named]
        if kind == "process":
            return run_process(manifest, folder, inputs, named, self.store, self.options.timeout, self.options.sandbox,
                               self.options.readable, tuple(self._secret_values()))
        if kind == "agent":
            calls = entry.setdefault("calls", [])

            def call(callee: str, args: dict) -> dict:  # [run.calls-listed], [run.calls-recorded]
                child = self.run_step(callee, args, depth + 1)
                calls.append({k: child[k] for k in CALL_FIELDS if k in child})
                if self._cancelled:
                    raise RunCancelled
                if child["status"] == "succeeded":
                    return {"outputs": readable(child["outputs"], self.catalog.steps[callee]["outputSchema"], self.store)}
                if child["status"] == "skipped":
                    return {"skipped": child.get("reason", "")}
                return {"error": child["error"]}

            return run_agent(manifest, folder, inputs, self.store, self.options.model, self.catalog.steps.__getitem__, call)
        if kind == "workflow":
            runtime = manifest["runtime"]["workflow"]
            path = folder / runtime["path"]
            if not inside(path, folder) or not path.is_file():
                raise StepFailed("pinned_mismatch", f"the workflow file {runtime['path']} isn't in the step's folder")
            inner = pinned_workflow(path, runtime["digest"])
            if inner is None:  # [run.pinned]
                raise StepFailed("pinned_mismatch", f"{runtime['path']} has changed since the step was published")
            result = check(inner, self.catalog, self.options.policy)
            if not result.ok:
                raise StepFailed("failed", "the workflow inside this step fails checking")
            steps, outputs, failed = self._steps(inner, result, with_defaults(inner.get("inputSchema", {}), inputs), depth + 1)
            entry["steps"] = steps
            if self._cancelled:
                raise RunCancelled
            if failed:
                raise StepFailed("failed", f"step {failed[0]!r} inside the workflow failed")
            return Outcome(outputs=outputs)
        raise StepFailed("failed", f"this runner doesn't run {kind!r} steps")

    # ---- secrets ----------------------------------------------------------------------------

    def _secret_values(self) -> list[str]:
        return [v for v in self.options.secrets.values() if v]  # a short secret is hidden too, whatever else it hides

    def _scrub(self, value):
        """`value` with every secret value replaced: inside text and keys, and as a whole for a number
        or other value whose JSON form holds one."""
        if isinstance(value, str):
            return self._redact(value)
        if isinstance(value, dict):
            return {self._redact(k): self._scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v) for v in value]
        return "[secret]" if self._holds_secret(json.dumps(value)) else value

    def _redact(self, text):
        for value in self._secret_values():
            text = text.replace(value.encode(), b"[secret]") if isinstance(text, bytes) else text.replace(value, "[secret]")
        return text

    def _holds_secret(self, value) -> bool:
        if isinstance(value, str):
            return any(secret in value for secret in self._secret_values())
        if isinstance(value, dict):
            return any(self._holds_secret(k) or self._holds_secret(v) for k, v in value.items())
        if isinstance(value, list):
            return any(self._holds_secret(v) for v in value)
        return False


def _cut_short(entry: dict) -> dict:
    """A step's record in a run cancelled between two steps."""
    if entry["status"] == "pending":
        return {**entry, "status": "skipped", "reason": CANCELLED}
    if entry["status"] == "running":
        return {**entry, "status": "failed", "error": {"code": "x-cancelled", "message": f"{CANCELLED} while this step was running"}}
    return entry
