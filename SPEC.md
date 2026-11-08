# Workflow format for shared jobs

Version 0.1, draft, 2026-11-07.

This spec defines a text format for building jobs out of published steps. A job written in it can be checked before it runs, runs the same way for everyone, and can be shared as a file. A step can be:
- code in a container
- an agent
- a tool on a remote MCP server
- another workflow

The spec is meant for:
- teams building a platform that runs these jobs: the runner, the step catalog, the MCP server, editors
- people who publish steps
- whoever writes workflows, often an agent

Two kinds of file sit next to it:
- **Schemas** in `schemas/`, which define each file format exactly.
- **Test cases** in `tests/`, which define what checking must report. The reference checker in `reference/` passes all of them.

"Must" and "should" carry their usual specification meaning: a "must" is required, and a "should" can be broken only for a stated reason.

## 1. The parts

| Part | Where it lives | Schema |
|---|---|---|
| Step manifest | a file next to the step's code | `schemas/step.schema.json` |
| Workflow | a file people share | `schemas/workflow.schema.json` |
| File value | inside inputs and outputs | `schemas/file.schema.json` |
| Lock file | written when a workflow is published | `schemas/lock.schema.json` |
| Run record | stored by the platform after each run | `schemas/run.schema.json` |

How the parts turn into each other:

| From | Action | Becomes |
|---|---|---|
| Repository | add a manifest, publish | step |
| Steps | connect them in a file | workflow |
| Workflow | check, then run | run record |
| Workflow | publish | step |
| Run record | save | workflow |

## 2. Design rules

1. **Everything runnable has the same interface.** Every step, whether it is a container, an agent, a remote tool or a workflow, has:
   - JSON Schema inputs and outputs
   - declared effects (whether it reads or changes anything outside its inputs)
   - a name@version

   That interface is an MCP tool definition plus a few fields, so any step can be offered to an agent as a tool.
2. **A workflow holds no logic.** It has no expressions, conditions or loops. Decisions live in steps, written as code or made by agents. This keeps checking simple and complete, and keeps the file readable by people who review it.
3. **Checking reports every problem at once.** Each problem carries a code whose meaning never changes, so an agent can fix a workflow from the codes alone.
4. **Shared files pin everything.** A published workflow names exact step versions, and its lock file records the content hash of each one.
5. **Steps declare whether they are safe to reuse or retry.** Nothing is cached or retried unless the step's manifest says it is safe.
6. **A run is a record that can become a workflow.** What an agent worked out once can be saved as a fixed workflow that anyone can rerun.

## 3. Step manifest

### 3.1 Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | `owner/name`, lowercase |
| `version` | yes | semantic version, such as `2.1.0` |
| `title` | no | short display name |
| `description` | yes | what the step does; agents choose steps by it |
| `inputSchema` | yes | JSON Schema of an object; each property is a named input |
| `outputSchema` | yes | JSON Schema of an object; each property is a named output |
| `effects` | yes | `none`, `reads` or `writes` (3.2) |
| `deterministic` | no, default false | the same input values always give the same outputs |
| `idempotent` | no, default false | for `writes` steps: repeating a call with the same inputs changes nothing more |
| `destructive` | no, default true | for `writes` steps: false when the step only adds things |
| `secrets` | no | names of secrets the step needs, such as `GITHUB_TOKEN` |
| `runtime` | yes | how it runs (3.3) |
| `resources` | no | `timeout` (`30s`, `5m`, `2h`), `cpu`, `memory` (`512Mi`, `2Gi`) |
| `examples` | no | sample inputs, and optionally outputs, for tests and for agents |

`name`, `title`, `description`, `inputSchema` and `outputSchema` are the fields of an MCP tool definition. Fields starting with `x-` are reserved for platforms and are ignored by checking.

### 3.2 Effects

| `effects` | Touches outside data | Cached | Retried automatically | MCP hints |
|---|---|---|---|---|
| `none` | no | if deterministic | yes | read-only, closed world |
| `reads` | reads only | if deterministic | yes | read-only, open world |
| `writes` | changes it | never | only if idempotent | not read-only; destructive and idempotent from the manifest |

- A model call counts as `reads`.
- A step that reads data which can change for the same inputs (for example "the latest build") is `reads` but not deterministic.
- A step that reads data which can't change once it exists (for example "the logs of finished run 1234") can be `reads` and deterministic. That makes it cacheable.
- A platform should enforce `effects`. For example, it can deny network access to a `none` step, and require approval for a `writes` step when the caller's policy asks for it.

### 3.3 Runtime kinds

`runtime` has exactly one key.

**`container`**: `image`, and optionally `command`. The contract between the platform and the container:
- `WF_INPUTS` is the path of a JSON file holding the input values, with defaults filled in. Each file value also gets a `path` field: a local, read-only copy of the file.
- The step writes a JSON object of its outputs to the path in `WF_OUTPUTS`. For a file output it writes `{"path": "...", "mediaType": "..."}`. The platform stores the file and replaces the value with a file value (section 4.2).
- Each secret named in `secrets` is set as an environment variable with the same name.
- Exit code 0 means success. stdout and stderr are the step's logs.
- The outputs are checked against `outputSchema`. A mismatch fails the step with the error code `invalid_output`.

**`agent`**: `model`, `instructions` (a file in the step's repository), and optionally `tools` (exact step versions), `maxTurns` and `mayPlan`.
- The platform runs the model with the instructions and the input values.
- The agent can call only the steps listed in `tools`, each offered as a tool.
- Every call is recorded in the run record's `calls`.
- The final answer must fit `outputSchema`. The platform may hand the validation error back to the agent to fix; each attempt counts toward `maxTurns`.
- With `mayPlan`, the agent may return a plan instead of an answer (section 7.7).

**`workflow`**: `path` to a workflow file. Section 8.3 says how its manifest is derived.

**`mcp`**: `server` (a URL) and `tool`, for a tool on a remote MCP server. Its code can't be pinned by content hash, so the manifest's author vouches for `effects` and `deterministic`. A platform should treat such a step as not deterministic unless it trusts the server's owner.

**`x-...`**: a platform-specific kind, such as a workflow for another agent framework.

### 3.4 When a manifest is invalid

Checking reports `invalid_manifest` when any of these hold:
- The manifest breaks `schemas/step.schema.json`.
- `inputSchema` or `outputSchema` isn't a valid JSON Schema.
- A name listed in `required` isn't declared in `properties`.
- An input's `default` doesn't fit that input's schema.
- An example's `inputs` or `outputs` don't fit the manifest's schemas.
- `idempotent` or `destructive` appears on a step that isn't `writes`.
- An agent's `effects` are lower than those of a tool it can call.

An agent's tool that isn't in the catalog is reported as `unknown_step`. Test cases: `tests/manifests/`.

### 3.5 Example

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
- a container that writes: `open-issue.yaml`
- a narrow agent: `triage-logs.yaml`
- a broad agent with tools: `investigate-failure.yaml`

## 4. Values and types

### 4.1 Schemas

Inputs and outputs are described with JSON Schema 2020-12, the version MCP uses. Input and output names match `^[A-Za-z_][A-Za-z0-9_-]*$`, so they can appear in reference paths.

### 4.2 Files

Files never travel inline. A file value is:

```json
{ "digest": "sha256:<64 hex>", "mediaType": "text/plain", "size": 48213, "name": "run-1234.log" }
```

- In a schema, a file is written `{"$ref": "urn:wfspec:file"}`.
- An optional `x-mediaType` narrows it, for example `text/plain`.
- An input may accept a family of types with a wildcard, such as `text/*`.
- Platforms store files by digest, so a file's identity is its content.

### 4.3 When a value fits an input

When a reference connects an output (or a workflow input) to a step's input, the checker compares the two schemas. Errors and warnings mean different things:
- **Error, `type_mismatch`:** the source can produce a value of the wrong shape.
- **Warning, `type_unchecked`:** the shapes fit, but a limit on the input can't be proven from the source's schema. Examples of such limits are a range, a pattern or a list of allowed values. The value is then checked when the step runs, and a failure there has the error code `invalid_input`.

```text
fits(src, dst):                                   # both with annotations removed
  if dst accepts anything or src == dst: ok
  if either is a file:
     src must be a file (or dst a plain object); media types must match, "text/*" matching "text/plain"
     dst names a media type and src doesn't         -> warning
  if dst uses $ref (other than files) or anyOf/oneOf/allOf/not/if...: warning, unless equal
  types: every type src can have must be accepted by dst ("integer" is accepted by "number")
         src declares no type                       -> warning
  enum/const on dst: every value src can produce is allowed     -> else error
                     src doesn't limit its values               -> warning
  limits on dst (minimum, maxLength, pattern, format, ...): proven by an equal or tighter
                     limit on src                               -> else warning
  objects: each property dst requires, src must require         -> else error
              (warning instead if src declares no properties)
           shared properties: fits(src.p, dst.p), recursively
           dst forbids extra properties and src declares some dst lacks -> error
  arrays:  fits(src.items, dst.items)
```

A value that may be missing can't feed a required input. That covers two cases: an output the source step doesn't list as required, and an optional workflow input with no default. This is a `type_mismatch` error.

## 5. Workflow file

### 5.1 Fields

| Field | Required | Meaning |
|---|---|---|
| `name`, `version` | to publish | as for steps |
| `title`, `description` | `description` to publish | as for steps |
| `inputSchema` | no | the workflow's own inputs; what a form, an API call or a parent workflow fills in |
| `outputs` | no | output name to reference |
| `steps` | yes | step id to `{uses, with}` |

A step id matches `^[a-z][a-z0-9_-]*$`. `uses` is `owner/name@version` with an exact version. There are no version ranges, because a shared file must mean one thing.

### 5.2 Values in `with`

Each input in `with` takes one of three forms:

| Form | Example | Means |
|---|---|---|
| literal | `style: short` | this value |
| reference | `logs: { from: steps.logs.outputs.logs }` | another step's output, or a workflow input |
| wrapped literal | `extra: { literal: { from: x } }` | this value, for the rare literal shaped like a reference |

A reference is either `inputs.<name>` or `steps.<id>.outputs.<name>`, and it passes the whole value. Picking a field out of an output isn't in version 0.1 (section 11).

### 5.3 Example

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

### 5.4 What a workflow can't say

A workflow has no conditions, loops or expressions. Where one is needed:
- **A fixed rule** goes in a small step that computes it.
- **A judgment** goes in an agent step with a typed output, such as `category: { enum: [test, build, infrastructure, flaky] }`.
- **A sequence that varies from run to run** goes in an agent step with `mayPlan` (section 7.7). The plan it chooses is checked and recorded, then run.

## 6. Checking

### 6.1 The rules

`check(workflow, catalog, policy?)` returns every error and warning at once. Nothing should run unless there are no errors.

```text
check(W, catalog, policy):
  W breaks workflow.schema.json                       -> invalid_workflow (stop here)
  W.inputSchema invalid, or required names undeclared -> invalid_workflow
  a workflow input's default doesn't fit              -> invalid_value
  for each step s:
    s.uses not in the catalog                         -> unknown_step  (message lists the versions it has)
    for each name, value in s.with:
      name not an input of s.uses                     -> unknown_input (message suggests a close name)
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
  no outputs and no step that writes                  -> no_outputs (stop here)
  a step no output needs and that doesn't write       -> warning step_never_runs
  a workflow input nothing uses                       -> warning unused_input
```

### 6.2 Codes

| Code | Level |
|---|---|
| `parse_error` | error |
| `invalid_workflow` | error |
| `invalid_manifest` | error |
| `unknown_step` | error |
| `unknown_input` | error |
| `missing_input` | error |
| `bad_reference` | error |
| `unknown_reference` | error |
| `type_mismatch` | error |
| `invalid_value` | error |
| `cycle` | error |
| `no_outputs` | error |
| `effects_not_allowed` | error |
| `secret_not_granted` | error |
| `duplicate_step` | error, in a catalog |
| `type_unchecked` | warning |
| `step_never_runs` | warning |
| `unused_input` | warning |

- A code keeps its meaning across versions of this spec, and new checks get new codes.
- Messages are for people and may change.
- For `parse_error`, the file couldn't be read: the YAML or JSON is invalid, or a key appears twice. YAML parsers usually keep the last copy of a repeated key without saying so, so the checker must reject it.

### 6.3 Output

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

`path` is a JSON Pointer into the workflow file. `ok` is true when there are no errors. Warnings don't block a run.

### 6.4 Caller policy

A policy says what the person or agent asking for the run may do:

```yaml
maxEffects: reads   # none | reads | writes
secrets: []         # secrets this caller may use; leave the key out to skip this check
```

The platform decides each caller's policy. The checker applies it, so an agent finds out before running that it can't use a step.

### 6.5 Test cases

- `tests/check/<case>/` holds a `workflow.yaml`, an `expected.yaml` and, optionally, a `policy.yaml`. The catalog for these cases is `examples/steps/` plus `tests/catalog/`.
- `tests/manifests/<case>/` holds a manifest in `steps/` and an `expected.yaml`.
- An implementation passes a case when its errors and warnings have exactly the expected `(code, path)` pairs, as multisets.

## 7. Running

### 7.1 Which steps run

```text
roots  := steps that workflow outputs reference, plus every step whose effects are writes
needed := roots, plus every step they depend on through references, transitively
```

Other steps don't run, and the checker warns about them (`step_never_runs`).

### 7.2 Order

A needed step runs once all the values it references exist. Steps that don't depend on each other should run in parallel.

### 7.3 Cache

```text
cacheable(s) := s.deterministic and s.effects != writes
key(s)       := digest({ "step": digest of s's manifest, "inputs": s's input values, defaults filled in })
```

- The key is computed just before a step would run, from the actual values it receives.
- File values enter the key as their digests, so a large file costs nothing extra to hash.
- If the store has an entry for a cacheable step's key, the platform reuses its outputs. The run record shows `cacheHit: true`.
- ComfyUI keys each node on its upstream nodes' keys instead. Keying on values means a non-deterministic step upstream, such as an agent, can't cause a stale cache hit downstream.

### 7.4 Retries

- A failed step may be retried automatically when its effects aren't `writes`, or when it is idempotent.
- `attempts` in the run record counts the tries; 0 means a cache hit.

### 7.5 Failure

- When a step fails, every step that depends on it is marked `skipped`.
- Independent steps still finish.
- The run's status is `failed`, and finished outputs stay in the record.

Run-time error codes:
- `invalid_input`: a value failed the input's schema. This is where `type_unchecked` warnings are settled.
- `invalid_output`: the step's outputs don't fit its `outputSchema`.
- `timeout`
- `failed`: the step itself reported failure.

### 7.6 Secrets

- The platform injects secrets at run time and never writes them into run records, logs or caches.
- A step gets only the secrets its manifest names.

### 7.7 Agent steps and plans

- Every tool call an agent step makes is recorded, in order, under that step's `calls`, with inputs, outputs and step digests.
- An agent step with `mayPlan: true` may finish by returning `{"plan": <workflow>}` instead of outputs. The platform then:
  1. checks the plan with section 6's rules, under the same caller policy. The plan may only use steps listed in the agent's `tools`, and its outputs must fit the agent step's `outputSchema`.
  2. runs the plan's needed steps, recording them under `steps` inside the agent step's record.
  3. gives the plan's outputs as the agent step's outputs.

  A plan that fails checking fails the agent step, unless the platform returns the errors to the agent for another turn.

### 7.8 Run record

`schemas/run.schema.json`. A run record holds:
- the workflow's digest and its lock file
- the run's inputs and outputs
- the caller, and timestamps
- an OpenTelemetry trace id, so logs and traces can be found
- for each step: its version and digest, cache key and whether the cache was hit, attempts, inputs, outputs, error, and an agent's calls and plan

Examples are in `examples/runs/`.

## 8. Publishing and versions

### 8.1 Digests

- A digest is `sha256:` followed by the SHA-256 of a document's canonical JSON: keys sorted, no whitespace, UTF-8.
- Before computing a step's digest, publishing resolves everything in the manifest that could change later. A container image tag becomes `image@sha256:...`, and the agent's instruction file is replaced by its content digest.
- The reference checker hashes the manifest as written, because it doesn't build images.

### 8.2 Lock file

Publishing a workflow writes `<workflow>.lock.json`, which maps each `uses` value to its digest (`schemas/lock.schema.json`, example `examples/ci-triage.lock.json`). A platform must refuse to run a published workflow whose steps no longer match its lock.

### 8.3 A workflow is a step

Published, a workflow gets a manifest derived from its contents:

| Field | Derived from |
|---|---|
| `inputSchema` | the workflow's `inputSchema` |
| `outputSchema` | each output's source schema; required when the source is |
| `effects` | the highest effects among needed steps |
| `deterministic` | true only if every needed step is |
| `idempotent` | true only if every needed `writes` step is |
| `destructive` | true if any needed `writes` step is |
| `secrets` | every secret the needed steps name |
| `runtime` | `{ workflow: { path } }` |

`wfspec as-step` prints it. Other workflows can then use the workflow like any step, and the MCP server can list it as a tool.

### 8.4 Versions

`uses` always names an exact version. Publishing a new version must bump the major number when any of these hold:
- an input is added and required
- an input or output is removed
- an output stops being required
- an input accepts fewer values
- an output can produce more kinds of values
- `effects` goes up

Other changes bump the minor or patch number. A platform should check these rules on publish.

### 8.5 Saving a run as a workflow

From a run record, a person or an agent writes a workflow:
- **Step versions:** every step and tool call in the record becomes a step with the same `uses`.
- **Values:** a value that came out of an earlier step becomes a reference to it. Values that came from the run's inputs become workflow inputs. The rest become literals.
- **Values an agent made up between calls** (for example the issue text it wrote) become the outputs of a narrower agent step with a typed schema, or fixed values.

The saved workflow is accepted when two things hold:
- It checks without errors.
- A rerun with the original inputs gives the same outputs for every deterministic step.

`examples/runs/investigate.run.json` records a broad agent (`acme/investigate-failure`) that fetched logs and opened an issue. `examples/workflows/ci-triage.yaml` is that run saved as a workflow:
- The two tool calls became fixed steps.
- The agent's reasoning became a narrow agent step (`acme/triage-logs`) with a typed output.

`examples/runs/ci-triage.run.json` is a later run of the saved workflow with a different `style`. The logs came from the cache, and only the agent and the write ran again.

## 9. MCP

### 9.1 A step as an MCP tool

| MCP tool field | From the manifest |
|---|---|
| `name` | `name` with `/` replaced by `.`, as MCP names allow letters, digits, `_`, `-` and `.` |
| `title`, `description` | same |
| `inputSchema`, `outputSchema` | same, with `urn:wfspec:file` references written out in full |
| `annotations` | from `effects`, as in section 3.2 |

`wfspec mcp-tool <manifest>` prints the result. MCP treats annotations as untrusted unless the server is trusted. The platform's own MCP server is trusted, and it enforces `effects` itself.

### 9.2 The platform's MCP server

A platform should offer one MCP server through which agents find steps, check workflows and run them. Copilot is one such agent.

| Tool | Arguments | Returns |
|---|---|---|
| `find_steps` | `query`, optional `effects` | name@version, title, description, effects |
| `get_step` | `ref` | the manifest |
| `check_workflow` | `workflow` (text) | section 6.3's result |
| `run_workflow` | `workflow` (text) or `ref`, `inputs` | a run id |
| `get_run` | `runId` | the run record so far |

- Each published workflow should also appear as its own tool. Calling that tool runs the workflow.
- **No editing tools are needed.** A workflow is a text file, which agents already edit. The server supplies what the agent can't know on its own: which steps exist, whether the file is correct, and what happened when it ran.
- **Long runs** can use MCP's tasks extension (`io.modelcontextprotocol/tasks`, in the 2026-07-28 revision), where a call returns a handle the client polls. Clients that don't support it poll `get_run`.
- **Forms:** a client that supports MCP Apps can show a published workflow's `inputSchema` as a form. Copilot Chat in VS Code supports MCP Apps.

## 10. Left to the platform

These are outside the spec:
- sign-in and permissions
- storage and retention
- scheduling and quotas
- billing
- sandboxing
- the catalog's API
- how approvals are shown
- any visual editor

The spec only requires that a platform:
- enforce `effects` and `secrets`
- refuse to run anything that fails checking
- write run records

## 11. Not in version 0.1

- Picking a field out of an output (`steps.x.outputs.y.z`).
- Running a step once per item of a list.
- Generic inputs, such as an input whose type follows another input's.
- Steps that run only when asked for, like ComfyUI's lazy inputs.
- Automatic upgrades from one major version to the next.
- Working out a saved workflow's references automatically from an agent's calls.
- A command that checks the rules in 8.4 between two versions of a step.
- Layout for visual editors. That would be a separate file, so layout never changes what a workflow means.

## 12. Where the ideas come from

| Idea | From |
|---|---|
| step descriptions served as data; literals and links; check before running; run only what outputs need; cache by inputs; graphs used as steps; plans | ComfyUI v0.44.2 (`execution.py`, `comfy_execution/`) |
| `uses: owner/name@version` and `with` | GitHub Actions |
| tool interface and effect hints | MCP, revision 2026-07-28 |
| versioned, typed components; a determinism flag for reuse | Azure ML pipelines (`is_deterministic`) |
| files addressed by content hash; immutable releases | Comfy API v2 |
