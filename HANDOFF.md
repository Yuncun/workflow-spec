# Handoff: adopting this spec

For a platform team, and the agents working with it, adopting SPEC.md as the foundation for a service that runs shared jobs. Nothing in this package depends on any particular platform.

## What to keep fixed

- **The file formats:** `schemas/`.
- **The checking rules and error codes:** SPEC.md, section 6.
- **The test cases:** `tests/`. A checker conforms when it passes all of them.

Change any of these only with a new spec version and a written reason. Error codes are what agents fix workflows by, so keep the meaning of each existing code, and add new codes rather than changing old ones.

## What is yours to decide

SPEC.md, section 10 lists what the spec leaves to the platform: sign-in, storage, scheduling, quotas, billing, sandboxing, the catalog's API, approvals, editors.

The reference checker is in Python. You can call it as it is, port it, or rewrite it in your own stack. The test cases are the contract either way.

## First task: compare with the current design

Paste this to the agents that hold the current design:

> Read SPEC.md and HANDOFF.md in this folder, then our current design documents: [list them]. Write a table with one row for each part of our design. Give the section of SPEC.md it corresponds to, and whether it matches, conflicts, or has no counterpart. For each conflict, recommend one of three things: keep our design, adopt the spec, or change the spec. Give the reason, and say what each choice would cost. List the parts of the spec our design has no counterpart for. Don't change any files.

A person decides each conflict. A change to the spec goes back into this package as a new version.

## Then: the first demo

| Piece | Done when |
|---|---|
| Three real steps from real repositories | each manifest passes `wfspec check-step` (with `--catalog` for agent steps) |
| Checker | passes every case in `tests/` |
| Runner for container and agent steps | follows SPEC.md, section 7, and writes run records that fit `schemas/run.schema.json` |
| MCP server | offers the tools in SPEC.md, section 9.2, and works from Copilot |

The demo, in order:

| Step | What it shows |
|---|---|
| Copilot writes a workflow from the catalog, fixes what the checker reports, and runs it | agents can write jobs from the checker's errors |
| Rerun with one input changed | unchanged steps come from the cache |
| A second person runs the published workflow from Copilot | sharing a job as a file works |
| A broad agent step's run is saved as a workflow | an agent's one-off work becomes a fixed job |

Each row passes or fails on evidence:
1. **Agents write jobs.** The agent reaches a workflow with no errors using only the MCP server's answers. Record how many checks it took.
2. **Cache.** The rerun's record shows `cacheHit: true` for the unchanged steps.
3. **Sharing.** The second person needs no help beyond the form or the chat.
4. **Saved runs.** The saved workflow passes checking. A dry run with the original inputs gives the same outputs for its deterministic steps, with steps that write returning their recorded outputs instead of running again (SPEC.md, section 8.5).

The fourth row is what would set this format apart from similar tools. If it doesn't hold up, say so plainly: the case for the format is much weaker without it.

## Rules for agents working on this

- Run the test cases after any change to a checker. All must pass.
- Don't add conditions, loops or expressions to the workflow format. Put logic in steps (SPEC.md, sections 2 and 5.4).
- Every new rule gets an error code, a row in SPEC.md, section 6.2, and a test case.
- Keep examples consistent. `reference/tests/test_examples.py` checks that the lock file and run records match the manifests and workflows. After changing an example step or workflow, run `examples/regenerate.py`.
- `experiments/agent-authoring/` holds a test of agents writing workflows from this format, with its method and results. Repeat it with your own steps and models; its README says what it didn't test.
