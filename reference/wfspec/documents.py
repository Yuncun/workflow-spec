"""Reading YAML and JSON files, the spec's JSON Schemas, and catalogs of step manifests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, best_match
from referencing import Registry, Resource
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.error import MarkedYAMLError, YAMLError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
EFFECT_ORDER = {"none": 0, "reads": 1, "writes": 2}


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
    """The file isn't valid YAML or JSON, or it repeats a key."""


def pointer(*parts: object) -> str:
    """A JSON Pointer (RFC 6901) built from path segments."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


class _Constructor(SafeConstructor):
    """YAML 1.2 values, kept JSON-compatible: a date or time stays the text that was written."""


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


def _text_keys(node, path: tuple = ()) -> None:
    """JSON allows only text keys; YAML also allows numbers and other values."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise ParseError(f"at {pointer(*path) or '/'}: the key {key!r} must be text; put it in quotes")
            _text_keys(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _text_keys(value, (*path, i))


def load_document(path: Path):
    """Parse a .json file as JSON and anything else as YAML 1.2."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        if Path(path).suffix == ".json":
            return json.loads(text, object_pairs_hook=_unique_pairs)
        doc = _yaml().load(text)
    except json.JSONDecodeError as exc:
        raise ParseError(str(exc)) from exc
    except MarkedYAMLError as exc:
        where = f"line {exc.problem_mark.line + 1}: " if exc.problem_mark else ""
        raise ParseError(where + (exc.problem or str(exc))) from exc
    except YAMLError as exc:
        raise ParseError(str(exc)) from exc
    _text_keys(doc)
    return doc


def _registry() -> Registry:
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


REGISTRY = _registry()
_FORMATS = FormatChecker()


def validator(schema: dict | str) -> Draft202012Validator:
    """A validator for an inline schema, or for one of the spec's schemas given by its $id."""
    if isinstance(schema, str):
        schema = REGISTRY.contents(schema)
    return Draft202012Validator(schema, registry=REGISTRY, format_checker=_FORMATS)


def schema_issues(doc, schema_id: str, code: str, file: str | None = None) -> list[Issue]:
    """One issue per place where `doc` breaks the spec schema `schema_id`."""
    errors = sorted(validator(schema_id).iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path])
    return [Issue(code, pointer(*e.absolute_path), e.message, file=file) for e in errors]


def value_problem(schema: dict, value) -> str | None:
    """Why `value` doesn't satisfy `schema`, or None if it does."""
    error = best_match(validator(schema).iter_errors(value))
    return None if error is None else error.message


@dataclass
class Catalog:
    """The published steps a workflow can use, keyed by name@version."""

    steps: dict[str, dict] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    problems: list[Issue] = field(default_factory=list)

    def versions(self, name: str) -> list[str]:
        found = [ref.split("@", 1)[1] for ref in self.steps if ref.split("@", 1)[0] == name]
        return sorted(found, key=_version_key)

    def names(self) -> list[str]:
        return sorted({ref.split("@", 1)[0] for ref in self.steps})


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
        declared = doc[key]["properties"]
        undeclared = [name for name in doc[key].get("required", []) if name not in declared]
        for name in undeclared:
            problem(pointer(key, "required"), f"{name!r} is required but not declared in properties")
        if not undeclared:
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


def load_catalog(dirs: list[Path]) -> Catalog:
    """Load every *.yaml, *.yml and *.json manifest in the given folders (not recursive)."""
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
            if issues:
                catalog.problems.extend(issues)
                continue
            ref = f"{doc['name']}@{doc['version']}"
            if ref in catalog.steps:
                catalog.problems.append(Issue("duplicate_step", "", f"{ref} is also defined in {catalog.files[ref]}", file=file))
                continue
            catalog.steps[ref] = doc
            catalog.files[ref] = file
    _check_agent_tools(catalog)
    return catalog


def _check_agent_tools(catalog: Catalog) -> None:
    """An agent may only call catalog steps, and declares at least the effects of what it can call."""
    for ref, manifest in catalog.steps.items():
        agent = manifest["runtime"].get("agent")
        if not agent:
            continue
        file = catalog.files[ref]
        for i, tool in enumerate(agent.get("tools", [])):
            target = catalog.steps.get(tool)
            if target is None:
                catalog.problems.append(Issue("unknown_step", pointer("runtime", "agent", "tools", i), f"the agent's tool {tool} isn't in the catalog", file=file))
            elif EFFECT_ORDER[target["effects"]] > EFFECT_ORDER[manifest["effects"]]:
                catalog.problems.append(Issue(
                    "invalid_manifest",
                    pointer("effects"),
                    f"the agent can call {tool}, whose effects are {target['effects']!r}, so its own effects must be at least that",
                    file=file,
                ))
