"""Mountable secret resolution for crewspec specs.

The CLI pipeline does not call this module. It is available for callers that
want to resolve secret values locally. :func:`resolve_server` substitutes
``${VAR}`` references from the given env mapping (the process environment
when none is given) and turns each ``${secret:NAME}`` or ``${file:PATH}``
reference into a runtime placeholder. It validates each secret but never
copies the value into the result.

The emitters take a different route and do not use this module. They copy
``${VAR}`` unchanged, rewrite ``${secret:NAME}`` to ``${NAME}`` (or, for a
lone Codex header secret, the bare name under ``env_http_headers``), and copy
``${file:PATH}`` verbatim with a warning. The host tool expands them.

Errors mirror the missing-env policy in
:mod:`crewspec.models.interpolate`: a missing or unreadable secret is a
path-aware error, not a silent empty string.

TODO: How do we handle vases where I wanna pass the literal ${VAR} through
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from crewspec.models.interpolate import (
    InterpolationError,
    SecretRef,
    SecretResolveFn,
)
from crewspec.models.mcp import McpServer

__all__ = [
    "SecretError",
    "SecretNotFound",
    "SecretUnreadable",
    "SecretPermission",
    "SecretBinding",
    "SecretResolver",
    "ResolvedServer",
    "resolve_server",
]


class SecretError(InterpolationError):
    """Base class for secret resolution failures."""


class SecretNotFound(SecretError):
    """A secret reference points at a missing env entry or file."""


class SecretUnreadable(SecretError):
    """A secret file exists but could not be read."""


class SecretPermission(SecretError):
    """A secret file is readable by group or other (overly permissive)."""


@dataclass(frozen=True)
class SecretBinding:
    """How the launcher should deliver one resolved secret at runtime.

    Carries a pointer only, never the secret value.
    """

    ref: SecretRef
    delivery: Literal["mount", "env"]
    #: Runtime token substituted into the resolved spec in the reference's
    #: place (a file path for ``mount``, a ``${NAME}`` env ref for ``env``).
    placeholder: str
    #: Where the launcher reads the real value from (file path or env name).
    source: str


class SecretResolver:
    """Resolve secret references against a file system and a secret env.

    The secret env is deliberately separate from the interpolation env passed
    to :meth:`McpServer.resolve`, so ordinary config and secrets cannot be
    confused for one another.
    """

    def __init__(self, secret_env: Mapping[str, str] | None = None) -> None:
        self._secret_env = dict(secret_env) if secret_env is not None else {}

    def resolve_ref(self, ref: SecretRef) -> SecretBinding:
        """Validate ``ref`` and return its delivery binding."""

        if ref.kind == "file":
            return self._resolve_file(ref)
        return self._resolve_env(ref)

    def _resolve_env(self, ref: SecretRef) -> SecretBinding:
        value = self._secret_env.get(ref.target)
        if value is None:
            raise SecretNotFound(
                f"secret '{ref.target}' is not set in the secret environment"
            )

        if value == "":
            raise SecretNotFound(
                f"secret '{ref.target}' is set but empty in the secret environment"
            )
        return SecretBinding(
            ref=ref,
            delivery="env",
            placeholder=f"${{{ref.target}}}",
            source=ref.target,
        )

    def _resolve_file(self, ref: SecretRef) -> SecretBinding:
        path = Path(ref.target)
        # A single stat() keeps existence, file-type, and permission checks on
        # one syscall and ensures every failure surfaces as a SecretError.
        try:
            info = path.stat()
        except FileNotFoundError:
            raise SecretNotFound(f"secret file '{ref.target}' does not exist") from None
        except OSError as exc:
            raise SecretUnreadable(
                f"secret file '{ref.target}' could not be read: {exc.strerror}"
            ) from exc

        if not stat.S_ISREG(info.st_mode):
            raise SecretNotFound(f"secret '{ref.target}' is not a regular file")

        perms = stat.S_IMODE(info.st_mode)
        if perms & 0o077:
            raise SecretPermission(
                f"secret file '{ref.target}' is readable by group or other "
                f"(mode {perms:#o}); tighten it to owner-only"
            )

        if not os.access(path, os.R_OK):
            raise SecretUnreadable(f"secret file '{ref.target}' is not readable")

        return SecretBinding(
            ref=ref,
            delivery="mount",
            placeholder=str(path),
            source=str(path),
        )


@dataclass(frozen=True)
class ResolvedServer:
    """An MCP server with references resolved, plus its secret bindings."""

    server: McpServer
    #: Deduplicated bindings for the launcher to satisfy at runtime.
    secrets: tuple[SecretBinding, ...]


def resolve_server(
    server: McpServer,
    env: Mapping[str, str] | None = None,
    secrets: SecretResolver | None = None,
) -> ResolvedServer:
    """Resolve ``server``, collecting secret bindings as a side channel.

    Environment references are inlined from ``env``; secret references are
    routed through ``secrets`` into runtime placeholders, never inlined. The
    returned :class:`ResolvedServer` pairs the resolved model with the
    deduplicated bindings the launcher must satisfy.
    """

    bindings: dict[tuple[str, str], SecretBinding] = {}
    callback: SecretResolveFn | None = None

    if secrets is not None:
        resolver = secrets

        def _collect(ref: SecretRef) -> str:
            binding = resolver.resolve_ref(ref)
            bindings.setdefault((ref.kind, ref.target), binding)
            return binding.placeholder

        callback = _collect

    resolved = server.resolve(env, secrets=callback)
    return ResolvedServer(server=resolved, secrets=tuple(bindings.values()))
