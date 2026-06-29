"""Tests for the env-var interpolation helper."""

import pytest

from crewspec.models.interpolate import (
    EnvRef,
    MalformedTemplate,
    MissingEnvVar,
    SecretRef,
    SecretResolutionDeferred,
    interpolate,
    parse_refs,
)


def test_plain_var_resolves() -> None:
    assert interpolate("${HOST}", {"HOST": "db.local"}) == "db.local"


def test_var_embedded_in_text() -> None:
    out = interpolate("https://${HOST}/v1", {"HOST": "db.local"})
    assert out == "https://db.local/v1"


def test_default_used_when_unset() -> None:
    assert interpolate("${PORT:-5432}", {}) == "5432"


def test_present_var_beats_default() -> None:
    assert interpolate("${PORT:-5432}", {"PORT": "6000"}) == "6000"


def test_empty_default_is_allowed() -> None:
    assert interpolate("${MAYBE:-}", {}) == ""


def test_missing_var_without_default_raises() -> None:
    with pytest.raises(MissingEnvVar, match="TOKEN"):
        interpolate("${TOKEN}", {})


def test_secret_ref_resolution_is_deferred() -> None:
    with pytest.raises(SecretResolutionDeferred, match="secret:API_KEY"):
        interpolate("${secret:API_KEY}", {})


def test_file_ref_resolution_is_deferred() -> None:
    with pytest.raises(SecretResolutionDeferred, match="file:/run/secret"):
        interpolate("${file:/run/secret}", {})


def test_unterminated_placeholder_raises() -> None:
    with pytest.raises(MalformedTemplate):
        interpolate("${HOST", {"HOST": "x"})


def test_invalid_var_name_raises() -> None:
    with pytest.raises(MalformedTemplate):
        interpolate("${1BAD}", {})


def test_empty_secret_target_raises() -> None:
    with pytest.raises(MalformedTemplate):
        interpolate("${secret:}", {})


def test_parse_refs_splits_literals_and_refs() -> None:
    fragments = parse_refs("a${VAR}b${secret:S}")
    assert fragments == [
        "a",
        EnvRef(name="VAR", default=None),
        "b",
        SecretRef(kind="secret", target="S"),
    ]


def test_parse_refs_captures_default() -> None:
    assert parse_refs("${PORT:-5432}") == [EnvRef(name="PORT", default="5432")]
