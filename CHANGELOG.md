# Changelog

## How versions change

- **Versions 0.x are drafts.** Any part can change from one draft to the next.
- **From version 1.0, a new major version** is needed for any change that makes a valid document invalid, or that changes what a valid document means.
- **Error codes never change meaning** (SPEC.md, [check.codes-stable]), and requirement ids are never reused.
- **Each version lists its changes here.**

## Unreleased

These changes ask less of a runner. The specification covers the format and what a run gives back, so that a workflow behaves the same on any runner. How a runner does its work is its own business.

**Removed**
- The cache (was 9.4): the cache key, `run.cache-key`, `run.cache-only-cacheable`, and `cacheKey` and `cacheHit` in run records. A runner may reuse outputs as it likes; `deterministic` says when that is safe.
- Retries (was 9.5): `run.retry` and `attempts` in run records. What it protected, that a step that writes doesn't run twice, is now [run.writes-once].
- Lock files (was 10.3): `publish.lock`, `run.lock`, `lock_mismatch`, `schemas/lock.schema.json`, and `lock` in run records. Versions that never change ([publish.immutable]) and pinned content ([run.pinned]) already keep a workflow from running different code.
- Saving a run as a workflow, and dry runs (was 10.6): `publish.saved-run`. Nobody built it, and it's a tool's job, not the format's.
- Proposals P15 (layout for editors), P17 (triggers and schedules), P18 (a service API) and P19 (a registry API), left to platforms.
- Exact agreement between checkers (was Annex A.3's order of rules, A.4's exceptions and A.5's paths and counts). Checkers must agree on pass or fail and on the error codes; a problem's path SHOULD point at its cause ([check.paths]). Proposal P23 goes with it.
- How a runner stores files (`types.file-store`), the advice to run independent steps in parallel, and the version-bump checklist (`publish.versions`, with proposal P13). Versions follow Semantic Versioning.
- `publish.resolve`: a publisher may write content digests into a manifest, but doesn't have to. A runner still checks the ones there are ([run.pinned]).
- `manifest.calls-one-per-major`.
- drafts/mcp.md (steps as MCP tools), the `mcp` runtime kind, and `wfspec mcp-tool`. Nothing used them; offering steps over MCP is left to platforms.
- Proposals P1 (plans), P2 (retrying steps that write), P3 (marking destructive steps), P5 (labels on workflow steps), P10 (lazy inputs), P11 (automatic upgrades), P12 (saving a run as a workflow), P14 (running examples at publish), P16 (programs that call steps) and P22 (what a refused approval records): runners', tools' or platforms' business, or nothing needs them. P9 keeps only `$defs`; `format` stays an annotation.
- The `agent` runtime kind (was drafts/runtime.md, section 2), with the reference runner's model options. A step that runs an agent is a `process` or `container` step whose program asks a model.
- Steps that call steps (was section 5.4): `calls` in manifests and run records, the `calls` runner feature, `manifest.calls-known`, `manifest.calls-effects`, `manifest.calls-secrets`, `run.calls-listed` and `run.calls-recorded`. [manifest.no-self] stays for `workflow` steps, in section 5.3. Nothing used either feature: the agents platforms run, such as Copilot sessions, have tools that aren't steps.

**Changed**
- Section 1 states the design goals in place of five principles.

**Relaxed**
- Warnings are optional. A checker SHOULD report `step_never_runs`, and MAY report `type_unchecked` and `unused_input`.
- Optional runner features (section 3): `workflow-step`, `file-inputs` and `run-record`. A runner passes the run cases whose features and runtime kinds it supports; each case lists them under `needs` in its `options.yaml`. The `effects` case checks the reference runner's own sandbox, so no other runner has to pass it.
- A platform's own `x-` errors don't break [conform.verdict], and a platform that knows why a version is missing may report its own code in place of `unknown_step`.
- `description` is optional in a manifest (section 5.1).
- drafts/runtime.md: a runner may add environment variables.

**Clarified**
- [run.workflow-step] (section 5.3) says how a `workflow` step runs: it waits for all its inputs, it fails if a step inside it fails, and a skip inside it leaves that step's outputs missing. The reference runner already did this. New run cases: `run/workflow-step-waits`, `run/workflow-step-fails` and `run/workflow-step-skips`.

**Fixed**
- The reference checker treated an empty `properties` or `required` as declaring some, where Annex A.1 says it counts as none (`check/empty-properties`).

**Reference tools**
- A program that starts runs for other programs can drive the runner. `Runner.run` takes the run's id, and saves the record in the store when the run starts and each time a step starts or ends. Raising `RunCancelled` ends the run with the status `cancelled`: a step cut short fails with the runner's own code `x-cancelled`, and the steps after it are skipped.
- `load_catalog` reads a path that is a file as one manifest.
- When a workflow names an input, an output or a step that doesn't exist and no name is close, the checker's message lists the names there are.

Sections 5.5 and 5.6 are now 5.4 and 5.5, 9.6 to 9.10 are now 9.4 to 9.8, and 10.4 and 10.5 are now 10.3 and 10.4.

## 0.4, 2026-11-10 (draft)

Version 0.4 settles what two reviews and a second, independent checker found, and moves the detail only a checker's author needs into an annex.

**What conforms**
- Every checker gives the same pass or fail for a workflow or manifest ([conform.verdict]). On a file with several mistakes, checkers may differ in which further problems they list, wherever the text doesn't say (proposals.md, P23). Before, the text implied they never differ.
- Within a stage of manifest checking, a checker reports every problem of every rule in that stage (Annex A.3).

**Extension points**
- A platform can add its own error codes and fields, with names that start with `x-`: a checker's own problems ([check.platform-codes]), a runner's own reasons for a failure ([run.error-codes]), and fields in run records ([run.extensions]), lock files, policies, a manifest's examples and its `workflow` runtime. Before, only the top level of manifests and workflows had this.

**Changed rules**
- A step's owner may contain `_`, as some account names do. The part of a name after the slash still may not, so a tool name reads back one way (drafts/mcp.md).
- A file is declared as the schema of a property or of `items`, at any depth, and not under another keyword such as `additionalProperties` or `anyOf` ([manifest.refs]).
- An output or workflow input whose schema is `false` never has a value, so it counts as missing ([types.maybe-missing]).
- A manifest or workflow is saved as UTF-8, and text that isn't valid Unicode is a `parse_error` ([doc.formats]).
- A schema that isn't valid JSON Schema is reported once, at the schema itself, such as `/inputSchema` ([check.paths]). A regular expression that doesn't compile makes its schema invalid ([manifest.json-schema]).
- Annex A.1 says when a schema "may be an object", and that an empty `properties` or `required` counts as none.

**Written down**, where only the test cases showed it before: [doc.formats], [manifest.invalid], [manifest.left-out], [manifest.duplicate], [manifest.required-declared], [workflow.input-schema], [workflow.reference-form], [check.all-at-once], [check.bad-reference], [check.step-never-runs], [check.policy], [check.paths], [publish.as-step] and section 6.3.

**Documents**
- The details only someone who writes a checker needs are in Annex A of SPEC.md, word for word: the schema comparison, the order of the manifest rules, and where each problem is reported.
- What a test case holds is described in tests/README.md, not in sections 8.5 and 9.11.
- Section 12, a list of the entries in proposals.md, is removed. "Where the ideas come from" is now section 12, "Prior art".
- HANDOFF.md is removed.

**Reference tools**
- The runner can ask before a step that writes, and report each step as it starts and ends (`Options.approve` and `Options.watch`). What a refused step records is an open question (proposals.md, P22).
- `wfspec check-step` reports exactly what loading the whole catalog reports for that manifest.
- Fixed: several places where the checker didn't do what the text said, and several unusual files that crashed a tool, such as one saved as UTF-16 or a step that outputs NaN. Each has a test now.

## 0.3, 2026-11-09 (draft)

- `publish.saved-run`: a dry run also compares what a saved workflow would write. Each step that writes must get the same value as the write it replays, for every fixed input. A write of the original run that the saved workflow leaves out is reported too.
- [run.failure]: a failed run's error has the code of the first step that failed.
- `run.retry`: `attempts` of 0 also covers a write replayed in a dry run.
- `schemas/run.schema.json`: an error always has a code, and a call records its step digest.
- Removed: section 2 of drafts/mcp.md, on the tools a platform's MCP server offers, and the guide that described that server. What the server offers is a platform's choice.

## 0.2, 2026-11-08 (draft)

Version 0.2 cut the specification down to the format layer.

- **Moved out:** how a runner starts a step, to drafts/runtime.md. Steps as MCP tools, to drafts/mcp.md. The `mayPlan`, `idempotent`, `destructive` and `resources` fields, to proposals.md (P1 to P4).
- **Added to the documents:** requirement ids, conformance classes (checker, runner and publisher), the BCP 14 key words, and test cases for runners.
- **Added to the format:** `calls`, which replaces `runtime.agent.tools`. The `process` runtime kind. `x-` fields in workflows. Caller policies. A `digest` on the `workflow` runtime kind.
- **New error codes:** `invalid_input` and `lock_mismatch` for a run that never starts, and `pinned_mismatch` for a step.
- **Changed:** steps that write are never retried automatically. `publish.versions` is a SHOULD. `schemas/step.schema.json` fixes the fields of the `workflow` runtime kind only.
- **Removed:** `description` on workflow steps, and `plan` in run records.

## 0.1, 2026-11-07 (draft)

First draft.
