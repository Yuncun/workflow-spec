# Changelog

## How versions change

- **Versions 0.x are drafts.** Any part can change from one draft to the next.
- **From version 1.0, a new major version** is needed for any change that makes a valid document invalid, or that changes what a valid document means.
- **Error codes never change meaning** (SPEC.md, [check.codes-stable]), and requirement ids are never reused.
- **Each version lists its changes here.**

## Unreleased

- A schema that isn't valid JSON Schema is reported once, at the schema itself, such as `/inputSchema` ([check.paths]). A regular expression that doesn't compile makes its schema invalid ([manifest.json-schema]).
- Several rules that only the test cases showed before are now written down. They are in [doc.formats], [manifest.invalid], [manifest.left-out], [manifest.duplicate], [manifest.required-declared], [workflow.input-schema], [workflow.reference-form], [check.all-at-once], [check.bad-reference], [check.step-never-runs], [check.policy], [check.paths], [publish.as-step] and section 6.3.
- The details that only someone who writes a checker needs moved to Annex A of SPEC.md, word for word. They are the schema comparison, the order of the manifest rules, and where each problem is reported.
- What a test case holds is described in tests/README.md, not in sections 8.5 and 9.11.
- Section 12, a list of the entries in proposals.md, is removed. "Where the ideas come from" is now section 12, "Prior art".
- HANDOFF.md is removed.
- The reference runner can ask before a step that writes, and report each step as it starts and ends (`Options.approve` and `Options.watch`). What a refused step records is an open question (proposals.md, P22).

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
