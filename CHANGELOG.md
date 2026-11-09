# Changelog

## How versions change

- **Versions 0.x are drafts.** Any part can change from one draft to the next.
- **From version 1.0, a new major version** is needed for any change that makes a valid document invalid, or that changes what a valid document means.
- **Error codes never change meaning** (SPEC.md, [check.codes-stable]), and requirement ids are never reused.
- **Each version lists its changes here,** by section.

## 0.3, 2026-11-09 (draft)

Version 0.3 fixes what a third review of version 0.2 found, and removes material that belongs to platforms or to version 0.1.

**Changed**
- [publish.saved-run]: a dry run also compares what a saved workflow would write. Each step that writes must be given the same value as the write it replays for every fixed input, meaning one that doesn't come from a model's answer or another step that isn't deterministic. Before, the dry run replayed writes without looking at their inputs, so a saved workflow that would write something else still passed. A write of the original run that the saved workflow leaves out is now reported too. Run order, which decides which write each step replays, is now a term (section 2.3).
- [run.failure]: a failed run's error has the code of the first step that failed. The test cases already required it.
- [run.retry]: `attempts` of 0 also covers a write replayed in a dry run.
- `schemas/run.schema.json` now holds what section 9 already required: an error always has a code, from the lists in [run.error-codes] and section 9.1, and a call records its step digest, and its outputs when it succeeded ([run.calls-recorded]).
- [manifest.workflow-derived] ignores the order of `required` lists at every depth, as it says; the reference checker sorted only the top level.
- [check.invalid-value]: the reference checker now checks a workflow input default's media type too, as the rule says.
- Section 9.11 says the runner test cases rely on the contracts in drafts/runtime.md and the tool names in drafts/mcp.md, so a runner with a runtime of its own ports their steps.
- The `effects` case tests only what [run.effects] requires. The reference runner's own rule against reading the home folder, and `--allow-read`, are now tested in `reference/tests/test_isolation.py`.

**Fixed**, in the reference tools
- `wfspec check-step` applies every rule the catalog loader applies. It skipped [manifest.no-self], [manifest.workflow-derived] and the check of a workflow step's workflow, so a manifest could pass it and still be left out of every catalog. Without `--catalog`, it uses the manifest's own folder.
- An agent step is told when a text file input was cut at 200,000 characters.
- An agent step whose pinned instructions file is missing or outside its folder fails with `pinned_mismatch`, as a program's pinned file does ([run.pinned]).

**Security**, in the reference runner
- Output files are read without following symbolic links, through a handle to the work folder opened before the program starts. A process the program left running could otherwise swap in a link to a file the sandbox denied it.
- A program runs in a process group of its own, which ends with it, on a timeout too.
- Programs that don't write can't send signals outside their process group.
- A `none` program can't read the shared temporary folders or other volumes either, so it reads no outside data wherever a repository sits.
- The runner no longer grants a program's installation folder by itself. For a program in a folder such as `~/bin`, that folder was the whole home folder.
- `wfspec run --secret` starts the runner again without the secrets in its environment, which any program of the same person can read.
- Secret values are hidden by walking the run record rather than by editing its text, which lost the record when a secret matched a number. Secrets of any length are hidden, and an output file that holds one fails its step.

**Removed**
- `experiments/`: a test of agents writing workflows in version 0.1, which no longer matches the format. Git history keeps it.
- [drafts/mcp.md](drafts/mcp.md), section 2: the tools a platform's MCP server offers. That is a platform's choice, like its service API (proposals.md, P18). Section 1, a step as an MCP tool, stays.
- `guides/use-from-an-mcp-client.md`, which described that server.

## 0.2, 2026-11-08 (draft)

Version 0.2 cuts the specification down to the format layer. Rules for a runner and a platform that no implementation had tested moved to drafts and proposals.

**Added**
- Requirement ids on every testable requirement (section 2.2), and `tests/coverage.yaml`, which maps each id to its tests or says why it has none.
- Conformance classes: checker, runner and publisher (section 3).
- Terminology (section 2.3), Security considerations (section 11) and References (section 14).
- The BCP 14 key words: MUST, SHOULD and MAY in capitals.
- `calls` (section 5.4). Any step may list the steps it can call, and the rules for calls apply whatever the runtime kind. It replaces `runtime.agent.tools`.
- The `process` runtime kind: a program started directly, under the same contract as `container` ([drafts/runtime.md](drafts/runtime.md)).
- Test cases for runners in `tests/run/` (section 9.11), and a reference runner that passes them.
- Run-level error codes `invalid_input` and `lock_mismatch` for runs that never start ([run.check-first], [run.lock]).
- Where each problem points, and how many times it's reported ([check.paths]), and the order in which manifest rules apply ([manifest.invalid]), so a second checker can pass the test cases without copying this one.
- [types.fits]: the comparison of two schemas is normative, not an example.
- [types.media-type]: a file value's media type must fit the input's `x-mediaType`, for literals and at run time, not only between references.
- [manifest.workflow-derived]: a `workflow` step's manifest can't claim lower effects, fewer secrets or more determinism than its workflow has. The `workflow` runtime kind now pins its file with `digest`.
- [workflow.extensions]: `x-` fields in workflows, like those in manifests.
- `schemas/policy.schema.json` for caller policies ([check.policy]).
- [run.pinned] and the step error code `pinned_mismatch`: a runner checks the content a manifest pins before the step runs.
- [manifest.no-self]: a step can't be able to run itself, through calls or its workflow.
- The comparison in section 6.3 lists every keyword it handles, and says where it stops.
- The order a checker reads a catalog's files in ([manifest.duplicate]), and the derived `inputSchema` of a workflow without one ([publish.as-step]).

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
- [publish.versions] is a SHOULD. Version 0.1 said a new version "must" bump its major number for a breaking change.
- `schemas/step.schema.json` fixes the fields of the `workflow` runtime kind only. The other kinds are open objects, so a change to a draft doesn't make manifests invalid.
- [check.output]: every problem has a `message`.
- [publish.saved-run] says what the publisher compares in a dry run, and covers only deterministic steps whose inputs match the original run.
- [doc.formats] and [doc.numbers] say what happens to values JSON can't hold, and which numbers the I-JSON range applies to.
- MCP annotations no longer set `destructiveHint` or `idempotentHint` ([drafts/mcp.md](drafts/mcp.md)).
- Vendor and tool names moved out of normative text into informative notes and the drafts.
- Section numbers: Documents are now section 4, Step manifest 5, Values and types 6, Workflow file 7, Checking 8, Running 9 and Publishing 10.

## 0.1, 2026-11-07 (draft)

First draft.
