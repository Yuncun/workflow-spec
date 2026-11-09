# Changelog

## How versions change

- **Versions 0.x are drafts.** Any part can change from one draft to the next.
- **From version 1.0, a new major version** is needed for any change that makes a valid document invalid, or that changes what a valid document means.
- **Error codes never change meaning** (SPEC.md, [check.codes-stable]), and requirement ids are never reused.
- **Each version lists its changes here,** by section.

## 0.2, 2026-11-08 (draft)

Version 0.2 cuts the specification down to the format layer. Rules for a runner and a platform that no implementation had tested moved to drafts and proposals.

**Added**
- Requirement ids on every testable requirement (section 2.2), and `tests/coverage.yaml`, which maps each id to its tests or says why it has none.
- Conformance classes: checker, runner and publisher (section 3).
- Terminology (section 2.3), Security considerations (section 11) and References (section 14).
- The BCP 14 key words: MUST, SHOULD and MAY in capitals.
- `calls` (section 5.4). Any step may list the steps it can call, and the rules for calls apply whatever the runtime kind. It replaces `runtime.agent.tools`.
- The `process` runtime kind: a program started directly, under the same contract as `container` ([drafts/runtime.md](drafts/runtime.md)).

**Moved**
- To [drafts/runtime.md](drafts/runtime.md): the contracts for containers and agents (0.1, section 3.3) and for remote tools (0.1, `mcp`). Agent steps now finish by calling a `finish` tool, and a runner hands schema problems back for another turn.
- To [drafts/mcp.md](drafts/mcp.md): steps as MCP tools and the platform's MCP server (0.1, section 9).
- To [proposals.md](proposals.md): `mayPlan` (P1), `idempotent` (P2), `destructive` (P3), `resources` (P4), running examples at publish (P14), and the list "Not in version 0.1".

**Removed**
- The rule against an output named `skip` when `maySkip` is set (0.1, section 3.4). No runtime kind needs it now.
- `description` on workflow steps, which the 0.1 schema allowed but the text never mentioned (proposals.md, P5).
- `plan` in run records.

**Changed**
- Steps that write are never retried automatically. Version 0.1 allowed it for steps marked `idempotent`.
- MCP annotations no longer set `destructiveHint` or `idempotentHint` ([drafts/mcp.md](drafts/mcp.md)).
- Vendor and tool names moved out of normative text into informative notes and the drafts.
- Section numbers: Documents are now section 4, Step manifest 5, Values and types 6, Workflow file 7, Checking 8, Running 9 and Publishing 10.

## 0.1, 2026-11-07 (draft)

First draft.
