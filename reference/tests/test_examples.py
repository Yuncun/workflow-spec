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


def test_lock_includes_the_steps_a_step_can_call(catalog):
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
    assert "idempotent" not in manifest and "destructive" not in manifest
    assert manifest["secrets"] == ["GITHUB_TOKEN"]
    assert manifest["outputSchema"]["required"] == ["report", "issue"]


def test_mcp_tool_definitions(catalog):
    issue = mcp_tool(catalog.steps["acme/open-issue@1.0.3"])
    assert issue["name"] == "acme_open-issue_v1"
    assert issue["annotations"] == {"readOnlyHint": False, "openWorldHint": True}
    logs = mcp_tool(catalog.steps["acme/fetch-ci-logs@2.1.0"])
    assert logs["annotations"] == {"readOnlyHint": True, "openWorldHint": True}
    assert "$ref" not in json.dumps(logs)
    assert logs["outputSchema"]["properties"]["logs"]["required"] == ["digest", "mediaType"]


def test_mcp_names_stay_short_and_distinct():
    long_a = {"name": "acme/" + "a" * 70, "version": "2.0.0"}
    long_b = {"name": "acme/" + "a" * 69 + "b", "version": "2.0.0"}
    assert len(mcp_name(long_a)) == 64
    assert mcp_name(long_a) != mcp_name(long_b)
    # Only an owner can contain _, so the name reads back from the right: version, step, owner (drafts/mcp.md).
    assert mcp_name({"name": "mona-cat_octo/open-issue", "version": "1.2.0"}) == "mona-cat_octo_open-issue_v1"


def test_a_platform_can_add_its_own_fields_and_error_codes():
    """Run records, lock files and policies take fields that start with x-, and an error takes a code
    that starts with x- (SPEC.md, [run.extensions], [run.error-codes], [publish.lock], [check.policy])."""
    record = json.loads((EXAMPLES / "runs" / "investigate.run.json").read_text())
    step = next(s for s in record["steps"].values() if s.get("calls"))
    record["x-queue"], step["x-machine"], step["calls"][0]["x-cost"], record["lock"]["x-registry"] = "fast", "m1", 0.01, "example.test"
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == []
    record.update(status="failed", error={"code": "x-quota", "message": "the caller is over its quota"})
    step.update(status="failed", error={"code": "x-quota", "message": "the caller is over its quota"})
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") == []
    record["error"]["code"] = "quota"  # not one of the specification's codes, and not marked as a platform's own
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") != []
    record["error"]["code"], record["queue"] = "x-quota", "fast"  # a field of the platform's own without the prefix
    assert schema_issues(record, "urn:wfspec:schema:run", "invalid") != []
    assert schema_issues({"maxEffects": "reads", "x-approval": "writes"}, "urn:wfspec:schema:policy", "invalid") == []


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


def test_a_wrong_name_gets_the_close_one_or_all_of_them(catalog):
    """A message suggests a close name. When none is close it lists the names there are, so an
    agent can fix the name from the message alone (SPEC.md, principle 3)."""
    steps = {"logs": {"uses": "acme/fetch-ci-logs@2.1.0", "with": {"repo": "acme/app", "run_id": 7, "branch": "main", "repos": "x"}}}
    result = check({"outputs": {"out": {"from": "steps.logs.outputs.text"}}, "steps": steps}, catalog)
    messages = {error.path: error.message for error in result.errors}
    assert messages["/steps/logs/with/repos"].endswith("did you mean 'repo'?")
    assert messages["/steps/logs/with/branch"].endswith("its inputs: 'repo', 'run_id'")
    assert messages["/outputs/out"].endswith("its outputs: 'logs'")


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


def test_cli_reads_an_input_declared_as_text_as_text():
    from wfspec.cli import _input_value
    assert _input_value("584e862", {"type": "string"}) == "584e862"  # a commit hash, not 584 x 10^862
    assert _input_value("everyone", {"enum": ["engineers", "everyone"]}) == "everyone"
    assert _input_value("5", {"type": "integer"}) == 5
    assert _input_value("[1, 2]", None) == [1, 2]


def test_digest_matches_a_known_answer():
    """RFC 8785 sorts keys and drops spaces; the digest is the SHA-256 of those bytes. Worked out by hand,
    so a wrong canonical form can't pass by agreeing with examples the same code produced."""
    import hashlib
    doc = {"b": [1, 2.5, "x"], "a": {"z": True, "y": None}}
    canonical = b'{"a":{"y":null,"z":true},"b":[1,2.5,"x"]}'
    assert digest(doc) == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_cache_key_matches_a_known_answer():
    """The key is the digest of {inputs, step}, with each file cut down to its digest and media type."""
    import hashlib
    manifest = {"inputSchema": {"type": "object", "properties": {"f": {"$ref": "urn:wfspec:file"}}}, "name": "t/x"}
    step = "sha256:" + hashlib.sha256(b'{"inputSchema":{"properties":{"f":{"$ref":"urn:wfspec:file"}},"type":"object"},"name":"t/x"}').hexdigest()
    file = {"digest": "sha256:" + "a" * 64, "mediaType": "text/plain", "name": "n.txt", "size": 3}
    expected = b'{"inputs":{"f":{"digest":"sha256:' + b"a" * 64 + b'","mediaType":"text/plain"},"n":1},"step":"' + step.encode() + b'"}'
    assert cache_key(manifest, {"f": file, "n": 1}) == "sha256:" + hashlib.sha256(expected).hexdigest()
