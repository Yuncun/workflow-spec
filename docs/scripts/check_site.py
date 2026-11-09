"""Check that every link inside the built site reaches a page and anchor that exist: python docs/scripts/check_site.py"""

from __future__ import annotations

import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "docs" / ".vitepress" / "dist"
BASE = os.environ.get("DOCS_BASE", "/workflow-spec/")
SITE = "http://site.invalid"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])


def url_of(file: Path) -> str:
    rel = file.relative_to(DIST).as_posix()
    rel = rel.removesuffix("index.html") if rel.endswith("index.html") else rel.removesuffix(".html")
    return SITE + BASE + rel


def file_of(path: str) -> Path | None:
    if not path.startswith(BASE):
        return None
    rel = path[len(BASE):]
    candidates = [DIST / rel / "index.html", DIST / (rel + ".html"), DIST / rel] if rel else [DIST / "index.html"]
    return next((c for c in candidates if c.is_file()), None)


def main() -> int:
    if not DIST.is_dir():
        print(f"no built site in {DIST.relative_to(ROOT)}; build it first", file=sys.stderr)
        return 1
    pages: dict[Path, Page] = {}
    for file in sorted(DIST.rglob("*.html")):
        page = Page()
        page.feed(file.read_text())
        pages[file] = page

    problems, checked = [], 0
    for file, page in pages.items():
        here = url_of(file)
        for href in page.links:
            target = urlsplit(urljoin(here, href))
            if f"{target.scheme}://{target.netloc}" != SITE:
                continue
            checked += 1
            target_file = file_of(target.path)
            where = file.relative_to(DIST).as_posix()
            if target_file is None:
                problems.append(f"{where}: link to {href}, a page the site doesn't have")
                continue
            anchor = unquote(target.fragment)
            if anchor and target_file.suffix == ".html" and anchor not in pages[target_file].ids:
                problems.append(f"{where}: link to {href}, but {target_file.relative_to(DIST).as_posix()} "
                                f"has no id '{anchor}'")
    for problem in sorted(set(problems)):
        print(problem)
    print(f"checked {checked} links on {len(pages)} pages; {len(set(problems))} broken")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
