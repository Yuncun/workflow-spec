# Step runtimes

Draft, 2026-11-10. **Not normative.** The reference runner follows section 1 for `process` steps, and the cases in `tests/run/` exercise them. This document becomes a normative companion to [SPEC.md](../SPEC.md) once a second runner, or a platform, confirms it in practice. Until then, a runner may follow it or not.

SPEC.md says what a run must produce. This document says how a runner starts one step, for each runtime kind a manifest can name in `runtime`. SPEC.md, section 5.3 defines the `workflow` kind.

| Kind | Runs | Fields |
|---|---|---|
| `process` | a program started on the runner's machine | `command` |
| `container` | a program in a container image | `image`, `command` |

A step that runs an agent is a program of either kind that asks a model.

## 1. Programs: `process` and `container`

Both kinds run a program under one contract. They differ only in how the program starts:
- **`process`:** the runner starts `command` directly, with the manifest's folder as the working directory.
- **`container`:** the runner starts `image`. When `command` is given, it replaces the image's entrypoint, as in Kubernetes. Without it, the image's own entrypoint and arguments run.

The contract:
- **Inputs.** The runner checks the input values and fills in defaults (SPEC.md, [run.input-check]). It then writes them as JSON to the file named by the environment variable `WF_INPUTS`.
  - Wherever `inputSchema` declares a file, at any depth through `properties` and `items`, the value also gets a `path` field: a local, read-only copy of the file.
  - `path` is added after the check and isn't part of the file value.
- **Outputs.** The program writes a JSON object of its outputs to the file named by `WF_OUTPUTS`.
  - Wherever `outputSchema` declares a file, the program writes `{"path": "...", "mediaType": "..."}`. It can leave out `mediaType` when the schema's `x-mediaType` names a single type.
  - A relative `path` is relative to the folder that holds the `WF_OUTPUTS` file, which is the program's work folder. It must lead to a regular file inside that folder without passing through a symbolic link; the runner refuses any other with `invalid_output`, because it reads the file outside any sandbox.
  - The runner stores each file, replaces it with a file value, and then checks the outputs ([run.output-check]). A file that holds the value of any of the run's secrets fails the step instead ([run.secrets-hidden]).
- **Environment.** The program gets at least these environment variables: `PATH`, `LANG`, `HOME` and `TMPDIR` (both set to its work folder), `WF_INPUTS`, `WF_OUTPUTS`, `WF_SKIP`, and its secrets. On macOS the system adds `__CF_USER_TEXT_ENCODING` to every program.
- **Skipping.** A step whose manifest has `maySkip` and that has nothing to do writes a reason to the file named by `WF_SKIP` instead of writing outputs ([run.skip]).
- **Secrets.** Each secret named in `secrets` is set as an environment variable with the same name, and no other secret is set ([run.secrets-named]).
- **Exit and logs.** Exit code 0 means success. Any other exit code fails the step with `failed`. Standard output and standard error are the step's logs.
- **Processes.** The program runs in a process group of its own. When it exits or times out, the runner ends every process left in that group.
- **Pinning.** A publisher may write content digests into the manifest, so the step's digest covers them:
  - for `container`, the image reference becomes `image@sha256:...`
  - for `process`, the runtime object gets `digests`, mapping each file the program needs, by its path relative to the manifest's folder, to the SHA-256 of its bytes, written `sha256:<hex>`

  A runner checks each digest before the step runs, and fails the step with `pinned_mismatch` when a file differs or isn't inside the manifest's folder (SPEC.md, [run.pinned]).

*Informative.* The reference runner (`reference/wfspec/runtimes.py`) enforces `effects` for programs on macOS with the system sandbox (`sandbox-exec`):
- A `none` step gets no network, and can't read the shared temporary folders (`/private/tmp` and `/private/var/folders`) or other volumes, apart from its work folder.
- Neither a `none` nor a `reads` step may write outside its work folder.
- Neither may read files under `/Users` or the home folder, except its manifest's folder, its work folder, and folders the caller grants with `wfspec run --allow-read FOLDER`. A program installed under a home folder, such as a Python from pyenv or uv, needs its installation granted the same way, and the runner's error names the folder.
- Neither may send signals outside its own process group.
- A step that writes runs without a sandbox, with the person's full access.

The sandbox can't stop a program from reading the environment that another program of the same person started with. So `wfspec run` starts itself again without the secrets given with `--secret`, and receives their values over a pipe. A step can still read the environment of other programs that hold secrets, including a later step that names them, if it leaves a process running outside its process group, which the runner can't find to end. Steps from people you don't trust belong in containers or virtual machines.

Where `sandbox-exec` isn't available, the runner refuses programs that don't write, unless the caller passes `--no-sandbox`. The test case `tests/run/effects/` and `reference/tests/test_isolation.py` check the sandbox.

## 2. Open questions

- What resource limits does a step declare (proposals.md, P4)?
- How does a runner report a step's progress while it runs?
