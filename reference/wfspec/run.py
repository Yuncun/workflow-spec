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
from typing import Callable

from .check import INPUT_REF, OUTPUT_REF, check, split_value
from .documents import Catalog, load_document, media_type_problems, value_problem
from .publish import cache_key, digest, lock, pinned_workflow
from .runtimes import Model, Outcome, StepFailed, inside, readable, run_agent, run_process
from .store import Store

CALL_FIELDS = ("uses", "digest", "cacheKey", "cacheHit", "attempts", "inputs", "outputs", "status", "reason",
               "started", "ended", "error", "logs", "calls", "steps")
MAX_DEPTH = 32  # how deeply steps may nest, through calls and workflow steps


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
    readable: list[Path] = field(default_factory=list)  # folders programs may read under the home folder
    # Asked with a step's name@version and input values before it writes; False leaves the step out ([run.effects]).
    approve: Callable[[str, dict], bool] | None = None
    # Told the step id and the record so far when each step of the workflow starts, and again when it ends.
    watch: Callable[[str, dict], None] | None = None


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
        return self._save(record)

    def _refuse(self, record: dict, code: str, message: str) -> dict:
        record.update(status="failed", error={"code": code, "message": message}, ended=now())
        return self._save(record)

    def _save(self, record: dict) -> dict:
        """Save the record with every secret value replaced, wherever it ended up ([run.secrets-hidden])."""
        record = self._scrub(record)
        self.store.save_run(record)
        return record

    def unreadable(self, raw: bytes, message: str) -> dict:
        """The record of a run refused because its workflow can't be read ([run.check-first])."""
        import hashlib

        record = {"id": "run_" + uuid.uuid4().hex[:12], "workflow": {"digest": "sha256:" + hashlib.sha256(raw).hexdigest()},
                  "lock": {"lockVersion": 1, "steps": {}}, "inputs": {}, "status": "running", "caller": self.options.caller,
                  "created": now(), "traceId": tokens.token_hex(16), "steps": {}}
        return self._refuse(record, "check_failed", f"the workflow can't be read: {message}")

    def _steps(self, workflow: dict, result, values: dict, depth: int = 0) -> tuple[dict, dict, list[str]]:
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
        outputs = {}
        for name, source in result.outputs.items():
            ref = f"inputs.{source.name}" if source.step is None else f"steps.{source.step}.outputs.{source.name}"
            found, v = self._resolve(ref, values, done)
            if found:  # outputs that depend on a skipped or failed step are left out
                outputs[name] = v
        return steps, outputs, [sid for sid in result.order if done[sid]["status"] == "failed"]

    def _tell(self, sid: str, entry: dict, depth: int) -> None:
        if self.options.watch is not None and depth == 0:  # the steps of the workflow itself, not of one used as a step
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
        """Run one step version with the given input values, and return its record (SPEC.md, sections 9.3 to 9.8)."""
        manifest = self.catalog.steps[ref]
        entry: dict = {"uses": ref, "digest": digest(manifest), "status": "running", "started": now(),
                       "inputs": given if isinstance(given, dict) else {}}
        cacheable = manifest.get("deterministic", False) and manifest["effects"] != "writes"
        try:
            if not isinstance(given, dict):  # a model can send anything as a call's arguments
                raise StepFailed("invalid_input", f"the input values aren't a JSON object: {json.dumps(given)[:200]}")
            if depth > MAX_DEPTH:
                raise StepFailed("failed", f"steps nest more than {MAX_DEPTH} levels deep")
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
            # An agent or a workflow writes only through the steps it runs, and each of those is asked about in turn.
            asks = manifest["effects"] == "writes" and next(iter(manifest["runtime"])) not in ("agent", "workflow")
            if asks and self.options.approve is not None and not self.options.approve(ref, self._scrub(inputs)):
                entry.update(status="skipped", reason="not approved", attempts=0, ended=now())
                return entry
            outcome = self._attempts(ref, manifest, inputs, entry, depth)
            if outcome.skipped is not None:
                entry.update(status="skipped", reason=self._redact(outcome.skipped))
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

    def _attempts(self, ref: str, manifest: dict, inputs: dict, entry: dict, depth: int) -> Outcome:
        """Run a step, retrying a failure only when the step doesn't write ([run.retry])."""
        attempts = 0
        while True:
            attempts += 1
            entry["attempts"] = attempts
            try:
                outcome = self._execute(ref, manifest, inputs, entry, depth)
                if outcome.logs:
                    entry["logs"] = self.store.put_bytes(self._redact(outcome.logs), "text/plain", f"{ref.split('/')[1]}.log")
                if outcome.skipped is None:
                    problem = value_problem(manifest["outputSchema"], outcome.outputs) or next(
                        iter(media_type_problems(outcome.outputs, manifest["outputSchema"])), None)
                    if problem:  # [run.output-check], [types.media-type]
                        raise StepFailed("invalid_output", problem, outcome.logs)
                    if self._holds_secret(outcome.outputs):  # [run.secrets-hidden]
                        raise StepFailed("failed", "the outputs contain a secret's value", outcome.logs)
                return outcome
            except StepFailed:
                if attempts <= self.options.retries and manifest["effects"] != "writes":
                    continue
                raise

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

    # ---- dry runs (SPEC.md, [publish.saved-run]) --------------------------------------------

    def _recorded_outputs(self, ref: str) -> dict | None:
        """The outputs the original run recorded for the next write of `ref` not yet replayed."""
        k = self._replayed.get(ref, 0)
        recorded = _recorded_write(self.options.replay, ref, k)
        if recorded is None:
            return None
        self._replayed[ref] = k + 1
        return recorded.get("outputs", {})


def _entries(record: dict) -> list[dict]:
    """Every step and call in a run record, in run order: steps by start time, each followed by
    what ran inside it."""
    return [entry for entry, _ in _walk(record)]


def _walk(record: dict, outer: tuple = ()) -> list[tuple[dict, tuple]]:
    """Every step and call in a run record, in run order, each with the steps and calls it ran inside."""
    found = []
    for entry in sorted(record.get("steps", {}).values(), key=lambda e: e.get("started", "")):
        found.extend(_walk_entry(entry, outer))
    return found


def _walk_entry(entry: dict, outer: tuple) -> list[tuple[dict, tuple]]:
    """A step or call, then the calls it made and the steps of its workflow, at every depth."""
    found, inside = [(entry, outer)], (*outer, entry)
    for call in entry.get("calls", []):
        found.extend(_walk_entry(call, inside))
    if "steps" in entry:
        found.extend(_walk(entry, inside))
    return found


def _recorded_write(record: dict, ref: str, k: int) -> dict | None:
    """The k-th succeeded step or call of `ref` in a run record, in run order, counting from 0."""
    found = [e for e in _entries(record) if e["uses"] == ref and e.get("status") == "succeeded"]
    return found[k] if k < len(found) else None


def compare_dry_run(original: dict, dry: dict, catalog: Catalog, workflow: dict) -> list[str]:
    """How a dry run of a saved workflow differs from the run it was saved from (SPEC.md,
    [publish.saved-run]): a deterministic step that gave other outputs than the original run
    recorded for the same inputs, a step that writes given another value for a fixed input than
    the write it replayed was given, or a write of the original run that nothing replayed."""
    differences = []
    before = _entries(original)
    for entry in _entries(dry):
        manifest = catalog.steps.get(entry["uses"], {})
        if entry.get("status") != "succeeded" or not manifest.get("deterministic") or manifest.get("effects") == "writes":
            continue
        same = [e for e in before if e["uses"] == entry["uses"] and digest(e.get("inputs", {})) == digest(entry.get("inputs", {}))]
        if same and digest(same[0].get("outputs", {})) != digest(entry.get("outputs", {})):
            differences.append(f"{entry['uses']} gave different outputs for the same inputs")
    order = check(workflow, catalog).order  # the order the dry run replayed its writes in
    fixed = _fixed_steps(workflow, catalog, order)
    counted: dict[str, int] = {}
    replayed: set[int] = set()  # the original run's writes the dry run replayed
    for sid in order:
        entry = dry["steps"].get(sid, {})
        if entry.get("status") != "succeeded" or catalog.steps[entry["uses"]]["effects"] != "writes":
            continue
        k = counted.get(entry["uses"], 0)
        counted[entry["uses"]] = k + 1
        recorded = _recorded_write(original, entry["uses"], k)
        if recorded is None:
            continue
        replayed.add(id(recorded))
        given = workflow["steps"][sid].get("with") or {}
        for name in sorted(set(entry.get("inputs", {})) | set(recorded.get("inputs", {}))):
            if name in given and not _is_fixed(given[name], fixed):
                continue  # a value that can differ from one run to the next, such as a model's answer
            if digest(entry.get("inputs", {}).get(name)) != digest(recorded.get("inputs", {}).get(name)):
                differences.append(f"{entry['uses']} gets a different {name!r} than in the original run")
    for entry, outer in _walk(original):  # a write that made no calls, replayed neither by itself nor with what it ran inside
        if (catalog.steps.get(entry["uses"], {}).get("effects") == "writes" and entry.get("status") == "succeeded"
                and not entry.get("calls") and "steps" not in entry and not any(id(e) in replayed for e in (entry, *outer))):
            differences.append(f"{entry['uses']} wrote in the original run, but no step of the saved workflow replays it")
    return differences


def _fixed_steps(workflow: dict, catalog: Catalog, order: list[str]) -> set[str]:
    """The steps whose outputs are fixed: deterministic steps whose inputs are all fixed."""
    fixed: set[str] = set()
    for sid in order:  # each step comes after the steps it references
        step = workflow["steps"][sid]
        if catalog.steps[step["uses"]].get("deterministic") and all(_is_fixed(v, fixed) for v in (step.get("with") or {}).values()):
            fixed.add(sid)
    return fixed


def _is_fixed(value, fixed: set[str]) -> bool:
    """Whether a `with` value is the same in every run with the same inputs: a literal, a workflow
    input, or an output of a fixed step."""
    kind, ref = split_value(value)
    if kind == "literal" or INPUT_REF.match(ref):
        return True
    m = OUTPUT_REF.match(ref)
    return bool(m) and m.group(1) in fixed
