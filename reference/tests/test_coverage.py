"""Every requirement id in SPEC.md has tests in tests/coverage.yaml, or says why it has none (SPEC.md, section 2.2)."""

import re
from collections import Counter
from pathlib import Path

from wfspec import load_document

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
DEFINITION = re.compile(r"\*\*\[([a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+)\]\*\*")
CASE_KINDS = ("check", "manifests", "run")


def spec_ids() -> list[str]:
    return DEFINITION.findall((ROOT / "SPEC.md").read_text())


def coverage() -> dict:
    return load_document(TESTS / "coverage.yaml")


def test_each_id_is_defined_once():
    repeated = [name for name, n in Counter(spec_ids()).items() if n > 1]
    assert repeated == []


def test_coverage_lists_exactly_the_ids_in_the_spec():
    spec, listed = set(spec_ids()), set(coverage())
    assert sorted(spec - listed) == [], "ids in SPEC.md missing from tests/coverage.yaml"
    assert sorted(listed - spec) == [], "ids in tests/coverage.yaml that SPEC.md doesn't define"


def test_entries_name_real_tests_or_a_reason():
    for rid, entry in coverage().items():
        if isinstance(entry, dict):
            assert set(entry) == {"untested"} and str(entry["untested"]).strip(), rid
            continue
        assert isinstance(entry, list) and entry, rid
        for test in entry:
            if "::" in test:
                file, function = test.split("::")
                source = (ROOT / "reference" / "tests" / file).read_text()
                assert re.search(rf"^def {re.escape(function)}\(", source, re.M), f"{rid}: {test} not found"
            else:
                assert (TESTS / test / "expected.yaml").is_file(), f"{rid}: {test} not found"


def test_every_case_covers_an_id():
    named = {test for entry in coverage().values() if isinstance(entry, list) for test in entry}
    cases = {f"{kind}/{p.name}" for kind in CASE_KINDS if (TESTS / kind).is_dir() for p in (TESTS / kind).iterdir() if p.is_dir()}
    assert sorted(cases - named) == []
