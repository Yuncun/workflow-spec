"""Publishing helpers: digests, cache keys, lock files and the manifest a workflow gets when it
is published as a step (SPEC.md, sections 9.4 and 10), and MCP tool definitions (drafts/mcp.md)."""

from __future__ import annotations

from pathlib import Path

from .check import check, closure
from .documents import _DATA_KEYWORDS, EFFECT_ORDER, FILE_REF, REGISTRY, Catalog, Issue, ParseError, canonical_json, digest, load_document, pointer
from .documents import mcp_name as tool_name

__all__ = ["canonical_json", "digest"]  # re-exported from documents, where the checker also needs them

_FILE_SCHEMA = {k: v for k, v in REGISTRY.contents(FILE_REF).items() if k not in ("$schema", "$id", "title", "description")}


def cache_key(manifest: dict, inputs: dict) -> str:
    """The cache key of one step call (SPEC.md, [run.cache-key]).

    `inputs` are the values the step receives, defaults filled in. Each file value counts only
    by its digest and media type, so a file's name doesn't change the key."""
    return digest({"step": digest(manifest), "inputs": _file_identities(inputs, manifest["inputSchema"])})


def _file_identities(value, schema):
    if not isinstance(schema, dict):
        return value
    if schema.get("$ref") == FILE_REF and isinstance(value, dict):
        return {k: value[k] for k in ("digest", "mediaType") if k in value}
    if isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: _file_identities(v, props.get(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_file_identities(v, schema.get("items")) for v in value]
    return value


def lock(workflow: dict, catalog: Catalog) -> dict:
    """The lock file for a workflow: the digest of every step version it can run, directly or not.

    That includes the steps its steps can call and the steps inside workflows it uses as steps
    (SPEC.md, [publish.lock])."""
    refs = _all_refs([step["uses"] for step in workflow["steps"].values()], catalog)
    out: dict = {"lockVersion": 1}
    if workflow.get("name") and workflow.get("version"):
        out["workflow"] = f"{workflow['name']}@{workflow['version']}"
    out["steps"] = {ref: {"digest": digest(catalog.steps[ref])} for ref in sorted(refs)}
    return out


def _all_refs(start: list[str], catalog: Catalog) -> set[str]:
    seen: set[str] = set()
    stack = list(start)
    while stack:
        ref = stack.pop()
        if ref in seen:
            continue
        manifest = catalog.steps.get(ref)
        if manifest is None:
            raise ValueError(f"not in the catalog: {ref}")
        seen.add(ref)
        stack.extend(manifest.get("calls", []))
        runtime = manifest["runtime"]
        if "workflow" in runtime:
            inner = pinned_workflow(Path(catalog.files[ref]).parent / runtime["workflow"]["path"], runtime["workflow"]["digest"])
            if inner is not None:  # a file changed since the catalog was loaded fails its step when it runs ([run.pinned])
                stack.extend(step["uses"] for step in inner["steps"].values())
    return seen


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
    missing = [key for key in ("name", "version", "description") if not workflow.get(key)]
    if missing:
        raise ValueError("a published workflow needs " + ", ".join(missing))
    manifest: dict = {"name": workflow["name"], "version": workflow["version"]}
    if "title" in workflow:
        manifest["title"] = workflow["title"]
    manifest["description"] = workflow["description"]
    derived = _derived(workflow, catalog, result)
    manifest.update({k: derived[k] for k in ("inputSchema", "outputSchema", "effects", "deterministic")})
    if derived["secrets"]:
        manifest["secrets"] = derived["secrets"]
    manifest["runtime"] = {"workflow": {"path": path, "digest": digest(workflow)}}
    return manifest


def _derived(workflow: dict, catalog: Catalog, result) -> dict:
    """The interface and behaviour a checked workflow has as a step (SPEC.md, [publish.as-step])."""
    steps = workflow["steps"]
    manifests = {sid: catalog.steps[steps[sid]["uses"]] for sid in result.needed}

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
    """A step may not be able to run itself, through calls or the workflow it runs (SPEC.md, [manifest.no-self])."""
    issues = []
    for i, callee in enumerate(manifest.get("calls", [])):
        if callee == ref or ref in _reachable(callee, catalog):
            issues.append(Issue("invalid_manifest", pointer("calls", i), f"{callee} can lead back to {ref}, so the step could run itself", file=file))
    runtime = manifest["runtime"].get("workflow")
    if runtime and file is not None:
        inner = load_document(Path(file).parent / runtime["path"])
        starts = [step["uses"] for step in inner.get("steps", {}).values()]
        if any(uses == ref or ref in _reachable(uses, catalog) for uses in starts):
            issues.append(Issue("invalid_manifest", pointer("runtime", "workflow", "path"), f"the workflow can run {ref} itself", file=file))
    return issues


def _reachable(start: str, catalog: Catalog) -> set[str]:
    """Every step version `start` can run, through calls and workflow steps, not counting itself."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        manifest = catalog.steps.get(stack.pop())
        if manifest is None:
            continue
        nexts = list(manifest.get("calls", []))
        runtime = manifest["runtime"].get("workflow")
        if runtime:
            ref = f"{manifest['name']}@{manifest['version']}"
            try:
                inner = load_document(Path(catalog.files[ref]).parent / runtime["path"])
                nexts += [step["uses"] for step in inner.get("steps", {}).values()]
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
    derived, issues = _derived(inner, catalog, result), []
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


def mcp_name(manifest: dict) -> str:
    """The MCP tool name of a step (drafts/mcp.md, section 1)."""
    return tool_name(manifest["name"], manifest["version"])


def mcp_tool(manifest: dict) -> dict:
    """The MCP tool definition for a step (drafts/mcp.md, section 1).

    For steps that write, destructiveHint and idempotentHint are left to MCP's defaults:
    destructive, and not idempotent."""
    effects = manifest["effects"]
    hints = {"readOnlyHint": effects != "writes", "openWorldHint": effects != "none"}
    tool = {"name": mcp_name(manifest)}
    if "title" in manifest:
        tool["title"] = manifest["title"]
    tool.update({
        "description": manifest["description"],
        "inputSchema": _inline_files(manifest["inputSchema"]),
        "outputSchema": _inline_files(manifest["outputSchema"]),
        "annotations": hints,
    })
    return tool


def _inline_files(schema):
    """Replace {"$ref": "urn:wfspec:file"} with the file schema itself, for clients that can't resolve it."""
    if isinstance(schema, dict):
        if schema.get("$ref") == FILE_REF:
            rest = {k: v for k, v in schema.items() if k != "$ref"}
            return {**_FILE_SCHEMA, **rest}
        return {k: _inline_files(v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_inline_files(v) for v in schema]
    return schema
