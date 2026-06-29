"""Tests for mountable secret resolution."""

from pathlib import Path

import pytest

from crewspec.models import StdioMcpServer
from crewspec.models.interpolate import SecretRef
from crewspec.resolver import (
    SecretNotFound,
    SecretPermission,
    SecretResolver,
    SecretUnreadable,
    resolve_server,
)

_SECRET_VALUE = "super-secret-value"


def _write_secret(tmp_path: Path, *, mode: int = 0o600) -> Path:
    path = tmp_path / "token"
    path.write_text(_SECRET_VALUE)
    path.chmod(mode)
    return path


def test_env_backed_secret_resolves_to_runtime_ref() -> None:
    resolver = SecretResolver({"API_KEY": _SECRET_VALUE})
    binding = resolver.resolve_ref(SecretRef(kind="secret", target="API_KEY"))
    assert binding.delivery == "env"
    assert binding.placeholder == "${API_KEY}"
    assert binding.source == "API_KEY"


def test_file_backed_secret_resolves_to_mount_path(tmp_path: Path) -> None:
    path = _write_secret(tmp_path)
    resolver = SecretResolver()
    binding = resolver.resolve_ref(SecretRef(kind="file", target=str(path)))
    assert binding.delivery == "mount"
    assert binding.placeholder == str(path)
    assert binding.source == str(path)


def test_missing_env_secret_raises() -> None:
    resolver = SecretResolver({})
    with pytest.raises(SecretNotFound, match="API_KEY"):
        resolver.resolve_ref(SecretRef(kind="secret", target="API_KEY"))


def test_empty_env_secret_raises() -> None:
    resolver = SecretResolver({"API_KEY": ""})
    with pytest.raises(SecretNotFound, match="empty"):
        resolver.resolve_ref(SecretRef(kind="secret", target="API_KEY"))


def test_directory_secret_target_raises(tmp_path: Path) -> None:
    resolver = SecretResolver()
    with pytest.raises(SecretNotFound, match="not a regular file"):
        resolver.resolve_ref(SecretRef(kind="file", target=str(tmp_path)))


def test_missing_file_secret_raises(tmp_path: Path) -> None:
    resolver = SecretResolver()
    missing = tmp_path / "nope"
    with pytest.raises(SecretNotFound, match=str(missing)):
        resolver.resolve_ref(SecretRef(kind="file", target=str(missing)))


def test_over_permissive_file_secret_raises(tmp_path: Path) -> None:
    path = _write_secret(tmp_path, mode=0o644)
    resolver = SecretResolver()
    with pytest.raises(SecretPermission, match="group or other"):
        resolver.resolve_ref(SecretRef(kind="file", target=str(path)))


def test_unreadable_file_secret_raises(tmp_path: Path) -> None:
    path = _write_secret(tmp_path, mode=0o000)
    resolver = SecretResolver()
    with pytest.raises(SecretUnreadable, match=str(path)):
        resolver.resolve_ref(SecretRef(kind="file", target=str(path)))


def test_resolve_server_collects_bindings_without_inlining(tmp_path: Path) -> None:
    path = _write_secret(tmp_path)
    server = StdioMcpServer.model_validate(
        {
            "command": "server",
            "args": ["--key-file", "${file:" + str(path) + "}"],
            "env": {"API_KEY": "${secret:API_KEY}"},
        }
    )
    resolver = SecretResolver({"API_KEY": _SECRET_VALUE})

    resolved = resolve_server(server, secrets=resolver)

    # The value never reaches the resolved model or its string form.
    assert _SECRET_VALUE not in repr(resolved.server)
    # Pointers are substituted in its place.
    assert resolved.server.args == ["--key-file", str(path)]
    assert resolved.server.env == {"API_KEY": "${API_KEY}"}
    assert {b.delivery for b in resolved.secrets} == {"mount", "env"}


def test_resolve_server_deduplicates_bindings() -> None:
    server = StdioMcpServer.model_validate(
        {
            "command": "server",
            "args": ["${secret:API_KEY}", "${secret:API_KEY}"],
        }
    )
    resolver = SecretResolver({"API_KEY": _SECRET_VALUE})

    resolved = resolve_server(server, secrets=resolver)

    assert len(resolved.secrets) == 1


def test_resolve_server_mixes_env_and_secrets(tmp_path: Path) -> None:
    path = _write_secret(tmp_path)
    server = StdioMcpServer.model_validate(
        {
            "command": "${BIN:-server}",
            "args": ["--token", "${secret:API_KEY}"],
            "env": {"KEY_FILE": "${file:" + str(path) + "}"},
        }
    )
    resolver = SecretResolver({"API_KEY": _SECRET_VALUE})

    resolved = resolve_server(server, {"BIN": "mcp"}, secrets=resolver)

    assert resolved.server.command == "mcp"
    assert resolved.server.args == ["--token", "${API_KEY}"]
    assert resolved.server.env == {"KEY_FILE": str(path)}
