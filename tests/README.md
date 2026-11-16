# Test cases

A checker must pass every checker case to conform, and a runner every runner case whose needs it supports (SPEC.md, section 3). [coverage.yaml](coverage.yaml) maps each requirement id in SPEC.md to the cases that cover it.

## Checker cases

- `check/<case>/` holds a `workflow.yaml` or `workflow.json`, an `expected.yaml` and, optionally, a `policy.yaml`. The catalog for these cases is `examples/steps/` plus `tests/catalog/`, plus the case's own `catalog/` folder when it has one. A case's own catalog may hold manifests that are meant to be left out.
- `manifests/<case>/` holds manifests in `steps/` and an `expected.yaml`. The catalog for these cases is `examples/steps/` plus the case's `steps/` folder. Files in subfolders of `steps/`, such as a workflow that a manifest names, aren't manifests.
- A checker passes a case when its `ok` matches and it reports every expected error code (SPEC.md, [conform.checker-tests]). The paths, counts and warnings in `expected.yaml` are what the reference checker reports; other checkers don't have to match them.

## Runner cases

`run/<case>/` holds a `workflow.yaml` and an `expected.yaml`. It may also hold:

| File | Holds |
|---|---|
| `inputs.yaml` | the run's inputs |
| `secrets.yaml` | the caller's secrets, by name |
| `policy.yaml` | the caller's policy |
| `options.yaml` | how to run the case |
| `catalog/` | steps only this case uses |

- The shared steps are in `run-catalog/`. They are programs (`process` steps, drafts/runtime.md).
- The cases rely on the `process` contract in drafts/runtime.md. A runner that starts steps another way passes them by giving these steps the same behaviour in its own runtime.

`options.yaml` can set:

| Key | Means |
|---|---|
| `needs` | the optional features (SPEC.md, section 3: `workflow-step`, `file-inputs`, `run-record`) and runtime kinds other than `process` that the case needs; a runner that lacks one doesn't have to pass the case |
| `sandbox` | `false` runs programs without a sandbox |
| `macos_only` | the case checks how the reference runner enforces effects, with the macOS sandbox. How a runner enforces effects is its choice ([run.effects]), so no other runner has to pass it |

`expected.yaml` says what the run must give:

| Key | Means |
|---|---|
| `status` | the run's status |
| `error` | the run's error code |
| `outputs` | the run's outputs |
| `steps` | for chosen steps, fields of their records, such as `status`, `error`, `reason` or `outputs` |
| `logs_hide` | values that nothing the runner stored may contain |

A record matches when every field the expectation gives matches. Other fields are free. A runner with the `run-record` feature must also make every run record fit `schemas/run.schema.json`.

The workflow and inputs can use three placeholders:
- `{tmp}` is a scratch folder. It holds a file `outside.txt` that no step is given.
- `{port}` is a listening port.
- `{case}` is the case folder.
