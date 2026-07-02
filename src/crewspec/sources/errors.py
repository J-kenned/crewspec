"""Errors raised while turning a source spec into a local crewfile tree.

The CLI maps each subclass onto an exit code: :class:`SourceSpecError` is a
usage error (a malformed argument, exit 2), while :class:`SourceFetchError` and
:class:`SourceLayoutError` are runtime failures (exit 1).
"""

from __future__ import annotations


class SourceError(Exception):
    """Base class for source resolution failures."""


class SourceSpecError(SourceError):
    """The source string is malformed (e.g. a bad ``github:`` shorthand)."""


class SourceFetchError(SourceError):
    """Fetching the source failed: network, HTTP status, or a corrupt archive."""


class SourceLayoutError(SourceError):
    """The fetched archive lacks the expected layout (e.g. a missing subdir)."""
