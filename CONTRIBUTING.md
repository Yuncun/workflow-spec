# Contributing

Questions and bug reports go in [issues](https://github.com/Yuncun/workflow-spec/issues). Changes come as pull requests.

## Changing the specification

A substantive change, one that changes what conforms, goes through a lightweight RFC process: it's proposed and settled before it's written into SPEC.md.

1. **Propose** it in [proposals.md](proposals.md): the problem, the idea, and what would settle it.
2. **Settle** it with evidence, such as a real workflow, a test, or a second implementation.
3. **Write the rule** in SPEC.md under a new requirement id, with an error code if checking reports it (section 8.1). Add test cases, and map the id to them in [tests/coverage.yaml](tests/coverage.yaml).
4. **Update the reference** checker or runner in [reference/](reference) until every test passes.
5. **Record it** in [CHANGELOG.md](CHANGELOG.md) under "Unreleased", and take its entry out of proposals.md.

An editorial change, such as clearer wording, doesn't change what conforms and needs only a pull request.

## Writing rules

- **Key words.** Requirements use MUST, SHOULD and MAY in capitals, as BCP 14 defines them ([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119), [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)), and only in sections marked normative.
- **Requirement ids.** Every testable requirement has an id, such as `[check.cycle]`. An id never changes meaning and is never reused. `tests/coverage.yaml` maps each id to its tests, or gives the reason it has none, and the tests fail when an id is missing from it, when it names an id SPEC.md doesn't have, or when a test case covers no id. This is requirements traceability.
- **Sections** are marked normative or informative.
- **Terms.** Use plain words. A new term is defined in SPEC.md, section 2.3, before it's used.
- **Design goals.** A change that works against a design goal in SPEC.md, section 1, such as adding conditions or loops that make workflows harder to check before they run, says so in its proposal.

## Tests

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e "reference[test]"
.venv/bin/python -m pytest reference/tests
```

All must pass after any change. Then check the documents themselves: that every example in them passes the checker, and that their links, requirement citations and section references resolve:

```bash
.venv/bin/python docs/scripts/check_examples.py
.venv/bin/python docs/scripts/build_pages.py
```

`build_pages.py` writes the documentation site's pages to `docs/.generated`, which isn't committed. CI runs the tests and both checks on every push to main and every pull request, on Linux and macOS; runner cases that need the macOS sandbox are skipped on Linux.

After changing an example step or workflow, run `examples/regenerate.py`. `reference/tests/test_examples.py` checks that the run records still match.
