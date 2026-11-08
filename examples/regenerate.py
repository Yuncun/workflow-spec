"""Recompute every digest in the examples from the current step manifests and workflows.

It rewrites ci-triage.lock.json, and in each run record the workflow digest, the lock, each
step's and each call's digest, and each cache key. Other fields are left as they are.
Run it after changing an example step or workflow:  .venv/bin/python examples/regenerate.py
"""

import json
from pathlib import Path

from wfspec import digest, load_catalog, load_document, lock
from wfspec.publish import cache_key

HERE = Path(__file__).resolve().parent


def main() -> None:
    catalog = load_catalog([HERE / "steps"])
    if catalog.problems:
        raise SystemExit("fix the example steps first: " + "; ".join(p.message for p in catalog.problems))
    triage = load_document(HERE / "workflows" / "ci-triage.yaml")
    (HERE / "ci-triage.lock.json").write_text(json.dumps(lock(triage, catalog), indent=2) + "\n")
    for path in sorted((HERE / "runs").glob("*.run.json")):
        run = json.loads(path.read_text())
        workflow = load_document(HERE / "workflows" / f"{path.name.split('.')[0]}.yaml")
        run["workflow"]["digest"] = digest(workflow)
        run["lock"] = lock(workflow, catalog)
        for step in run["steps"].values():
            manifest = catalog.steps[step["uses"]]
            step["digest"] = digest(manifest)
            if "cacheKey" in step:
                step["cacheKey"] = cache_key(manifest, step["inputs"])
            for call in step.get("calls", []):
                call["digest"] = digest(catalog.steps[call["uses"]])
        path.write_text(json.dumps(run, indent=2) + "\n")


if __name__ == "__main__":
    main()
