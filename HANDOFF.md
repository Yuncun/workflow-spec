# Handoff: adopting this spec

For a platform team, and the agents working with it, adopting SPEC.md as the foundation for a service that runs shared jobs. Nothing in this package depends on any particular platform.

## What to keep fixed

- **The file formats:** `schemas/`.
- **The checking rules and error codes:** SPEC.md, section 8.
- **The requirement ids and test cases:** every testable rule in SPEC.md has an id, and `tests/coverage.yaml` says which test cases cover it. A checker must pass all of them to conform, but that isn't enough: it must also follow every rule in sections 4 to 8 (SPEC.md, section 3).

Change any of these only with a new spec version and a written reason. Error codes are what agents fix workflows by, so keep the meaning of each existing code, and add new codes rather than changing old ones.

## What is yours to decide

- **What SPEC.md leaves to platforms** (section 3): sign-in, storage, triggers and schedules, quotas, billing, sandboxing, the service API, the catalog's API, approvals and editors.
- **The drafts:** `drafts/runtime.md` (how a runner starts a step) and `drafts/mcp.md` (steps as MCP tools, and the platform's MCP server). Neither is normative yet. Adopt them, change them, or replace them, and send back what works in practice.
- **The proposals:** `proposals.md` lists features left out on purpose, each with what would settle it. Evidence from your platform is what moves one into the spec.

The reference checker and runner are in Python. You can call them as they are, port them, or rewrite them in your own stack. The test cases are the contract either way: `tests/check/` and `tests/manifests/` for checkers, `tests/run/` for runners.

## First task: compare with the current design

Paste this to the agents that hold the current design:

> Read SPEC.md, HANDOFF.md, drafts/ and proposals.md in this folder, then our current design documents: [list them]. Write a table with one row for each part of our design. Give the section of SPEC.md or the draft it corresponds to, and whether it matches, conflicts, or has no counterpart. For each conflict, recommend one of three things: keep our design, adopt the spec, or change the spec. Give the reason, and say what each choice would cost. List the parts of the spec our design has no counterpart for. Don't change any files.

A person decides each conflict. A change to the spec goes back into this package as a new version.

## Then: the first demo

| Piece | Done when |
|---|---|
| Three real steps from real repositories | each manifest passes `wfspec check-step`, with `--catalog` naming the folders of any steps it calls or runs |
| Checker | passes every case in `tests/check/` and `tests/manifests/` |
| Runner for program and agent steps | passes every case in `tests/run/`. Their steps follow drafts/runtime.md and the tool names in drafts/mcp.md, so with a runtime of your own, port those steps first (SPEC.md, section 9.11) |
| MCP server | offers the tools in drafts/mcp.md, section 2, and works from Copilot |

The demo, in order:

| Step | What it shows |
|---|---|
| Copilot writes a workflow from the catalog, fixes what the checker reports, and runs it | agents can write jobs from the checker's errors |
| Rerun with one input changed | unchanged steps come from the cache |
| A second person runs the published workflow from Copilot | sharing a job as a file works |
| An agent step's run, with its calls, is saved as a workflow | an agent's one-off work becomes a fixed job |

Each row passes or fails on evidence:
1. **Agents write jobs.** The agent reaches a workflow with no errors using only the MCP server's answers. Record how many checks it took.
2. **Cache.** The rerun's record shows `cacheHit: true` for the unchanged steps.
3. **Sharing.** The second person needs no help beyond the form or the chat.
4. **Saved runs.** The saved workflow passes checking. A dry run with the original inputs gives the same outputs for its deterministic steps, gives each step that writes the same fixed inputs the original run gave it, and leaves out none of the original run's writes, with steps that write returning their recorded outputs instead of running again (SPEC.md, section 10.6).

The fourth row is what would set this format apart from similar tools. If it doesn't hold up, say so plainly: the case for the format is much weaker without it.

## Rules for agents working on this

- Follow [CONTRIBUTING.md](CONTRIBUTING.md): how a change to the spec is made, the writing rules, and the tests, which must all pass after any change.
- Don't add conditions, loops or expressions to the workflow format. Put logic in steps (SPEC.md, sections 1 and 7.4).
- `experiments/agent-authoring/` holds a test of agents writing workflows in version 0.1 of the format, with its method and results. Repeat it with your own steps and models; its README says what it didn't test.
