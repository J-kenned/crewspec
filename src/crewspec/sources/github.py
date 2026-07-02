"""Fetch a crewfile pack from GitHub over HTTPS,

The ``github:owner/repo[@ref][/subdir]`` shorthand resolves to a codeload
``tar.gz`` — the same approach degit/giget/``create-next-app`` take for
"grab a repo (or a subdir of one) and lay it down". :func:`fetch_github`
downloads and extracts into a temporary directory and yields the local path the
rest of the pipeline consumes; the directory is removed when the context exits.
"""

from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from crewspec.sources.errors import (
    SourceFetchError,
    SourceLayoutError,
    SourceSpecError,
)

#: Prefix that marks a source string as a GitHub shorthand.
GITHUB_PREFIX = "github:"

#: Ref used when the shorthand omits ``@ref``; codeload resolves it to the
#: repository's default branch.
DEFAULT_REF = "HEAD"

_USER_AGENT = "crewspec"
_TIMEOUT_SECONDS = 30

_SPEC_RE = re.compile(
    r"^(?P<owner>[^/@]+)/(?P<repo>[^/@]+)"
    r"(?:@(?P<ref>[^/]+))?"
    r"(?:/(?P<subdir>.+))?$"
)


def is_github_spec(spec: str) -> bool:
    """Return whether ``spec`` is a ``github:`` shorthand."""

    return spec.startswith(GITHUB_PREFIX)


@dataclass(frozen=True)
class GitHubRef:
    """A parsed ``github:owner/repo[@ref][/subdir]`` reference."""

    owner: str
    repo: str
    ref: str = DEFAULT_REF
    subdir: str | None = None

    @classmethod
    def parse(cls, spec: str) -> GitHubRef:
        """Parse a ``github:`` shorthand into a :class:`GitHubRef`.

        Raises :class:`SourceSpecError` when ``spec`` is not a ``github:``
        reference or does not match ``owner/repo[@ref][/subdir]``.
        """

        if not is_github_spec(spec):
            raise SourceSpecError(
                f"not a github source: {spec!r} (expected {GITHUB_PREFIX!r} prefix)"
            )

        match = _SPEC_RE.match(spec[len(GITHUB_PREFIX) :])
        if match is None:
            raise SourceSpecError(
                f"malformed github source {spec!r}; expected "
                f"'{GITHUB_PREFIX}owner/repo[@ref][/subdir]'"
            )

        subdir = match.group("subdir")
        if subdir is not None:
            subdir = subdir.strip("/")
            if not subdir:
                subdir = None
            elif ".." in subdir.split("/"):
                raise SourceSpecError(
                    f"github source subdir may not contain '..': {subdir!r}"
                )

        return cls(
            owner=match.group("owner"),
            repo=match.group("repo"),
            ref=match.group("ref") or DEFAULT_REF,
            subdir=subdir,
        )

    def codeload_url(self) -> str:
        """The codeload ``tar.gz`` URL for this ref."""

        return (
            f"https://codeload.github.com/{self.owner}/{self.repo}"
            f"/tar.gz/{self.ref}"
        )

    def slug(self) -> str:
        """A short human-readable identifier for error messages."""

        return f"{GITHUB_PREFIX}{self.owner}/{self.repo}@{self.ref}"


@contextmanager
def fetch_github(ref: GitHubRef) -> Generator[Path, None, None]:
    """Fetch ``ref`` into a temp dir and yield the pack root.

    The yielded path is the extracted repository (descended into ``subdir`` when
    present). The temporary directory is removed when the context exits.
    """

    tmp = Path(tempfile.mkdtemp(prefix="crewspec-github-"))
    try:
        archive = tmp / "pack.tar.gz"
        _download(ref, archive)
        extract_dir = tmp / "extracted"
        extract_dir.mkdir()
        _extract(archive, extract_dir, ref)
        yield _locate_root(extract_dir, ref)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _download(ref: GitHubRef, dest: Path) -> None:
    url = ref.codeload_url()
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            dest.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SourceFetchError(
                f"{ref.slug()} not found (HTTP 404) — check the repository name "
                f"and ref '{ref.ref}'"
            ) from exc
        raise SourceFetchError(
            f"failed to fetch {ref.slug()}: HTTP {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SourceFetchError(f"failed to fetch {ref.slug()}: {exc.reason}") from exc


def _extract(archive: Path, dest: Path, ref: GitHubRef) -> None:
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            # The "data" filter (3.12+) blocks path traversal and unsafe members.
            tar.extractall(dest, filter="data")
    except tarfile.TarError as exc:
        raise SourceFetchError(
            f"{ref.slug()} did not return a valid .tar.gz archive: {exc}"
        ) from exc


def _locate_root(extract_dir: Path, ref: GitHubRef) -> Path:
    # GitHub archives contain a single top-level ``{repo}-{ref}`` directory whose
    # exact name is normalized (tags/SHAs), so find it rather than guess it.
    tops = [child for child in extract_dir.iterdir() if child.is_dir()]
    if len(tops) != 1:
        raise SourceLayoutError(
            f"unexpected archive layout for {ref.slug()}: expected one top-level "
            f"directory, found {len(tops)}"
        )

    root = tops[0]
    if ref.subdir is None:
        return root

    target = root / ref.subdir
    if not target.is_dir():
        raise SourceLayoutError(f"subdir '{ref.subdir}' not found in {ref.slug()}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise SourceLayoutError(f"subdir '{ref.subdir}' escapes the fetched repository")
    return target
