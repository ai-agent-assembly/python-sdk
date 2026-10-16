"""Unit tests for the runtime-backed pre-execution check (AAASM-3049, AAASM-3106).

Wave 3 of AAASM-3021: a reachable runtime's ``deny`` blocks a tool via the
adapter ``check_tool_start`` contract.

AAASM-3106 adds the failure posture: under ``enforce`` an unreachable runtime, a
raising ``query_policy``, or an error-sentinel ``decision`` must deny (fail
closed); under ``observe`` / ``disabled`` those paths still proceed (fail open).
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest

from agent_assembly.adapters.langchain import AssemblyCallbackHandler
from agent_assembly.core import runtime_interceptor
from agent_assembly.core.runtime_interceptor import (
    RuntimeQueryInterceptor,
    _FailClosedInterceptor,
    build_governance_interceptor,
)
from agent_assembly.exceptions import OpTerminatedError, ToolExecutionBlockedError


class _FakeRuntimeClient:
    """Mock native RuntimeClient capturing query_policy calls."""

    def __init__(self, decision: str, reason: str = "") -> None:
        self._decision = decision
        self._reason = reason
        self.calls: list[tuple[Any, ...]] = []

    def query_policy(
        self,
        agent_id: str,
        action_type: str,
        tool_name: str | None = None,
        tool_args_json: str | None = None,
    ) -> dict[str, str]:
        self.calls.append((agent_id, action_type, tool_name, tool_args_json))
        return {"decision": self._decision, "reason": self._reason}


class _FakeGatewayClient:
    """Stand-in GatewayClient with no check_tool_start (production shape)."""

    def __init__(self) -> None:
        self.agent_id = "agent-001"
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_deny_decision_maps_to_block_status() -> None:
    runtime_client = _FakeRuntimeClient("deny", reason="policy violation")
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), runtime_client, "agent-001")

    result = interceptor.check_tool_start(
        serialized={"name": "web_search"},
        input_str="query",
        tool_name="web_search",
        args={"q": "x"},
    )

    assert result == {"status": "deny", "reason": "policy violation"}
    assert runtime_client.calls == [("agent-001", "tool_call", "web_search", '{"q": "x"}')]


@pytest.mark.parametrize("decision", ["allow", "unspecified", "anything-else"])
def test_non_deny_decisions_proceed(decision: str) -> None:
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient(decision), "agent-001")

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


def test_pending_decision_routes_to_existing_approval_path() -> None:
    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _FakeRuntimeClient("pending", reason="awaiting"), "agent-001"
    )

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "pending", "reason": "awaiting"}


def test_query_raising_fails_open() -> None:
    class _Raising:
        def query_policy(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
            raise RuntimeError("native boom")

    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _Raising(), "agent-001")

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


def test_delegates_unknown_attributes_to_gateway_client() -> None:
    client = _FakeGatewayClient()
    interceptor = RuntimeQueryInterceptor(client, _FakeRuntimeClient("allow"), "agent-001")

    interceptor.close()

    assert client.closed is True


def test_callback_handler_blocks_on_runtime_deny() -> None:
    """End-to-end: a DENY runtime drives on_tool_start to raise."""
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient("deny", reason="nope"), "agent-001")
    handler = AssemblyCallbackHandler(interceptor)
    run_id = uuid4()

    with pytest.raises(ToolExecutionBlockedError, match="nope"):
        handler.on_tool_start(
            serialized={"name": "web_search"},
            input_str="query",
            run_id=run_id,
            tool_name="web_search",
            args={"q": "x"},
        )


def test_callback_handler_allows_on_runtime_allow() -> None:
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient("allow"), "agent-001")
    handler = AssemblyCallbackHandler(interceptor)

    handler.on_tool_start(
        serialized={"name": "web_search"},
        input_str="query",
        run_id=uuid4(),
        tool_name="web_search",
        args={"q": "x"},
    )


def test_build_interceptor_without_native_core_fails_closed_under_enforce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AAASM-4760: no native extension under the default (enforce) posture must fail
    CLOSED — a deny-all interceptor, not the old bare-client fail-open that ran tool
    calls ungoverned. The warning fires because no in-process decision is possible."""
    monkeypatch.delitem(sys.modules, "agent_assembly._core", raising=False)

    # Force the import inside build_governance_interceptor to fail.
    import builtins

    real_import = builtins.__import__

    def _no_core_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "agent_assembly._core":
            raise ImportError("native extension unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_core_import)

    client = _FakeGatewayClient()
    with pytest.warns(UserWarning, match="native runtime extension"):
        result = build_governance_interceptor(client, "agent-001")

    assert isinstance(result, _FailClosedInterceptor)
    assert result.check_tool_start(serialized={"name": "t"}, input_str="i")["status"] == "deny"


def test_build_interceptor_returns_bare_client_when_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ``observe`` a native extension present but no reachable runtime:
    connect() raises, so the bare client is returned (fail-open) rather than a
    denying interceptor. (The ``None`` default now fails closed here — AAASM-4130.)"""

    class _UnreachableRuntimeClient:
        @staticmethod
        def connect(_socket_path: str) -> Any:
            raise OSError("no such socket")

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _UnreachableRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    client = _FakeGatewayClient()
    result = build_governance_interceptor(client, "agent-001", "observe")

    assert result is client


def test_build_interceptor_wraps_client_when_runtime_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native extension present and runtime reachable: the client is wrapped so
    a runtime deny can block."""
    runtime_client = _FakeRuntimeClient("deny", reason="blocked")

    class _ConnectingRuntimeClient:
        @staticmethod
        def connect(_socket_path: str) -> Any:
            return runtime_client

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _ConnectingRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    client = _FakeGatewayClient()
    result = build_governance_interceptor(client, "agent-001")

    assert isinstance(result, RuntimeQueryInterceptor)
    assert result.check_tool_start(serialized={"name": "t"}, input_str="i") == {
        "status": "deny",
        "reason": "blocked",
    }


def test_default_mode_unreachable_runtime_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """AAASM-4130: with no ``enforcement_mode`` (the advertised ``enforce`` default)
    a native-present-but-unreachable runtime yields a deny-all interceptor, not the
    silent fail-open bare client. The default posture must match its advertised
    ``enforce`` behavior locally."""

    class _UnreachableRuntimeClient:
        @staticmethod
        def connect(_socket_path: str) -> Any:
            raise OSError("no such socket")

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _UnreachableRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    client = _FakeGatewayClient()
    result = build_governance_interceptor(client, "agent-001")  # no mode -> default

    assert isinstance(result, _FailClosedInterceptor)
    assert result.check_tool_start(serialized={"name": "t"}, input_str="i")["status"] == "deny"


def test_default_mode_warns_when_native_core_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """AAASM-4130 / AAASM-4760: pure-Python install (native extension absent) under the
    default ``enforce`` posture cannot run a local decision, so it warns loudly *and*
    fails closed (deny-all) rather than proceeding ungoverned."""
    monkeypatch.delitem(sys.modules, "agent_assembly._core", raising=False)

    import builtins

    real_import = builtins.__import__

    def _no_core_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "agent_assembly._core":
            raise ImportError("native extension unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_core_import)

    client = _FakeGatewayClient()
    with pytest.warns(UserWarning, match="native runtime extension"):
        result = build_governance_interceptor(client, "agent-001")  # no mode -> default

    assert isinstance(result, _FailClosedInterceptor)
    assert result.check_tool_start(serialized={"name": "t"}, input_str="i")["status"] == "deny"


def test_observe_mode_reports_enforcement_is_not_applied_when_native_core_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two separate contracts, and the test name used to assert only the first.

    The enforce-specific ``UserWarning`` stays scoped to the enforce posture — an
    explicit ``observe`` dry-run legitimately fails open, and telling that caller
    their tools are being denied would be false. But silence about *enforcement*
    was the AAASM-5661 gap: this branch hands back the bare client, whose missing
    ``check_tool_start`` makes the adapters allow, and the only loud signal the
    caller got was about registration.
    """
    monkeypatch.delitem(sys.modules, "agent_assembly._core", raising=False)

    import builtins
    import warnings

    real_import = builtins.__import__

    def _no_core_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "agent_assembly._core":
            raise ImportError("native extension unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_core_import)

    client = _FakeGatewayClient()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = build_governance_interceptor(client, "agent-001", "observe")

    assert result is client
    stderr = capsys.readouterr().err
    assert "SDK-layer enforcement is NOT applied" in stderr
    # The remedy has to be in the same breath as the gap; a caller who reads only
    # the registration warning has no reason to look for a second one.
    assert "agent_assembly._core" in stderr


def test_native_missing_deny_reason_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """AAASM-4760 regression: the native-missing fail-closed deny carries a clear,
    actionable reason so the developer knows *governance is unavailable* (install the
    native extension / opt into observe) rather than mistaking it for a policy deny —
    the loud, logged reason the ticket requires."""
    monkeypatch.delitem(sys.modules, "agent_assembly._core", raising=False)

    import builtins

    real_import = builtins.__import__

    def _no_core_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "agent_assembly._core":
            raise ImportError("native extension unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_core_import)

    with pytest.warns(UserWarning, match="native runtime extension"):
        result = build_governance_interceptor(_FakeGatewayClient(), "agent-001", "enforce")

    verdict = result.check_tool_start(serialized={"name": "t"}, input_str="i")
    assert verdict["status"] == "deny"
    assert "governance unavailable" in verdict["reason"]
    assert "ungoverned" in verdict["reason"]


def test_enforce_query_raising_fails_closed() -> None:
    """AAASM-3106: under enforce a raising query_policy denies, not allows."""

    class _Raising:
        def query_policy(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
            raise RuntimeError("native boom")

    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _Raising(), "agent-001", enforce=True)

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result["status"] == "deny"


@pytest.mark.parametrize("decision", ["query_failed", "channel_closed", "shutdown"])
def test_enforce_error_decision_fails_closed(decision: str) -> None:
    """AAASM-3106: native error-sentinel decisions deny under enforce."""
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient(decision), "agent-001", enforce=True)

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result["status"] == "deny"


@pytest.mark.parametrize("decision", ["query_failed", "channel_closed"])
def test_observe_error_decision_still_fails_open(decision: str) -> None:
    """Without enforce the error-sentinel decisions keep proceeding."""
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient(decision), "agent-001")

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


class _NonDictRuntimeClient:
    """A runtime whose query_policy returns a non-dict (e.g. ``None``)."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def query_policy(self, *_args: Any, **_kwargs: Any) -> Any:
        return self._result


@pytest.mark.parametrize("bad_result", [None, "deny", 42, ["deny"]])
def test_enforce_non_dict_return_fails_closed(bad_result: Any) -> None:
    """AAASM-4167: a non-dict query_policy return denies under enforce rather
    than raising AttributeError on ``result.get``."""
    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _NonDictRuntimeClient(bad_result), "agent-001", enforce=True
    )

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result["status"] == "deny"


@pytest.mark.parametrize("bad_result", [None, "deny", 42, ["deny"]])
def test_observe_non_dict_return_fails_open(bad_result: Any) -> None:
    """AAASM-4167: without enforce a non-dict return degrades to a clean allow,
    not an AttributeError."""
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _NonDictRuntimeClient(bad_result), "agent-001")

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


def test_enforce_unreachable_runtime_denies_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """AAASM-3106: native present but runtime unreachable yields a deny-all
    interceptor under enforce, not the fail-open bare client."""

    class _UnreachableRuntimeClient:
        @staticmethod
        def connect(_socket_path: str) -> Any:
            raise OSError("no such socket")

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _UnreachableRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    client = _FakeGatewayClient()
    result = build_governance_interceptor(client, "agent-001", "enforce")

    assert result is not client
    assert result.check_tool_start(serialized={"name": "t"}, input_str="i")["status"] == "deny"
    # Non-check attributes still delegate to the wrapped client.
    result.close()
    assert client.closed is True


def test_observe_unreachable_runtime_returns_bare_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without enforce an unreachable runtime keeps the fail-open bare client."""

    class _UnreachableRuntimeClient:
        @staticmethod
        def connect(_socket_path: str) -> Any:
            raise OSError("no such socket")

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _UnreachableRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    client = _FakeGatewayClient()

    assert build_governance_interceptor(client, "agent-001", "observe") is client


def test_observe_unreachable_runtime_reports_enforcement_is_not_applied(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second fail-open entry: native present, socket unreachable (AAASM-5661).

    Distinct from the native-missing case above and previously the quieter of the
    two — this branch never emitted anything at all, in either posture.
    """

    class _UnreachableRuntimeClient:
        @staticmethod
        def connect(_socket_path: str) -> Any:
            raise OSError("no such socket")

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _UnreachableRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    client = _FakeGatewayClient()
    build_governance_interceptor(client, "agent-001", "observe")

    stderr = capsys.readouterr().err
    assert "SDK-layer enforcement is NOT applied" in stderr
    assert "runtime unreachable" in stderr


def test_enforce_posture_does_not_report_enforcement_as_unapplied(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The notice is scoped to the fail-open branch — a positive control for it.

    Without this, deleting the ``if not enforce`` guard and warning on every path
    would leave the two controls above green while telling a fail-closed caller
    that enforcement is not applied, which is the opposite of what happens.
    """
    monkeypatch.delitem(sys.modules, "agent_assembly._core", raising=False)

    import builtins
    import warnings

    real_import = builtins.__import__

    def _no_core_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "agent_assembly._core":
            raise ImportError("native extension unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_core_import)

    client = _FakeGatewayClient()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = build_governance_interceptor(client, "agent-001", "enforce")

    assert isinstance(result, _FailClosedInterceptor)
    assert "SDK-layer enforcement is NOT applied" not in capsys.readouterr().err


def test_enforce_wraps_with_fail_closed_query_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_governance_interceptor under enforce wraps a reachable runtime so a
    raising query denies (the wrapper carries enforce=True)."""

    class _Raising:
        def query_policy(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
            raise RuntimeError("boom")

    class _ConnectingRuntimeClient:
        @staticmethod
        def connect(_socket_path: str) -> Any:
            return _Raising()

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _ConnectingRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    result = build_governance_interceptor(_FakeGatewayClient(), "agent-001", "enforce")

    assert isinstance(result, RuntimeQueryInterceptor)
    assert result.check_tool_start(serialized={"name": "t"}, input_str="i")["status"] == "deny"


def test_callback_handler_blocks_on_enforce_fail_closed() -> None:
    """End-to-end: under enforce a failing runtime drives on_tool_start to raise."""

    class _Raising:
        def query_policy(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
            raise RuntimeError("native down")

    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _Raising(), "agent-001", enforce=True)
    handler = AssemblyCallbackHandler(interceptor)
    run_id = uuid4()

    with pytest.raises(ToolExecutionBlockedError):
        handler.on_tool_start(
            serialized={"name": "web_search"},
            input_str="query",
            run_id=run_id,
            tool_name="web_search",
            args={"q": "x"},
        )


def test_resolve_socket_path_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AA_RUNTIME_SOCKET", "/custom/runtime.sock")
    assert runtime_interceptor._resolve_runtime_socket_path("agent-001") == "/custom/runtime.sock"


def test_resolve_socket_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AA_RUNTIME_SOCKET", raising=False)
    assert runtime_interceptor._resolve_runtime_socket_path("agent-001") == "/tmp/aa-runtime-agent-001.sock"


# ── Live op-control kill switch (AAASM-3491) ──────────────────────────────────


class _FakeOpControl:
    """Minimal OpControlSubscriber stand-in driving await_op behavior.

    ``terminated`` op_ids raise OpTerminatedError; any other op_id records the
    await and returns — modelling the real subscriber once a pause has been
    resumed (it blocks on a threading.Event then returns).
    """

    def __init__(self, *, terminated: set[str] | None = None, paused: set[str] | None = None) -> None:
        self._terminated = terminated or set()
        # `paused` is accepted for call-site readability; the fake models the
        # post-resume return for both paused and non-paused ops.
        self._paused = paused or set()
        self.awaited: list[str] = []

    def await_op(self, op_id: str, **_kwargs: Any) -> None:
        self.awaited.append(op_id)
        if op_id in self._terminated:
            raise OpTerminatedError(f"op {op_id} was terminated by the gateway", op_id=op_id)


def test_terminated_op_denies_before_runtime_query() -> None:
    """A terminate for the call's op halts the tool — and the runtime is never
    even queried (the kill switch short-circuits)."""
    runtime_client = _FakeRuntimeClient("allow")
    op_control = _FakeOpControl(terminated={"trace-1:span-1"})
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), runtime_client, "agent-001", op_control=op_control)

    result = interceptor.check_tool_start(
        serialized={"name": "web_search"},
        input_str="query",
        trace_id="trace-1",
        span_id="span-1",
    )

    assert result["status"] == "deny"
    assert "terminated" in result["reason"]
    assert op_control.awaited == ["trace-1:span-1"]
    # Short-circuited: the runtime query must not have run for a terminated op.
    assert runtime_client.calls == []


def test_paused_op_consults_await_then_proceeds() -> None:
    """A paused op blocks in await_op; once it returns (resume) the tool
    proceeds to the normal runtime allow."""
    runtime_client = _FakeRuntimeClient("allow")
    op_control = _FakeOpControl(paused={"trace-2:span-2"})
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), runtime_client, "agent-001", op_control=op_control)

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i", op_id="trace-2:span-2")

    assert result == {"status": "allow"}
    assert op_control.awaited == ["trace-2:span-2"]
    assert len(runtime_client.calls) == 1


def test_no_op_id_skips_op_control() -> None:
    """Without a trace identity there is no op to address — the subscriber is
    never consulted and the call proceeds normally."""
    op_control = _FakeOpControl(terminated={"trace-x:span-x"})
    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _FakeRuntimeClient("allow"), "agent-001", op_control=op_control
    )

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}
    assert op_control.awaited == []


def test_op_id_composed_from_trace_and_span() -> None:
    assert runtime_interceptor._extract_op_id({"trace_id": "t", "span_id": "s"}) == "t:s"
    assert runtime_interceptor._extract_op_id({"op_id": "explicit"}) == "explicit"
    assert runtime_interceptor._extract_op_id({"trace_id": "t"}) == "t:"
    assert runtime_interceptor._extract_op_id({}) is None


# --- UDS socket-squat guard (AAASM-3920) ------------------------------------


def test_socket_trust_allows_nonexistent_path(tmp_path: Any) -> None:
    """A path the runtime has not yet bound is trusted (it will create it)."""
    missing = str(tmp_path / "aa-runtime-absent.sock")
    assert runtime_interceptor._runtime_socket_is_trusted(missing) is True


@contextmanager
def _bound_unix_socket() -> Any:
    """Yield the path of a bound AF_UNIX socket in a short-lived /tmp dir.

    AF_UNIX paths are length-capped (~104 bytes on macOS), so the deep pytest
    tmp_path cannot host the socket; bind under a short mkdtemp in /tmp instead.
    """
    import shutil
    import socket as _socket
    import tempfile

    directory = tempfile.mkdtemp(dir="/tmp")  # noqa: S108 - short path for AF_UNIX
    path = os.path.join(directory, "s")
    server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    server.bind(path)
    try:
        yield path
    finally:
        server.close()
        shutil.rmtree(directory, ignore_errors=True)


def test_socket_trust_allows_own_socket() -> None:
    """A socket owned by the current user is trusted."""
    with _bound_unix_socket() as path:
        assert runtime_interceptor._runtime_socket_is_trusted(path) is True


def test_socket_trust_rejects_foreign_owned_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """A socket owned by another local user (a squat) is refused — fail closed."""
    with _bound_unix_socket() as path:
        # Pretend the current process is a different uid than the socket owner.
        owner_uid = os.stat(path).st_uid
        monkeypatch.setattr(os, "getuid", lambda: owner_uid + 1)
        assert runtime_interceptor._runtime_socket_is_trusted(path) is False


def test_socket_trust_rejects_non_socket_squat(tmp_path: Any) -> None:
    """A regular file squatting the socket path is refused — fail closed."""
    path = tmp_path / "aa-runtime-regular.sock"
    path.write_text("not a socket")
    assert runtime_interceptor._runtime_socket_is_trusted(str(path)) is False


def test_socket_trust_skips_check_without_getuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Platforms without ``os.getuid`` (e.g. Windows) have no UDS ownership model,
    so the ownership check is skipped and the path is treated as trusted."""
    monkeypatch.delattr(os, "getuid", raising=False)
    assert runtime_interceptor._runtime_socket_is_trusted("/tmp/aa-runtime-any.sock") is True


def test_socket_trust_rejects_unstatable_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that cannot be stat-ed (e.g. permission denied) is refused — fail
    closed rather than assume the socket is safe."""
    monkeypatch.setattr(os, "getuid", lambda: 1000)

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("stat denied")

    monkeypatch.setattr(os, "stat", _raise)
    assert runtime_interceptor._runtime_socket_is_trusted("/tmp/aa-runtime-any.sock") is False


def test_connect_returns_none_for_untrusted_default_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``connect_runtime_client`` fails closed (returns ``None``) when the default
    socket path is squatted, without ever handing the path to the native client."""
    monkeypatch.delenv(runtime_interceptor.ENV_RUNTIME_SOCKET, raising=False)
    monkeypatch.setattr(runtime_interceptor, "_runtime_socket_is_trusted", lambda _path: False)

    class _NeverConnectsRuntimeClient:
        @staticmethod
        def connect(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("connect must not be called for an untrusted socket")

    fake_core = types.ModuleType("agent_assembly._core")
    fake_core.RuntimeClient = _NeverConnectsRuntimeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_assembly._core", fake_core)

    assert runtime_interceptor.connect_runtime_client("agent-001") is None


# --- AAASM-4014: LangChain co-install bypass + fail-closed unknown decision ---


@pytest.mark.parametrize("decision", ["garbage", "maybe", "allowish", ""])
def test_unknown_decision_denies_under_enforce(decision: str) -> None:
    """An unknown / empty native decision is not an authoritative allow, so it
    must fail closed under ``enforce`` (AAASM-4014) rather than default to allow."""
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient(decision), "agent-001", enforce=True)

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result["status"] == "deny"


def test_missing_decision_key_denies_under_enforce() -> None:
    """A ``query_policy`` result with no ``decision`` key denies under enforce."""

    class _EmptyRuntime:
        def query_policy(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
            return {}

    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _EmptyRuntime(), "agent-001", enforce=True)

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result["status"] == "deny"


def test_allow_decision_proceeds_under_enforce() -> None:
    """An authoritative ``allow`` still proceeds under enforce. ``unspecified``
    is not one — it fails closed (AAASM-4166) — and neither is ``redact``
    (AAASM-6352): the SDK cannot apply it, so it is covered by the redact tests."""
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient("allow"), "agent-001", enforce=True)

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


def test_redact_decision_denies_under_enforce() -> None:
    """AAASM-6352: no redacted payload crosses the ABI, so allowing ``redact``
    would run the tool with the original secret-bearing args. Deny, with a reason
    naming the limitation."""
    interceptor = RuntimeQueryInterceptor(_FakeGatewayClient(), _FakeRuntimeClient("redact"), "agent-001", enforce=True)

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result["status"] == "deny"
    assert "cannot apply client-side" in result["reason"]
    assert "AAASM-1920" in result["reason"]


def test_redact_decision_allows_under_observe() -> None:
    """Observe / disabled keep the dry-run fail-open posture for ``redact``."""
    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _FakeRuntimeClient("redact"), "agent-001", enforce=False
    )

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


def test_unspecified_decision_denies_under_enforce() -> None:
    """AAASM-4166: the proto3 zero value ``unspecified`` ("no decision rendered")
    is not an authoritative allow — it must fail closed under enforce (deny),
    matching the Node SDK, rather than being folded onto allow as before."""
    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _FakeRuntimeClient("unspecified"), "agent-001", enforce=True
    )

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result["status"] == "deny"


def test_unspecified_decision_allows_under_observe() -> None:
    """Under observe the ``unspecified`` verdict still proceeds (fail open), like
    any other non-authoritative decision."""
    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _FakeRuntimeClient("unspecified"), "agent-001", enforce=False
    )

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


def test_unknown_decision_allows_under_observe() -> None:
    """Under observe the unknown-decision path still proceeds (fail open)."""
    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _FakeRuntimeClient("garbage"), "agent-001", enforce=False
    )

    result = interceptor.check_tool_start(serialized={"name": "t"}, input_str="i")

    assert result == {"status": "allow"}


def test_langchain_coinstall_denies_through_crewai_adapter() -> None:
    """Reproduce-then-fix AAASM-4014.

    When ``langchain`` is co-installed it registers first and its
    ``AssemblyCallbackHandler`` (wrapping the real interceptor) is threaded to
    every subsequently-registered adapter as the governance interceptor
    (``core/assembly.py``). A non-LangChain adapter (crewai) looks up
    ``check_tool_start`` on that handler. Before the fix the handler exposed no
    such method, so the lookup returned ``None`` and the adapter fell back to
    allow — silently bypassing a runtime DENY under enforce. Delegation must now
    route the check to real governance so the tool is blocked.
    """
    from agent_assembly.adapters.crewai import patch as crewai_patch

    interceptor = RuntimeQueryInterceptor(
        _FakeGatewayClient(), _FakeRuntimeClient("deny", reason="blocked"), "agent-001", enforce=True
    )
    callback_handler = AssemblyCallbackHandler(interceptor)

    # Enforce posture is still detected through the wrapping handler.
    assert crewai_patch._interceptor_enforces(callback_handler) is True

    decision = crewai_patch._invoke_sync_tool_check(
        callback_handler, tool_name="web_search", tool_args={"q": "x"}, agent_id="agent-001"
    )
    status, reason = crewai_patch._normalize_decision(decision, enforce=True)

    assert status == "deny"
    assert reason == "blocked"


def test_missing_interceptor_fallback_denies_under_enforce() -> None:
    """Defense-in-depth: an interceptor genuinely lacking ``check_tool_start``
    denies under enforce instead of the historical silent allow (AAASM-4014)."""
    from agent_assembly.adapters.crewai import patch as crewai_patch

    class _EnforcingNoCheck:
        _enforce = True

    result = crewai_patch._invoke_sync_tool_check(_EnforcingNoCheck(), tool_name="x", tool_args={}, agent_id=None)

    assert result == {"status": "deny", "reason": crewai_patch._MISSING_INTERCEPTOR_REASON}


def test_missing_interceptor_fallback_allows_under_observe() -> None:
    """The missing-interceptor fallback still proceeds when not enforcing."""
    from agent_assembly.adapters.crewai import patch as crewai_patch

    class _NoCheck:
        pass

    result = crewai_patch._invoke_sync_tool_check(_NoCheck(), tool_name="x", tool_args={}, agent_id=None)

    assert result == {"status": "allow"}
