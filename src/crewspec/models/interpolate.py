"""Env-var interpolation for crewspec specs.

Specs store values exactly as authored, ``${VAR}`` references are *not*
substituted at parse time. Interpolation is done lazily, via
:func:`interpolate`, against an environment mapping. This keeps specs
secret-free and faithfully round-trippable, and keeps parsing deterministic
(independent of the ambient process environment).

Supported forms inside ``${...}``:

* ``${VAR}``: substitute the value of ``VAR``.
* ``${VAR:-default}``: substitute ``VAR`` if set, otherwise ``default``.
* ``${secret:NAME}`` / ``${file:PATH}``: secret references. The vocabulary is
  parsed and validated here, but the backing value is *never* inlined. Pass a
  ``secrets`` callback to :func:`interpolate` to turn each reference into a
  runtime placeholder (see :mod:`crewspec.resolver.secrets`); without one,
  :func:`interpolate` raises :class:`SecretResolutionDeferred`.

Missing-env policy: resolving an unset ``VAR`` with no default is an error
(:class:`MissingEnvVar`). Provide a default to opt out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Union

__all__ = [
    "EnvRef",
    "SecretRef",
    "Fragment",
    "SecretResolveFn",
    "parse_refs",
    "interpolate",
    "InterpolationError",
    "MalformedTemplate",
    "MissingEnvVar",
    "SecretResolutionDeferred",
]


class InterpolationError(ValueError):
    """Base class for interpolation failures.

    Subclasses ``ValueError`` so that syntax errors raised inside pydantic
    field validators are wrapped into path-aware ``ValidationError``s.
    """


class MalformedTemplate(InterpolationError):
    """A ``${...}`` placeholder is syntactically invalid."""


class MissingEnvVar(InterpolationError):
    """An env var is referenced but unset, and no default was supplied."""


class SecretResolutionDeferred(InterpolationError):
    """A secret reference was encountered; backend resolution is not yet done."""


@dataclass(frozen=True)
class EnvRef:
    """A reference to an environment variable, with an optional default."""

    name: str
    default: str | None = None


@dataclass(frozen=True)
class SecretRef:
    """A reserved reference to a secret backend. Resolution is deferred."""

    kind: Literal["secret", "file"]
    target: str


Fragment = Union[str, EnvRef, SecretRef]

#: Maps a secret reference to the runtime placeholder substituted in its place.
#: The callback resolves/validates the backing secret; the value is never
#: returned here (only a pointer the launcher dereferences).
SecretResolveFn = Callable[["SecretRef"], str]

# A ``${...}`` placeholder. The body is validated separately so we can emit a
# precise error for malformed contents rather than silently treating ``${`` as
# a literal.
_PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")

_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _parse_body(body: str) -> EnvRef | SecretRef:
    """Parse the contents of a single ``${...}`` placeholder."""

    if body.startswith("secret:") or body.startswith("file:"):
        kind, _, target = body.partition(":")
        if not target.strip():
            raise MalformedTemplate(f"empty target in '${{{body}}}'")
        # kind is narrowed by the startswith checks above.
        return SecretRef(kind=kind, target=target)  # type: ignore[arg-type]

    name, sep, default = body.partition(":-")
    if not _VAR_NAME.fullmatch(name):
        raise MalformedTemplate(f"invalid variable name in '${{{body}}}'")
    return EnvRef(name=name, default=default if sep else None)


def parse_refs(template: str) -> list[Fragment]:
    """Split ``template`` into literal text and reference fragments.

    Raises :class:`MalformedTemplate` if a ``${`` is opened but never closed,
    or a placeholder body is invalid.
    """

    fragments: list[Fragment] = []
    pos = 0
    for match in _PLACEHOLDER.finditer(template):
        start, end = match.span()
        if start > pos:
            fragments.append(template[pos:start])
        fragments.append(_parse_body(match.group(1)))
        pos = end

    rest = template[pos:]
    if "${" in rest:
        raise MalformedTemplate(f"unterminated '${{' in {template!r}")
    if rest:
        fragments.append(rest)
    return fragments


def validate_template(template: str) -> None:
    """Validate placeholder syntax without resolving anything.

    Used by model validators so malformed templates fail at parse time, while
    actual substitution stays lazy.
    """

    parse_refs(template)


def interpolate(
    template: str,
    env: Mapping[str, str],
    *,
    secrets: SecretResolveFn | None = None,
) -> str:
    """Substitute ``${...}`` references in ``template`` using ``env``.

    Environment references are inlined from ``env``. Secret references are
    handed to the optional ``secrets`` callback, which returns the runtime
    placeholder to substitute (the secret value is never inlined). When no
    callback is supplied, encountering a secret reference is an error.

    Raises :class:`MissingEnvVar` for an unset var with no default,
    :class:`SecretResolutionDeferred` for a secret reference when ``secrets``
    is ``None``, and :class:`MalformedTemplate` for invalid placeholder syntax.
    """

    out: list[str] = []
    for fragment in parse_refs(template):
        if isinstance(fragment, str):
            out.append(fragment)
        elif isinstance(fragment, EnvRef):
            value = env.get(fragment.name, fragment.default)
            if value is None:
                raise MissingEnvVar(
                    f"environment variable '{fragment.name}' is not set "
                    f"and no default was provided"
                )
            out.append(value)
        elif secrets is None:
            raise SecretResolutionDeferred(
                f"cannot resolve '{fragment.kind}:{fragment.target}': no secret "
                f"resolver was provided"
            )
        else:
            out.append(secrets(fragment))
    return "".join(out)
