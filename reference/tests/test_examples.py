"""The files in ../../examples agree with the spec and with each other."""

import json
from pathlib import Path

import pytest

from wfspec import as_step, check, digest, load_catalog, load_document, lock, mcp_tool
from wfspec.cli import main
from wfspec.documents import schema_issues

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
WORKFLOWS = ["ci-triage", "investigate"]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog([EXAMPLES / "steps"])


def workflow(name: str) -> dict:
    return load_document(EXAMPLES / "workflows" / f"{name}.yaml")


def test_example_steps_are_clean(catalog):
    assert catalog.problems == []


@pytest.mark.parametrize("name", WORKFLOWS)
def test_example_workflows_check_clean(catalog, name):
    result = check(workflow(name), catalog)
    assert result.ok and not result.warnings, result.to_dict()


def test_lock_file_is_current(catalog):
    saved = load_document(EXAMPLES / "ci-triage.lock.json")
    assert schema_issues(saved, "urn:wfspec:schema:lock", "invalid") == []
    assert saved == lock(workflow("ci-triage"), catalog)


@pytest.mark.parametrize("name", WORKFLOWS)
def test_run_records_are_valid_and_pinned(catalog, name):
    run = load_document(EXAMPLES / "runs" / f"{name}.run.json")
    assert schema_issues(run, "urn:wfspec:schema:run", "invalid") == []
    assert run["workflow"]["digest"] == digest(workflow(name))
    assert run["lock"] == lock(workflow(name), catalog)
    for step in run["steps"].values():
        assert step["digest"] == run["lock"]["steps"][step["uses"]]["digest"]
        for call in step.get("calls", []):
            assert call["digest"] == digest(catalog.steps[call["uses"]])


def test_cache_key_is_step_digest_plus_input_values():
    logs = load_document(EXAMPLES / "runs" / "ci-triage.run.json")["steps"]["logs"]
    assert logs["cacheHit"] is True
    assert logs["cacheKey"] == digest({"step": logs["digest"], "inputs": logs["inputs"]})


def test_saved_workflow_uses_the_steps_the_agent_called():
    agent_run = load_document(EXAMPLES / "runs" / "investigate.run.json")
    called = {call["uses"] for call in agent_run["steps"]["investigate"]["calls"]}
    saved = {step["uses"] for step in workflow("ci-triage")["steps"].values()}
    assert called <= saved


def test_workflow_published_as_step(catalog):
    manifest = as_step(workflow("ci-triage"), catalog, "workflows/ci-triage.yaml")
    assert schema_issues(manifest, "urn:wfspec:schema:step", "invalid") == []
    assert manifest["effects"] == "writes"
    assert manifest["deterministic"] is False
    assert (manifest["idempotent"], manifest["destructive"]) == (False, False)
    assert manifest["secrets"] == ["GITHUB_TOKEN"]
    assert manifest["outputSchema"]["required"] == ["report", "issue"]


def test_mcp_tool_definitions(catalog):
    issue = mcp_tool(catalog.steps["acme/open-issue@1.0.3"])
    assert issue["name"] == "acme.open-issue"
    assert issue["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
    logs = mcp_tool(catalog.steps["acme/fetch-ci-logs@2.1.0"])
    assert logs["annotations"] == {"readOnlyHint": True, "openWorldHint": True}
    assert "$ref" not in json.dumps(logs)
    assert logs["outputSchema"]["properties"]["logs"]["required"] == ["digest", "mediaType"]


def test_cli_exit_codes(capsys):
    steps = str(EXAMPLES / "steps")
    assert main(["check", str(EXAMPLES / "workflows" / "ci-triage.yaml"), "--catalog", steps]) == 0
    broken = ROOT / "tests" / "check" / "type-mismatch-scalars" / "workflow.yaml"
    assert main(["check", str(broken), "--catalog", steps, "--catalog", str(ROOT / "tests" / "catalog"), "--json"]) == 1
    out = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert [e["code"] for e in out["errors"]] == ["type_mismatch", "type_mismatch"]
