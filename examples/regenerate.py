"""Recompute every digest in the examples from the current step manifests and workflows.

It rewrites, in each run record, the workflow digest and each step's and each call's digest.
Other fields are left as they are.
Run it after changing an example step or workflow:  .venv/bin/python examples/regenerate.py
"""

import json
from pathlib import Path

from wfspec import digest, load_catalog, load_document

HERE = Path(__file__).resolve().parent


def main() -> None:
    catalog = load_catalog([HERE / "steps"])
    if catalog.problems:
        raise SystemExit("fix the example steps first: " + "; ".join(p.message for p in catalog.problems))
    for path in sorted((HERE / "runs").glob("*.run.json")):
        run = json.loads(path.read_text())
        workflow = load_document(HERE / "workflows" / f"{path.name.split('.')[0]}.yaml")
        run["workflow"]["digest"] = digest(workflow)
        for step in run["steps"].values():
            step["digest"] = digest(catalog.steps[step["uses"]])
            for call in step.get("calls", []):
                call["digest"] = digest(catalog.steps[call["uses"]])
        path.write_text(json.dumps(run, indent=2) + "\n")


if __name__ == "__main__":
    main()
