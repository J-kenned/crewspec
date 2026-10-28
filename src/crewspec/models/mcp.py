"""MCP server definitions.

A single ``McpServer`` discriminated union (on ``type``) covers the transports
crewspec understands today:

* ``stdio``: a local process (``command`` / ``args`` / ``env``).
* ``http`` / ``sse``: a remote endpoint (``url`` / ``headers``).

String fields may carry ``${...}`` references; their *syntax* is validated at
parse time, but substitution is lazy, call :meth:`resolve` with an environment
mapping to get an interpolated copy.

Every server carries a ``scope``: ``user`` (default) is merged into the Claude
target's user-scope config at install; ``project`` is declared for dependency
checking and the pack's own per-project tooling and the Claude target never
writes it there. Kiro's per-agent specs under ``~/.kiro`` carry every server an
agent can reach, project scope included, and are removed on uninstall.
"""

from __future__ import annotations

import os
from typing import Annotated, Literal, Mapping, Self, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from crewspec.models.interpolate import (
    SecretResolveFn,
    interpolate,
    validate_template,
)


def _validate_templates(value: str | list[str] | dict[str, str]) -> None:
    """Validate ``${...}`` placeholder syntax across a field's strings."""

    if isinstance(value, str):
        validate_template(value)
    elif isinstance(value, list):
        for item in value:
            validate_template(item)
    else:
        for item in value.values():
            validate_template(item)


class StdioMcpServer(BaseModel):
    """An MCP server launched as a local subprocess over stdio."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["stdio"] = "stdio"
    scope: Literal["user", "project"] = "user"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("command", "args", "env")
    @classmethod
    def _check_templates(
        cls, value: str | list[str] | dict[str, str]
    ) -> str | list[str] | dict[str, str]:
        _validate_templates(value)
        return value

    def resolve(
        self,
        env: Mapping[str, str] | None = None,
        *,
        secrets: SecretResolveFn | None = None,
    ) -> Self:
        """Return a copy with ``${...}`` references substituted from ``env``.

        Secret references are routed through ``secrets`` (see
        :func:`crewspec.resolver.resolve_server`) so their values are never
        inlined; without it, a secret reference raises.
        """

        src = os.environ if env is None else env
        return self.model_copy(
            update={
                "command": interpolate(self.command, src, secrets=secrets),
                "args": [interpolate(arg, src, secrets=secrets) for arg in self.args],
                "env": {
                    k: interpolate(v, src, secrets=secrets) for k, v in self.env.items()
                },
            }
        )


class _RemoteMcpServer(BaseModel):
    """Shared fields for HTTP-style transports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["user", "project"] = "user"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url", "headers")
    @classmethod
    def _check_templates(cls, value: str | dict[str, str]) -> str | dict[str, str]:
        _validate_templates(value)
        return value

    def resolve(
        self,
        env: Mapping[str, str] | None = None,
        *,
        secrets: SecretResolveFn | None = None,
    ) -> Self:
        """Return a copy with ``${...}`` references substituted from ``env``.

        Secret references are routed through ``secrets`` (see
        :func:`crewspec.resolver.resolve_server`) so their values are never
        inlined; without it, a secret reference raises.
        """

        src = os.environ if env is None else env
        return self.model_copy(
            update={
                "url": interpolate(self.url, src, secrets=secrets),
                "headers": {
                    k: interpolate(v, src, secrets=secrets)
                    for k, v in self.headers.items()
                },
            }
        )


class HttpMcpServer(_RemoteMcpServer):
    """An MCP server reached over streamable HTTP."""

    type: Literal["http"] = "http"


class SseMcpServer(_RemoteMcpServer):
    """An MCP server reached over server-sent events."""

    type: Literal["sse"] = "sse"


McpServer = Annotated[
    Union[StdioMcpServer, HttpMcpServer, SseMcpServer],
    Field(discriminator="type"),
]
"""Any supported MCP server. Unknown ``type`` values are rejected."""
