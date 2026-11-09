"""``init_assembly(org_id=..., registration_key=...)`` reaches the native register (AAASM-6444).

The organization is a *claim* the gateway validates against the registration
key, so the SDK must (a) carry both to the native client, (b) leave the
pre-existing call shape untouched when neither is given, and (c) never fall back
to registering the agent org-less on a native build that cannot carry them.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_assembly import init_assembly
from agent_assembly.core import assembly as core_assembly
from agent_assembly.core.audit_sink import AUDIT_SINK_ABSENT
from agent_assembly.exceptions import ConfigurationError

from ._fake_core import FakeRuntimeClient, install_fake_core
from .test_init_registration import _API_KEY, _GRPC_ENDPOINT, _GW_URL, _no_network


class _OrgAwareRuntimeClient(FakeRuntimeClient):
    """A native client whose ``register`` accepts the org claim and key."""

    def __init__(self) -> None:
        super().__init__(decision="allow")
        self.org_register_calls: list[tuple[Any, ...]] = []

    def register(
        self,
        agent_id: str,
        name: str,
        framework: str,
        gateway_endpoint: str | None = None,
        team_id: str | None = None,
        parent_agent_id: str | None = None,
        org_id: str | None = None,
        registration_key: str | None = None,
    ) -> str:
        self.org_register_calls.append(
            (agent_id, name, framework, gateway_endpoint, team_id, parent_agent_id, org_id, registration_key)
        )
        if self.register_should_raise is not None:
            raise self.register_should_raise
        return "policy-id-001"


def _init(monkeypatch: pytest.MonkeyPatch, client: FakeRuntimeClient, **kwargs: Any) -> Any:
    install_fake_core(monkeypatch, client)
    _no_network(monkeypatch)
    monkeypatch.setattr(core_assembly, "_register_adapters", lambda **_kw: ([], AUDIT_SINK_ABSENT))
    return init_assembly(gateway_url=_GW_URL, api_key=_API_KEY, mode="sdk-only", **kwargs)


def test_init_assembly_forwards_org_claim_and_registration_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _OrgAwareRuntimeClient()
    context = _init(
        monkeypatch,
        client,
        agent_id="acme-bot",
        team_id="platform",
        org_id="acme",
        registration_key="aa_00112233445566778899aabbccddeeff",
    )
    try:
        assert client.org_register_calls == [
            (
                "acme-bot",
                "acme-bot",
                "python",
                _GRPC_ENDPOINT,
                "platform",
                None,
                "acme",
                "aa_00112233445566778899aabbccddeeff",
            )
        ]
    finally:
        context.shutdown()


def test_init_assembly_key_alone_lets_the_gateway_derive_the_org(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _OrgAwareRuntimeClient()
    context = _init(monkeypatch, client, agent_id="derived", registration_key="aa_k")
    try:
        assert client.org_register_calls[0][6:] == (None, "aa_k")
    finally:
        context.shutdown()


def test_init_assembly_without_org_keeps_the_existing_register_call_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeRuntimeClient(decision="allow")
    context = _init(monkeypatch, client, agent_id="plain")
    try:
        assert client.register_calls == [("plain", "plain", "python", _GRPC_ENDPOINT, None, None)]
    finally:
        context.shutdown()


def test_init_assembly_never_registers_org_less_when_native_cannot_carry_the_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: the legacy 6-arg client has no org parameters. Silently
    dropping the claim would register the agent outside the organization the
    caller asked for, so init fails instead and nothing is registered."""
    client = FakeRuntimeClient(decision="allow")  # register() has no org_id kwarg
    with pytest.raises(ConfigurationError, match="cannot carry org_id"):
        _init(monkeypatch, client, agent_id="acme-bot", org_id="acme", enforcement_mode="enforce")
    assert client.register_calls == []


def test_init_assembly_treats_blank_org_and_key_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank value must not become an empty claim or an empty key header."""
    client = FakeRuntimeClient(decision="allow")  # legacy 6-arg register: any org kwarg would raise TypeError
    context = _init(monkeypatch, client, agent_id="blank", org_id="  ", registration_key="")
    try:
        assert client.register_calls == [("blank", "blank", "python", _GRPC_ENDPOINT, None, None)]
    finally:
        context.shutdown()


def test_registration_failure_output_never_contains_the_registration_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """The SDK adds nothing to a failed registration's output: the key is neither
    in the stderr warning nor in any log record, whatever the native layer raised."""
    secret = "aa_" + "9" * 32
    client = _OrgAwareRuntimeClient()
    client.register_should_raise = RuntimeError("gateway rejected registration: no valid key")
    with caplog.at_level("DEBUG"):
        context = _init(monkeypatch, client, agent_id="leaky", org_id="acme", registration_key=secret)
    try:
        captured = capsys.readouterr()
        assert "NOT registered" in captured.err  # the failure path really ran
        assert secret not in captured.err + captured.out
        assert secret not in caplog.text
    finally:
        context.shutdown()
