# Changelog

## How versions change

- **Versions 0.x are drafts.** Any part can change from one draft to the next.
- **From version 1.0, a new major version** is needed for any change that makes a valid document invalid, or that changes what a valid document means.
- **Error codes never change meaning** (SPEC.md, [check.codes-stable]), and requirement ids are never reused.
- **Each version lists its changes here.**

## Unreleased

**Reference tools**
- A program that starts runs for other programs can drive the runner. `Runner.run` takes the run's id, and saves the record in the store when the run starts and each time a step starts or ends. Raising `RunCancelled` ends the run with the status `cancelled`: a step cut short fails with the runner's own code `x-cancelled`, and the steps after it are skipped.
- `load_catalog` reads a path that is a file as one manifest.

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

- [publish.saved-run]: a dry run also compares what a saved workflow would write. Each step that writes must get the same value as the write it replays, for every fixed input. A write of the original run that the saved workflow leaves out is reported too.
- [run.failure]: a failed run's error has the code of the first step that failed.
- [run.retry]: `attempts` of 0 also covers a write replayed in a dry run.
- `schemas/run.schema.json`: an error always has a code, and a call records its step digest.
- Removed: section 2 of drafts/mcp.md, on the tools a platform's MCP server offers, and the guide that described that server. What the server offers is a platform's choice.

## 0.2, 2026-11-08 (draft)

Version 0.2 cut the specification down to the format layer.

- **Moved out:** how a runner starts a step, to drafts/runtime.md. Steps as MCP tools, to drafts/mcp.md. The `mayPlan`, `idempotent`, `destructive` and `resources` fields, to proposals.md (P1 to P4).
- **Added to the documents:** requirement ids, conformance classes (checker, runner and publisher), the BCP 14 key words, and test cases for runners.
- **Added to the format:** `calls`, which replaces `runtime.agent.tools`. The `process` runtime kind. `x-` fields in workflows. Caller policies. A `digest` on the `workflow` runtime kind.
- **New error codes:** `invalid_input` and `lock_mismatch` for a run that never starts, and `pinned_mismatch` for a step.
- **Changed:** steps that write are never retried automatically. [publish.versions] is a SHOULD. `schemas/step.schema.json` fixes the fields of the `workflow` runtime kind only.
- **Removed:** `description` on workflow steps, and `plan` in run records.

## 0.1, 2026-11-07 (draft)

First draft.
