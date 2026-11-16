# Proposals

Features left out of [SPEC.md](SPEC.md), and open questions. **Nothing here is normative.**

Every entry has the same parts:
- **Status:** `open` (undecided), `deferred` (decided to wait), or `rejected`
- **Problem:** what goes wrong without it
- **Idea:** the best idea so far, if there is one
- **Settled by:** the evidence or test that would decide it

A proposal joins the specification only through a new version of SPEC.md, with requirement ids and tests.

## P4. Resource limits

- **Status:** open.
- **Problem:** a step can't say it needs a long time or a lot of memory, so a runner applies one default to every step.
- **Idea:** declare limits such as a timeout, CPU and memory in the manifest, with the runner free to lower them.
- **Settled by:** a runner that schedules steps on machines of different sizes.

## P6. Picking a field out of an output

- **Status:** open.
- **Problem:** a reference passes a whole output. Using one field of an object takes an extra step.
- **Idea:** references such as `steps.x.outputs.y.z`, checked against the output's schema.
- **Settled by:** how often real workflows insert such a step.

## P7. Running a step once per item

- **Status:** open.
- **Problem:** handling each item of a list needs a step that takes the whole list.
- **Idea:** a mapping form in `with` that runs the step once per item and collects the outputs into a list. ComfyUI has `INPUT_IS_LIST` and `OUTPUT_IS_LIST`.
- **Settled by:** real workflows that need it, and how each item would be recorded.

## P8. Generic steps

- **Status:** open.
- **Problem:** steps such as "pick the first item" or "map a value" can only declare loose types, so the checker can't follow types through them and warns with `type_unchecked`.
- **Idea:** type variables in a manifest, so that an output's schema follows an input's.
- **Settled by:** whether the warnings hide real mistakes in a larger catalog.

## P9. Shared definitions inside schemas

- **Status:** deferred.
- **Problem:** schemas can't share definitions (`$defs`), so a manifest repeats a large schema each place it's used.
- **Idea:** allow local `$defs`.
- **Settled by:** manifests that repeat large schemas.

## P20. A shared step kind for asking a person

- **Status:** open.
- **Problem:** a step can already wait for a person's answer, as a program, a remote tool or a platform's own runtime kind. But each platform names its own kind, so a step that asks a person on one platform doesn't run on another.
- **Idea:** a runtime kind in [drafts/runtime.md](drafts/runtime.md) that shows a person the step's inputs and takes their answer as its outputs. Approval before a step that writes is separate, and a runner can already ask for it (SPEC.md, [run.effects]).
- **Settled by:** two platforms that want to share steps that ask a person.

## P21. The dialect of regular expressions

- **Status:** open.
- **Problem:** JSON Schema recommends ECMA-262 regular expressions, but validators use their own language's engine. Python's `re`, which the reference checker uses, rejects `\p{L}` and accepts `(?P<name>…)`, while ECMA-262 does the opposite. Two checkers can then disagree on whether a pattern is valid, and on which values fit it. A pattern that never finishes on some input, such as `^(a+)+$`, also stalls one engine and not another. The reference checker reads `$` as the end of the text, as ECMA-262 does, where Python's own `$` also matches before a final newline.
- **Idea:** require ECMA-262 with the `u` flag, and give the reference an ECMA-262 engine such as `regress`. Or allow only a subset that every engine reads the same way, such as I-Regexp [[RFC 9485](https://www.rfc-editor.org/rfc/rfc9485)].
- **Settled by:** a manifest that needs a pattern the two engines read differently.
