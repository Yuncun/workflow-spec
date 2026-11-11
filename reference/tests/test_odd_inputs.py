"""Inputs the reference tools once crashed on: each now gets an ordinary result."""

import math
from pathlib import Path

import pytest

from wfspec import ParseError, check, load_catalog, load_document
from wfspec.documents import end_of_text, schema_issues
from wfspec.run import Options, Runner
from wfspec.runtimes import SANDBOX
from wfspec.store import Store

ROOT = Path(__file__).resolve().parents[2]
ECHO = {"uses": "test/echo@1.0.0", "with": {"text": "hi"}}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog([ROOT / "tests" / "catalog"])


def codes(workflow, catalog) -> list[str]:
    return [e.code for e in check(workflow, catalog).errors]


def test_a_schema_nested_very_deeply_is_an_invalid_schema(catalog):
    deep = {"type": "string"}
    for _ in range(150):
        deep = {"type": "object", "properties": {"k": deep}}
    workflow = {"inputSchema": {"type": "object", "properties": {"v": deep}}, "steps": {"e": ECHO}, "outputs": {"o": {"from": "steps.e.outputs.text"}}}
    assert codes(workflow, catalog) == ["invalid_workflow"]


def test_a_long_chain_of_steps_is_checked(catalog):
    n = 1500
    steps = {f"s{i}": {"uses": "test/echo@1.0.0", "with": {"text": {"from": f"steps.s{i + 1}.outputs.text"} if i < n - 1 else "hi"}} for i in range(n)}
    assert codes({"steps": steps, "outputs": {"o": {"from": "steps.s0.outputs.text"}}}, catalog) == []


@pytest.mark.parametrize("text", [
    "steps:\n  e: { uses: test/echo@1.0.0, with: { text: &x [*x] } }\n",  # a value inside itself
    "%YAML 1.3\n---\nsteps: {}\n",  # a version of YAML the reader doesn't know
], ids=["alias-inside-its-anchor", "yaml-1.3"])
def test_documents_that_cant_be_read_are_parse_errors(tmp_path, text):
    (tmp_path / "wf.yaml").write_text(text)
    with pytest.raises(ParseError):
        load_document(tmp_path / "wf.yaml")


def test_dollar_means_the_end_of_the_text():
    assert end_of_text(r"^[a-z]+$") == r"^[a-z]+\Z"
    assert end_of_text(r"^a\$[$]$") == r"^a\$[$]\Z"  # an escaped $ and one in a character class stay


@pytest.mark.parametrize("workflow", [None, 5, "name", []], ids=repr)
def test_running_something_that_isnt_a_workflow_gives_a_record(tmp_path, workflow):
    """SPEC.md, [run.check-first]: a run that never starts still gets a record."""
    record = Runner(load_catalog([ROOT / "tests" / "run-catalog"]), Options(store=Store(tmp_path))).run(workflow, {})
    assert (record["status"], record["error"]["code"]) == ("failed", "check_failed")
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == []


@pytest.mark.parametrize("value", [math.nan, math.inf, 2**60], ids=repr)
def test_a_run_refuses_numbers_json_cant_hold(tmp_path, value):
    """A run record is a JSON document, so its numbers follow I-JSON (SPEC.md, section 4, [doc.numbers])."""
    workflow = {"inputSchema": {"type": "object", "properties": {"v": {}}, "required": ["v"]},
                "steps": {"e": {"uses": "test/echo@1.0.0", "with": {"text": {"from": "inputs.v"}}}},
                "outputs": {"o": {"from": "steps.e.outputs.text"}}}
    options = Options(store=Store(tmp_path), sandbox=SANDBOX is not None)
    record = Runner(load_catalog([ROOT / "tests" / "run-catalog"]), options).run(workflow, {"v": value})
    assert (record["status"], record["error"]["code"]) == ("failed", "invalid_input")
