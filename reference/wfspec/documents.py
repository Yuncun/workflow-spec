"""Reading YAML and JSON files, the spec's JSON Schemas, and catalogs of step manifests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, best_match
from referencing import Registry, Resource

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


class _Loader(yaml.SafeLoader):
    """A safe YAML loader that rejects a repeated key instead of keeping the last value."""


def _mapping(loader: _Loader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise ParseError(f"line {key_node.start_mark.line + 1}: the key {key!r} appears twice")
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ParseError(f"the key {key!r} appears twice")
        out[key] = value
    return out


def load_document(path: Path):
    """Parse a .json file as JSON and anything else as YAML."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        if Path(path).suffix == ".json":
            return json.loads(text, object_pairs_hook=_unique_pairs)
        return yaml.load(text, Loader=_Loader)  # a SafeLoader subclass
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ParseError(str(exc)) from exc


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
    for key in ("inputSchema", "outputSchema"):
        try:
            Draft202012Validator.check_schema(doc[key])
        except SchemaError as exc:
            issues.append(Issue("invalid_manifest", pointer(key, *exc.path), f"not a valid JSON Schema: {exc.message}", file=file))
            continue
        declared = doc[key]["properties"]
        for name in doc[key].get("required", []):
            if name not in declared:
                issues.append(Issue("invalid_manifest", pointer(key, "required"), f"{name!r} is required but not declared in properties", file=file))
    if not any(i.path.startswith("/inputSchema") for i in issues):
        for name, schema in doc["inputSchema"]["properties"].items():
            if isinstance(schema, dict) and "default" in schema:
                problem = value_problem(schema, schema["default"])
                if problem:
                    issues.append(Issue("invalid_manifest", pointer("inputSchema", "properties", name, "default"), f"the default doesn't fit the input: {problem}", file=file))
    if doc["effects"] != "writes":
        for flag in ("idempotent", "destructive"):
            if flag in doc:
                issues.append(Issue("invalid_manifest", pointer(flag), f"{flag!r} only applies to steps whose effects are 'writes'", file=file))
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
