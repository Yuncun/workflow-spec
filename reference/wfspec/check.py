"""Checking a workflow against a catalog of step manifests (SPEC.md, section 8)."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .documents import EFFECT_ORDER, Catalog, Issue, media_type_problems, pointer, reference_issues, schema_issues, value_problem
from .types import Finding, assignable

INPUT_REF = re.compile(r"^inputs\.([A-Za-z_][A-Za-z0-9_-]*)$")
OUTPUT_REF = re.compile(r"^steps\.([a-z][a-z0-9_-]*)\.outputs\.([A-Za-z_][A-Za-z0-9_-]*)$")
NO_INPUTS = {"type": "object", "properties": {}}
_EFFECT_WORDS = {
    "none": "touches nothing outside its inputs",
    "reads": "reads outside data",
    "writes": "changes outside data",
}


@dataclass
class Source:
    """Where a referenced value comes from: a workflow input, or a step's output."""

    step: str | None  # None for a workflow input
    name: str
    schema: dict | None  # None when the step isn't in the catalog
    optional: bool  # the value may be absent

    def describe(self) -> str:
        if self.step is None:
            return f"workflow input {self.name!r}"
        return f"output {self.name!r} of step {self.step!r}"


@dataclass
class Result:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    needed: list[str] = field(default_factory=list)  # steps a run would execute, in file order
    order: list[str] = field(default_factory=list)  # the needed steps, each after what it references
    outputs: dict[str, Source] = field(default_factory=dict)  # the workflow's outputs, resolved
    deps: dict[str, set[str]] = field(default_factory=dict)  # step -> steps it references

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


def split_value(value) -> tuple[str, object]:
    """("reference", path) for {from: path}; ("literal", x) for {literal: x} or any other value.

    An object whose only key is `from` is a reference whatever its value; a path that isn't
    text is a bad_reference (SPEC.md, [workflow.reference-form])."""
    if isinstance(value, dict) and len(value) == 1:
        if "from" in value:
            return "reference", value["from"]
        if "literal" in value:
            return "literal", value["literal"]
    return "literal", value


def check(workflow, catalog: Catalog, policy: dict | None = None) -> Result:
    """Every rule violation at once, plus warnings (SPEC.md, section 8)."""
    return _Checker(workflow, catalog, policy).run()


class _Checker:
    def __init__(self, workflow, catalog: Catalog, policy: dict | None):
        self.wf = workflow
        self.catalog = catalog
        self.policy = policy
        self.result = Result()
        self.used_inputs: set[str] = set()

    def error(self, code, path, message, step=None, input=None):
        self.result.errors.append(Issue(code, path, message, step, input))

    def warn(self, code, path, message, step=None, input=None):
        self.result.warnings.append(Issue(code, path, message, step, input))

    def run(self) -> Result:
        issues = schema_issues(self.wf, "urn:wfspec:schema:workflow", "invalid_workflow")
        if issues:
            self.result.errors = issues
            return self.result
        self.inputs = self.wf.get("inputSchema", NO_INPUTS)
        self.steps = self.wf["steps"]
        self.deps: dict[str, set[str]] = {sid: set() for sid in self.steps}
        self._check_inputs()
        self.manifests = {sid: self._manifest(sid, step) for sid, step in self.steps.items()}
        for sid in self.steps:
            self._check_step(sid)
        self._check_outputs()
        self._check_graph()
        self.result.errors.sort(key=_order)
        self.result.warnings.sort(key=_order)
        return self.result

    def _check_inputs(self):
        self.inputs_usable = False  # whether values can be compared with the input schemas
        try:
            Draft202012Validator.check_schema(self.inputs)
        except SchemaError as exc:
            self.error("invalid_workflow", pointer("inputSchema", *exc.path), f"not a valid JSON Schema: {exc.message}")
            return
        references = reference_issues(self.inputs, ("inputSchema",))
        for path, message in references:
            self.error("invalid_workflow", path, message)
        if references:
            return
        self.inputs_usable = True
        props = self.inputs["properties"]
        for name in self.inputs.get("required", []):
            if name not in props:
                self.error("invalid_workflow", pointer("inputSchema", "required"), f"{name!r} is required but not declared in properties")
        for name, schema in props.items():
            if _has_default(schema):
                problem = value_problem(schema, schema["default"]) or next(iter(media_type_problems(schema["default"], schema)), None)
                if problem:  # [check.invalid-value], [types.media-type]
                    self.error("invalid_value", pointer("inputSchema", "properties", name, "default"), f"the default doesn't fit the input: {problem}")

    def _manifest(self, sid: str, step: dict) -> dict | None:
        uses = step["uses"]
        manifest = self.catalog.steps.get(uses)
        if manifest is None:
            name, version = uses.split("@", 1)
            versions = self.catalog.versions(name)
            if uses in self.catalog.invalid:
                message = f"{uses} is in the catalog, but its manifest is invalid: {self.catalog.invalid[uses]}"
            elif versions:
                message = f"{name} has no version {version}; the catalog has {', '.join(versions)}"
            else:
                message = f"there is no step named {name} in the catalog" + _suggest(name, self.catalog.names())
            self.error("unknown_step", pointer("steps", sid, "uses"), message, step=sid)
        return manifest

    def _check_step(self, sid: str):
        step = self.steps[sid]
        manifest = self.manifests[sid]
        given = step.get("with") or {}
        props = manifest["inputSchema"]["properties"] if manifest else {}
        required = set(manifest["inputSchema"].get("required", [])) if manifest else set()
        for name, value in given.items():
            where = pointer("steps", sid, "with", name)
            known = manifest is not None and name in props
            if manifest is not None and not known:
                self.error("unknown_input", where, f"{step['uses']} has no input {name!r}" + _suggest(name, props), sid, name)
            kind, payload = split_value(value)
            if kind == "reference":
                source = self._resolve(payload, where, sid, name)
                if known and source is not None and source.schema is not None:
                    self._check_types(source, props[name], name in required, where, sid, name)
            elif known:
                problem = value_problem(props[name], payload) or next(iter(media_type_problems(payload, props[name])), None)
                if problem:  # [check.invalid-value], [types.media-type]
                    self.error("invalid_value", where, problem, sid, name)
        if manifest is None:
            return
        for name in sorted(required - set(given)):
            if not _has_default(props.get(name)):
                self.error("missing_input", pointer("steps", sid, "with"), f"{step['uses']} needs input {name!r}", sid, name)
        self._check_policy(sid, step, manifest)

    def _resolve(self, ref, where: str, sid: str | None = None, name: str | None = None) -> Source | None:
        if not isinstance(ref, str):
            self.error("bad_reference", where, "a reference's 'from' must be text", sid, name)
            return None
        match = INPUT_REF.match(ref)
        if match:
            input_name = match.group(1)
            self.used_inputs.add(input_name)
            props = self.inputs["properties"]
            if input_name not in props:
                self.error("unknown_reference", where, f"the workflow has no input {input_name!r}" + _suggest(input_name, props), sid, name)
                return None
            schema = props[input_name] if isinstance(props[input_name], dict) else {}
            optional = input_name not in self.inputs.get("required", []) and not _has_default(schema)
            return Source(None, input_name, schema if self.inputs_usable else None, optional)
        match = OUTPUT_REF.match(ref)
        if match:
            upstream, output = match.groups()
            if upstream not in self.steps:
                self.error("unknown_reference", where, f"there is no step {upstream!r}" + _suggest(upstream, self.steps), sid, name)
                return None
            if sid is not None:
                self.deps[sid].add(upstream)
            manifest = self.manifests[upstream]
            if manifest is None:
                return Source(upstream, output, None, False)
            outputs = manifest["outputSchema"]
            if output not in outputs["properties"]:
                self.error(
                    "unknown_reference",
                    where,
                    f"step {upstream!r} ({self.steps[upstream]['uses']}) has no output {output!r}" + _suggest(output, outputs["properties"]),
                    sid,
                    name,
                )
                return None
            return Source(upstream, output, outputs["properties"][output], output not in outputs.get("required", []))
        self.error("bad_reference", where, f"{ref!r} isn't a reference; write inputs.<name> or steps.<id>.outputs.<name>", sid, name)
        return None

    def _check_types(self, source: Source, target: dict, required: bool, where: str, sid: str, name: str):
        if source.optional and required and not _has_default(target):
            self.error("type_mismatch", where, f"{source.describe()} may be missing, but input {name!r} is required", sid, name)
            return
        findings = assignable(source.schema, target)
        errors = [f for f in findings if f.error]
        if errors:
            self.error("type_mismatch", where, _explain(source, name, errors), sid, name)
        elif findings:
            self.warn(
                "type_unchecked",
                where,
                _explain(source, name, findings) + ". The value is checked when the step runs",
                sid,
                name,
            )

    def _check_policy(self, sid: str, step: dict, manifest: dict):
        if not self.policy:
            return
        where = pointer("steps", sid, "uses")
        limit = self.policy.get("maxEffects")
        if limit and EFFECT_ORDER[manifest["effects"]] > EFFECT_ORDER[limit]:
            self.error(
                "effects_not_allowed",
                where,
                f"{step['uses']} {_EFFECT_WORDS[manifest['effects']]}; this caller may only run steps whose effects are at most {limit!r}",
                sid,
            )
        if "secrets" in self.policy:
            granted = set(self.policy["secrets"])
            for secret in manifest.get("secrets", []):
                if secret not in granted:
                    self.error("secret_not_granted", where, f"{step['uses']} needs the secret {secret}, which this caller doesn't have", sid)

    def _check_outputs(self):
        for name, ref in (self.wf.get("outputs") or {}).items():
            source = self._resolve(ref["from"], pointer("outputs", name))
            if source is not None:
                self.result.outputs[name] = source

    def _check_graph(self):
        order = list(self.steps)
        for cycle in _cycles(self.deps, order):
            self.error("cycle", pointer("steps", cycle[0]), "these steps depend on each other in a loop: " + ", ".join(cycle), step=cycle[0])
        writes = {sid for sid, m in self.manifests.items() if m and m["effects"] == "writes"}
        unknown = {sid for sid, m in self.manifests.items() if m is None}
        if not self.wf.get("outputs") and not writes and not unknown:
            self.error("no_outputs", "", "the workflow has no outputs and no step that writes, so a run would do nothing")
            return
        roots = {s.step for s in self.result.outputs.values() if s.step} | writes | unknown
        needed = _closure(roots, self.deps)
        self.result.needed = [sid for sid in order if sid in needed]
        self.result.deps = self.deps
        if not any(i.code == "cycle" for i in self.result.errors):
            self.result.order = _run_order(self.result.needed, self.deps)
        for sid in order:
            if sid not in needed:
                self.warn("step_never_runs", pointer("steps", sid), "no output uses this step and it doesn't write, so a run would skip it", step=sid)
        for name in self.inputs["properties"]:
            if name not in self.used_inputs:
                self.warn("unused_input", pointer("inputSchema", "properties", name), f"no step or output uses input {name!r}")


def _explain(source: Source, name: str, findings: list[Finding]) -> str:
    parts = [(f"{f.where}: " if f.where else "") + f.message for f in findings]
    return f"{source.describe()} → input {name!r}: " + "; ".join(parts)


def _has_default(schema) -> bool:
    return isinstance(schema, dict) and "default" in schema


def _suggest(name: str, options) -> str:
    close = difflib.get_close_matches(name, list(options), n=1, cutoff=0.6)
    return f"; did you mean {close[0]!r}?" if close else ""


def _order(issue: Issue):
    return (issue.path, issue.code, issue.input or "")


def _closure(roots: set[str], deps: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [r for r in roots if r in deps]
    while stack:
        sid = stack.pop()
        if sid not in seen:
            seen.add(sid)
            stack.extend(deps[sid] - seen)
    return seen


def closure(roots: set[str], deps: dict[str, set[str]]) -> set[str]:
    """The given steps and every step they reference, directly or not."""
    return _closure(roots, deps)


def _run_order(needed: list[str], deps: dict[str, set[str]]) -> list[str]:
    """Each needed step after the steps it references; ties keep file order."""
    done: list[str] = []
    waiting = list(needed)
    while waiting:
        ready = next(s for s in waiting if deps[s] <= set(done))
        done.append(ready)
        waiting.remove(ready)
    return done


def _cycles(deps: dict[str, set[str]], order: list[str]) -> list[list[str]]:
    """Groups of steps that depend on each other (Tarjan's algorithm), each in file order."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(v: str):
        index[v] = low[v] = len(index)
        stack.append(v)
        on_stack.add(v)
        for w in sorted(deps[v], key=order.index):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1 or v in deps[v]:
                found.append(sorted(group, key=order.index))

    for v in order:
        if v not in index:
            visit(v)
    return sorted(found, key=lambda g: order.index(g[0]))
