"""Tests for the crewfile 0.2 per-agent runtime blocks and the rules around them."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from crewspec.ir import ResolvedIR
from crewspec.models import CrewDoc
from crewspec.parser import UnknownReferenceError, load_crewfile
from tests.conftest import Runner

NIGHT_SHIFT = Path(__file__).resolve().parent.parent / "examples" / "night-shift"


def _agent(**extra: object) -> dict[str, object]:
    return {"persona": "agents/a.md", **extra}


def _doc(**agent: object) -> dict[str, object]:
    version = agent.pop("version", "0.2")
    return {"version": version, "name": "p", "agents": {"a": _agent(**agent)}}


LIMITS = {"tokens_per_run": 1, "wall_clock_s": 1}
INBOX = [{"kind": "slack", "approvals_inbox": True}]


def _err(doc: dict[str, object]) -> str:
    with pytest.raises(ValidationError) as info:
        CrewDoc.model_validate(doc)
    return str(info.value)


# -- version gate -------------------------------------------------------------


def test_version_must_be_0_1_or_0_2() -> None:
    assert "version" in _err(_doc(version="0.3"))


def test_runtime_block_under_0_1_names_agent_and_block() -> None:
    assert "agents.a.limits" in _err(_doc(version="0.1", limits=LIMITS))


def test_extension_under_0_1_is_rejected() -> None:
    assert "agents.a.x-operative" in _err(_doc(version="0.1", **{"x-operative": {}}))


# -- blocks -------------------------------------------------------------------


def test_blocks_parse_and_report_in_fixed_order() -> None:
    doc = CrewDoc.model_validate(
        _doc(
            limits=LIMITS,
            sandbox={"kind": "docker"},
            **{"x-operative": {"backend": "claude_code"}},
        )
    )
    agent = doc.agents["a"]
    assert agent.runtime_blocks() == ("sandbox", "limits", "x-operative")
    assert agent.extensions == {"x-operative": {"backend": "claude_code"}}


def test_limits_require_tokens_per_run_and_wall_clock() -> None:
    assert "tokens_per_run" in _err(_doc(limits={"wall_clock_s": 1}))
    assert "wall_clock_s" in _err(_doc(limits={"tokens_per_run": 1}))


def test_secret_ref_must_be_vault_scheme() -> None:
    assert "vault://" in _err(_doc(secrets={"t": {"ref": "env:TOKEN"}}))


def test_secret_blind_defaults_true() -> None:
    doc = CrewDoc.model_validate(_doc(secrets={"t": {"ref": "vault://x"}}))
    assert (doc.agents["a"].secrets or {})["t"].blind is True


@pytest.mark.parametrize("key", ["inject", "bogus"])
def test_unknown_keys_are_still_forbidden(key: str) -> None:
    assert key in _err(_doc(secrets={"t": {"ref": "vault://x", key: "mcp:slack"}}))
    assert key in _err(_doc(**{key: 1}))


def test_extension_must_be_a_mapping() -> None:
    assert "x-foo must be a mapping" in _err(_doc(**{"x-foo": 1}))


def test_extensions_key_is_reserved_even_without_x_keys() -> None:
    assert "reserved" in _err(_doc(extensions={"foo": {}}))


def test_two_approvals_inboxes_are_rejected() -> None:
    two = [
        {"kind": "slack", "approvals_inbox": True},
        {"kind": "cli", "approvals_inbox": True},
    ]
    assert "at most one channel" in _err(_doc(channels=two))


def test_approvals_block_needs_exactly_one_inbox() -> None:
    assert "exactly one channel" in _err(_doc(approvals={"ask": ["delete"]}))
    CrewDoc.model_validate(_doc(approvals={"ask": ["delete"]}, channels=INBOX))


def test_approvals_ask_is_a_closed_enum() -> None:
    assert "ask" in _err(_doc(approvals={"ask": ["launch"]}, channels=INBOX))


# -- secret grammar (loader) --------------------------------------------------


def _copy_night_shift(tmp_path: Path) -> tuple[Path, dict]:
    import shutil

    shutil.copytree(NIGHT_SHIFT, tmp_path / "ns")
    crewfile = tmp_path / "ns" / "night-shift.crewfile"
    return crewfile, yaml.safe_load(crewfile.read_text())


def _save(crewfile: Path, raw: dict) -> Path:
    crewfile.write_text(yaml.safe_dump(raw, sort_keys=False))
    return crewfile


def test_night_shift_loads_clean_under_0_2() -> None:
    loaded = load_crewfile(NIGHT_SHIFT / "night-shift.crewfile")
    assert loaded.doc.version == "0.2"
    assert "x-operative" in loaded.doc.agents["night-shift"].extensions


def test_undeclared_secret_used_by_mcp_fails_with_line(tmp_path: Path) -> None:
    crewfile, raw = _copy_night_shift(tmp_path)
    del raw["agents"]["night-shift"]["secrets"]["slack_bot"]
    with pytest.raises(UnknownReferenceError) as info:
        load_crewfile(_save(crewfile, raw))
    msg = str(info.value)
    assert "mcps.slack uses ${secret:slack_bot} but no agent declares it" in msg
    assert info.value.line is not None


def test_agent_reaching_server_must_declare_its_secret(tmp_path: Path) -> None:
    crewfile, raw = _copy_night_shift(tmp_path)
    second = copy.deepcopy(raw["agents"]["night-shift"])
    del second["secrets"]["github_token"]
    raw["agents"]["second"] = second
    with pytest.raises(
        UnknownReferenceError, match="agent 'second' reaches the server"
    ):
        load_crewfile(_save(crewfile, raw))


def test_inherit_all_agent_must_declare_every_server_secret(tmp_path: Path) -> None:
    crewfile, raw = _copy_night_shift(tmp_path)
    raw["agents"]["second"] = {
        "persona": "agents/night-shift.md"
    }  # no tools: inherits all
    with pytest.raises(
        UnknownReferenceError, match="agent 'second' reaches the server"
    ):
        load_crewfile(_save(crewfile, raw))


def test_0_1_keeps_secret_passthrough(tmp_path: Path) -> None:
    crewfile, raw = _copy_night_shift(tmp_path)
    raw["version"] = "0.1"
    for block in (
        "triggers",
        "channels",
        "sandbox",
        "secrets",
        "limits",
        "approvals",
        "x-operative",
    ):
        del raw["agents"]["night-shift"][block]
    load_crewfile(_save(crewfile, raw))  # ${secret:…} undeclared, still fine on 0.1


# -- CLI: withheld reporting, emit does not crash ----------------------------


def test_validate_json_lists_withheld_blocks(run_cli: Runner) -> None:
    result = run_cli("validate", str(NIGHT_SHIFT), "--json")
    assert result.exit_code == 0, result.output
    ir = ResolvedIR.model_validate_json(result.output)
    assert ir.agents[0].withheld == (
        "triggers",
        "channels",
        "sandbox",
        "secrets",
        "limits",
        "approvals",
        "x-operative",
    )


def test_validate_tree_mentions_withheld(run_cli: Runner) -> None:
    result = run_cli("validate", str(NIGHT_SHIFT))
    assert result.exit_code == 0, result.output
    assert (
        "withheld: triggers, channels, sandbox, secrets, limits, approvals, x-operative"
        in result.output
    )


@pytest.mark.parametrize("target", ["claude", "codex"])
def test_install_withholds_0_2_blocks_without_crashing(
    run_cli: Runner, tmp_path: Path, target: str
) -> None:
    result = run_cli(
        "install", str(NIGHT_SHIFT), "--target", target, "--out", str(tmp_path)
    )
    assert result.exit_code == 0, result.output
    written = "".join(
        p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file()
    )
    for block in ("tokens_per_run", "vault://", "approvals_inbox", "host_access"):
        assert block not in written


def test_sandbox_mounts_parse_with_ro_default_and_reject_bad_mode() -> None:
    doc = CrewDoc.model_validate(
        _doc(limits=LIMITS, sandbox={"kind": "docker", "host_access": "host-disk",
                                     "mounts": [{"path": "/srv/data"}, {"path": "/srv/out", "mode": "rw"}]})
    )
    mounts = doc.agents["a"].sandbox.mounts
    assert [(m.path, m.mode) for m in mounts] == [("/srv/data", "ro"), ("/srv/out", "rw")]
    with pytest.raises(ValidationError):
        CrewDoc.model_validate(_doc(limits=LIMITS, sandbox={"kind": "docker", "mounts": [{"path": "/x", "mode": "rwx"}]}))


def test_secret_login_url_is_web_login_only_field() -> None:
    from crewspec.models.runtime import Secret

    s = Secret(ref="vault://jira/demo", kind="web-login", hosts=["jira.example.com"],
               login_url="https://jira.example.com/login")
    assert s.login_url == "https://jira.example.com/login"
    assert Secret(ref="vault://x/y").login_url is None


def test_sandbox_mounts_need_host_disk_and_absolute_paths() -> None:
    with pytest.raises(ValidationError, match="host_access: host-disk"):
        CrewDoc.model_validate(_doc(limits=LIMITS, sandbox={"kind": "docker", "mounts": [{"path": "/srv"}]}))
    for bad in ("srv/data", "/srv/../etc"):
        with pytest.raises(ValidationError, match="absolute"):
            CrewDoc.model_validate(_doc(
                limits=LIMITS, sandbox={"kind": "docker", "host_access": "host-disk", "mounts": [{"path": bad}]}
            ))
