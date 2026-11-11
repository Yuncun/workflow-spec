# Test cases

A checker and a runner must pass these cases to conform (SPEC.md, section 3). [coverage.yaml](coverage.yaml) maps each requirement id in SPEC.md to the cases that cover it.

## Checker cases

- `check/<case>/` holds a `workflow.yaml` or `workflow.json`, an `expected.yaml` and, optionally, a `policy.yaml`. The catalog for these cases is `examples/steps/` plus `tests/catalog/`, plus the case's own `catalog/` folder when it has one. A case's own catalog may hold manifests that are meant to be left out.
- `manifests/<case>/` holds manifests in `steps/` and an `expected.yaml`. The catalog for these cases is `examples/steps/` plus the case's `steps/` folder. Files in subfolders of `steps/`, such as a workflow that a manifest names, aren't manifests.
- A checker passes a case when it reports exactly the expected errors and warnings: the same `(code, path)` pairs, each the same number of times.

## Runner cases

`run/<case>/` holds a `workflow.yaml` and an `expected.yaml`. It may also hold:

| File | Holds |
|---|---|
| `inputs.yaml` | the run's inputs |
| `secrets.yaml` | the caller's secrets, by name |
| `policy.yaml` | the caller's policy |
| `model.yaml` | scripted model replies for agent steps |
| `options.yaml` | how to run the case |
| `catalog/` | steps only this case uses |

- The shared steps are in `run-catalog/`. They are programs (`process` steps, drafts/runtime.md).
- Agent cases give their model replies as a script, served as an OpenAI-compatible API, so they need no model.
- The cases rely on the `process` and `agent` contracts in drafts/runtime.md, and on the tool names in drafts/mcp.md. A runner that starts steps another way passes them by giving these steps the same behaviour in its own runtime.

`options.yaml` can set:

| Key | Means |
|---|---|
| `runs` | how many times to run the workflow, or a list with the inputs of each run |
| `retries` | how many times the runner may retry a failed step |
| `sandbox` | `false` runs programs without a sandbox |
| `lock` | a lock file to run under, or `current` for the workflow's lock as it is now |
| `replay` | a run record; the run is a dry run that replays it (SPEC.md, [publish.saved-run]) |
| `macos_only` | the case needs the macOS sandbox, which the reference runner uses to enforce effects |

`expected.yaml` says what the last run must give:

| Key | Means |
|---|---|
| `status` | the run's status |
| `error` | the run's error code |
| `outputs` | the run's outputs |
| `steps` | for chosen steps, fields of their records, such as `status`, `error`, `cacheHit`, `attempts`, `reason` or `outputs` |
| `calls` | for a step id, the records of the calls it made, in order |
| `model_saw_tools` | the tool names the model was offered in its first request |
| `dry_run_differences` | what the dry run found different from the original run |
| `logs_hide` | values that nothing the runner stored may contain |

A record matches when every field the expectation gives matches. Other fields are free. Every run record must fit `schemas/run.schema.json`.

The workflow, inputs and options can use three placeholders:
- `{tmp}` is a scratch folder. It holds a file `outside.txt` that no step is given.
- `{port}` is a listening port.
- `{case}` is the case folder.
