"""Reference implementation of the checking rules in SPEC.md.

It reads the files in ../schemas, so it runs from a checkout of this repository.
"""

from .check import Result, check
from .documents import Catalog, Issue, ParseError, load_catalog, load_document
from .publish import as_step, digest, lock, mcp_tool

__all__ = [
    "Catalog",
    "Issue",
    "ParseError",
    "Result",
    "as_step",
    "check",
    "digest",
    "load_catalog",
    "load_document",
    "lock",
    "mcp_tool",
]
