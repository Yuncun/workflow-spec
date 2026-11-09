"""Runs the language-neutral test cases in ../../tests against the reference checker.

tests/check/<case>/      workflow.yaml or workflow.json, expected.yaml, optional policy.yaml and catalog/
tests/manifests/<case>/  steps/*.yaml, expected.yaml
A case passes when the (code, path) pairs match exactly; messages aren't compared.
"""

from collections import Counter
from pathlib import Path

import pytest

from wfspec import Issue, ParseError, check, load_catalog, load_document
from wfspec.documents import check_step

ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIRS = [ROOT / "examples" / "steps", ROOT / "tests" / "catalog"]
CHECK_CASES = sorted(p for p in (ROOT / "tests" / "check").iterdir() if p.is_dir())
MANIFEST_CASES = sorted(p for p in (ROOT / "tests" / "manifests").iterdir() if p.is_dir())


def pairs(items) -> Counter:
    return Counter((i["code"], i["path"]) if isinstance(i, dict) else (i.code, i.path) for i in items or [])


def test_catalog_is_clean():
    assert load_catalog(CATALOG_DIRS).problems == []


@pytest.mark.parametrize("case", CHECK_CASES, ids=lambda p: p.name)
def test_check_case(case: Path):
    expected = load_document(case / "expected.yaml")
    policy = load_document(case / "policy.yaml") if (case / "policy.yaml").exists() else None
    dirs = CATALOG_DIRS + ([case / "catalog"] if (case / "catalog").is_dir() else [])
    try:
        source = case / "workflow.json" if (case / "workflow.json").exists() else case / "workflow.yaml"
        result = check(load_document(source), load_catalog(dirs), policy)
        errors, warnings, ok = result.errors, result.warnings, result.ok
    except ParseError as exc:
        errors, warnings, ok = [Issue("parse_error", "", str(exc))], [], False
    assert pairs(errors) == pairs(expected.get("errors")), [i.message for i in errors]
    assert pairs(warnings) == pairs(expected.get("warnings")), [i.message for i in warnings]
    assert ok == expected["ok"]


@pytest.mark.parametrize("case", MANIFEST_CASES, ids=lambda p: p.name)
def test_manifest_case(case: Path):
    expected = load_document(case / "expected.yaml")
    catalog = load_catalog([ROOT / "examples" / "steps", case / "steps"])
    problems = [i for i in catalog.problems if Path(i.file).parent == case / "steps"]
    assert pairs(problems) == pairs(expected.get("errors")), [i.message for i in problems]


@pytest.mark.parametrize("case", MANIFEST_CASES, ids=lambda p: p.name)
def test_check_step_agrees_with_the_catalog(case: Path):
    """`wfspec check-step` reports, for each manifest, what loading it into a catalog reports, apart
    from duplicates, which only a whole catalog has."""
    dirs = [ROOT / "examples" / "steps", case / "steps"]
    catalog = load_catalog(dirs)
    for path in sorted(p for p in (case / "steps").iterdir() if p.suffix in (".yaml", ".yml", ".json")):
        expected = [i for i in catalog.problems if i.file == str(path) and i.code != "duplicate_step"]
        try:
            found = check_step(path, dirs)
        except ParseError as exc:
            found = [Issue("parse_error", "", str(exc))]
        assert pairs(found) == pairs(expected), f"{path.name}: {[i.message for i in found]}"
