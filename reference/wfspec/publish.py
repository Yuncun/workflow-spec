"""Publishing helpers: digests and the manifest a workflow gets when it is published as a step
(SPEC.md, section 10)."""

from __future__ import annotations

from pathlib import Path

from .check import SUBGRAPH, check, closure
from .documents import _DATA_KEYWORDS, EFFECT_ORDER, Catalog, Issue, ParseError, canonical_json, digest, load_document, pointer

__all__ = ["canonical_json", "digest"]  # re-exported from documents, where the checker also needs them

def pinned_workflow(path: Path, pinned: str) -> dict | None:
    """The workflow in `path` when it still has the digest its manifest pins, otherwise None."""
    try:
        doc = load_document(path)
    except (ParseError, OSError):
        return None
    return doc if digest(doc) == pinned else None


def as_step(workflow: dict, catalog: Catalog, path: str) -> dict:
    """The manifest a checked workflow gets when it is published, so other workflows can use it
    (SPEC.md, [publish.as-step]).

    `path` is where the workflow file will sit, relative to the folder of the manifest."""
    result = check(workflow, catalog)
    if not result.ok:
        raise ValueError("the workflow has errors; run `wfspec check` first")
    missing = [key for key in ("name", "version") if not workflow.get(key)]
    if missing:
        raise ValueError("a published workflow needs " + ", ".join(missing))
    manifest: dict = {"name": workflow["name"], "version": workflow["version"]}
    for key in ("title", "description"):
        if key in workflow:
            manifest[key] = workflow[key]
    derived = _derived(workflow, result)
    manifest.update({k: derived[k] for k in ("inputSchema", "outputSchema", "effects", "deterministic")})
    if derived["secrets"]:
        manifest["secrets"] = derived["secrets"]
    manifest["runtime"] = {"workflow": {"path": path, "digest": digest(workflow)}}
    return manifest


def _derived(workflow: dict, result) -> dict:
    """The interface and behaviour a checked workflow has as a step (SPEC.md, [publish.as-step])."""
    manifests = {sid: result.manifests[sid] for sid in result.needed}

    def always_present(source) -> bool:
        # A value is always there when its source always gives it and nothing upstream can skip.
        if source.optional:
            return False
        if source.step is None:
            return True
        return not any(manifests[s].get("maySkip", False) for s in closure({source.step}, result.deps))

    return {
        "inputSchema": workflow.get("inputSchema", {"type": "object", "properties": {}}),
        "outputSchema": {
            "type": "object",
            "properties": {name: source.schema for name, source in result.outputs.items()},
            "required": [name for name, source in result.outputs.items() if always_present(source)],
        },
        "effects": max((m["effects"] for m in manifests.values()), key=EFFECT_ORDER.__getitem__, default="none"),
        "deterministic": bool(manifests) and all(m.get("deterministic", False) for m in manifests.values()),
        "secrets": sorted({s for m in manifests.values() for s in m.get("secrets", [])}),
    }


def _meaning(schema: dict) -> str:
    """A schema's digest with the order of every `required` list ignored, at any depth."""
    return digest(_required_sorted(schema))


_NAMED_SCHEMAS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")  # names, then schemas


def _required_sorted(node):
    """A schema with each `required` list sorted, leaving data such as defaults as they are."""
    if isinstance(node, list):
        return [_required_sorted(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in _DATA_KEYWORDS or key.startswith("x-"):
            out[key] = value
        elif key == "required" and isinstance(value, list):
            out[key] = sorted(value, key=str)
        elif key in _NAMED_SCHEMAS and isinstance(value, dict):
            out[key] = {name: _required_sorted(schema) for name, schema in value.items()}
        else:
            out[key] = _required_sorted(value)
    return out


def self_reach_issues(ref: str, manifest: dict, catalog: Catalog, file: str | None) -> list[Issue]:
    """A workflow step may not be able to run itself through the workflow it runs (SPEC.md, [manifest.no-self])."""
    runtime = manifest["runtime"].get("workflow")
    if not runtime or file is None:
        return []
    starts = _uses(load_document(Path(file).parent / runtime["path"]))
    if any(uses == ref or ref in _reachable(uses, catalog) for uses in starts):
        return [Issue("invalid_manifest", pointer("runtime", "workflow", "path"), f"the workflow can run {ref} itself", file=file)]
    return []


def _uses(workflow: dict) -> list[str]:
    """The published steps a workflow file names, in its steps and in its subgraphs' (SPEC.md, section 7.5)."""
    graphs = [workflow, *workflow.get("subgraphs", {}).values()]
    return [s["uses"] for g in graphs for s in g.get("steps", {}).values() if not s["uses"].startswith(SUBGRAPH)]


def _reachable(start: str, catalog: Catalog) -> set[str]:
    """Every step version `start` can run, through workflow steps, not counting itself."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        manifest = catalog.steps.get(stack.pop())
        if manifest is None:
            continue
        nexts = []
        runtime = manifest["runtime"].get("workflow")
        if runtime:
            ref = f"{manifest['name']}@{manifest['version']}"
            try:
                nexts = _uses(load_document(Path(catalog.files[ref]).parent / runtime["path"]))
            except (KeyError, OSError, ParseError):
                pass
        for n in nexts:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return seen


def workflow_step_issues(manifest: dict, catalog: Catalog, file: str | None) -> list[Issue]:
    """A workflow step's manifest may not claim less than its workflow does (SPEC.md, [manifest.workflow-derived]).

    The workflow must pass checking against the catalog, and the manifest must declare the interface
    [publish.as-step] derives, at least its effects and secrets, and no determinism it doesn't have."""
    runtime = manifest["runtime"].get("workflow")
    if not runtime or file is None:
        return []
    inner = load_document(Path(file).parent / runtime["path"])  # workflow_runtime_issues has read it already
    result = check(inner, catalog)
    if not result.ok:
        codes = ", ".join(sorted({e.code for e in result.errors}))
        return [Issue("invalid_manifest", pointer("runtime", "workflow", "path"), f"the workflow inside this step fails checking ({codes})", file=file)]
    derived, issues = _derived(inner, result), []
    for key in ("inputSchema", "outputSchema"):
        if _meaning(manifest[key]) != _meaning(derived[key]):
            issues.append(Issue("invalid_manifest", pointer(key), f"{key} isn't the one the workflow derives; see wfspec as-step", file=file))
    if EFFECT_ORDER[manifest["effects"]] < EFFECT_ORDER[derived["effects"]]:
        issues.append(Issue("invalid_manifest", pointer("effects"), f"the workflow's steps have effects {derived['effects']!r}, so the step's must be at least that", file=file))
    if manifest.get("deterministic", False) and not derived["deterministic"]:
        issues.append(Issue("invalid_manifest", pointer("deterministic"), "a step the workflow runs isn't deterministic, so the workflow isn't either", file=file))
    missing = sorted(set(derived["secrets"]) - set(manifest.get("secrets", [])))
    if missing:
        issues.append(Issue("invalid_manifest", pointer("secrets"), f"the workflow's steps need {', '.join(missing)}, so the step's secrets must include them", file=file))
    return issues
