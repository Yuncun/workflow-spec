# Proposals

Features left out of [SPEC.md](SPEC.md), and open questions. **Nothing here is normative.**

Every entry has the same parts:
- **Status:** `open` (undecided), `deferred` (decided to wait), or `rejected`
- **Problem:** what goes wrong without it
- **Idea:** the best idea so far, if there is one
- **Settled by:** the evidence or test that would decide it

A proposal joins the specification only through a new version of SPEC.md, with requirement ids and tests.

## P1. Plans

- **Status:** deferred.
- **Problem:** a step that works out a sequence of steps at run time can run them only as calls, one by one, so the sequence is visible only once the run ends.
- **Idea:** the step returns a workflow, its plan. The runner checks the plan under the same policy, runs it, and records it. ComfyUI has a precedent: a node can return new nodes to run (`expand`).
- **Settled by:** a real task that calls can't express, or a case where seeing the plan before it runs matters.

## P2. Retrying steps that write

- **Status:** deferred.
- **Problem:** steps that write are never retried automatically, even when repeating one is harmless, such as setting a label.
- **Idea:** an `idempotent` flag, which only steps that write may set, allowing automatic retries.
- **Settled by:** failure rates from a runner showing that retries matter for steps that write.

## P3. Marking destructive steps

- **Status:** deferred.
- **Problem:** a step that deletes can't be told apart from one that only adds, so approval rules and MCP's `destructiveHint` treat both as destructive.
- **Idea:** a `destructive` flag, defaulting to true, which only steps that write may set.
- **Settled by:** a platform whose approval rules need the difference.

## P4. Resource limits

- **Status:** open.
- **Problem:** a step can't say it needs a long time or a lot of memory, so a runner applies one default to every step.
- **Idea:** declare limits such as a timeout, CPU and memory in the manifest, with the runner free to lower them.
- **Settled by:** a runner that schedules steps on machines of different sizes.

## P5. Labels on workflow steps

- **Status:** open.
- **Problem:** a step id such as `translate_ja` is the only label a step in a workflow has. When one step is used twice, both nodes in a drawing share the step's title.
- **Idea:** an optional `title` or `description` on each step entry.
- **Settled by:** people reading drawn workflows and saying whether step ids are enough.

## P6. Picking a field out of an output

- **Status:** open.
- **Problem:** a reference passes a whole output. Using one field of an object takes an extra step.
- **Idea:** references such as `steps.x.outputs.y.z`, checked against the output's schema.
- **Settled by:** how often real workflows insert such a step.

## P7. Running a step once per item

- **Status:** open.
- **Problem:** handling each item of a list needs a step that takes the whole list, or an agent step that makes one call per item.
- **Idea:** a mapping form in `with` that runs the step once per item and collects the outputs into a list. ComfyUI has `INPUT_IS_LIST` and `OUTPUT_IS_LIST`.
- **Settled by:** real workflows that need it, and how each item would be cached and recorded.

## P8. Generic steps

- **Status:** open.
- **Problem:** steps such as "pick the first item" or "map a value" can only declare loose types, so the checker can't follow types through them and warns with `type_unchecked`.
- **Idea:** type variables in a manifest, so that an output's schema follows an input's.
- **Settled by:** whether the warnings hide real mistakes in a larger catalog.

## P9. References inside schemas, and enforced `format`

- **Status:** deferred.
- **Problem:** schemas can't share definitions (`$defs`), and `format` is never enforced.
- **Idea:** allow local `$defs`, and choose a fixed set of formats every implementation enforces the same way.
- **Settled by:** manifests that repeat large schemas.

## P10. Lazy inputs

- **Status:** open.
- **Problem:** a step that uses an input only sometimes still waits for it, so the steps that produce it always run.
- **Idea:** inputs a step asks for only when it needs them, as in ComfyUI's lazy inputs.
- **Settled by:** a workflow where the extra work costs real time or money.

## P11. Automatic upgrades between major versions

- **Status:** open.
- **Problem:** workflows name exact versions, so moving one to a new major version is manual.
- **Idea:** migration notes in a manifest that a tool can apply to workflows.
- **Settled by:** catalogs with steps that have reached a second major version.

## P12. Working out a saved workflow from the calls

- **Status:** open.
- **Problem:** saving a run as a workflow (SPEC.md, section 10.6) is done by hand.
- **Idea:** a tool that drafts the workflow from the run record, turning values that came from earlier calls into references.
- **Settled by:** saving several real runs by hand, to see which parts a tool can draft.

## P13. Checking version rules

- **Status:** open.
- **Problem:** SPEC.md, [publish.versions] says when a new version should bump its major number, but nothing checks it.
- **Idea:** a command that compares two versions of a step and reports breaking changes.
- **Settled by:** a publisher that wants to enforce the rule.

## P14. Running examples at publish

- **Status:** deferred.
- **Problem:** a manifest's `examples` are only checked against its schemas, so a step whose code no longer fits its own examples can still be published.
- **Idea:** a publisher runs the examples and checks the outputs. It doesn't run the examples of a step that writes, since that would change real data.
- **Settled by:** a publisher with a place to run steps safely.

## P15. Layout for visual editors

- **Status:** deferred.
- **Problem:** a workflow has no positions, so every editor lays it out its own way.
- **Idea:** a separate layout file, so that layout never changes what a workflow means.
- **Settled by:** an editor that wants to save positions.

## P16. Program steps that call steps

- **Status:** open.
- **Problem:** only agent steps can call steps ([drafts/runtime.md](drafts/runtime.md)). A program that wants to call a step has no way to reach the runner.
- **Idea:** the runner gives the program an address it can send calls to.
- **Settled by:** a program step that needs it. Any answer adds an interface between runner and program, so it overlaps with P18.

## P17. Triggers and schedules

- **Status:** open, and left to platforms for now.
- **Problem:** the format doesn't say when a workflow starts, for example on a push or every night.
- **Idea:** keep triggers outside the workflow file, in the platform, so that one file can be started in different ways.
- **Settled by:** whether platforms need to exchange triggers.

## P18. A service API

- **Status:** open, and left to platforms for now.
- **Problem:** each platform has its own way to start, watch, cancel and read runs, so clients don't work across platforms.
- **Idea:** standardize only once a second platform exists. GA4GH's Workflow Execution Service already covers starting, watching, cancelling and reading workflow runs, and is the model to read first.
- **Settled by:** two platforms that need to share clients.

## P19. A registry API

- **Status:** open, and left to platforms for now.
- **Problem:** publishing and finding steps works differently on each platform.
- **Idea:** GA4GH's Tool Registry Service, which WorkflowHub implements, is the model to read first.
- **Settled by:** steps shared between platforms.

## P20. A shared step kind for asking a person

- **Status:** open.
- **Problem:** a step can already wait for a person's answer, as a program, a remote tool or a platform's own runtime kind. But each platform names its own kind, so a step that asks a person on one platform doesn't run on another.
- **Idea:** a runtime kind in [drafts/runtime.md](drafts/runtime.md) that shows a person the step's inputs and takes their answer as its outputs. Approval before a step that writes is separate, and a runner can already ask for it (SPEC.md, [run.effects]).
- **Settled by:** two platforms that want to share steps that ask a person.

## P21. The dialect of regular expressions

- **Status:** open.
- **Problem:** JSON Schema recommends ECMA-262 regular expressions, but validators use their own language's engine. Python's `re`, which the reference checker uses, rejects `\p{L}` and accepts `(?P<name>…)`, while ECMA-262 does the opposite. Two checkers can then disagree on whether a pattern is valid, and on which values fit it.
- **Idea:** require ECMA-262 with the `u` flag, and give the reference an ECMA-262 engine such as `regress`. Or allow only a subset that every engine reads the same way, such as I-Regexp [[RFC 9485](https://www.rfc-editor.org/rfc/rfc9485)].
- **Settled by:** a manifest that needs a pattern the two engines read differently.

## P22. What a run records when approval is refused

- **Status:** open.
- **Problem:** a runner may ask for approval before a step that writes (SPEC.md, [run.effects]), but nothing says what the run record holds when the answer is no. One runner could fail the step, another skip it, and a third cancel the run.
- **Idea:** record the step as `skipped` with a reason, as the reference runner does. The steps that depend on it are skipped too, and the run still succeeds.
- **Settled by:** a second runner that asks for approval.
