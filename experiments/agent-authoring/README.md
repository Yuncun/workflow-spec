# Can agents write workflows from step manifests and checker errors?

Run on 2026-11-07, against spec version 0.1.

**Result:** 16 of 16 workflows were correct. 15 passed checking on their first check, and the checker never reported an error in any attempt. The agents found every type mismatch by reading the step manifests. The test therefore shows that agents can write this format from typed manifests. It doesn't yet show that the checker's errors help them, because they never needed the errors.

## Method

- **Agents:** one per task and model, using the subagent tool's `sonnet` and `haiku` models. Each was a general-purpose subagent with file access and a shell, like a coding agent in an editor.
- **What each agent got:**
  - one task in plain English
  - the guide `guides/write-a-workflow.md`
  - a catalog folder of step manifests
  - `check.sh`, which runs `wfspec check` and saves every attempt as `attempt-N.yaml` and `attempt-N.json` in the agent's run folder
- **Rules:** read only the guide, the catalog and its own folder; use only catalog steps; use the checker as the only feedback; stop at 0 errors.
- **Grading:** each final workflow was compared with the answer key in `solutions/`.
- **Nothing was run.** The steps are manifests only.

| Round | Catalog | Tasks | Designed to test |
|---|---|---|---|
| 1 | 16 steps | 5 | choosing and connecting steps whose types line up |
| 2 | 25 steps | 3 | connecting steps whose types don't line up, with older versions present |

In round 2, each task's obvious wiring fails checking with `type_mismatch` (`round-2/obvious-wiring/`), and the catalog has a step that fixes it:

| Task | Mismatch | Fixing step |
|---|---|---|
| summarize-ci-run | a file into a text input | `acme/read-text-file` |
| fix-first-failure | a list into a single-item input | `acme/pick-first` |
| label-ci-issue | categories that don't match the label names | `acme/map-value` |

## Results

| Round | Task | Sonnet checks | Haiku checks | Correct |
|---|---|---|---|---|
| 1 | ci-alert | 1 | 1 | 2 of 2 |
| 1 | pr-summary | 1 | 1 | 2 of 2 |
| 1 | outdated-deps | 1 | 1 | 2 of 2 |
| 1 | release-translations | 1 | 2 | 2 of 2 |
| 1 | test-failures | 1 | 1 | 2 of 2 |
| 2 | summarize-ci-run | 1 | 1 | 2 of 2 |
| 2 | fix-first-failure | 1 | 1 | 2 of 2 |
| 2 | label-ci-issue | 1 | 1 | 2 of 2 |

`python summarize.py round-1` (or `round-2`) prints each run's attempts from the saved files.

- **Errors:** none in 17 checks.
- **Warnings:** 12 of the 16 final workflows kept `type_unchecked` warnings. These were limits such as a maximum length that the checker can't prove, which are checked when a step runs.
- **The one second check:** the Haiku agent on release-translations added a pattern to its `repo` input to clear a warning.
- **Round 2:** all six agents inserted the fixing step before their first check. The label agents wrote the mapping `{test: test, flaky: flaky, infrastructure: infra, build: bug}`. None chose the older versions of `acme/open-issue` or `acme/fetch-ci-logs`.
- **Conditions and composition:** both test-failures agents used the step that opens an issue only when tests fail, and both ci-alert agents built the message with `acme/format-text`. No agent tried to put logic in the workflow itself.

## What this shows and what it doesn't

**Shown:**
- Sonnet and Haiku both write correct workflows for jobs of two to five steps from typed manifests. They choose current versions, and they bridge mismatched types with the right step.
- The no-logic rule held: every decision went into a published step.

**Not shown:**
- **Whether checker errors help agents fix workflows.** In catalogs small enough to read in full, the agents never made a mistake for the checker to catch. The errors should matter in a catalog too large to read, where an agent sees search results and opens a few manifests. They may also matter for weaker setups, and when a new version of a step changes its interface. None of these was tested.
- **Realistic descriptions.** One person wrote both the catalog and the tasks, so step descriptions match the tasks' wording more closely than real ones would.
- **Running.** Nothing ran. Correctness was judged by reading each workflow against the answer key.

## Next test

Put a catalog of several hundred steps behind `find_steps` and `get_step` (SPEC.md, section 9.2), with descriptions written by different people, and repeat both rounds. Count first-check errors and the checks needed to reach zero. That measures the checker's part directly.
