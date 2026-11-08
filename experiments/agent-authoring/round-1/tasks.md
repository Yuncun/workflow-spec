# Round 1 tasks

Each agent got one of these, word for word.

| Id | Task |
|---|---|
| ci-alert | When a CI run fails, triage its logs, open an issue labeled ci-failure containing the triage report, and post a message in the #ci-alerts chat channel that says "New CI issue: " followed by the issue's link. The repository and the CI run id are inputs of the workflow. |
| pr-summary | Summarize a pull request for a non-technical audience in at most 150 words, and post the summary as a comment on the pull request. The repository and the pull request number are inputs of the workflow. |
| outdated-deps | Open one issue in a repository, titled "Outdated dependencies", whose body is a table of the repository's outdated dependencies with the columns name, current and latest. The repository is an input of the workflow. |
| release-translations | Translate a release's notes into Japanese and into German, and post each translation as its own message in the #releases chat channel. The repository and the release's tag are inputs of the workflow. |
| test-failures | Run a repository's integration tests on a branch. If any tests fail, open an issue listing the failures; if none fail, don't open an issue. The repository and the branch are inputs, and the workflow should output whether the tests passed. |
