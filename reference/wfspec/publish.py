"""Publishing helpers (SPEC.md, sections 8 and 9): content digests, lock files, the manifest a
workflow gets when it is published as a step, and MCP tool definitions."""

from __future__ import annotations

import hashlib
import json

from .check import check
from .documents import EFFECT_ORDER, REGISTRY, Catalog
from .types import FILE_REF

_FILE_SCHEMA = {k: v for k, v in REGISTRY.contents(FILE_REF).items() if k not in ("$schema", "$id", "title", "description")}


def canonical_json(doc) -> bytes:
    """Sorted keys, no spaces, UTF-8: the bytes a digest is computed over."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(doc) -> str:
    """sha256 of a document's canonical JSON. A platform also folds in built artifacts, such as an image digest."""
    return "sha256:" + hashlib.sha256(canonical_json(doc)).hexdigest()


def lock(workflow: dict, catalog: Catalog) -> dict:
    """The lock file for a workflow: the digest of every step version it uses."""
    uses = sorted({step["uses"] for step in workflow["steps"].values()})
    missing = [u for u in uses if u not in catalog.steps]
    if missing:
        raise ValueError("not in the catalog: " + ", ".join(missing))
    out: dict = {"lockVersion": 1}
    if workflow.get("name") and workflow.get("version"):
        out["workflow"] = f"{workflow['name']}@{workflow['version']}"
    out["steps"] = {u: {"digest": digest(catalog.steps[u])} for u in uses}
    return out


def as_step(workflow: dict, catalog: Catalog, path: str) -> dict:
    """The manifest a checked workflow gets when it is published, so other workflows can use it."""
    result = check(workflow, catalog)
    if not result.ok:
        raise ValueError("the workflow has errors; run `wfspec check` first")
    missing = [key for key in ("name", "version", "description") if not workflow.get(key)]
    if missing:
        raise ValueError("a published workflow needs " + ", ".join(missing))
    manifests = [catalog.steps[workflow["steps"][sid]["uses"]] for sid in result.needed]
    effects = max((m["effects"] for m in manifests), key=EFFECT_ORDER.__getitem__, default="none")
    manifest: dict = {"name": workflow["name"], "version": workflow["version"]}
    if "title" in workflow:
        manifest["title"] = workflow["title"]
    manifest.update({
        "description": workflow["description"],
        "inputSchema": workflow.get("inputSchema", {"type": "object", "properties": {}}),
        "outputSchema": {
            "type": "object",
            "properties": {name: source.schema for name, source in result.outputs.items()},
            "required": [name for name, source in result.outputs.items() if not source.optional],
        },
        "effects": effects,
        "deterministic": bool(manifests) and all(m.get("deterministic", False) for m in manifests),
    })
    if effects == "writes":
        writers = [m for m in manifests if m["effects"] == "writes"]
        manifest["idempotent"] = all(m.get("idempotent", False) for m in writers)
        manifest["destructive"] = any(m.get("destructive", True) for m in writers)
    secrets = sorted({s for m in manifests for s in m.get("secrets", [])})
    if secrets:
        manifest["secrets"] = secrets
    manifest["runtime"] = {"workflow": {"path": path}}
    return manifest


def mcp_tool(manifest: dict) -> dict:
    """The MCP tool definition for a step (SPEC.md, section 9.1)."""
    effects = manifest["effects"]
    if effects == "writes":
        hints = {
            "readOnlyHint": False,
            "destructiveHint": manifest.get("destructive", True),
            "idempotentHint": manifest.get("idempotent", False),
            "openWorldHint": True,
        }
    else:
        hints = {"readOnlyHint": True, "openWorldHint": effects == "reads"}
    tool = {"name": manifest["name"].replace("/", ".")}
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
