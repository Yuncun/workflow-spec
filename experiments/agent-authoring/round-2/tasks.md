# Round 2 tasks

The catalog adds steps from other teams whose types don't line up with the round-1 steps, plus two older versions of existing steps. Each task's most obvious wiring fails checking with `type_mismatch`, and the catalog has a step that fixes it.

| Id | Task | Obvious wiring fails because |
|---|---|---|
| summarize-ci-run | Fetch the logs of a CI run, summarize them in a short paragraph, and post the summary to the #builds chat channel. The repository and the CI run id are inputs of the workflow. | the logs are a file; the summarizer takes text |
| fix-first-failure | Run a branch's unit tests, have an agent propose a fix for the first failing test, and open a pull request with the proposed changes on a new branch named fix/first-failure. The repository and the branch are inputs of the workflow. | the tests give a list of failures; the fixer takes one |
| label-ci-issue | Triage a failed CI run's logs, open an issue with the triage report, then label the issue by the failure's category: test failures get the label 'test', flaky ones 'flaky', infrastructure problems 'infra', and build failures 'bug'. The repository and the CI run id are inputs of the workflow. | the triage categories and the label names differ |
