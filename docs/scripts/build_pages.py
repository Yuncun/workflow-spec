"""Write the site's pages to docs/.generated from the repository's own files: python docs/scripts/build_pages.py"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / ".generated"
REPOSITORY = "https://github.com/Yuncun/workflow-spec"
REF = os.environ.get("DOCS_SOURCE_REF", "main")

HOME = ("README.md", "index.md")

# Where each Markdown file of the repository appears on the site, in sidebar order.
# A group nested in a section is a (label, [(source, page), ...]) pair. A (source, "#anchor")
# pair links to a heading inside a page placed elsewhere, under that heading's own text.
SECTIONS = [
    ("Specification", [
        ("SPEC.md", "spec.md"),
        ("Drafts", [
            ("drafts/runtime.md", "drafts/runtime.md"),
            ("drafts/mcp.md", "drafts/mcp.md"),
        ]),
        ("proposals.md", "proposals.md"),
        ("CHANGELOG.md", "changelog.md"),
    ]),
    ("Guides", [
        ("guides/write-a-workflow.md", "guides/write-a-workflow.md"),
        ("guides/publish-a-step.md", "guides/publish-a-step.md"),
    ]),
    ("Reference", "generated"),
    ("Project", [
        ("HANDOFF.md", "adopting.md"),
        ("CONTRIBUTING.md", "contributing.md"),
    ]),
]

# The folders whose Markdown files are documents rather than test data.
DOCUMENT_GLOBS = ["*.md", "guides/*.md", "drafts/*.md"]

REQUIREMENT_ID = r"[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+"
DEFINITION = re.compile(rf"\*\*\[({REQUIREMENT_ID})\]\*\*")
CITATION = re.compile(rf"(?<![*\w\[])\[({REQUIREMENT_ID})\](?![(\]])")
LINK = re.compile(r"(?<!!)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(([^)\s]+)\)")
KEY_WORD = re.compile(r"\b(MUST NOT|MUST|SHALL NOT|SHALL|SHOULD NOT|SHOULD|NOT RECOMMENDED|RECOMMENDED|REQUIRED|MAY|OPTIONAL)\b")
FENCE = re.compile(r"^(```|~~~)[^\n]*\n.*?^\1[ \t]*$", re.S | re.M)

yaml = YAML(typ="safe")


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- helpers


def slugify(text: str) -> str:
    """A heading's anchor as GitHub makes it; docs/.vitepress/config.mts gives VitePress the same rule."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"\s", "-", text)


def heading_text(line: str) -> str:
    """The text of a Markdown heading line, as VitePress slugs it: without markup."""
    text = re.sub(r"^#+\s*", "", line).strip()
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.replace("*", "")


def outside_code(text: str, change) -> str:
    """Apply change() to the parts of Markdown text that aren't code blocks or code spans."""
    out, at = [], 0
    for fence in FENCE.finditer(text):
        out.append(_outside_spans(text[at:fence.start()], change))
        out.append(fence.group(0))
        at = fence.end()
    out.append(_outside_spans(text[at:], change))
    return "".join(out)


def _outside_spans(text: str, change) -> str:
    parts = re.split(r"(`+[^`]*?`+)", text)
    return "".join(part if i % 2 else change(part) for i, part in enumerate(parts))


def headings_and_ids(text: str) -> set[str]:
    """Every anchor a page will have: heading slugs and requirement ids."""
    anchors = set(DEFINITION.findall(text))
    plain = FENCE.sub("", text)
    for line in plain.splitlines():
        if re.match(r"^#{1,6}\s", line):
            anchors.add(slugify(heading_text(line)))
    return anchors


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return heading_text(line)
    raise BuildError("a page has no title")


def github_url(repo_path: str) -> str:
    kind = "tree" if (ROOT / repo_path).is_dir() else "blob"
    return f"{REPOSITORY}/{kind}/{REF}/{repo_path}"


def relative(page: str, target_page: str) -> str:
    rel = os.path.relpath(target_page, os.path.dirname(page) or ".")
    return rel if rel.startswith(".") else "./" + rel


def plain(text: str) -> str:
    """Text from a schema or a YAML file, made safe for a page."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("{{", "&#123;&#123;"))


def table_cell(text: str) -> str:
    return plain(text).replace("|", "\\|").replace("\n", " ")


def front_matter(**fields) -> str:
    lines = ["---"] + [f"{k}: {json.dumps(v)}" for k, v in fields.items()] + ["---", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- copied pages


def copied_pages() -> list[tuple[str, str]]:
    pages = [HOME]
    for _, items in SECTIONS:
        if items == "generated":
            continue
        for item in items:
            if isinstance(item[1], list):
                pages.extend(item[1])
            elif not item[1].startswith("#"):
                pages.append(item)
    return pages


def is_file_name(label: str, written: str, repo_path: str) -> bool:
    """Whether a link's text is only the file name it links to, such as [SPEC.md](SPEC.md)."""
    label = label.strip("`")
    return label in (written, repo_path, os.path.basename(repo_path))


def check_every_document_placed(pages: list[tuple[str, str]]) -> None:
    placed = {source for source, _ in pages}
    found = {str(p.relative_to(ROOT)) for pattern in DOCUMENT_GLOBS for p in ROOT.glob(pattern)}
    missing = sorted(found - placed)
    if missing:
        raise BuildError(
            "these documents have no place on the site; add them to SECTIONS in "
            f"docs/scripts/build_pages.py: {', '.join(missing)}")


def copy_page(source: str, page: str, page_of: dict[str, str], anchors: dict[str, set[str]],
              defined: set[str], titles: dict[str, str] | None) -> str:
    text = (ROOT / source).read_text()
    spec_page = page_of["SPEC.md"]

    def link(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):
            return match.group(0)
        path, _, anchor = target.partition("#")
        if not path:
            if anchor and anchor not in anchors[page]:
                raise BuildError(f"{source}: link to #{anchor}, which this page doesn't have")
            return match.group(0)
        repo_path = os.path.normpath(os.path.join(os.path.dirname(source), path))
        if repo_path in page_of:
            target_page = page_of[repo_path]
            if anchor and anchor not in anchors[target_page]:
                raise BuildError(f"{source}: link to {target}, but {repo_path} has no heading or id '{anchor}'")
            if titles and not anchor and is_file_name(label, path, repo_path):
                label = titles[target_page]
            return f"[{label}]({relative(page, target_page)}{'#' + anchor if anchor else ''})"
        if not (ROOT / repo_path).exists():
            raise BuildError(f"{source}: link to {target}, which doesn't exist")
        return f"[{label}]({github_url(repo_path)}{'#' + anchor if anchor else ''})"

    def requirement(match: re.Match) -> str:
        rid = match.group(1)
        if rid not in defined:
            raise BuildError(f"{source}: cites [{rid}], which SPEC.md doesn't define")
        here = "" if page == spec_page else relative(page, spec_page)
        return f"[[{rid}]]({here}#{rid})"

    def definition(match: re.Match) -> str:
        rid = match.group(1)
        return f'<a id="{rid}"></a>[**[{rid}]**](#{rid})'

    def change(part: str) -> str:
        part = LINK.sub(link, part)
        # "drafts/mcp.md: steps as MCP tools" would read "Steps as MCP tools: steps as MCP tools".
        part = part if not titles else re.sub(r"(\[([^\]]+)\]\([^)\s]+\)): ([^|\n]+?)(?=\s*(\||$))",
                      lambda m: m.group(1) if m.group(3).strip().lower() == m.group(2).lower() else m.group(0),
                      part, flags=re.M)
        part = CITATION.sub(requirement, part)  # a definition, **[id]**, isn't a citation
        if page == spec_page:
            part = DEFINITION.sub(definition, part)
        return part

    body = outside_code(text, change)
    return front_matter(editUrl=f"{REPOSITORY}/edit/{REF}/{source}") + body


# ---------------------------------------------------------------- requirements


def requirement_index(spec: str) -> list[dict]:
    """Each requirement id in SPEC.md, in order, with its section, key word and text."""
    found, chapter, section, table_header = [], "", None, None
    lines = spec.splitlines()
    in_fence = False
    for i, line in enumerate(lines):
        if re.match(r"^(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## "):
            chapter, section = heading_text(line), None
        elif line.startswith("### "):
            section = heading_text(line)
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s|:-]+\|$", lines[i + 1]):
            table_header = line
        elif not line.startswith("|"):
            table_header = None
        for rid in DEFINITION.findall(line):
            words = KEY_WORD.findall(line.split(f"**[{rid}]**", 1)[1])
            if not words and line.startswith("|") and table_header:
                words = KEY_WORD.findall(table_header)
            text = line
            for follow in lines[i + 1:]:
                if not follow.startswith("  ") or follow.lstrip().startswith("- **["):
                    break
                text += "\n" + follow
            found.append({"id": rid, "chapter": chapter, "section": section, "word": words[0] if words else "",
                          "text": text})
    return found


def test_link(test: str) -> str:
    if "::" in test:
        file, function = test.split("::")
        path = f"reference/tests/{file}"
        source = (ROOT / path).read_text().splitlines()
        line = next((n for n, l in enumerate(source, 1) if l.startswith(f"def {function}(")), None)
        if line is None:
            raise BuildError(f"tests/coverage.yaml names {test}, which doesn't exist")
        return f"[`{test}`]({REPOSITORY}/blob/{REF}/{path}#L{line})"
    if not (ROOT / "tests" / test).is_dir():
        raise BuildError(f"tests/coverage.yaml names {test}, which doesn't exist")
    return f"[`{test}`]({github_url('tests/' + test)})"


def requirements_page(requirements: list[dict], spec_page: str) -> str:
    coverage = yaml.load((ROOT / "tests" / "coverage.yaml").read_text())
    page = "reference/requirements.md"
    untested = [r for r in requirements if isinstance(coverage.get(r["id"]), dict)]
    out = [front_matter(editLink=False),
           "# Requirements and tests", "",
           f"Generated from the requirement ids in [SPEC.md]({relative(page, spec_page)}) and "
           f"[tests/coverage.yaml]({github_url('tests/coverage.yaml')}).", "",
           "| Requirements | With tests | Without tests |", "|---|---|---|",
           f"| {len(requirements)} | {len(requirements) - len(untested)} | {len(untested)} |", ""]
    chapter = section = None
    table_open = False
    for r in requirements:
        if r["chapter"] != chapter:
            chapter, section, table_open = r["chapter"], None, False
            out += ["", f"## {chapter}", ""]
        if r["section"] != section:
            section, table_open = r["section"], False
            out += ["", f"### {section}", ""]
        if not table_open:
            out += ["| Requirement | Key word | Tests |", "|---|---|---|"]
            table_open = True
        entry = coverage.get(r["id"])
        if entry is None:
            raise BuildError(f"tests/coverage.yaml has no entry for [{r['id']}]")
        tests = (f"untested: {table_cell(str(entry['untested']))}" if isinstance(entry, dict)
                 else "<br>".join(test_link(t) for t in entry))
        out.append(f"| [`{r['id']}`]({relative(page, spec_page)}#{r['id']}) | {r['word'] or '—'} | {tests} |")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- codes


def expected_codes() -> dict[str, list[str]]:
    """Each code that a test case's expected.yaml names, with those cases."""
    found: dict[str, list[str]] = {}

    def walk(value, case):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("code", "error") and isinstance(item, str):
                    found.setdefault(item, [])
                    if case not in found[item]:
                        found[item].append(case)
                walk(item, case)
        elif isinstance(value, list):
            for item in value:
                walk(item, case)

    for expected in sorted((ROOT / "tests").glob("*/*/expected.yaml")):
        case = f"{expected.parent.parent.name}/{expected.parent.name}"
        walk(yaml.load(expected.read_text()), case)
    return found


def codes_page(spec: str, requirements: list[dict], spec_page: str) -> str:
    page = "reference/codes.md"
    lines = FENCE.sub("", spec).splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r"^\|\s*Code\s*\|\s*Level\s*\|", line)), None)
    if start is None:
        raise BuildError("SPEC.md has no table of codes headed | Code | Level |")
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    run_schema = json.loads((ROOT / "schemas" / "run.schema.json").read_text())
    run_codes = run_schema["properties"]["error"]["properties"]["code"]["enum"]
    step_codes = run_schema["$defs"]["stepRun"]["properties"]["error"]["properties"]["code"]["enum"]
    cases = expected_codes()

    def naming(code: str) -> str:
        ids = [r["id"] for r in requirements if f"`{code}`" in r["text"]]
        return ", ".join(f"[`{rid}`]({relative(page, spec_page)}#{rid})" for rid in ids) or "—"

    def case_links(code: str) -> str:
        return ", ".join(f"[`{c}`]({github_url('tests/' + c)})" for c in cases.get(code, [])) or "None."

    out = [front_matter(editLink=False),
           "# Error and warning codes", "",
           f"Generated from [SPEC.md]({relative(page, spec_page)}), section 8.2, "
           f"[schemas/run.schema.json]({github_url('schemas/run.schema.json')}) and the test cases' "
           f"expected results in [tests/]({github_url('tests')}).", "",
           "## Checking", "",
           "| Code | Level | Requirements that name it |", "|---|---|---|"]
    check_codes = [row[0].strip("`") for row in rows]
    for code, level, _ in rows:
        bare = code.strip("`")
        out.append(f"| [{code}](#{slugify(bare)}) | {level} | {naming(bare)} |")
    out += ["", "## Running", "",
            "| Code | In a run's error | In a step's error | Requirements that name it |", "|---|---|---|---|"]
    run_all = list(dict.fromkeys(run_codes + step_codes))
    for code in run_all:
        out.append(f"| [`{code}`](#{slugify(code)}) | {'yes' if code in run_codes else '—'} "
                   f"| {'yes' if code in step_codes else '—'} | {naming(code)} |")
    out += ["", "## Test cases for each code", ""]
    for code in check_codes + run_all:
        out += [f"### `{code}`", "", case_links(code), ""]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- schemas


SCHEMA_PAGES = [
    ("step.schema.json", "reference/schemas/step.md"),
    ("workflow.schema.json", "reference/schemas/workflow.md"),
    ("file.schema.json", "reference/schemas/file.md"),
    ("lock.schema.json", "reference/schemas/lock.md"),
    ("run.schema.json", "reference/schemas/run.md"),
    ("policy.schema.json", "reference/schemas/policy.md"),
]

# The section of SPEC.md whose "| Field | Required | Meaning |" table describes a schema's
# fields. The build fails when the table and the schema don't list the same fields, or don't
# agree on which are required.
FIELD_TABLES = {"step.schema.json": "Step manifest", "workflow.schema.json": "Workflow file"}

SCHEMA_IDS: dict[str, tuple[str, str]] = {}


def code(text) -> str:
    return "`" + str(text).replace("`", "'") + "`"


def schema_pages_by_id() -> dict[str, tuple[str, str]]:
    """Each schema's $id, with its page and title."""
    found = {}
    for file, page in SCHEMA_PAGES:
        schema = json.loads((ROOT / "schemas" / file).read_text())
        found[schema["$id"]] = (page, schema.get("title", file))
    return found


def reference_link(ref: str, page: str) -> str:
    """A $ref as a link: to a definition on this page, or to another schema's page."""
    base, _, pointer = ref.partition("#")
    if base:
        if base not in SCHEMA_IDS:
            return code(ref)
        target, title = SCHEMA_IDS[base]
        href = relative(page, target)
    else:
        target, title, href = page, "", ""
    parts = pointer.strip("/").split("/") if pointer else []
    if len(parts) >= 2 and parts[0] == "$defs":
        label = "/".join(parts[1:]) if base == "" else f"{title}: {'/'.join(parts[1:])}"
        return f"[{label}]({href}#{slugify(parts[1])})"
    if pointer:
        return code(ref)
    return f"[{title}]({href})"


def type_of(schema, page: str) -> str:
    if schema is True or schema == {}:
        return "any"
    if schema is False:
        return "nothing"
    parts = []
    ref = schema.get("$ref")
    if ref:
        parts.append(reference_link(ref, page))
    if "const" in schema:
        parts.append("the value " + code(json.dumps(schema["const"])))
    if "enum" in schema:
        parts.append(" or ".join(code(json.dumps(v)) for v in schema["enum"]))
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema:
            parts.append(" or ".join(type_of(s, page) for s in schema[keyword]))
    kinds = schema.get("type")
    if kinds and "enum" not in schema and "const" not in schema:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        named = []
        for kind in kinds:
            if kind == "array" and isinstance(schema.get("items"), (dict, bool)):
                named.append(f"array of {type_of(schema['items'], page)}")
            elif kind == "object" and not schema.get("properties") and isinstance(schema.get("additionalProperties"), dict) \
                    and schema["additionalProperties"]:
                named.append(f"object of {type_of(schema['additionalProperties'], page)}")
            else:
                named.append(kind)
        parts.append(" or ".join(named))
    if not parts:
        parts.append("any")
    limits = []
    for keyword in ("pattern", "format", "minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum",
                    "minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        if keyword in schema:
            limits.append(f"{keyword} {code(schema[keyword])}")
    if schema.get("uniqueItems"):
        limits.append("unique items")
    if "x-mediaType" in schema:
        limits.append(f"x-mediaType {code(schema['x-mediaType'])}")
    return "; ".join([" or ".join(parts)] + limits)


def spec_sections(spec: str) -> dict[str, str]:
    """Each numbered section of SPEC.md, such as "5.2", with its heading's anchor."""
    found = {}
    for line in FENCE.sub("", spec).splitlines():
        match = re.match(r"^#{2,3}\s+(\d+(?:\.\d+)*)\.?\s", line)
        if match:
            found[match.group(1)] = slugify(heading_text(line))
    return found


def spec_field_tables(spec: str) -> dict[str, dict[str, tuple[str, str]]]:
    """SPEC.md's "| Field | Required | Meaning |" tables, by the title of the section they're in."""
    tables: dict[str, dict[str, tuple[str, str]]] = {}
    lines = FENCE.sub("", spec).splitlines()
    chapter = ""
    for i, line in enumerate(lines):
        heading = re.match(r"^##\s+\d+\.\s+(.*)$", line)
        if heading:
            chapter = heading_text(heading.group(1))
        if not re.match(r"^\|\s*Field\s*\|\s*Required\s*\|\s*Meaning\s*\|", line):
            continue
        fields = tables.setdefault(chapter, {})
        for row in lines[i + 2:]:
            if not row.startswith("|"):
                break
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            for name in cells[0].split(","):
                fields[name.strip().strip("`")] = (cells[1], cells[2])
    return tables


def spec_cell(text: str, page: str, sections: dict[str, str]) -> str:
    """A cell copied from SPEC.md, with its "section N" mentions linked to those sections."""
    spec = relative(page, "spec.md")

    def link(match: re.Match) -> str:
        number = match.group(2)
        if number not in sections:
            raise BuildError(f"SPEC.md's field table mentions section {number}, which SPEC.md doesn't have")
        return f"[{match.group(1)}]({spec}#{sections[number]})"

    return re.sub(r"\b((?:section|Section) (\d+(?:\.\d+)*))\b", link, text)


def field_rows(schema: dict, page: str, meanings: dict[str, str] | None = None, prefix: str = "",
               depth: int = 0) -> list[tuple[str, str, str, str]]:
    rows = []
    required = set(schema.get("required", []))
    for name, sub in (schema.get("properties") or {}).items():
        path = f"{prefix}{name}"
        sub = sub if isinstance(sub, dict) else {}
        description = (meanings or {}).get(name) or table_cell(sub.get("description", ""))
        rows.append((code(path), table_cell(type_of(sub, page)), "yes" if name in required else "no", description))
        if depth < 3:
            if sub.get("type") == "object" and sub.get("properties"):
                rows += field_rows(sub, page, None, path + ".", depth + 1)
            items = sub.get("items")
            if sub.get("type") == "array" and isinstance(items, dict) and items.get("properties"):
                rows += field_rows(items, page, None, path + "[].", depth + 1)
    for pattern in schema.get("patternProperties") or {}:
        rows.append((f"fields matching {code(pattern)}",
                     table_cell(type_of(schema["patternProperties"][pattern], page)), "no", ""))
    return rows


def field_table(rows: list[tuple[str, str, str, str]]) -> list[str]:
    """A table of fields; the Description column is left out when no field has one."""
    if any(row[3] for row in rows):
        return ["| Field | Type | Required | Description |", "|---|---|---|---|",
                *(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows), ""]
    return ["| Field | Type | Required |", "|---|---|---|", *(f"| {a} | {b} | {c} |" for a, b, c, _ in rows), ""]


def meanings_from_spec(file: str, schema: dict, page: str, spec: str) -> tuple[dict[str, str], str]:
    """The Meaning cells of the SPEC.md table for this schema, checked against the schema."""
    title = FIELD_TABLES.get(file)
    if not title:
        return {}, ""
    table = spec_field_tables(spec).get(title)
    if not table:
        raise BuildError(f"SPEC.md has no | Field | Required | Meaning | table in a section titled '{title}'")
    declared = {name for name in schema.get("properties", {}) if name != "$schema"}
    if set(table) != declared:
        raise BuildError(
            f"schemas/{file} and the field table in SPEC.md, section '{title}', list different fields: "
            f"only in the schema {sorted(declared - set(table))}, only in SPEC.md {sorted(set(table) - declared)}")
    required = set(schema.get("required", []))
    for name, (needed, _) in table.items():
        if needed.startswith("yes") != (name in required):
            raise BuildError(f"schemas/{file} and SPEC.md, section '{title}', disagree on whether "
                             f"'{name}' is required")
    sections = spec_sections(spec)
    number = next((n for n, slug in sections.items() if slug.endswith(slugify(title)) and "." not in n), None)
    if number is None:
        raise BuildError(f"SPEC.md has no numbered section titled '{title}'")
    meanings = {name: spec_cell(meaning, page, sections) for name, (_, meaning) in table.items()}
    source = f"[SPEC.md, section {number}]({relative(page, 'spec.md')}#{sections[number]})"
    return meanings, source


def schema_page(file: str, page: str, spec: str) -> str:
    schema = json.loads((ROOT / "schemas" / file).read_text())
    meanings, meanings_source = meanings_from_spec(file, schema, page, spec)
    sources = f"[schemas/{file}]({github_url('schemas/' + file)})"
    if meanings_source:
        sources += f", with the descriptions of its fields from {meanings_source}"
    out = [front_matter(editLink=False),
           f"# {schema.get('title', file)}", "",
           f"Generated from {sources}.", ""]
    if schema.get("description"):
        out += [plain(schema["description"]), ""]
    out += [f"`$id`: {code(schema.get('$id', ''))}", ""]
    rows = field_rows(schema, page, meanings)
    if rows:
        out += ["## Fields", "", *field_table(rows)]
    definitions = schema.get("$defs") or {}
    if definitions:
        out += ["## Definitions", ""]
        for name, sub in definitions.items():
            out += [f"### {name}", ""]
            if sub.get("description"):
                out += [plain(sub["description"]), ""]
            out += [f"Type: {type_of({k: v for k, v in sub.items() if k != 'description'}, page)}", ""]
            rows = field_rows(sub, page)
            if rows:
                out += field_table(rows)
    out += ["## Schema", "", "```json", (ROOT / "schemas" / file).read_text().rstrip(), "```", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- command line


def cli_page() -> str:
    env = {**os.environ, "COLUMNS": "100", "NO_COLOR": "1", "PYTHON_COLORS": "0"}

    def help_of(*args: str) -> str:
        result = subprocess.run([sys.executable, "-m", "wfspec.cli", *args, "--help"], capture_output=True,
                                text=True, env=env, cwd=ROOT)
        if result.returncode != 0:
            raise BuildError(f"wfspec {' '.join(args)} --help failed: {result.stderr.strip()}")
        return result.stdout.rstrip()

    top = help_of()
    commands = re.search(r"\{([a-z,-]+)\}", top).group(1).split(",")
    out = [front_matter(editLink=False), "# wfspec", "",
           f"Generated from the help of the `wfspec` command in [reference/]({github_url('reference')}).", "",
           "```text", top, "```", ""]
    for command in commands:
        out += [f"## wfspec {command}", "", "```text", help_of(command), "```", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- examples


def examples_page() -> str:
    groups = [("Steps", "examples/steps/*.yaml"), ("Workflows", "examples/workflows/*.yaml"),
              ("Run records", "examples/runs/*.json"), ("Lock files", "examples/*.lock.json")]
    out = [front_matter(editLink=False), "# Examples", "",
           f"Generated from [examples/]({github_url('examples')}).", ""]
    for title, pattern in groups:
        out += [f"## {title}", ""]
        for path in sorted(ROOT.glob(pattern)):
            rel = str(path.relative_to(ROOT))
            language = "json" if path.suffix == ".json" else "yaml"
            out += [f"### {path.name}", "", f"[{rel}]({github_url(rel)})", "",
                    f"```{language}", path.read_text().rstrip(), "```", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- site


def build() -> None:
    SCHEMA_IDS.update(schema_pages_by_id())
    pages = copied_pages()
    check_every_document_placed(pages)
    page_of = dict(pages)
    texts = {source: (ROOT / source).read_text() for source, _ in pages}
    anchors = {page: headings_and_ids(texts[source]) for source, page in pages}
    spec = texts["SPEC.md"]
    defined = set(DEFINITION.findall(spec))
    requirements = requirement_index(spec)
    spec_page = page_of["SPEC.md"]

    written: dict[str, str] = {}
    written["reference/requirements.md"] = requirements_page(requirements, spec_page)
    written["reference/codes.md"] = codes_page(spec, requirements, spec_page)
    for file, page in SCHEMA_PAGES:
        written[page] = schema_page(file, page, spec)
    written["reference/cli.md"] = cli_page()
    written["reference/examples.md"] = examples_page()

    # Each page's title on the site: its first heading, and for SPEC.md its version.
    label_of = {page: first_heading(texts[source]) for source, page in pages}
    version = re.search(r"^Version ([0-9.]+)", spec, re.M)
    label_of[spec_page] = f"Specification {version.group(1)}" if version else "Specification"
    label_of.update({page: first_heading(text) for page, text in written.items()})

    def route(page: str) -> str:
        return "/" + page.removesuffix(".md").removesuffix("index")

    def item(source: str, target: str) -> dict:
        if target.startswith("#"):
            anchor = target[1:]
            heading = next((heading_text(line) for line in FENCE.sub("", texts[source]).splitlines()
                            if re.match(r"^#{1,6}\s", line) and slugify(heading_text(line)) == anchor), None)
            if heading is None:
                raise BuildError(f"SECTIONS names {source}{target}, a heading {source} doesn't have")
            return {"text": heading, "link": route(page_of[source]) + target}
        return {"text": label_of[target], "link": route(target)}

    sidebar, nav = [], []
    for title, items in SECTIONS:
        if items == "generated":
            entries = [item("", "reference/requirements.md"), item("", "reference/codes.md"),
                       {"text": "Schemas", "collapsed": False,
                        "items": [item("", page) for _, page in SCHEMA_PAGES]},
                       item("", "reference/cli.md"), item("", "reference/examples.md")]
        else:
            entries = []
            for entry in items:
                if isinstance(entry[1], list):
                    entries.append({"text": entry[0], "collapsed": False,
                                    "items": [item(s, p) for s, p in entry[1]]})
                else:
                    entries.append(item(*entry))
        sidebar.append({"text": title, "items": entries})
        first_page = next(e for e in entries if "link" in e and "#" not in e["link"])
        nav.append({"text": title, "link": first_page["link"]})

    # Only the home page shows page titles in place of file names: it lists the documents, while
    # the documents themselves name each other by file name in their running text.
    for source, page in pages:
        titles = label_of if source == "README.md" else None
        written[page] = copy_page(source, page, page_of, anchors, defined, titles)

    # The home page is the README in VitePress's home layout: its title becomes the large
    # heading, with a button for each section that starts somewhere to read.
    readme = texts["README.md"]
    home_page = page_of["README.md"]
    body = written[home_page].split("---\n", 2)[2]
    body = re.sub(r"\A\s*# .*\n", "", body)
    actions = [{"theme": "brand" if i == 0 else "alt", "text": n["text"], "link": n["link"]}
               for i, n in enumerate(nav[:3])]
    written[home_page] = front_matter(layout="home", hero={"name": first_heading(readme), "actions": actions}) + body

    if OUT.exists():
        shutil.rmtree(OUT)
    for page, text in written.items():
        target = OUT / page
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    description = next(line for line in readme.splitlines()[1:] if line.strip() and not line.startswith("#"))
    site = {"title": first_heading(readme), "description": description.strip(), "repository": REPOSITORY,
            "ref": REF, "nav": nav, "sidebar": sidebar}
    (OUT / "site.json").write_text(json.dumps(site, indent=2) + "\n")
    print(f"wrote {len(written)} pages to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        build()
    except BuildError as error:
        print(f"docs build failed: {error}", file=sys.stderr)
        sys.exit(1)
