"""The reference runner: the running rules in SPEC.md, section 9, with the step runtimes in
drafts/runtime.md (`process`, `agent`) and SPEC.md's `workflow` kind.

It runs on one machine, one step at a time. On macOS it enforces `effects` for programs with the
system sandbox: a `none` step gets no network, and neither a `none` nor a `reads` step may write
outside its work folder.
"""

from __future__ import annotations

import getpass
import json
import secrets as tokens
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .check import INPUT_REF, OUTPUT_REF, check, split_value
from .documents import Catalog, load_document, media_type_problems, value_problem
from .publish import cache_key, digest, lock
from .runtimes import Model, Outcome, StepFailed, readable, run_agent, run_process
from .store import Store

CALL_FIELDS = ("uses", "digest", "cacheKey", "cacheHit", "attempts", "inputs", "outputs", "status", "reason",
               "started", "ended", "error", "logs")


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
    retries: int = 0  # automatic retries for a failed step that doesn't write
    timeout: float = 300  # seconds, per program
    model: Model = field(default_factory=Model)
    sandbox: bool = True
    caller: str = field(default_factory=getpass.getuser)
    replay: dict | None = None  # a run record: steps that write return its outputs (a dry run)
    lock: dict | None = None  # the workflow's lock file, when it was published


class Runner:
    def __init__(self, catalog: Catalog, options: Options):
        self.catalog, self.options, self.store = catalog, options, options.store
        self._replayed: dict[str, int] = {}

    # ---- a whole run ----------------------------------------------------------------------

    def run(self, workflow: dict, inputs: dict) -> dict:
        """Run a workflow and return its run record, which is also saved in the store."""
        record = {
            "id": "run_" + uuid.uuid4().hex[:12],
            "workflow": {k: workflow[k] for k in ("name", "version") if k in workflow} | {"digest": digest(workflow)},
            "lock": {"lockVersion": 1, "steps": {}},
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
        record["lock"] = lock(workflow, self.catalog)
        if self.options.lock is not None and self.options.lock.get("steps") != record["lock"]["steps"]:  # [run.lock]
            return self._refuse(record, "lock_mismatch", "a step version no longer matches the workflow's lock file")
        schema = workflow.get("inputSchema", {"type": "object", "properties": {}})
        values = with_defaults(schema, inputs)
        problem = value_problem(schema, values) or next(iter(media_type_problems(values, schema)), None)
        if problem:
            return self._refuse(record, "invalid_input", f"the inputs don't fit the workflow's inputSchema: {problem}")
        record["inputs"] = values
        record["started"] = now()
        steps, outputs, failed = self._steps(workflow, result, values)
        record["steps"], record["outputs"] = steps, outputs
        record["status"] = "failed" if failed else "succeeded"
        if failed:
            first = steps[failed[0]]["error"]
            record["error"] = {"code": first["code"], "message": f"step {failed[0]!r}: {first['message']}"}
        record["ended"] = now()
        self.store.save_run(record)
        return record

    def _refuse(self, record: dict, code: str, message: str) -> dict:
        record.update(status="failed", error={"code": code, "message": message}, ended=now())
        self.store.save_run(record)
        return record

    def _steps(self, workflow: dict, result, values: dict) -> tuple[dict, dict, list[str]]:
        """Run the needed steps in order (SPEC.md, sections 9.2 to 9.6). Returns the step records, the
        workflow's outputs, and the ids of the steps that failed."""
        done: dict[str, dict] = {}
        for sid in result.order:
            step = workflow["steps"][sid]
            blocked = [d for d in sorted(result.deps[sid]) if done[d]["status"] in ("failed", "skipped")]
            if blocked:  # [run.failure], [run.skip]
                upstream = done[blocked[0]]
                why = upstream.get("reason") if upstream["status"] == "skipped" else f"step {blocked[0]!r} failed"
                done[sid] = {"uses": step["uses"], "status": "skipped", "reason": why or f"step {blocked[0]!r} skipped"}
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
            done[sid] = self.run_step(step["uses"], given)
        steps = {sid: done.get(sid, {"uses": workflow["steps"][sid]["uses"], "status": "not_needed"}) for sid in workflow["steps"]}
        outputs = {}
        for name, source in result.outputs.items():
            ref = f"inputs.{source.name}" if source.step is None else f"steps.{source.step}.outputs.{source.name}"
            found, v = self._resolve(ref, values, done)
            if found:  # outputs that depend on a skipped or failed step are left out
                outputs[name] = v
        return steps, outputs, [sid for sid in result.order if done[sid]["status"] == "failed"]

    @staticmethod
    def _resolve(ref: str, values: dict, done: dict) -> tuple[bool, object]:
        if m := INPUT_REF.match(ref):
            return (m.group(1) in values), values.get(m.group(1))
        m = OUTPUT_REF.match(ref)
        entry = done.get(m.group(1), {})
        outputs = entry.get("outputs", {}) if entry.get("status") == "succeeded" else {}
        return (m.group(2) in outputs), outputs.get(m.group(2))

    # ---- one step -------------------------------------------------------------------------

    def run_step(self, ref: str, given: dict) -> dict:
        """Run one step version with the given input values, and return its record (SPEC.md, sections 9.3 to 9.8)."""
        manifest = self.catalog.steps[ref]
        entry: dict = {"uses": ref, "digest": digest(manifest), "status": "running", "started": now()}
        cacheable = manifest.get("deterministic", False) and manifest["effects"] != "writes"
        try:
            inputs = with_defaults(manifest["inputSchema"], given)
            entry["inputs"] = inputs
            problem = value_problem(manifest["inputSchema"], inputs) or next(iter(media_type_problems(inputs, manifest["inputSchema"])), None)
            if problem:  # [run.input-check], [types.media-type]
                raise StepFailed("invalid_input", problem)
            key = None
            if cacheable:  # [run.cache-key], [run.cache-only-cacheable]
                key = cache_key(manifest, inputs)
                entry["cacheKey"] = key
                hit = self.store.cached(key)
                if hit is not None:
                    entry.update(status="succeeded", outputs=hit, cacheHit=True, attempts=0, ended=now())
                    return entry
                entry["cacheHit"] = False
            if self.options.replay is not None and manifest["effects"] == "writes":
                recorded = self._recorded_outputs(ref)
                if recorded is None:
                    raise StepFailed("failed", "dry run: the original run has no recorded outputs for this step")
                entry.update(status="succeeded", outputs=recorded, attempts=0, ended=now(),
                             reason=f"dry run: outputs recorded in {self.options.replay['id']}")
                return entry
            outcome = self._attempts(ref, manifest, inputs, entry)
            if outcome.skipped is not None:
                entry.update(status="skipped", reason=outcome.skipped)
            else:
                entry.update(status="succeeded", outputs=outcome.outputs)
                if key:
                    self.store.cache(key, outcome.outputs)
        except StepFailed as exc:
            entry.update(status="failed", error={"code": exc.code, "message": self._redact(exc.message)})
            if exc.logs:
                entry["logs"] = self.store.put_bytes(self._redact(exc.logs), "text/plain", f"{ref.split('/')[1]}.log")
        except Exception as exc:  # a bug in a runtime or the runner still leaves a complete record
            entry.update(status="failed", error={"code": "failed", "message": self._redact(f"the runner failed: {type(exc).__name__}: {exc}")})
        entry["ended"] = now()
        return entry

    def _attempts(self, ref: str, manifest: dict, inputs: dict, entry: dict) -> Outcome:
        """Run a step, retrying a failure only when the step doesn't write ([run.retry])."""
        attempts = 0
        while True:
            attempts += 1
            entry["attempts"] = attempts
            try:
                outcome = self._execute(ref, manifest, inputs, entry)
                if outcome.logs:
                    entry["logs"] = self.store.put_bytes(self._redact(outcome.logs), "text/plain", f"{ref.split('/')[1]}.log")
                if outcome.skipped is None:
                    problem = value_problem(manifest["outputSchema"], outcome.outputs)
                    if problem:  # [run.output-check]
                        raise StepFailed("invalid_output", problem, outcome.logs)
                    if self._holds_secret(outcome.outputs):  # [run.secrets-hidden]
                        raise StepFailed("failed", "the outputs contain a secret's value", outcome.logs)
                return outcome
            except StepFailed:
                if attempts <= self.options.retries and manifest["effects"] != "writes":
                    continue
                raise

    def _execute(self, ref: str, manifest: dict, inputs: dict, entry: dict) -> Outcome:
        kind = next(iter(manifest["runtime"]))
        folder = Path(self.catalog.files[ref]).parent
        missing = [s for s in manifest.get("secrets", []) if s not in self.options.secrets]
        if missing:
            raise StepFailed("failed", f"secrets this run doesn't have: {', '.join(missing)}")
        named = {s: self.options.secrets[s] for s in manifest.get("secrets", [])}  # [run.secrets-named]
        if kind == "process":
            return run_process(manifest, folder, inputs, named, self.store, self.options.timeout, self.options.sandbox)
        if kind == "agent":
            calls = entry.setdefault("calls", [])

            def call(callee: str, args: dict) -> dict:  # [run.calls-listed], [run.calls-recorded]
                child = self.run_step(callee, args)
                calls.append({k: child[k] for k in CALL_FIELDS if k in child})
                if child["status"] == "succeeded":
                    return {"outputs": readable(child["outputs"], self.catalog.steps[callee]["outputSchema"], self.store)}
                if child["status"] == "skipped":
                    return {"skipped": child.get("reason", "")}
                return {"error": child["error"]}

            return run_agent(manifest, folder, inputs, self.store, self.options.model, self.catalog.steps.__getitem__, call)
        if kind == "workflow":
            inner = load_document(folder / manifest["runtime"]["workflow"]["path"])
            result = check(inner, self.catalog, self.options.policy)
            if not result.ok:
                raise StepFailed("failed", "the workflow inside this step fails checking")
            steps, outputs, failed = self._steps(inner, result, with_defaults(inner.get("inputSchema", {}), inputs))
            entry["steps"] = steps
            if failed:
                raise StepFailed("failed", f"step {failed[0]!r} inside the workflow failed")
            return Outcome(outputs=outputs)
        raise StepFailed("failed", f"this runner doesn't run {kind!r} steps")

    # ---- secrets ----------------------------------------------------------------------------

    def _secret_values(self) -> list[str]:
        return [v for v in self.options.secrets.values() if len(v) >= 4]

    def _redact(self, text):
        for value in self._secret_values():
            text = text.replace(value.encode(), b"[secret]") if isinstance(text, bytes) else text.replace(value, "[secret]")
        return text

    def _holds_secret(self, outputs) -> bool:
        dumped = json.dumps(outputs, ensure_ascii=False)
        return any(value in dumped for value in self._secret_values())

    # ---- dry runs (SPEC.md, [publish.saved-run]) --------------------------------------------

    def _recorded_outputs(self, ref: str) -> dict | None:
        """The outputs the original run recorded for the next unused call of `ref`, in run order."""
        seen = -1
        for entry in _entries(self.options.replay):
            if entry["uses"] == ref and entry.get("status") == "succeeded":
                seen += 1
                if seen == self._replayed.get(ref, 0):
                    self._replayed[ref] = seen + 1
                    return entry.get("outputs", {})
        return None


def _entries(record: dict) -> list[dict]:
    """Every step and call in a run record, in the order they ran."""
    found = []
    for entry in record.get("steps", {}).values():
        found.append(entry)
        found.extend(entry.get("calls", []))
        found.extend(_entries(entry) if "steps" in entry else [])
    return found


def compare_dry_run(original: dict, dry: dict, catalog: Catalog) -> list[str]:
    """Differences between a dry run of a saved workflow and the run it was saved from: for each
    deterministic step that ran, the original call with the same step and inputs must have given
    the same outputs."""
    differences = []
    before = _entries(original)
    for entry in _entries(dry):
        manifest = catalog.steps.get(entry["uses"], {})
        if entry.get("status") != "succeeded" or not manifest.get("deterministic") or "dry run" in entry.get("reason", ""):
            continue
        same = [e for e in before if e["uses"] == entry["uses"] and digest(e.get("inputs", {})) == digest(entry.get("inputs", {}))]
        if same and digest(same[0].get("outputs", {})) != digest(entry.get("outputs", {})):
            differences.append(f"{entry['uses']} gave different outputs for the same inputs")
    return differences
