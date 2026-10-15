"""AAASM-6352: a ``redact`` verdict must not let the tool run under enforce.

The native query returns only ``(decision, reason)``; no redacted payload crosses
the ABI, so a tool run after ``redact`` would receive the ORIGINAL, secret-bearing
arguments. These tests drive the real ``RuntimeQueryInterceptor`` through two
adapter paths that consume ``check_tool_start`` and assert on what the tool
actually receives, each paired with an ``allow`` control on the same flow.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from agent_assembly.adapters._shared.tool_governance import run_governed_async_tool
from agent_assembly.adapters.langchain import AssemblyCallbackHandler
from agent_assembly.core.runtime_interceptor import RuntimeQueryInterceptor
from agent_assembly.exceptions import PolicyViolationError, ToolExecutionBlockedError

_SECRET = {"api_key": "sk-live-SECRET"}


class _Runtime:
    def __init__(self, decision: str) -> None:
        self._decision = decision

    def query_policy(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"decision": self._decision, "reason": "r"}


class _Gateway:
    agent_id = "agent-001"


def _interceptor(decision: str, *, enforce: bool) -> RuntimeQueryInterceptor:
    return RuntimeQueryInterceptor(_Gateway(), _Runtime(decision), "agent-001", enforce=enforce)


def _run_shared(decision: str, *, enforce: bool) -> list[dict[str, Any]]:
    """Run the shared governed-tool flow; return the args each tool invocation received."""
    received: list[dict[str, Any]] = []

    def tool() -> str:
        received.append(dict(_SECRET))
        return "ok"

    asyncio.run(
        run_governed_async_tool(
            _interceptor(decision, enforce=enforce),
            enforce=enforce,
            tool_name="t",
            tool_args=dict(_SECRET),
            agent_id="agent-001",
            run_id="run-1",
            invoke_original=tool,
        )
    )
    return received


def test_shared_path_redact_under_enforce_does_not_invoke_tool() -> None:
    received: list[dict[str, Any]] = []

    def tool() -> str:
        received.append(dict(_SECRET))
        return "ok"

    with pytest.raises(PolicyViolationError):
        asyncio.run(
            run_governed_async_tool(
                _interceptor("redact", enforce=True),
                enforce=True,
                tool_name="t",
                tool_args=dict(_SECRET),
                agent_id="agent-001",
                run_id="run-1",
                invoke_original=tool,
            )
        )

    assert received == []


def test_shared_path_allow_control_invokes_tool_with_args() -> None:
    assert _run_shared("allow", enforce=True) == [_SECRET]


def test_shared_path_redact_under_observe_still_runs_tool() -> None:
    assert _run_shared("redact", enforce=False) == [_SECRET]


def _langchain_start(decision: str, *, enforce: bool) -> None:
    AssemblyCallbackHandler(_interceptor(decision, enforce=enforce)).on_tool_start(
        serialized={"name": "web_search"}, input_str="q", run_id=uuid4()
    )


def test_langchain_redact_under_enforce_blocks_tool_start() -> None:
    with pytest.raises(ToolExecutionBlockedError):
        _langchain_start("redact", enforce=True)


def test_langchain_allow_control_does_not_block() -> None:
    _langchain_start("allow", enforce=True)


def test_langchain_redact_under_observe_does_not_block() -> None:
    _langchain_start("redact", enforce=False)
