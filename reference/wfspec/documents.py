"""Reading YAML and JSON files, the spec's JSON Schemas, and catalogs of step manifests."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.error import MarkedYAMLError, YAMLError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
EFFECT_ORDER = {"none": 0, "reads": 1, "writes": 2}
FILE_REF = "urn:wfspec:file"
# Schema keywords that point elsewhere or name a part of a schema. Version 0.1 allows only
# {"$ref": "urn:wfspec:file"} (SPEC.md, section 4.1).
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


def _json_only(node, path: tuple = ()) -> None:
    """Refuse what JSON can't hold: keys that aren't text, and NaN or infinite numbers."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise ParseError(f"at {pointer(*path) or '/'}: the key {key!r} must be text; put it in quotes")
            _json_only(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _json_only(value, (*path, i))
    elif isinstance(node, float) and not math.isfinite(node):
        raise ParseError(f"at {pointer(*path) or '/'}: {node} isn't a JSON number")


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

    `format` stays an annotation, as in JSON Schema 2020-12 by default (SPEC.md, section 4.1)."""
    if isinstance(schema, str):
        schema = REGISTRY.contents(schema)
    return Draft202012Validator(schema, registry=REGISTRY)


def schema_issues(doc, schema_id: str, code: str, file: str | None = None) -> list[Issue]:
    """One issue per place where `doc` breaks the spec schema `schema_id`."""
    errors = sorted(validator(schema_id).iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path])
    return [Issue(code, pointer(*e.absolute_path), e.message, file=file) for e in errors]


def value_problem(schema: dict, value) -> str | None:
    """Why `value` doesn't satisfy `schema`, or None if it does."""
    try:
        error = best_match(validator(schema).iter_errors(value))
    except Unresolvable as exc:
        return f"the schema refers to something that can't be found: {exc}"
    return None if error is None else error.message


def reference_issues(schema, path: tuple) -> list[tuple[str, str]]:
    """(pointer, message) for each reference keyword other than {"$ref": "urn:wfspec:file"}."""
    found = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in _REFERENCE_KEYWORDS and not (key == "$ref" and value == FILE_REF):
                found.append((pointer(*path, key), f"{key} isn't allowed in version 0.1; only {{\"$ref\": \"{FILE_REF}\"}} is"))
            else:
                found += reference_issues(value, (*path, key))
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            found += reference_issues(value, (*path, i))
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
    """Problems with one step manifest on its own (SPEC.md, section 3.4)."""
    issues = schema_issues(doc, "urn:wfspec:schema:step", "invalid_manifest", file)
    if issues:
        return issues

    def problem(path: str, message: str) -> None:
        issues.append(Issue("invalid_manifest", path, message, file=file))

    usable = set()  # the schemas that are valid, so values can be checked against them
    for key in ("inputSchema", "outputSchema"):
        try:
            Draft202012Validator.check_schema(doc[key])
        except SchemaError as exc:
            problem(pointer(key, *exc.path), f"not a valid JSON Schema: {exc.message}")
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
    if doc["effects"] != "writes":
        for flag in ("idempotent", "destructive"):
            if flag in doc:
                problem(pointer(flag), f"{flag!r} only applies to steps whose effects are 'writes'")
    return issues


def agent_tool_issues(manifest: dict, catalog: Catalog, file: str | None = None) -> list[Issue]:
    """An agent may call only catalog steps, and declares at least their effects and secrets."""
    agent = manifest["runtime"].get("agent")
    if not agent:
        return []
    issues = []
    secrets = set(manifest.get("secrets", []))
    for i, tool in enumerate(agent.get("tools", [])):
        target = catalog.steps.get(tool)
        if target is None:
            issues.append(Issue("unknown_step", pointer("runtime", "agent", "tools", i), f"the agent's tool {tool} isn't in the catalog", file=file))
            continue
        if EFFECT_ORDER[target["effects"]] > EFFECT_ORDER[manifest["effects"]]:
            issues.append(Issue(
                "invalid_manifest",
                pointer("effects"),
                f"the agent can call {tool}, whose effects are {target['effects']!r}, so its own effects must be at least that",
                file=file,
            ))
        missing = sorted(set(target.get("secrets", [])) - secrets)
        if missing:
            issues.append(Issue(
                "invalid_manifest",
                pointer("secrets"),
                f"the agent can call {tool}, which needs {', '.join(missing)}, so its secrets must include them",
                file=file,
            ))
    return issues


def load_catalog(dirs: list[Path]) -> Catalog:
    """Load every *.yaml, *.yml and *.json manifest in the given folders (not recursive).

    A manifest with problems is left out, so no workflow can use it."""
    catalog = Catalog()
    for folder in dirs:
        paths = sorted(p for p in Path(folder).iterdir() if p.suffix in (".yaml", ".yml", ".json"))
        for path in paths:
            file = str(path)
            try:
                doc = load_document(path)
            except ParseError as exc:
                catalog.problems.append(Issue("parse_error", "", str(exc), file=file))
                continue
            issues = manifest_issues(doc, file)
            ref = _ref_of(doc)
            if issues:
                if ref and ref not in catalog.steps:
                    catalog.leave_out(ref, issues)
                else:
                    catalog.problems.extend(issues)
                continue
            if ref in catalog.steps:
                catalog.problems.append(Issue("duplicate_step", "", f"{ref} is also defined in {catalog.files[ref]}", file=file))
                continue
            catalog.steps[ref] = doc
            catalog.files[ref] = file
    # Leaving an agent out can leave another agent without a tool, so repeat until nothing changes.
    changed = True
    while changed:
        changed = False
        for ref, manifest in list(catalog.steps.items()):
            issues = agent_tool_issues(manifest, catalog, catalog.files[ref])
            if issues:
                catalog.leave_out(ref, issues)
                changed = True
    return catalog


def _ref_of(doc) -> str | None:
    if isinstance(doc, dict) and isinstance(doc.get("name"), str) and isinstance(doc.get("version"), str):
        return f"{doc['name']}@{doc['version']}"
    return None
