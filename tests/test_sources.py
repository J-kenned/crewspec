"""Tests for the ``sources`` package: ``github:`` shorthand parsing and fetch.

Parsing is unit-tested directly. The fetch path is exercised end-to-end through
the real ``crewspec install`` CLI with the network mocked — ``urllib.request``'s
``urlopen`` is monkeypatched to serve an in-memory ``tar.gz`` built from the
checked-in example pack, so no real request is ever made.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import pytest
from click.testing import CliRunner, Result

from crewspec.cli import cli
from crewspec.sources import SourceSpecError
from crewspec.sources.github import DEFAULT_REF, GitHubRef

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "code-review-crew"
PLUGIN = (".claude", "skills", "code-review-crew")


def test_parse_owner_repo_uses_default_ref() -> None:
    ref = GitHubRef.parse("github:acme/pack")
    assert (ref.owner, ref.repo, ref.ref, ref.subdir) == (
        "acme",
        "pack",
        DEFAULT_REF,
        None,
    )


def test_parse_explicit_ref() -> None:
    ref = GitHubRef.parse("github:acme/pack@release")
    assert (ref.ref, ref.subdir) == ("release", None)


def test_parse_ref_and_subdir() -> None:
    ref = GitHubRef.parse("github:acme/pack@main/examples/code-review-crew")
    assert (ref.ref, ref.subdir) == ("main", "examples/code-review-crew")


def test_parse_subdir_with_default_ref() -> None:
    ref = GitHubRef.parse("github:acme/pack/examples/crew")
    assert (ref.ref, ref.subdir) == (DEFAULT_REF, "examples/crew")


def test_codeload_url() -> None:
    url = GitHubRef.parse("github:acme/pack@release").codeload_url()
    assert url == "https://codeload.github.com/acme/pack/tar.gz/release"


@pytest.mark.parametrize(
    "spec",
    [
        "acme/pack",  # missing prefix
        "github:acme",  # missing repo
        "github:",  # empty
        "github:acme/pack@main/../secrets",  # parent-dir traversal in subdir
    ],
)
def test_parse_rejects_malformed(spec: str) -> None:
    with pytest.raises(SourceSpecError):
        GitHubRef.parse(spec)


def _tarball(src: Path, arcname: str) -> bytes:
    """Gzip-tar ``src`` under ``arcname``, mirroring a GitHub codeload archive."""

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(src, arcname=arcname)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Callable[[str], object]) -> None:
    """Route ``urlopen`` through ``handler(url)`` (returns a response or raises)."""

    def urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> object:
        return handler(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def _install(*args: str) -> Result:
    return CliRunner().invoke(cli, ["install", *args], prog_name="crewspec")


def test_install_from_github_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tarball = _tarball(EXAMPLE, "crewspec-main/examples/code-review-crew")
    _serve(monkeypatch, lambda url: _FakeResponse(tarball))

    result = _install(
        "github:acme/crewspec@main/examples/code-review-crew",
        "--out",
        str(tmp_path),
    )

    assert result.exit_code == 0, result.output
    root = tmp_path.joinpath(*PLUGIN)
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert (root / "agents" / "reviewer.md").is_file()
    assert (root / "skills" / "code-review" / "SKILL.md").is_file()
    assert (tmp_path / ".claude.json").is_file()


def test_install_from_github_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tarball = _tarball(EXAMPLE, "pack-main")
    _serve(monkeypatch, lambda url: _FakeResponse(tarball))

    result = _install("github:acme/pack@main", "--out", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert tmp_path.joinpath(*PLUGIN, "agents", "reviewer.md").is_file()


def test_install_hits_codeload_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tarball = _tarball(EXAMPLE, "pack-main")
    seen: list[str] = []

    def handler(url: str) -> object:
        seen.append(url)
        return _FakeResponse(tarball)

    _serve(monkeypatch, handler)
    _install("github:acme/pack@main", "--out", str(tmp_path))

    assert seen == ["https://codeload.github.com/acme/pack/tar.gz/main"]


def test_install_reports_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def handler(url: str) -> object:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    _serve(monkeypatch, handler)
    result = _install("github:acme/nope@main", "--out", str(tmp_path))

    assert result.exit_code == 1
    assert "not found" in result.stderr.lower()
    assert not tmp_path.joinpath(*PLUGIN).exists()


def test_install_reports_network_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(url: str) -> object:
        raise urllib.error.URLError("name resolution failed")

    _serve(monkeypatch, handler)
    result = _install("github:acme/pack@main", "--out", str(tmp_path))

    assert result.exit_code == 1
    assert "name resolution failed" in result.stderr


def test_install_reports_missing_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tarball = _tarball(EXAMPLE, "pack-main")
    _serve(monkeypatch, lambda url: _FakeResponse(tarball))

    result = _install("github:acme/pack@main/does/not/exist", "--out", str(tmp_path))

    assert result.exit_code == 1
    assert "does/not/exist" in result.stderr


def test_malformed_spec_is_usage_error(tmp_path: Path) -> None:
    result = _install("github:acme", "--out", str(tmp_path))
    assert result.exit_code == 2  # click rejects a malformed SOURCE argument


def test_temp_dir_removed_after_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args: object, **kwargs: object) -> str:
        kwargs.setdefault("dir", str(tmp_path))
        path = real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]
        created.append(Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", tracking_mkdtemp)
    _serve(monkeypatch, lambda url: _FakeResponse(_tarball(EXAMPLE, "pack-main")))

    result = _install("github:acme/pack@main", "--out", str(tmp_path / "out"))

    assert result.exit_code == 0, result.output
    assert created, "fetch never created a temp dir"
    assert all(not path.exists() for path in created)
