"""Whether a value described by one JSON Schema can be passed where another is expected.

SPEC.md, section 6.3. The checker proves shapes and leaves limits it can't prove to run time:
- an error means the source can produce a value of the wrong shape for the target;
- a warning means the shapes fit, but a limit on the target (a range, a pattern, a list of
  allowed values...) can't be proven from the source's schema, so the value is checked when
  the step runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import rfc8785

from .documents import media_type_fits

FILE_REF = "urn:wfspec:file"

_ANNOTATIONS = frozenset({
    "title", "description", "examples", "default", "deprecated", "readOnly", "writeOnly",
    "$comment", "$schema", "$id", "$anchor",
    "format",  # an annotation: neither checked nor enforced (SPEC.md, [types.format])
})
_SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "dependentSchemas")
_SCHEMA_VALUES = (
    "items", "additionalProperties", "not", "if", "then", "else", "contains",
    "propertyNames", "unevaluatedProperties", "unevaluatedItems",
)
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_COMPOSITION = frozenset({
    "anyOf", "oneOf", "allOf", "not", "if", "then", "else", "dependentSchemas", "dependentRequired",
    "prefixItems", "contains", "patternProperties", "propertyNames", "unevaluatedProperties",
    "unevaluatedItems",
})
# Each limit an input can set, with the source limits that prove it: (source keyword, holds).
# An exclusive limit proves an inclusive one at the same value, and an inclusive limit proves
# an exclusive one only when it is strictly inside it.
_LIMITS = {
    "minimum": (("minimum", lambda s, d: s >= d), ("exclusiveMinimum", lambda s, d: s >= d)),
    "exclusiveMinimum": (("exclusiveMinimum", lambda s, d: s >= d), ("minimum", lambda s, d: s > d)),
    "maximum": (("maximum", lambda s, d: s <= d), ("exclusiveMaximum", lambda s, d: s <= d)),
    "exclusiveMaximum": (("exclusiveMaximum", lambda s, d: s <= d), ("maximum", lambda s, d: s < d)),
    "minLength": (("minLength", lambda s, d: s >= d),),
    "maxLength": (("maxLength", lambda s, d: s <= d),),
    "minItems": (("minItems", lambda s, d: s >= d),),
    "maxItems": (("maxItems", lambda s, d: s <= d),),
    "minProperties": (("minProperties", lambda s, d: s >= d),),
    "maxProperties": (("maxProperties", lambda s, d: s <= d),),
}
_HANDLED = frozenset({"type", "properties", "required", "additionalProperties", "items", "$ref", "x-mediaType", "enum", "const"}) | _LIMITS.keys()


@dataclass(frozen=True)
class Finding:
    error: bool
    where: str  # place inside the value, e.g. "summary.body"; "" is the value itself
    message: str


def same(a, b) -> bool:
    """JSON equality: true and 1 differ, 1 and 1.0 don't (Python's == says the opposite of the first)."""
    return _json_text(a) == _json_text(b)


def _json_text(value) -> bytes:
    try:
        return rfc8785.dumps(value)
    except Exception:  # numbers outside the range RFC 8785 covers
        return json.dumps(value, sort_keys=True).encode()


def canonical(schema):
    """The schema without annotations or x- keywords (except x-mediaType), at every level."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in _ANNOTATIONS or (key.startswith("x-") and key != "x-mediaType"):
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            value = {name: canonical(s) for name, s in value.items()}
        elif key in _SCHEMA_VALUES:
            value = canonical(value)
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            value = [canonical(s) for s in value]
        out[key] = value
    return out


def assignable(src, dst, where: str = "") -> list[Finding]:
    """Findings for passing a value described by `src` to an input described by `dst`."""
    src, dst = canonical(src), canonical(dst)
    if dst is True or dst == {}:
        return []
    if same(src, dst):
        return []
    if dst is False:
        return [Finding(True, where, "the input accepts no value")]
    if src is True or src == {}:
        return [Finding(False, where, "the source doesn't declare a type")]

    if _is_file(src) or _is_file(dst):
        return _files(src, dst, where)
    if "$ref" in src or "$ref" in dst:
        # Files that pass the manifest and workflow rules never get here (SPEC.md, [manifest.refs]); this keeps a
        # direct caller with other schemas from getting a wrong "fits".
        return [Finding(False, where, "the checker doesn't follow $ref, other than to files")]
    if any(k in dst for k in _COMPOSITION):
        return [Finding(False, where, "the input's schema combines schemas (anyOf, oneOf...), which the checker doesn't compare")]

    findings: list[Finding] = []
    want = _types(dst)
    have = _types(src) or _types_of_values(src)
    if want is not None:
        if have is None:
            findings.append(Finding(False, where, "the source doesn't declare a type"))
        else:
            wrong = sorted(t for t in have if not _accepts(want, t))
            if wrong:
                return [Finding(True, where, f"{_names(wrong)} can't be given where {_names(sorted(want))} is expected")]

    findings += _allowed_values(src, dst, where)
    unproven = _unproven_limits(src, dst)
    if unproven:
        noun = "limit" if len(unproven) == 1 else "limits"
        findings.append(Finding(False, where, f"can't prove the input's {_names(unproven, 'and')} {noun}; declare the same {noun} on the source to prove {'it' if noun == 'limit' else 'them'}"))
    if _may_be(dst, "object") and _may_be(src, "object"):
        findings += _objects(src, dst, where)
    if _may_be(dst, "array") and _may_be(src, "array") and "items" in dst:
        inner = _join(where, "[]")
        if "items" in src:
            findings += assignable(src["items"], dst["items"], inner)
        else:
            findings.append(Finding(False, inner, "the source doesn't declare its item type"))
    return findings


def _is_file(schema: dict) -> bool:
    return isinstance(schema, dict) and schema.get("$ref") == FILE_REF


def _files(src: dict, dst: dict, where: str) -> list[Finding]:
    if not _is_file(src):
        return [Finding(True, where, "a file is expected here")]
    if not _is_file(dst):
        extra = dst.get("additionalProperties", True)
        if _may_be(dst, "object") and not dst.get("required") and not dst.get("properties") and extra in (True, {}):
            return []  # an object schema that accepts any properties accepts a file value
        return [Finding(True, where, f"a file can't be given where {_names(sorted(_types(dst) or ['this value']))} is expected")]
    have, want = src.get("x-mediaType"), dst.get("x-mediaType")
    if want is None or have == want:
        return []
    if have is None:
        return [Finding(False, where, f"the input expects {want} files and the source doesn't say what kind it gives")]
    if media_type_fits(have, want):
        return []
    return [Finding(True, where, f"a {have} file can't be given where {want} is expected")]


def _types(schema: dict) -> set[str] | None:
    t = schema.get("type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return set(t)
    return None


def _types_of_values(schema: dict) -> set[str] | None:
    values = _values(schema)
    if values is None:
        return None
    return {_type_of(v) for v in values}


def _type_of(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _accepts(want: set[str], t: str) -> bool:
    return t in want or (t == "integer" and "number" in want)


def _may_be(schema: dict, t: str) -> bool:
    types = _types(schema)
    return types is None or t in types or (t == "number" and "integer" in types)


def _values(schema: dict) -> list | None:
    if "const" in schema:
        return [schema["const"]]
    if "enum" in schema:
        return list(schema["enum"])
    return None


def _allowed_values(src: dict, dst: dict, where: str) -> list[Finding]:
    allowed = _values(dst)
    if allowed is None:
        return []
    produced = _values(src)
    if produced is None:
        return [Finding(False, where, f"the input allows only {_show(allowed)} and the source doesn't limit its values")]
    outside = [v for v in produced if not any(same(v, a) for a in allowed)]
    if outside:
        return [Finding(True, where, f"the source can produce {_show(outside)}, which the input doesn't allow")]
    return []


def _unproven_limits(src: dict, dst: dict) -> list[str]:
    unproven = []
    for key, provers in _LIMITS.items():
        if key in dst and not any(p in src and holds(src[p], dst[key]) for p, holds in provers):
            unproven.append(key)
    for key in sorted(set(dst) - _HANDLED - _COMPOSITION):
        if not same(src.get(key), dst[key]):
            unproven.append(key)
    return unproven


def _objects(src: dict, dst: dict, where: str) -> list[Finding]:
    findings = []
    have_props = src.get("properties", {})
    have_required = set(src.get("required", []))
    declared = "properties" in src or "required" in src
    for name in dst.get("required", []):
        if name in have_required:
            continue
        if declared:
            findings.append(Finding(True, _join(where, name), "required here, but the source may leave it out"))
        else:
            findings.append(Finding(False, _join(where, name), "required here, and the source doesn't declare its properties"))
    for name, schema in dst.get("properties", {}).items():
        if name in have_props:
            findings += assignable(have_props[name], schema, _join(where, name))
    if dst.get("additionalProperties") is False:
        extra = sorted(set(have_props) - set(dst.get("properties", {})))
        if extra:
            findings.append(Finding(True, where, f"the source can include {_names(extra, 'and')}, which the input doesn't allow"))
        elif src.get("additionalProperties") is not False:
            findings.append(Finding(False, where, "the input allows no extra properties and the source doesn't rule them out"))
    return findings


def _join(where: str, part: str) -> str:
    if part == "[]":
        return f"each item of {where}" if where else "each item"
    return f"{where}.{part}" if where else part


def _names(items, joiner: str = "or") -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {joiner} " + items[-1]


def _show(values: list) -> str:
    return ", ".join(json.dumps(v) for v in values)
