"""Reading YAML and JSON files, the spec's JSON Schemas, and catalogs of step manifests."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
import rfc8785
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.error import MarkedYAMLError, YAMLError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
EFFECT_ORDER = {"none": 0, "reads": 1, "writes": 2}
FILE_REF = "urn:wfspec:file"
# Schema keywords that point elsewhere or name a part of a schema. Only {"$ref": "urn:wfspec:file"}
# is allowed (SPEC.md, section 6.1, [manifest.refs]).
_REFERENCE_KEYWORDS = ("$ref", "$dynamicRef", "$defs", "definitions", "$id", "$anchor", "$dynamicAnchor")


@dataclass(frozen=True)
class Issue:
    """One problem a check found. `code` never changes meaning; `message` is for people."""

    code: str
    path: str  # JSON Pointer into the checked file; "" is the whole file
    message: str
    step: str | None = None
    input: str | None = None
    file: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


class ParseError(Exception):
    """The file isn't valid YAML 1.2 or JSON, or holds something JSON can't."""


def pointer(*parts: object) -> str:
    """A JSON Pointer (RFC 6901) built from path segments."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


class _Constructor(SafeConstructor):
    """YAML 1.2 values, kept JSON-compatible: a date or time stays the text that was written,
    and merge keys (<<, a YAML 1.1 feature) are refused."""

    def flatten_mapping(self, node):
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise ParseError(f"line {key_node.start_mark.line + 1}: merge keys (<<) aren't allowed; write the values out")
        return super().flatten_mapping(node)


_Constructor.add_constructor("tag:yaml.org,2002:timestamp", SafeConstructor.construct_yaml_str)


def _yaml() -> YAML:
    # YAML 1.2, so yes, no, on and off are text (YAML 1.1 made them booleans).
    # ruamel.yaml also rejects a repeated key rather than keeping the last value.
    yaml = YAML(typ="safe", pure=True)
    yaml.Constructor = _Constructor
    return yaml


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ParseError(f"the key {key!r} appears twice")
        out[key] = value
    return out


SAFE_INTEGER = 2**53 - 1  # I-JSON (RFC 7493): whole numbers every JSON parser reads exactly


def _json_only(node, path: tuple = ()) -> None:
    """Refuse what I-JSON (RFC 7493) can't hold, since digests use RFC 8785, which needs it:
    keys that aren't text, the key "<<", NaN, infinities, and whole numbers beyond 2**53 - 1."""
    where = pointer(*path) or "/"
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise ParseError(f"at {where}: the key {key!r} must be text; put it in quotes")
            if key == "<<":
                raise ParseError(f"at {where}: the key '<<' isn't allowed; YAML 1.1 parsers read it as a merge")
            _json_only(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _json_only(value, (*path, i))
    elif isinstance(node, float) and not math.isfinite(node):
        raise ParseError(f"at {where}: {node} isn't a JSON number")
    elif isinstance(node, int) and not isinstance(node, bool) and abs(node) > SAFE_INTEGER:
        raise ParseError(f"at {where}: {node} is beyond ±(2**53 - 1), which not every JSON parser reads exactly")
    elif node is not None and not isinstance(node, (str, bool, int, float)):
        raise ParseError(f"at {where}: a {type(node).__name__} value, such as YAML's !!binary or !!set, isn't one JSON can hold")


def load_document(path: Path):
    """Parse a .json file as JSON and anything else as YAML 1.2."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        if Path(path).suffix == ".json":
            doc = json.loads(text, object_pairs_hook=_unique_pairs)
        else:
            doc = _yaml().load(text)
    except json.JSONDecodeError as exc:
        raise ParseError(str(exc)) from exc
    except MarkedYAMLError as exc:
        where = f"line {exc.problem_mark.line + 1}: " if exc.problem_mark else ""
        raise ParseError(where + (exc.problem or str(exc))) from exc
    except YAMLError as exc:
        raise ParseError(str(exc)) from exc
    _json_only(doc)
    return doc


def _registry() -> Registry:
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


REGISTRY = _registry()


def validator(schema: dict | str) -> Draft202012Validator:
    """A validator for an inline schema, or for one of the spec's schemas given by its $id.

    `format` stays an annotation, as in JSON Schema 2020-12 by default (SPEC.md, [types.format])."""
    if isinstance(schema, str):
        schema = REGISTRY.contents(schema)
    return Draft202012Validator(schema, registry=REGISTRY)


def schema_issues(doc, schema_id: str, code: str, file: str | None = None) -> list[Issue]:
    """One issue per location in `doc` where validation against the spec schema `schema_id` fails
    (SPEC.md, Annex A.5), so the count doesn't depend on how many keywords a validator reports."""
    errors = sorted(validator(schema_id).iter_errors(doc), key=lambda e: ([str(p) for p in e.absolute_path], e.message))
    first: dict[str, str] = {}
    for e in errors:
        first.setdefault(pointer(*e.absolute_path), e.message)
    return [Issue(code, where, message, file=file) for where, message in first.items()]


def value_problem(schema: dict, value) -> str | None:
    """Why `value` doesn't satisfy `schema`, or None if it does."""
    try:
        error = best_match(validator(schema).iter_errors(value))
    except Unresolvable as exc:
        return f"the schema refers to something that can't be found: {exc}"
    return None if error is None else error.message


MCP_NAME_LIMIT = 64


def mcp_name(name: str, version: str) -> str:
    """owner_name_v<major>: letters, digits, _ and -, which MCP and model tool APIs all accept.

    Only an owner can contain _, so the parts read back from the right: the version, then the
    step's name, and what is left is the owner. Names over 64 characters
    are cut and given 8 hex characters of the full name's SHA-256, so they stay unique."""
    owner, short = name.split("/")
    full = f"{owner}_{short}_v{version.split('.')[0]}"
    if len(full) <= MCP_NAME_LIMIT:
        return full
    return full[: MCP_NAME_LIMIT - 9] + "_" + hashlib.sha256(full.encode()).hexdigest()[:8]


_DATA_KEYWORDS = ("default", "const", "enum", "examples")  # their values are data, not schemas
_NAMED_SCHEMAS = ("properties", "patternProperties", "dependentSchemas")  # names map to schemas


def reference_issues(schema, path: tuple) -> list[tuple[str, str]]:
    """(pointer, message) for each reference keyword other than {"$ref": "urn:wfspec:file"}
    (SPEC.md, [manifest.refs]). Data such as a default, and property names, are not looked at."""
    found = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in _REFERENCE_KEYWORDS and not (key == "$ref" and value == FILE_REF):
                found.append((pointer(*path, key), f"{key} isn't allowed; the only reference allowed is {{\"$ref\": \"{FILE_REF}\"}}"))
            elif key in _DATA_KEYWORDS or key.startswith("x-"):
                continue
            elif key in _NAMED_SCHEMAS and isinstance(value, dict):
                for name, sub in value.items():
                    found += reference_issues(sub, (*path, key, name))
            else:
                found += reference_issues(value, (*path, key))
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            found += reference_issues(value, (*path, i))
    return found


def canonical_json(doc) -> bytes:
    """RFC 8785 (JSON Canonicalization Scheme): the bytes every digest is computed over."""
    return rfc8785.dumps(doc)


def digest(doc) -> str:
    """sha256 of a document's canonical JSON (SPEC.md, [publish.digest])."""
    return "sha256:" + hashlib.sha256(canonical_json(doc)).hexdigest()


def media_type_fits(actual: str, declared: str) -> bool:
    """Whether a file of type `actual` fits an input that declares `declared`, which may end in /*."""
    actual, declared = actual.lower(), declared.lower()
    if declared in ("*/*", actual):
        return True
    return declared.endswith("/*") and actual.split("/")[0] == declared[:-2]


def media_type_problems(value, schema, path: tuple = ()) -> list[str]:
    """Each file value whose mediaType doesn't fit the x-mediaType its schema declares (SPEC.md, [types.media-type])."""
    if not isinstance(schema, dict):
        return []
    if schema.get("$ref") == FILE_REF:
        declared, actual = schema.get("x-mediaType"), value.get("mediaType", "") if isinstance(value, dict) else ""
        if declared and isinstance(value, dict) and not media_type_fits(actual, declared):
            return [f"{pointer(*path) or 'the value'} is a {actual or 'untyped'} file, where {declared} is expected"]
        return []
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            found += media_type_problems(v, schema.get("properties", {}).get(k), (*path, k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found += media_type_problems(v, schema.get("items"), (*path, i))
    return found


@dataclass
class Catalog:
    """The published steps a workflow can use, keyed by name@version."""

    steps: dict[str, dict] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    problems: list[Issue] = field(default_factory=list)
    invalid: dict[str, str] = field(default_factory=dict)  # name@version -> why it was left out

    def versions(self, name: str) -> list[str]:
        found = [ref.split("@", 1)[1] for ref in self.steps if ref.split("@", 1)[0] == name]
        return sorted(found, key=_version_key)

    def names(self) -> list[str]:
        return sorted({ref.split("@", 1)[0] for ref in self.steps})

    def leave_out(self, ref: str, issues: list[Issue]) -> None:
        self.problems.extend(issues)
        self.invalid[ref] = issues[0].message
        self.steps.pop(ref, None)


def _version_key(version: str) -> tuple:
    core, _, pre = version.partition("-")
    return (*(int(n) for n in core.split(".")), pre == "", pre)


def manifest_issues(doc, file: str | None = None) -> list[Issue]:
    """Problems with one step manifest on its own (SPEC.md, section 5)."""
    issues = schema_issues(doc, "urn:wfspec:schema:step", "invalid_manifest", file)
    if issues:
        return issues

    def problem(path: str, message: str) -> None:
        issues.append(Issue("invalid_manifest", path, message, file=file))

    usable = set()  # the schemas that are valid, so values can be checked against them
    for key in ("inputSchema", "outputSchema"):
        try:
            Draft202012Validator.check_schema(doc[key])
        except SchemaError as exc:  # once, at the schema (SPEC.md, [check.paths])
            problem(pointer(key), f"not a valid JSON Schema, at {pointer(key, *exc.path)}: {exc.message}")
            continue
        references = reference_issues(doc[key], (key,))
        for path, message in references:
            problem(path, message)
        declared = doc[key]["properties"]
        undeclared = [name for name in doc[key].get("required", []) if name not in declared]
        for name in undeclared:
            problem(pointer(key, "required"), f"{name!r} is required but not declared in properties")
        if not references and not undeclared:
            usable.add(key)
    if "inputSchema" in usable:
        for name, schema in doc["inputSchema"]["properties"].items():
            if isinstance(schema, dict) and "default" in schema:
                why = value_problem(schema, schema["default"])
                if why:
                    problem(pointer("inputSchema", "properties", name, "default"), f"the default doesn't fit the input: {why}")
    for n, example in enumerate(doc.get("examples", [])):
        for part, key in (("inputs", "inputSchema"), ("outputs", "outputSchema")):
            if part in example and key in usable:
                why = value_problem(doc[key], example[part])
                if why:
                    problem(pointer("examples", n, part), f"the example's {part} don't fit {key}: {why}")
    seen: dict[str, str] = {}
    for i, callee in enumerate(doc.get("calls", [])):
        name = mcp_name(*callee.split("@"))
        if name in seen:
            problem(pointer("calls", i), f"{callee} and {seen[name]} have the same major version; list one version per major version")
        seen.setdefault(name, callee)
    return issues


def workflow_runtime_issues(manifest: dict, file: str | None) -> list[Issue]:
    """A workflow step's file must exist next to its manifest, be a workflow, and have the digest the
    manifest pins (SPEC.md, [manifest.workflow-file]). Whether it passes checking needs the catalog:
    see publish.workflow_step_issues."""
    runtime = manifest["runtime"].get("workflow")
    if not runtime or file is None:
        return []
    where = pointer("runtime", "workflow", "path")
    target = Path(file).parent / runtime["path"]
    if not target.resolve().is_relative_to(Path(file).parent.resolve()):
        return [Issue("invalid_manifest", where, f"{runtime['path']} isn't inside the manifest's folder", file=file)]
    if not target.is_file():
        return [Issue("invalid_manifest", where, f"there is no workflow file at {target}", file=file)]
    try:
        doc = load_document(target)
    except ParseError as exc:
        return [Issue("invalid_manifest", where, f"{target} can't be read: {exc}", file=file)]
    if schema_issues(doc, "urn:wfspec:schema:workflow", "invalid_workflow"):
        return [Issue("invalid_manifest", where, f"{target} isn't a valid workflow; check it with wfspec check", file=file)]
    if digest(doc) != runtime["digest"]:
        return [Issue("invalid_manifest", pointer("runtime", "workflow", "digest"),
                      f"{target} has the digest {digest(doc)}, not the one the manifest pins", file=file)]
    return []


def calls_issues(manifest: dict, catalog: Catalog, file: str | None = None) -> list[Issue]:
    """A step may call only catalog steps, and declares at least their effects and secrets (SPEC.md, section 5.4)."""
    issues = []
    secrets = set(manifest.get("secrets", []))
    for i, callee in enumerate(manifest.get("calls", [])):
        target = catalog.steps.get(callee)
        if target is None:
            issues.append(Issue("unknown_step", pointer("calls", i), f"{callee}, which this step can call, isn't in the catalog", file=file))
            continue
        if EFFECT_ORDER[target["effects"]] > EFFECT_ORDER[manifest["effects"]]:
            issues.append(Issue(
                "invalid_manifest",
                pointer("effects"),
                f"this step can call {callee}, whose effects are {target['effects']!r}, so its own effects must be at least that",
                file=file,
            ))
        missing = sorted(set(target.get("secrets", [])) - secrets)
        if missing:
            issues.append(Issue(
                "invalid_manifest",
                pointer("secrets"),
                f"this step can call {callee}, which needs {', '.join(missing)}, so its secrets must include them",
                file=file,
            ))
    return issues


def load_catalog(dirs: list[Path]) -> Catalog:
    """Load every *.yaml, *.yml and *.json manifest in the given folders (not recursive).

    A manifest with problems is left out, so no workflow can use it."""
    catalog = Catalog()
    seen: dict[str, str] = {}  # name@version -> the file that first had it, valid or not
    for folder in dirs:
        paths = sorted(p for p in Path(folder).iterdir() if p.suffix in (".yaml", ".yml", ".json"))
        for path in paths:
            file = str(path)
            try:
                doc = load_document(path)
            except ParseError as exc:
                catalog.problems.append(Issue("parse_error", "", str(exc), file=file))
                continue
            ref = _ref_of(doc)
            if ref in seen:  # [manifest.duplicate]: the first one read is kept, valid or not
                catalog.problems.append(Issue("duplicate_step", "", f"{ref} is also defined in {seen[ref]}", file=file))
                continue
            if ref:
                seen[ref] = file
            issues = manifest_issues(doc, file) or workflow_runtime_issues(doc, file)
            if issues:
                if ref:
                    catalog.leave_out(ref, issues)
                else:
                    catalog.problems.extend(issues)
                continue
            catalog.steps[ref] = doc
            catalog.files[ref] = file
    # Rules that need the catalog: calls, and the workflows inside workflow steps. Leaving one step
    # out can break another, so repeat until nothing changes ([manifest.left-out]).
    changed = True
    while changed:
        changed = False
        for ref, manifest in list(catalog.steps.items()):
            issues = catalog_issues(ref, manifest, catalog, catalog.files[ref])
            if issues:
                catalog.leave_out(ref, issues)
                changed = True
    return catalog


def catalog_issues(ref: str, manifest: dict, catalog: Catalog, file: str | None) -> list[Issue]:
    """The rules for one manifest that need the rest of its catalog: what it calls, whether it can
    run itself, and the workflow a workflow step runs (SPEC.md, [manifest.invalid])."""
    from .publish import self_reach_issues, workflow_step_issues  # publish imports check, which imports this module

    return (calls_issues(manifest, catalog, file) + self_reach_issues(ref, manifest, catalog, file)
            + workflow_step_issues(manifest, catalog, file))  # every problem of the stage (SPEC.md, Annex A.3)


def check_step(path: Path, dirs: list[Path]) -> list[Issue]:
    """What loading the manifest in `path` into a catalog of the manifests in `dirs` would report
    about it, the same rules in the same order as load_catalog."""
    file = str(path)
    doc = load_document(path)
    found = manifest_issues(doc, file) or workflow_runtime_issues(doc, file)
    if found:
        return found
    catalog = load_catalog(dirs)
    ref = _ref_of(doc)
    catalog.steps[ref], catalog.files[ref] = doc, file
    return catalog_issues(ref, doc, catalog, file)


def _ref_of(doc) -> str | None:
    if isinstance(doc, dict) and isinstance(doc.get("name"), str) and isinstance(doc.get("version"), str):
        return f"{doc['name']}@{doc['version']}"
    return None
