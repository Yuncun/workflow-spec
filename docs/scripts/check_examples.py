"""Check every YAML and JSON example in the site's documents: python docs/scripts/check_examples.py [--verbose]"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator
from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_pages import FENCE, ROOT, copied_pages  # noqa: E402

yaml = YAML(typ="safe")
CATALOG = ROOT / "examples" / "steps"


def kind_of(data) -> str:
    if not isinstance(data, dict):
        return "other"
    if "runtime" in data:
        return "manifest"
    if "steps" in data:
        return "workflow"
    if data and set(data) <= {"maxEffects", "secrets"}:
        return "policy"
    if {"digest", "mediaType"} <= set(data):
        return "file value"
    return "other"


def wfspec(*args: str) -> dict:
    result = subprocess.run([sys.executable, "-m", "wfspec.cli", *args, "--json"], capture_output=True, text=True,
                            cwd=ROOT)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "errors": [{"code": "crash", "message": (result.stderr or result.stdout).strip()}]}


def catalog_for(manifest: dict, scratch: Path) -> Path:
    """The example catalog, less any manifest of the same name and version: an example of a step
    that's in the catalog stands in for its file there, as a catalog holds one manifest per version."""
    def same_step(folder, names):
        if Path(folder) != CATALOG:
            return []
        return [n for n in names if n.endswith((".yaml", ".yml", ".json")) and (CATALOG / n).is_file()
                and isinstance(step := yaml.load((CATALOG / n).read_text()), dict)
                and (step.get("name"), step.get("version")) == (manifest.get("name"), manifest.get("version"))]

    folder = Path(tempfile.mkdtemp(dir=scratch)) / "catalog"
    shutil.copytree(CATALOG, folder, ignore=same_step)
    return folder


def problems_of(kind: str, data, body: str, scratch: Path) -> list[str]:
    if kind == "manifest":
        folder = Path(tempfile.mkdtemp(dir=scratch))
        path = folder / "step.yaml"
        path.write_text(body)
        result = wfspec("check-step", str(path), "--catalog", str(catalog_for(data, scratch)))
    elif kind == "workflow":
        path = Path(tempfile.mkdtemp(dir=scratch)) / "workflow.yaml"
        path.write_text(body)
        result = wfspec("check", str(path), "--catalog", str(CATALOG))
    elif kind in ("policy", "file value"):
        name = "policy.schema.json" if kind == "policy" else "file.schema.json"
        schema = json.loads((ROOT / "schemas" / name).read_text())
        return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                for e in Draft202012Validator(schema).iter_errors(data)]
    else:
        return []
    return [f"{e.get('code')} at {e.get('path', '')}: {e.get('message', '')}" for e in result.get("errors", [])]


def main(verbose: bool) -> int:
    failures, counts = 0, {}
    with tempfile.TemporaryDirectory() as scratch:
        for source, _ in copied_pages():
            text = (ROOT / source).read_text()
            for block in FENCE.finditer(text):
                first = block.group(0).splitlines()[0]
                language = first.strip("`~ ").split()[0] if first.strip("`~ ") else ""
                body = block.group(0).split("\n", 1)[1].rsplit("\n", 1)[0] + "\n"  # without the fences
                line = text[:block.start()].count("\n") + 1
                where = f"{source}:{line}"
                if language not in ("yaml", "json"):
                    counts["not checked"] = counts.get("not checked", 0) + 1
                    if verbose:
                        print(f"{where}: {language or 'plain'} block, not checked")
                    continue
                try:
                    data = yaml.load(body) if language == "yaml" else json.loads(body)
                except Exception as error:  # a block that doesn't parse is reported, not skipped
                    failures += 1
                    print(f"{where}: {language} block doesn't parse: {error}")
                    continue
                kind = kind_of(data)
                if kind == "file value" and "<" in str(data.get("digest", "")):
                    kind = "other"
                counts[kind if kind != "other" else "not checked"] = counts.get(kind if kind != "other" else "not checked", 0) + 1
                problems = problems_of(kind, data, body, Path(scratch))
                if problems:
                    failures += 1
                    print(f"{where}: {kind} fails checking")
                    for problem in problems:
                        print(f"    {problem}")
                elif verbose:
                    print(f"{where}: {kind}{', checked' if kind != 'other' else ', not checked'}")
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    print(f"examples in the documents: {summary}; {failures} failing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main("--verbose" in sys.argv[1:]))
