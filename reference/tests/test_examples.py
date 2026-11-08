"""The files in ../../examples agree with the spec and with each other."""

import json
from pathlib import Path

import pytest

from wfspec import as_step, check, digest, load_catalog, load_document, lock, mcp_tool
from wfspec.cli import main
from wfspec.documents import schema_issues
from wfspec.publish import cache_key, mcp_name

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
WORKFLOWS = ["ci-triage", "investigate", "pr-summary"]
RUNS = ["ci-triage", "investigate"]


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


@pytest.mark.parametrize("name", RUNS)
def test_run_records_are_valid_and_pinned(catalog, name):
    run = load_document(EXAMPLES / "runs" / f"{name}.run.json")
    assert schema_issues(run, "urn:wfspec:schema:run", "invalid") == []
    assert run["workflow"]["digest"] == digest(workflow(name))
    assert run["lock"] == lock(workflow(name), catalog)
    for step in run["steps"].values():
        assert step["digest"] == run["lock"]["steps"][step["uses"]]["digest"]
        for call in step.get("calls", []):
            assert call["digest"] == digest(catalog.steps[call["uses"]])


def test_cache_key_is_step_digest_plus_input_values(catalog):
    logs = load_document(EXAMPLES / "runs" / "ci-triage.run.json")["steps"]["logs"]
    assert logs["cacheHit"] is True
    assert logs["cacheKey"] == cache_key(catalog.steps[logs["uses"]], logs["inputs"])
    assert logs["cacheKey"] == digest({"step": logs["digest"], "inputs": logs["inputs"]})


def test_cache_key_ignores_file_names(catalog):
    manifest = catalog.steps["acme/triage-logs@0.4.1"]
    file = {"digest": "sha256:" + "a" * 64, "mediaType": "text/plain", "size": 10}
    one = cache_key(manifest, {"logs": {**file, "name": "one.log"}, "style": "short"})
    two = cache_key(manifest, {"logs": {**file, "name": "two.log"}, "style": "short"})
    assert one == two
    assert one != cache_key(manifest, {"logs": file, "style": "long"})


def test_lock_includes_an_agents_tools(catalog):
    steps = lock(workflow("investigate"), catalog)["steps"]
    assert set(steps) == {"acme/investigate-failure@0.1.0", "acme/fetch-ci-logs@2.1.0", "acme/open-issue@1.0.3"}


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
    assert issue["name"] == "acme_open-issue_v1"
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


def test_mcp_names_stay_short_and_distinct():
    long_a = {"name": "acme/" + "a" * 70, "version": "2.0.0"}
    long_b = {"name": "acme/" + "a" * 69 + "b", "version": "2.0.0"}
    assert len(mcp_name(long_a)) == 64
    assert mcp_name(long_a) != mcp_name(long_b)


def test_outputs_after_a_step_that_may_skip_are_optional(catalog, tmp_path):
    skipper = dict(catalog.steps["acme/fetch-ci-logs@2.1.0"], name="acme/fetch-if-failed", maySkip=True)
    (tmp_path / "skipper.json").write_text(json.dumps(skipper))
    both = load_catalog([EXAMPLES / "steps", tmp_path])
    assert both.problems == []
    flow = workflow("ci-triage")
    flow["steps"]["logs"]["uses"] = "acme/fetch-if-failed@2.1.0"
    manifest = as_step(flow, both, "ci-triage.yaml")
    assert manifest["outputSchema"]["required"] == []


def test_cli_exit_codes(capsys):
    steps = str(EXAMPLES / "steps")
    assert main(["check", str(EXAMPLES / "workflows" / "ci-triage.yaml"), "--catalog", steps]) == 0
    assert "a run would do: logs -> triage -> file" in capsys.readouterr().out
    broken = ROOT / "tests" / "check" / "type-mismatch-scalars" / "workflow.yaml"
    assert main(["check", str(broken), "--catalog", steps, "--catalog", str(ROOT / "tests" / "catalog"), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [e["code"] for e in out["errors"]] == ["type_mismatch", "type_mismatch"]


def test_lock_includes_the_steps_inside_a_published_workflow(catalog, tmp_path):
    # Publish ci-triage as a step whose workflow file sits next to its manifest.
    (tmp_path / "ci-triage.yaml").write_text((EXAMPLES / "workflows" / "ci-triage.yaml").read_text())
    published = as_step(workflow("ci-triage"), catalog, "ci-triage.yaml")
    (tmp_path / "ci-triage.step.json").write_text(json.dumps(published))
    both = load_catalog([EXAMPLES / "steps", tmp_path])
    outer = {"steps": {"triage": {"uses": "acme/ci-triage@1.2.0", "with": {"repo": "acme/web", "run_id": 7}}}}
    assert check(outer, both).ok
    assert set(lock(outer, both)["steps"]) == {
        "acme/ci-triage@1.2.0", "acme/fetch-ci-logs@2.1.0", "acme/triage-logs@0.4.1", "acme/open-issue@1.0.3",
    }


def _first_yaml_block(guide: str) -> str:
    text = (ROOT / "guides" / guide).read_text()
    return text.split("```yaml\n", 1)[1].split("```", 1)[0]


def test_guide_workflow_is_the_example_file():
    assert _first_yaml_block("write-a-workflow.md") == (EXAMPLES / "workflows" / "pr-summary.yaml").read_text()


def test_guide_manifest_passes(tmp_path):
    (tmp_path / "step.yaml").write_text(_first_yaml_block("publish-a-step.md"))
    from wfspec.documents import manifest_issues
    assert manifest_issues(load_document(tmp_path / "step.yaml")) == []
