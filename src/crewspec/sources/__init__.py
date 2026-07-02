"""Turn a source string into a local crewfile tree.

A source is either a local path (a crewfile or a directory containing one) or a
``github:owner/repo[@ref][/subdir]`` shorthand. :func:`open_source` normalizes
both to a local :class:`~pathlib.Path` the pipeline consumes, fetching remote
sources into a temporary directory that is cleaned up when the context exits.

This is the seam future ``add`` / registry work builds on: new source kinds slot
in behind :func:`open_source` without touching the parse/resolve/emit pipeline.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from crewspec.sources.errors import (
    SourceError,
    SourceFetchError,
    SourceLayoutError,
    SourceSpecError,
)
from crewspec.sources.github import (
    GITHUB_PREFIX,
    GitHubRef,
    fetch_github,
    is_github_spec,
)

__all__ = [
    "GITHUB_PREFIX",
    "GitHubRef",
    "SourceError",
    "SourceFetchError",
    "SourceLayoutError",
    "SourceSpecError",
    "fetch_github",
    "is_github_spec",
    "open_source",
]


@contextmanager
def open_source(spec: str) -> Generator[Path, None, None]:
    """Yield a local path for ``spec``.

    A ``github:`` shorthand is fetched into a temporary directory that is removed
    when the context exits; any other string is treated as a local path and
    yielded as-is (the caller validates its existence and locates the crewfile).
    """

    if is_github_spec(spec):
        with fetch_github(GitHubRef.parse(spec)) as local:
            yield local
    else:
        yield Path(spec)
