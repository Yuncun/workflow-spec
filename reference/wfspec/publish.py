"""Publishing helpers: digests, cache keys, lock files and the manifest a workflow gets when it
is published as a step (SPEC.md, sections 9.4 and 10), and MCP tool definitions (drafts/mcp.md)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import rfc8785

from .check import check, closure
from .documents import EFFECT_ORDER, FILE_REF, REGISTRY, Catalog, load_document
from .documents import mcp_name as tool_name

_FILE_SCHEMA = {k: v for k, v in REGISTRY.contents(FILE_REF).items() if k not in ("$schema", "$id", "title", "description")}


def canonical_json(doc) -> bytes:
    """RFC 8785 (JSON Canonicalization Scheme): the bytes every digest is computed over."""
    return rfc8785.dumps(doc)


def digest(doc) -> str:
    """sha256 of a document's canonical JSON. A platform also folds in built artifacts, such as an image digest."""
    return "sha256:" + hashlib.sha256(canonical_json(doc)).hexdigest()


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
            target = Path(catalog.files[ref]).parent / runtime["workflow"]["path"]
            if not target.is_file():
                raise ValueError(f"{ref}: there is no workflow file at {target}")
            inner = load_document(target)
            stack.extend(step["uses"] for step in inner["steps"].values())
    return seen


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
    steps = workflow["steps"]
    manifests = {sid: catalog.steps[steps[sid]["uses"]] for sid in result.needed}
    effects = max((m["effects"] for m in manifests.values()), key=EFFECT_ORDER.__getitem__, default="none")

    def always_present(source) -> bool:
        # A value is always there when its source always gives it and nothing upstream can skip.
        if source.optional:
            return False
        if source.step is None:
            return True
        return not any(manifests[s].get("maySkip", False) for s in closure({source.step}, result.deps))

    manifest: dict = {"name": workflow["name"], "version": workflow["version"]}
    if "title" in workflow:
        manifest["title"] = workflow["title"]
    manifest.update({
        "description": workflow["description"],
        "inputSchema": workflow.get("inputSchema", {"type": "object", "properties": {}}),
        "outputSchema": {
            "type": "object",
            "properties": {name: source.schema for name, source in result.outputs.items()},
            "required": [name for name, source in result.outputs.items() if always_present(source)],
        },
        "effects": effects,
        "deterministic": bool(manifests) and all(m.get("deterministic", False) for m in manifests.values()),
    })
    secrets = sorted({s for m in manifests.values() for s in m.get("secrets", [])})
    if secrets:
        manifest["secrets"] = secrets
    manifest["runtime"] = {"workflow": {"path": path}}
    return manifest


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
