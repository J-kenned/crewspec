"""The crewfile 0.2 per-agent runtime blocks.

Every block here is a boundary (what wakes an agent, where it talks, what it
may touch, what it may spend) and so lives on the agent, never at pack level:
a reader must see an agent's whole ceiling in one place. Share with YAML
anchors, not inheritance. No current emitter renders these; they are carried
through the IR and reported as withheld (see :mod:`crewspec.plan`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Agent keys that are 0.2 blocks. Order is the order ``withheld`` reports them.
RUNTIME_BLOCKS = ("triggers", "channels", "sandbox", "secrets", "limits", "approvals")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Coalesce(_Strict):
    key: str
    window_s: int
    max_wait_s: int | None = None


class Claim(_Strict):
    """Labels a label-triggered agent swaps in to claim and to report failure."""

    working: str
    failed: str


class TriggerRun(_Strict):
    fresh_session: bool = False


class OnFailure(_Strict):
    auto_disable_after: int | None = None
    notify: str | None = None


class Trigger(_Strict):
    """One thing that wakes the agent. ``prompt`` is the brief it receives."""

    provider: Literal["github", "cron", "webhook", "channel"]
    type: str | None = None
    filter: dict[str, str] = Field(default_factory=dict)
    cron: str | None = None
    fire_at: str | None = None
    prompt: str | None = None
    claim: Claim | None = None
    coalesce: Coalesce | None = None
    deliver_only: bool = False
    run: TriggerRun = Field(default_factory=TriggerRun)
    on_failure: OnFailure | None = None


class Channel(_Strict):
    kind: Literal["slack", "github", "cli", "push"]
    binding: dict[str, str] = Field(default_factory=dict)
    approvals_inbox: bool = False


class Leaves(_Strict):
    hidden: list[str] = Field(default_factory=list)
    readonly: list[str] = Field(default_factory=list)


class Mount(_Strict):
    """A host path exposed inside the sandbox; only honoured with `host_access: host-disk`."""

    path: str
    mode: Literal["ro", "rw"] = "ro"


class Sandbox(_Strict):
    kind: Literal["none", "docker", "vm"]
    required: bool = False
    policy: Literal["read-only", "workspace-write", "full-access"] = "read-only"
    network: Literal["none", "broker-only", "allowlist"] = "none"
    allow: list[str] = Field(default_factory=list)
    host_access: Literal["none", "own-machine", "host-ui", "host-disk"] = "none"
    disk_mb: int | None = None
    leaves: Leaves = Field(default_factory=Leaves)
    mounts: list[Mount] = Field(default_factory=list)


class Secret(_Strict):
    """Where a named secret comes from and how the runtime may deliver it.

    ``blind`` (the default) means the agent's process never holds the value:
    the broker swaps it at egress for the listed ``hosts``. ``blind: false`` is
    the declared exception for a tool that cannot be brokered.
    """

    ref: str = Field(pattern=r"^vault://.+")
    blind: bool = True
    hosts: list[str] = Field(default_factory=list)
    kind: Literal["api", "web-login"] = "api"
    #: ``kind: web-login`` only: the page the runtime's login tool opens. Its host
    #: must be one of ``hosts``; absent means the origin itself.
    login_url: str | None = None


class Limits(_Strict):
    """Hard ceilings. The two required ones make every agent's cost visible."""

    tokens_per_run: int
    wall_clock_s: int
    tokens_per_day: int | None = None
    max_prs_per_night: int | None = None
    retries: int | None = None
    deny: list[str] = Field(default_factory=list)


Action = Literal["send", "post", "pay", "sign", "delete", "merge", "promote"]


class Approvals(_Strict):
    policy: Literal["untrusted", "on-request", "never"] = "on-request"
    ask: list[Action] = Field(default_factory=list)
    timeout_s: int | None = None
    on_timeout: Literal["deny", "allow"] = "deny"
    deferred: bool = False
    unattended: Literal["deny", "allow"] = "deny"
