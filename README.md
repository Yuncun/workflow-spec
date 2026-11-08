# Workflow format for shared jobs

A text format for jobs built from published steps. A step can be:
- code in a container
- an agent
- a tool on a remote MCP server
- another workflow

The package also gives the rules for:
- checking a job before it runs
- running it the same way for everyone
- saving an agent's run as a job anyone can rerun

Read [SPEC.md](SPEC.md) first. If you're a team adopting the format, read [HANDOFF.md](HANDOFF.md) next.

```text
SPEC.md                 the format and its rules
HANDOFF.md              how a platform team adopts it, and the first demo to build
schemas/                JSON Schemas: step, workflow, file value, lock file, run record
examples/
├─ steps/               seven step manifests: containers, narrow agents, a broad agent
├─ workflows/           an agent-led workflow, the fixed workflow saved from its run, a PR summary
├─ runs/                run records for the first two
├─ ci-triage.lock.json  the lock file for the fixed workflow
└─ regenerate.py        recomputes the digests above after an example changes
guides/                 drafts for step authors, workflow authors and Copilot users
tests/                  test cases any checker must pass
reference/              a small Python checker that passes them
experiments/            agents writing workflows in this format: method, runs, results
```

## Try it

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e "reference[test]"

# check a workflow (0 errors, 0 warnings)
.venv/bin/wfspec check examples/workflows/ci-triage.yaml --catalog examples/steps

# see errors an agent would get
.venv/bin/wfspec check tests/check/unknown-references/workflow.yaml \
  --catalog examples/steps --catalog tests/catalog

# the manifest a workflow gets when published, and a step as an MCP tool
.venv/bin/wfspec as-step examples/workflows/ci-triage.yaml --catalog examples/steps
.venv/bin/wfspec mcp-tool examples/steps/open-issue.yaml

# run every test case and example check
.venv/bin/python -m pytest reference/tests
```

## Status

- Version 0.1, a draft dated 2026-11-07.
- The reference checker passes every test case.
- Nothing in this package runs steps. The runner, the step catalog and the MCP server are for the platform to build; see HANDOFF.md.
