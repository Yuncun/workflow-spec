# Workflow format for shared jobs

Version 0.2, draft, 2026-11-08. Changes from version 0.1 are listed in [CHANGELOG.md](CHANGELOG.md).

This specification defines a text format for jobs built from published steps. It says how such a job is checked before it runs, and what running it must produce. A job written in the format can be shared as a file, and it runs the same way on any conforming runner.

## 1. Introduction

*This section is informative.*

A **step** is a published, versioned unit of work with typed inputs and outputs. It can be a program, an agent, a tool on a remote server, or another workflow. A **workflow** connects steps. Each input of a step gets either a literal value or a reference to a workflow input or to another step's output. A workflow holds no logic: decisions live inside steps.

Systems that run workflows are built in layers. This specification covers the format layer, which includes what a run must produce. Each other layer has its own document or is left to platforms:

| Layer | Covers | Document | Status |
|---|---|---|---|
| format | files, checking, what a run produces | this specification | draft |
| step runtime | how a runner starts one step | [drafts/runtime.md](drafts/runtime.md) | draft, not normative |
| agent access | steps as MCP tools | [drafts/mcp.md](drafts/mcp.md) | draft, not normative |
| service API | starting and watching runs | none | left to platforms |
| registry | publishing and finding steps | none | left to platforms |

Deferred features and open questions are in [proposals.md](proposals.md). Nothing in that file is normative.

How the parts turn into each other:

| From | Action | Becomes |
|---|---|---|
| program | add a manifest, publish | step |
| steps | connect them in a file | workflow |
| workflow | check, then run | run record |
| workflow | publish | step |
| run record | save | workflow |

The design rests on six principles:
1. **Everything runnable has the same interface:** JSON Schema inputs and outputs, declared effects, and a name@version. That interface is an MCP tool definition plus a few fields, so any step can be offered to an agent as a tool.
2. **A workflow holds no logic.** It has no expressions, conditions or loops. This keeps checking simple and complete, and keeps the file readable by the people who review it.
3. **Checking reports every problem at once,** each with a code whose meaning never changes, so an agent can fix a workflow from the codes alone.
4. **Shared files pin everything.** A workflow names exact step versions, and a published workflow's lock file records the content hash of each one.
5. **Steps declare whether they are safe to reuse or retry.** Nothing is cached or retried unless the manifest says it is safe.
6. **A run is a record that can become a workflow.** What an agent worked out once can be saved as a fixed workflow that anyone can rerun.

## 2. Conventions and terminology

*This section is normative.*

### 2.1 Requirement language

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "NOT RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in BCP 14 [[RFC2119](https://www.rfc-editor.org/rfc/rfc2119)] [[RFC8174](https://www.rfc-editor.org/rfc/rfc8174)] when, and only when, they appear in all capitals, as shown here.

Each section says whether it is normative or informative. Examples and notes marked informative are not requirements.

### 2.2 Requirement ids

Each testable requirement begins with an id in bold brackets, such as `[check.cycle]`. An id never changes meaning and is never reused.

[tests/coverage.yaml](tests/coverage.yaml) maps every id to the tests that cover it, or says why it has none yet. `reference/tests/test_coverage.py` fails in three cases:
- an id in this document is missing from that file
- the file names an id this document doesn't have
- a test case covers no id

### 2.3 Terms

| Term | Meaning |
|---|---|
| step | a published unit of work, described by a manifest |
| manifest | the document that describes a step (section 5) |
| step reference | `owner/name@version`, naming one published step exactly |
| catalog | the manifests a workflow can use |
| workflow | a document that connects steps (section 7) |
| step id | the key of a step entry in a workflow's `steps` |
| literal | a value written in a workflow |
| reference | `{from: ...}`: a value taken from a workflow input or from a step's output |
| call | a step run by another step while that step runs (section 5.4) |
| caller | the person or program that asks for a check or a run |
| policy | what a caller may do (section 8.4) |
| run | one execution of a workflow |
| run record | the document a run produces (section 9.9) |
| lock file | the content hash of every step version a published workflow can run (section 10.3) |
| checker, runner, publisher | the conformance classes (section 3) |
| platform | a system that offers a catalog, a publisher and a runner to its callers |

## 3. Conformance

*This section is normative.*

There are three conformance classes. One program can belong to several.

| Class | Does | Requirements |
|---|---|---|
| checker | reads documents and checks workflows | sections 4 to 8 |
| runner | runs workflows | section 9, and section 6.2's [types.file-store] |
| publisher | publishes steps and workflows | section 10 |

- **[conform.checker-tests]** A checker MUST pass every test case in `tests/check/` and `tests/manifests/` (section 8.5).
- **[conform.runner-tests]** A runner MUST pass every test case in `tests/run/`.

*Informative.* This specification says what a run must produce, not how a runner works. A runner can be a hosted service, a command-line tool, or an adapter onto another workflow engine. In practice, a runner has to be able to:
- run a step with JSON values in and JSON values out ([drafts/runtime.md](drafts/runtime.md) describes how, for each runtime kind)
- store files by content hash and hand them to steps
- give each step only the secrets its manifest names
- refuse a workflow that fails checking, and enforce effects and the caller's policy
- follow the order, cache, skip and failure rules, and write a run record

This specification leaves these to platforms:
- sign-in and permissions
- storage and retention
- triggers and schedules, which decide when a workflow starts
- quotas, billing and sandboxing
- the service API and the catalog's API
- how approvals are shown
- editors

## 4. Documents

*This section is normative.*

Manifests, workflows, lock files and run records are JSON documents. Manifests and workflows may also be written in YAML.

- **[doc.formats]** A manifest or workflow MUST be YAML 1.2 or JSON [[RFC8259](https://www.rfc-editor.org/rfc/rfc8259)], and every value in it MUST be one that JSON can hold.
- **[doc.yaml-1.2]** A reader MUST read YAML with the YAML 1.2 core schema, so that `yes`, `no`, `on` and `off` are text, not booleans.
- **[doc.duplicate-keys]** A key that appears twice in one mapping MUST be an error, in YAML and in JSON.
- **[doc.text-keys]** Every key MUST be text. A date MUST stay the text that was written.
- **[doc.merge-key]** The key `<<` MUST NOT appear, whether as a YAML 1.1 merge key or quoted.
- **[doc.numbers]** Numbers MUST follow I-JSON [[RFC7493](https://www.rfc-editor.org/rfc/rfc7493)]: they are finite, and whole numbers are within ±(2^53 − 1). Then every JSON parser reads them exactly, and digests agree (section 10.1).
- **[doc.parse-error]** A checker MUST report a document that can't be read, or that breaks any rule in this section, as `parse_error`, and check nothing else in it.

*Informative.* Many YAML parsers default to YAML 1.1, keep the last of two equal keys without a word, and accept `<<` and huge numbers, so a checker has to test for these itself. GitHub Actions files rely on `on` being text.

## 5. Step manifest

*This section is normative.*

### 5.1 Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | `owner/name`, lowercase |
| `version` | yes | a semantic version, such as `2.1.0` |
| `title` | no | a short display name |
| `description` | yes | what the step does; agents choose steps by it |
| `inputSchema` | yes | the JSON Schema of an object; each property is a named input |
| `outputSchema` | yes | the JSON Schema of an object; each property is a named output |
| `effects` | yes | `none`, `reads` or `writes` (section 5.2) |
| `deterministic` | no, default false | the same input values always give the same outputs |
| `maySkip` | no, default false | the step can finish by skipping when it has nothing to do (section 9.6) |
| `secrets` | no | the names of secrets the step needs, such as `GITHUB_TOKEN` |
| `calls` | no | the steps this step may call while it runs (section 5.4) |
| `runtime` | yes | how the step runs (section 5.3) |
| `examples` | no | sample inputs, and optionally outputs |

- **[manifest.schema]** A manifest MUST conform to `schemas/step.schema.json`.
- **[manifest.extensions]** Fields whose names start with `x-` are reserved for platforms. A checker MUST ignore them.

*Informative.* `name`, `title`, `description`, `inputSchema` and `outputSchema` are the fields of an MCP tool definition. [drafts/mcp.md](drafts/mcp.md) maps a step to a tool.

### 5.2 Effects

`effects` says whether a step reads or changes anything outside its inputs. Section 9 uses it to decide what may be cached and retried:

| `effects` | Touches outside data | Cached | Retried automatically |
|---|---|---|---|
| `none` | no | if deterministic | allowed |
| `reads` | reads only | if deterministic | allowed |
| `writes` | changes it | never | never |

*Informative.*
- A call to a model counts as `reads`.
- A step that reads data which can change for the same inputs, such as "the latest build", is `reads` and not deterministic.
- A step that reads data which can't change once it exists, such as "the logs of finished run 1234", can be `reads` and deterministic, so its results can be cached.

### 5.3 Runtime

`runtime` says how a step runs. It has exactly one key, the runtime kind.

| Kind | Defined in |
|---|---|
| `workflow` | this section |
| `process`, `container`, `agent`, `mcp` | [drafts/runtime.md](drafts/runtime.md) |
| `x-...` | the platform that defines it |

For `workflow`, `path` names a workflow file relative to the folder of the manifest. Section 10.4 says how a published workflow's manifest is derived.

- **[manifest.workflow-file]** The file a `workflow` runtime names MUST exist, be readable, and be a valid workflow (section 7).

### 5.4 Steps that call steps

A step can call other steps while it runs. An agent step that uses steps as tools is the usual case. `calls` lists the exact step versions it may call.

- **[manifest.calls-known]** Each entry in `calls` MUST name a step in the catalog.
- **[manifest.calls-effects]** A step's `effects` MUST be at least the effects of every step in its `calls`, in the order `none`, `reads`, `writes`.
- **[manifest.calls-secrets]** A step's `secrets` MUST include every secret named by the steps in its `calls`.
- **[manifest.calls-one-per-major]** `calls` MUST NOT list two versions of one step with the same major version.

*Informative.*
- Without the effects and secrets rules, a step could do more than its manifest says.
- The one-per-major rule exists because a runtime offers each called step under a name that carries only its major version ([drafts/mcp.md](drafts/mcp.md)).
- Section 9.8 says what a runner does with calls.

### 5.5 When a manifest is invalid

- **[manifest.json-schema]** `inputSchema` and `outputSchema` MUST be valid JSON Schemas (2020-12).
- **[manifest.required-declared]** Every name listed in `required` MUST be declared in `properties`.
- **[manifest.default-fits]** An input's `default` MUST fit that input's schema.
- **[manifest.examples-fit]** An example's `inputs` and `outputs` MUST fit the manifest's schemas.
- **[manifest.refs]** A schema MUST NOT use `$ref` for anything but `urn:wfspec:file`, and MUST NOT use `$defs`, `$id`, `$anchor` or `$dynamicRef` (section 6.1).
- **[manifest.invalid]** A checker MUST report a manifest that breaks a rule in section 5 as `invalid_manifest`, except that an unknown entry in `calls` is reported as `unknown_step` at that entry. It MUST leave the manifest out of the catalog.
- **[manifest.left-out]** A workflow that names a manifest left out of the catalog MUST get `unknown_step`, with a message saying why the manifest was left out. Leaving a manifest out can leave another manifest calling a step that is no longer in the catalog, so a checker MUST apply the rules in section 5.4 again until no more manifests are left out.
- **[manifest.duplicate]** When two manifests in one catalog have the same name and version, a checker MUST report `duplicate_step` and keep the first one it read.

### 5.6 Example

*This section is informative.*

```yaml
name: acme/fetch-ci-logs
version: 2.1.0
description: Download the logs of one finished CI run as a text file.
inputSchema:
  type: object
  properties:
    repo: { type: string, pattern: "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$" }
    run_id: { type: integer, minimum: 1 }
  required: [repo, run_id]
outputSchema:
  type: object
  properties:
    logs: { $ref: urn:wfspec:file, x-mediaType: text/plain }
  required: [logs]
effects: reads
deterministic: true       # a finished run's logs never change
secrets: [GITHUB_TOKEN]
runtime:
  container: { image: ghcr.io/acme/fetch-ci-logs:2.1.0, command: [fetch-ci-logs] }
```

More examples are in `examples/steps/`:
- `open-issue.yaml`: a step that writes
- `triage-logs.yaml`: an agent step without calls
- `investigate-failure.yaml`: an agent step with calls

## 6. Values and types

*This section is normative.*

### 6.1 Schemas

- **[types.json-schema]** Inputs and outputs are described with JSON Schema 2020-12, the version MCP uses. Input and output names MUST match `^[A-Za-z_][A-Za-z0-9_-]*$`, so they can appear in references.
- **[types.format]** `format` is an annotation. A checker or runner MUST NOT treat a value as invalid because of `format`.

The only reference a schema may use is `{"$ref": "urn:wfspec:file"}`. See [manifest.refs], and [workflow.input-schema] for a workflow's own `inputSchema`.

*Informative.* Whether a JSON Schema validator enforces `format` depends on which libraries are installed, so treating it as an annotation keeps implementations in agreement. A step that needs, say, a valid URI checks it itself. Other references are left for a later version, so that a schema can always be checked on its own.

### 6.2 Files

Files never travel inline. A file value is:

```json
{ "digest": "sha256:<64 hex>", "mediaType": "text/plain", "size": 48213, "name": "run-1234.log" }
```

- In a schema, a file is written `{"$ref": "urn:wfspec:file"}`. An optional `x-mediaType` narrows it, for example to `text/plain`, and an input may accept a family of types with a wildcard such as `text/*`.
- **[types.file-value]** A file value MUST fit `schemas/file.schema.json`.
- **[types.file-store]** A runner MUST store files by digest, so a file's identity is its content.

### 6.3 When a value fits an input

When a reference connects an output, or a workflow input, to a step's input, the checker compares the two schemas:
- **[types.fits-error]** A checker MUST report `type_mismatch` when the source can produce a value of the wrong shape.
- **[types.fits-warning]** A checker MUST report `type_unchecked`, a warning, when the shapes fit but a limit on the input can't be proven from the source's schema. Examples of such limits are a range, a pattern or a list of allowed values. The value is then checked when the step runs ([run.input-check]).
- **[types.maybe-missing]** A value that may be missing MUST NOT feed a required input that has no default; a checker reports `type_mismatch`. A value may be missing when it is an output its step doesn't list as required, or an optional workflow input without a default.

*Informative.* The comparison, as pseudocode:

```text
fits(src, dst):                                   # both with annotations (and format) removed
  if dst accepts anything or src equals dst as JSON: ok          # true isn't 1; 1 is 1.0
  if either is a file:
     src must be a file (or dst a plain object); media types must match, "text/*" matching "text/plain"
     dst names a media type and src doesn't         -> warning
  if dst uses $ref (other than files) or anyOf/oneOf/allOf/not/if...: warning, unless equal
  types: every type src can have must be accepted by dst ("integer" is accepted by "number");
         an enum or const on src implies its types
         src declares no type                       -> warning
  enum/const on dst: every value src can produce is allowed (JSON equality) -> else error
                     src doesn't limit its values               -> warning
  limits on dst (minimum, maxLength, pattern, ...): proven by a limit on src at least as tight;
                     an exclusive limit proves an inclusive one at the same value, and an
                     inclusive one proves an exclusive one only when strictly inside it
                                                                -> else warning
  objects: each property dst requires, src must require         -> else error
              (warning instead if src declares no properties)
           shared properties: fits(src.p, dst.p), recursively
           dst forbids extra properties and src declares some dst lacks -> error
              (warning if src declares none but doesn't forbid extras either)
  arrays:  fits(src.items, dst.items); src declares no item type -> warning
```

## 7. Workflow file

*This section is normative.*

### 7.1 Fields

| Field | Required | Meaning |
|---|---|---|
| `name`, `version` | to publish | as for steps |
| `title`, `description` | `description` to publish | as for steps |
| `inputSchema` | no | the workflow's own inputs, which a form, an API call or a parent workflow fills in |
| `outputs` | no | output name to reference |
| `steps` | yes | step id to `{uses, with}` |

- **[workflow.schema]** A workflow MUST conform to `schemas/workflow.schema.json`. A step id matches `^[a-z][a-z0-9_-]*$`. `uses` is a step reference with an exact version. There are no version ranges, because a shared file must mean one thing.
- **[workflow.input-schema]** A workflow's `inputSchema` MUST be a valid JSON Schema of an object. It MUST NOT use references other than to files, and it MUST declare every name it lists in `required`.
- **[workflow.input-default]** The `default` of each workflow input MUST fit that input's schema.

### 7.2 Values in `with`

Each input in `with` takes one of three forms:

| Form | Example | Means |
|---|---|---|
| literal | `style: short` | this value |
| reference | `logs: { from: steps.logs.outputs.logs }` | another step's output, or a workflow input |
| wrapped literal | `extra: { literal: { from: x } }` | this value, for the rare literal shaped like a reference |

- **[workflow.reference-form]** An object whose only key is `from` MUST be read as a reference, whatever its value. A reference is `inputs.<name>` or `steps.<id>.outputs.<name>`, and it passes the whole value.
- **[workflow.literal-form]** An object whose only key is `literal` MUST be read as a wrapped literal, and its value used as it is.

Picking a field out of an output isn't in this version ([proposals.md](proposals.md)).

### 7.3 Example

*This section is informative.*

```yaml
name: acme/ci-triage
version: 1.2.0
description: Read a failed CI run's logs, write a short report, and file it as an issue.
inputSchema:
  type: object
  properties:
    repo: { type: string, pattern: "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$" }
    run_id: { type: integer, minimum: 1 }
    style: { type: string, enum: [short, long], default: short }
  required: [repo, run_id]
outputs:
  report: { from: steps.triage.outputs.report }
  issue: { from: steps.file.outputs.url }
steps:
  logs:
    uses: acme/fetch-ci-logs@2.1.0
    with: { repo: { from: inputs.repo }, run_id: { from: inputs.run_id } }
  triage:
    uses: acme/triage-logs@0.4.1
    with: { logs: { from: steps.logs.outputs.logs }, style: { from: inputs.style } }
  file:
    uses: acme/open-issue@1.0.3
    with:
      repo: { from: inputs.repo }
      title: { from: steps.triage.outputs.title }
      body: { from: steps.triage.outputs.report }
      labels: [ci-failure]
```

### 7.4 What a workflow can't say

*This section is informative.*

A workflow has no conditions, loops or expressions. Where one is needed:
- **A fixed rule** goes in a small step that computes it.
- **A judgment** goes in an agent step with a typed output, such as `category: { enum: [test, build, infrastructure, flaky] }`.
- **"Only if there's something to do"** is a step with `maySkip`. When it skips, the steps after it are skipped too (section 9.6).
- **A sequence that varies from run to run** goes in a step that calls other steps (section 5.4).

## 8. Checking

*This section is normative.*

### 8.1 The rules

`check(workflow, catalog, policy?)` returns every error and warning at once. A run never starts while there are errors ([run.check-first]).

- **[check.all-at-once]** A checker MUST report every problem in a workflow. It MAY stop after an `invalid_workflow` that comes from the workflow schema, and after `no_outputs`.

| Requirement | A checker reports | When |
|---|---|---|
| **[check.invalid-workflow]** | `invalid_workflow` | the workflow breaks [workflow.schema] or [workflow.input-schema] |
| **[check.invalid-value]** | `invalid_value` | a literal in `with`, or a workflow input's default, doesn't fit its input |
| **[check.unknown-step]** | `unknown_step` | `uses` isn't in the catalog; the message lists the versions it has, or why the manifest was left out |
| **[check.unknown-input]** | `unknown_input` | `with` names an input the step doesn't have; the message suggests a close name |
| **[check.missing-input]** | `missing_input` | a required input has no value and no default |
| **[check.bad-reference]** | `bad_reference` | a reference isn't `inputs.<name>` or `steps.<id>.outputs.<name>`, or its `from` isn't text |
| **[check.unknown-reference]** | `unknown_reference` | a reference names a missing input, step or output |
| **[check.type-mismatch]** | `type_mismatch` | [types.fits-error] or [types.maybe-missing] |
| **[check.type-unchecked]** | `type_unchecked`, a warning | [types.fits-warning] |
| **[check.outputs-resolve]** | `bad_reference` or `unknown_reference` | a workflow output doesn't resolve the way a reference must |
| **[check.cycle]** | `cycle` | references form a loop |
| **[check.no-outputs]** | `no_outputs` | the workflow has no outputs and no step that writes, and every step is known |
| **[check.effects-not-allowed]** | `effects_not_allowed` | a step's effects are above the policy's `maxEffects` |
| **[check.secret-not-granted]** | `secret_not_granted` | a step needs a secret the policy doesn't grant |
| **[check.step-never-runs]** | `step_never_runs`, a warning | no output needs a step, and it doesn't write |
| **[check.unused-input]** | `unused_input`, a warning | nothing uses a workflow input |

*Informative.* An order a checker can follow:

```text
check(W, catalog, policy):
  W breaks workflow.schema.json                       -> invalid_workflow (stop here)
  W.inputSchema invalid, uses a reference other than a file,
    or lists required names it doesn't declare       -> invalid_workflow
  a workflow input's default doesn't fit              -> invalid_value
  for each step s:
    s.uses not in the catalog                         -> unknown_step
    for each name, value in s.with:
      name not an input of s.uses                     -> unknown_input
      value is a reference:
        not inputs.<n> or steps.<id>.outputs.<n>      -> bad_reference
        names a missing input, step or output         -> unknown_reference
        source may be missing, input is required      -> type_mismatch
        fits(source schema, input schema) has errors  -> type_mismatch
                                       only warnings  -> warning type_unchecked
      value is a literal and doesn't fit the input    -> invalid_value
    a required input without a value or a default     -> missing_input
    policy.maxEffects below s's effects               -> effects_not_allowed
    s needs a secret not in policy.secrets            -> secret_not_granted
  each workflow output resolves like a reference      -> bad_reference, unknown_reference
  references form a loop                              -> cycle
  no outputs, no step that writes, all steps known    -> no_outputs (stop here)
  a step no output needs and that doesn't write       -> warning step_never_runs
  a workflow input nothing uses                       -> warning unused_input
```

### 8.2 Codes

| Code | Level | Requirement |
|---|---|---|
| `parse_error` | error | [doc.parse-error] |
| `invalid_workflow` | error | [check.invalid-workflow] |
| `invalid_manifest` | error | [manifest.invalid] |
| `unknown_step` | error | [check.unknown-step], [manifest.invalid] |
| `unknown_input` | error | [check.unknown-input] |
| `missing_input` | error | [check.missing-input] |
| `bad_reference` | error | [check.bad-reference] |
| `unknown_reference` | error | [check.unknown-reference] |
| `type_mismatch` | error | [check.type-mismatch] |
| `invalid_value` | error | [check.invalid-value] |
| `cycle` | error | [check.cycle] |
| `no_outputs` | error | [check.no-outputs] |
| `effects_not_allowed` | error | [check.effects-not-allowed] |
| `secret_not_granted` | error | [check.secret-not-granted] |
| `duplicate_step` | error, in a catalog | [manifest.duplicate] |
| `type_unchecked` | warning | [check.type-unchecked] |
| `step_never_runs` | warning | [check.step-never-runs] |
| `unused_input` | warning | [check.unused-input] |

- **[check.codes-stable]** A code MUST keep its meaning in every later version of this specification, and new checks MUST get new codes. Messages are for people and MAY change.

### 8.3 Output

```json
{
  "ok": false,
  "errors": [
    { "code": "unknown_input", "path": "/steps/take/with/txt", "step": "take", "input": "txt",
      "message": "test/take-values@1.0.0 has no input 'txt'; did you mean 'text'?" }
  ],
  "warnings": []
}
```

- **[check.output]** A checker's result MUST have `ok`, `errors` and `warnings`. Each problem MUST have a `code` and a `path`, a JSON Pointer [[RFC6901](https://www.rfc-editor.org/rfc/rfc6901)] into the workflow file. A problem MAY also have `step`, `input` and `message`. `ok` is true when there are no errors; warnings don't block a run.

### 8.4 Caller policy

A policy says what the person or agent asking for a check or a run may do:

```yaml
maxEffects: reads   # none | reads | writes
secrets: []         # the secrets this caller may use; leave the key out to skip this check
```

- **[check.policy]** Given a policy, a checker MUST apply it ([check.effects-not-allowed], [check.secret-not-granted]), so that an agent finds out before a run that it can't use a step.

The platform decides each caller's policy.

### 8.5 Test cases

- `tests/check/<case>/` holds a `workflow.yaml` or `workflow.json`, an `expected.yaml` and, optionally, a `policy.yaml`. The catalog for these cases is `examples/steps/` plus `tests/catalog/`, plus the case's own `catalog/` folder when it has one. A case's own catalog may hold manifests that are meant to be left out.
- `tests/manifests/<case>/` holds manifests in `steps/` and an `expected.yaml`.
- An implementation passes a case when its errors and warnings have exactly the expected `(code, path)` pairs, as multisets.

## 9. Running

*This section is normative for runners. No runner has been tested against it yet, so [tests/coverage.yaml](tests/coverage.yaml) lists each of its requirements as untested.*

### 9.1 Before a run

- **[run.check-first]** A runner MUST check a workflow under the caller's policy before running it, and MUST NOT start a run that has errors. A run that never starts gets the error code `check_failed` in its record.
- **[run.lock]** A runner MUST NOT run a published workflow when any step version it can run no longer matches the workflow's lock file (section 10.3).

### 9.2 Which steps run

```text
roots  := steps that workflow outputs reference, plus every step whose effects are writes
needed := roots, plus every step they depend on through references, transitively
```

- **[run.needed]** A runner MUST run the needed steps and no others. The checker warns about the others ([check.step-never-runs]).

### 9.3 Order, values and checks

- **[run.order]** A needed step MUST NOT start before every step it references has finished. Steps that don't depend on each other SHOULD run in parallel.
- **[run.missing-value]** When a referenced value is missing, the input it feeds MUST be left unset, so the input's default applies. A value is missing when it is an optional output its step didn't produce, or an optional workflow input nobody gave. If the input has no default, the step runs without it; section 6.3 allows this only for optional inputs.
- **[run.input-check]** Before a step starts, a runner MUST fill in defaults and check the input values against the step's `inputSchema`. Values that don't fit fail the step with `invalid_input`.
- **[run.output-check]** A runner MUST check a step's outputs against its `outputSchema`. Outputs that don't fit fail the step with `invalid_output`.

### 9.4 Cache

```text
cacheable(s) := s.deterministic and s.effects != writes
key(s)       := digest({ "step": digest of s's manifest,
                         "inputs": s's input values, defaults filled in,
                                   each file value cut down to its digest and mediaType })
```

- **[run.cache-key]** A runner that caches MUST compute the key as above, just before the step would run, from the values the step receives.
- **[run.cache-only-cacheable]** A runner MUST NOT reuse stored outputs for a step that isn't cacheable. When it does reuse them, the step's record MUST show `cacheHit: true`.

*Informative.*
- A file counts by its content and type, not its name, and a large file costs nothing extra to hash.
- ComfyUI keys each node on the keys of the nodes upstream. Keying on values instead means a step upstream that isn't deterministic, such as an agent, can't cause a stale cache hit downstream.

### 9.5 Retries

- **[run.retry]** A runner MAY retry a failed step automatically only when its effects aren't `writes`. `attempts` in the step's record counts the tries; 0 means a cache hit.

### 9.6 Failure and skipping

- **[run.failure]** When a step fails, every step that depends on it MUST be marked `skipped`. Steps that don't depend on it still finish. The run's status is `failed`, and finished outputs stay in the record.
- **[run.skip]** A step whose manifest has `maySkip` MAY finish with a reason instead of outputs, when it has nothing to do. The step and every step that depends on it are then marked `skipped`, with the reason. The run still succeeds, and workflow outputs that depend on a skipped step are left out of the run's outputs.
- **[run.error-codes]** A runner MUST record why a step failed with one of these codes: `invalid_input` and `invalid_output` (section 9.3), `timeout`, or `failed` when the step itself reported failure.

How a step says it skips or fails depends on its runtime kind ([drafts/runtime.md](drafts/runtime.md)). Skipping is how a workflow says "only if there is something to do" without conditions.

### 9.7 Secrets

- **[run.secrets-named]** A runner MUST give a step only the secrets its manifest names.
- **[run.secrets-hidden]** A runner MUST NOT write secret values into run records, logs or caches.

### 9.8 Effects, policy and calls

- **[run.effects]** A runner MUST NOT let a step act beyond its declared effects or the caller's policy. How it enforces this is its choice. It can, for example, deny network access to a `none` step, or ask for approval before a `writes` step when the caller's policy requires it.
- **[run.calls-listed]** A runner MUST let a step call only the steps in its `calls`, and MUST run each call as a step under the same caller policy, with the input, output, effects and secrets rules.
- **[run.calls-recorded]** A runner MUST record every call, in order, under the calling step's `calls` in the run record, with its inputs, outputs and step digest.

### 9.9 Run record

- **[run.record]** A runner MUST write a run record that fits `schemas/run.schema.json`.

A run record holds:
- the workflow's digest and its lock file
- the run's inputs and outputs
- the caller, and timestamps
- an OpenTelemetry trace id, so logs and traces can be found
- for a run that failed or never started, an `error` with a message and a code
- for each step: its version and digest, cache key and whether the cache was hit, attempts, inputs, outputs and error
- for each call a step made: the same facts as for a step

Examples are in `examples/runs/`.

## 10. Publishing and versions

*This section is normative for publishers.*

### 10.1 Digests

- **[publish.digest]** A digest MUST be `sha256:` followed by the SHA-256 of the document's canonical JSON as defined by RFC 8785, the JSON Canonicalization Scheme [[RFC8785](https://www.rfc-editor.org/rfc/rfc8785)].
- **[publish.resolve]** Before computing a step's digest, a publisher MUST resolve everything in the manifest that could change later. [drafts/runtime.md](drafts/runtime.md) says what that means for each runtime kind. A container image tag, for example, becomes `image@sha256:...`.

*Informative.* With RFC 8785, every language computes the same digest; for example, `1.0` and `1` are written the same way. The reference implementation hashes manifests as written, because it doesn't build images.

### 10.2 Versions never change

- **[publish.immutable]** A publisher MUST refuse to publish different content under a name and version it already has. Lock files and caches depend on this.

### 10.3 Lock file

- **[publish.lock]** Publishing a workflow MUST write a lock file that fits `schemas/lock.schema.json`. The lock file maps every step version the workflow can run, directly or not, to its digest. That includes:
  - each step's `uses`
  - every step in their `calls`
  - the steps inside workflows used as steps, and so on down

`examples/ci-triage.lock.json` is an example. A runner refuses a workflow whose lock no longer matches ([run.lock]).

### 10.4 A workflow is a step

- **[publish.as-step]** A published workflow MUST get a manifest derived from its contents as follows:

| Field | Derived from |
|---|---|
| `inputSchema` | the workflow's `inputSchema` |
| `outputSchema` | each output's source schema; an output is required only when its source is required and no step the source depends on has `maySkip` |
| `effects` | the highest effects among the needed steps |
| `deterministic` | true only if every needed step is |
| `secrets` | every secret the needed steps name |
| `runtime` | `{ workflow: { path } }` |

Other workflows can then use the workflow like any step.

### 10.5 Versions

`uses` always names an exact version.

- **[publish.versions]** A new version SHOULD bump the major number when any of these hold, and a publisher SHOULD check that it does:
  - an input is added and required
  - an input or output is removed
  - an output stops being required
  - an input accepts fewer values
  - an output can produce more kinds of values
  - `effects` goes up

Other changes bump the minor or patch number.

### 10.6 Saving a run as a workflow

From a run record, a person or an agent writes a workflow:
- **Steps** the run executed stay as they are, except steps that made calls.
- **Steps that made calls** are replaced by the calls they made, each a step with the same `uses`.
- **Values a step made up between calls**, such as the issue text an agent wrote, become the outputs of a narrower step with a typed schema, or fixed values.
- **Values** that came out of an earlier step become references to it. Values that came from the run's inputs become workflow inputs. The rest become literals.

- **[publish.saved-run]** A saved workflow MUST be accepted only when it checks without errors and a dry run with the original inputs gives the same outputs for every deterministic step. A dry run executes every step except those that write. Each step that writes returns the outputs recorded in the original run, so nothing outside changes a second time.

*Informative.* `examples/runs/investigate.run.json` records an agent step (`acme/investigate-failure`) that fetched logs and opened an issue. `examples/workflows/ci-triage.yaml` is that run saved as a workflow:
- The two calls became fixed steps.
- The agent's reasoning became a narrower agent step, `acme/triage-logs`, with a typed output.

`examples/runs/ci-triage.run.json` is a later run of the saved workflow with a different `style`. The logs came from the cache, and only the agent and the step that writes ran again.

## 11. Security considerations

*This section is informative.*

- **Steps are other people's code.** `effects`, `secrets` and `calls` are claims made by a manifest's author. A platform enforces them ([run.effects], [run.secrets-named], [run.calls-listed]), typically by sandboxing each step.
- **Steps that call steps can be steered by what they read.** An agent step that reads untrusted text, such as an issue or a log, can be led to call its steps in ways its author didn't intend. Its `calls`, its effects and the caller's policy bound what it can do, and every call is recorded. Give such a step only the calls it needs, and prefer calls that only read.
- **Secrets** reach only the steps that name them, and never records, logs or caches ([run.secrets-named], [run.secrets-hidden]). Because a step's secrets include those of its calls, the manifest shows everything the step can reach.
- **Caches can cross callers.** A cached output computed for one caller can be served to another with the same inputs. A platform should decide how far a cache is shared, because outputs can contain private data.
- **YAML aliases can expand without limit**, an attack known as the "billion laughs". Readers should limit document size and alias expansion.
- **Pinning protects shared workflows.** Lock files ([publish.lock]) and versions that never change ([publish.immutable]) stop a shared workflow from silently running different code.
- **MCP annotations are hints.** [drafts/mcp.md](drafts/mcp.md) says how a platform's own server is trusted.

## 12. Not in this version

*This section is informative.*

[proposals.md](proposals.md) lists features left out of this version, each with the problem it would solve and what would settle it:
- plans
- retrying steps that write
- per-step resource limits
- labels on workflow steps
- picking a field out of an output
- running a step once per item
- generic steps
- references inside schemas
- lazy inputs
- automatic upgrades
- running examples at publish
- layout for visual editors
- triggers, the service API and the registry API

## 13. Where the ideas come from

*This section is informative.*

| Idea | From |
|---|---|
| step descriptions served as data; literals and links; check before running; run only what outputs need; cache by inputs; graphs used as steps | ComfyUI v0.44.2 (`execution.py`, `comfy_execution/`) |
| `uses: owner/name@version` and `with` | GitHub Actions |
| tool interface and effect hints | MCP, revision 2026-07-28 |
| versioned, typed components; a determinism flag for reuse | Azure ML pipelines (`is_deterministic`) |
| files addressed by content hash; releases that never change | Comfy API v2 |
| one specification per layer | GA4GH (CWL and WDL, WES, TES, TRS) |

## 14. References

*This section is informative; the normative references are those cited in normative sections.*

Normative:
- [RFC2119] Key words for use in RFCs to Indicate Requirement Levels. https://www.rfc-editor.org/rfc/rfc2119
- [RFC8174] Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words. https://www.rfc-editor.org/rfc/rfc8174
- [RFC8259] The JavaScript Object Notation (JSON) Data Interchange Format. https://www.rfc-editor.org/rfc/rfc8259
- [RFC7493] The I-JSON Message Format. https://www.rfc-editor.org/rfc/rfc7493
- [RFC8785] JSON Canonicalization Scheme (JCS). https://www.rfc-editor.org/rfc/rfc8785
- [RFC6901] JavaScript Object Notation (JSON) Pointer. https://www.rfc-editor.org/rfc/rfc6901
- JSON Schema 2020-12. https://json-schema.org/draft/2020-12
- YAML 1.2.2. https://yaml.org/spec/1.2.2/

Informative:
- Model Context Protocol, revision 2026-07-28. https://modelcontextprotocol.io/specification
- GitHub Actions workflow syntax. https://docs.github.com/actions/reference/workflow-syntax-for-github-actions
- GA4GH Workflow Execution Service. https://github.com/ga4gh/workflow-execution-service-schemas
- OpenTelemetry. https://opentelemetry.io/docs/specs/otel/
