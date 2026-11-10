"""``init_assembly()`` alone governs LangChain tool calls (AAASM-6445).

The README Quick Start promises that one ``init_assembly()`` call sends each
LangChain tool call through the pre-execution policy gate. Until AAASM-6445 the
adapter only stored an ``AssemblyCallbackHandler`` singleton and never handed it to
LangChain, so a denied tool still ran unless the app passed the handler itself.

The adapter now registers a process-wide handler through LangChain's public
``register_configure_hook`` extension point. These tests drive the real ``init_assembly``
(fake native runtime as the decision source) and bare ``langchain_core`` tools through
LangChain's own callback manager; the tool body writes a file, so a deny is asserted
as the absence of that file and each deny test has an allow positive control.

Needs the real ``langchain_core`` (``uv sync --group langchain-test``); skipped otherwise.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langchain_core")
from langchain_core.tools import StructuredTool  # noqa: E402

from agent_assembly import init_assembly  # noqa: E402
from agent_assembly.adapters.langchain import AssemblyCallbackHandler, LangChainAdapter  # noqa: E402
from agent_assembly.core import assembly as core_assembly  # noqa: E402
from agent_assembly.exceptions import ToolExecutionBlockedError  # noqa: E402

from .core._fake_core import FakeRuntimeClient, install_fake_core  # noqa: E402
from .negative_control import FileSideEffect  # noqa: E402

_DENY_REASON = "policy forbids disk writes"


@pytest.fixture(autouse=True)
def _cleanup_active_context() -> None:
    active = core_assembly._ACTIVE_CONTEXT
    if active is not None and not active.is_shutdown:
        active.shutdown()
    core_assembly._ACTIVE_CONTEXT = None


@pytest.fixture(autouse=True)
def _langchain_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "langchain", types.ModuleType("langchain"))


@pytest.fixture
def file_effect(tmp_path: Path) -> FileSideEffect:
    return FileSideEffect(path=tmp_path / "langchain-tool-write.txt")


@pytest.fixture
def init(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., tuple[Any, FakeRuntimeClient]]]:
    contexts: list[Any] = []

    def _init(*, decision: str = "allow", enforcement_mode: str = "enforce") -> tuple[Any, FakeRuntimeClient]:
        runtime = FakeRuntimeClient(decision=decision, reason=_DENY_REASON)
        install_fake_core(monkeypatch, runtime)
        monkeypatch.setattr(
            core_assembly,
            "_start_network_layer",
            lambda **_kwargs: ("sdk-only", core_assembly._noop_shutdown),
        )
        context = init_assembly(
            gateway_url="https://gateway.test",
            api_key="test-key",
            agent_id="quickstart-langchain-govern-agent",
            mode="sdk-only",
            enforcement_mode=enforcement_mode,  # type: ignore[arg-type]
        )
        contexts.append(context)
        return context, runtime

    yield _init
    for context in contexts:
        context.shutdown()


def _write_tool(effect: FileSideEffect) -> StructuredTool:
    def write_to_disk(content: str) -> str:
        """Write ``content`` to disk."""
        return effect.write(content)

    return StructuredTool.from_function(write_to_disk, name="write_to_disk", description="Write to disk.")


def test_allowed_tool_executes_with_no_explicit_callbacks(init: Any, file_effect: FileSideEffect) -> None:
    _, runtime = init(decision="allow")

    result = _write_tool(file_effect).invoke({"content": "allowed"})

    assert result == str(file_effect.path)
    assert file_effect.content() == "allowed"
    assert [call[2] for call in runtime.query_calls] == ["write_to_disk"]


def test_denied_tool_never_executes_with_no_explicit_callbacks(init: Any, file_effect: FileSideEffect) -> None:
    _, runtime = init(decision="deny")

    with pytest.raises(ToolExecutionBlockedError, match=_DENY_REASON):
        _write_tool(file_effect).invoke({"content": "denied"})

    assert file_effect.occurred() is False
    assert [call[2] for call in runtime.query_calls] == ["write_to_disk"]


def test_denied_tool_never_executes_in_a_plain_thread(init: Any, file_effect: FileSideEffect) -> None:
    """A plain ``threading.Thread`` does not inherit contextvars: the gate must still hold."""
    init(decision="deny")
    errors: list[Exception] = []

    def _run() -> None:
        try:
            _write_tool(file_effect).invoke({"content": "denied"})
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()

    assert [type(e) for e in errors] == [ToolExecutionBlockedError]
    assert file_effect.occurred() is False


def test_denied_tool_never_executes_on_the_async_path(init: Any, file_effect: FileSideEffect) -> None:
    init(decision="deny")

    with pytest.raises(ToolExecutionBlockedError):
        asyncio.run(_write_tool(file_effect).ainvoke({"content": "denied"}))

    assert file_effect.occurred() is False


def test_explicit_handler_is_not_checked_twice(init: Any, file_effect: FileSideEffect) -> None:
    context, runtime = init(decision="allow")
    adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))

    _write_tool(file_effect).invoke({"content": "x"}, config={"callbacks": [adapter.get_callback_handler()]})

    assert len(runtime.query_calls) == 1


class _AllowAllInterceptor:
    """A weaker interceptor an app might build its own handler around."""

    def check_tool_start(self, **_kwargs: object) -> dict[str, str]:
        return {"status": "allow"}


def test_explicit_handler_with_a_weaker_interceptor_does_not_suppress_governance(
    init: Any, file_effect: FileSideEffect
) -> None:
    init(decision="deny")
    weak = AssemblyCallbackHandler(_AllowAllInterceptor())

    with pytest.raises(ToolExecutionBlockedError, match=_DENY_REASON):
        _write_tool(file_effect).invoke({"content": "x"}, config={"callbacks": [weak]})

    assert file_effect.occurred() is False


def test_denied_call_offers_its_record_to_the_audit_hook(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = init(decision="deny")
    adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))
    interceptor = adapter.get_callback_handler()._interceptor
    records: list[dict[str, Any]] = []

    def _record(*, tool_name: str, result: object, **extra: Any) -> None:
        records.append({"tool_name": tool_name, "result": result, **extra})

    monkeypatch.setattr(interceptor, "record_result", _record, raising=False)

    with pytest.raises(ToolExecutionBlockedError):
        _write_tool(file_effect).invoke({"content": "x"})

    assert len(records) == 1
    assert records[0]["tool_name"] == "write_to_disk"
    assert records[0]["result"] == _DENY_REASON
    assert records[0].get("denied") is True
    assert isinstance(records[0].get("run_id"), str) and records[0]["run_id"]


def test_langgraph_tool_node_is_governed(init: Any, file_effect: FileSideEffect) -> None:
    pytest.importorskip("langgraph.prebuilt")
    from langchain_core.messages import AIMessage
    from langgraph.graph import START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    init(decision="deny")
    call = AIMessage(content="", tool_calls=[{"name": "write_to_disk", "args": {"content": "x"}, "id": "c1"}])

    with pytest.raises(ToolExecutionBlockedError):
        graph = StateGraph(MessagesState)
        graph.add_node("tools", ToolNode([_write_tool(file_effect)]))
        graph.add_edge(START, "tools")
        graph.compile().invoke({"messages": [call]})

    assert file_effect.occurred() is False


def test_runtime_query_failure_under_enforce_blocks_the_tool(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runtime = init(decision="allow")

    def _unreachable(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise ConnectionError("runtime unreachable")

    monkeypatch.setattr(runtime, "query_policy", _unreachable)

    with pytest.raises(ToolExecutionBlockedError, match="runtime query failed"):
        _write_tool(file_effect).invoke({"content": "must not run"})

    assert file_effect.occurred() is False


def test_unexpected_handler_exception_blocks_the_tool(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = init(decision="allow")
    adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))
    handler = adapter.get_callback_handler()

    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("handler bug")

    monkeypatch.setattr(handler._interceptor, "check_tool_start", _boom)

    with pytest.raises(RuntimeError, match="handler bug"):
        _write_tool(file_effect).invoke({"content": "must not run"})

    assert file_effect.occurred() is False


def test_observe_mode_still_checks_but_the_runtime_lets_the_tool_run(init: Any, file_effect: FileSideEffect) -> None:
    """Under ``observe`` the runtime answers allow (dry-run); the SDK adds no extra blocking."""
    _, runtime = init(decision="allow", enforcement_mode="observe")

    _write_tool(file_effect).invoke({"content": "observed"})

    assert file_effect.content() == "observed"
    assert [call[2] for call in runtime.query_calls] == ["write_to_disk"]


def test_shutdown_stops_governing_tools(init: Any, file_effect: FileSideEffect) -> None:
    context, _ = init(decision="deny")
    context.shutdown()

    _write_tool(file_effect).invoke({"content": "after shutdown"})

    assert file_effect.content() == "after shutdown"


def _count_interceptor_calls(interceptor: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Wrap the active interceptor's start check and end hook with call counters."""
    counts = {"starts": 0, "ends": 0}
    real_start = interceptor.check_tool_start
    real_end = getattr(interceptor, "on_tool_end", None)

    def _start(**kwargs: Any) -> Any:
        counts["starts"] += 1
        return real_start(**kwargs)

    def _end(**kwargs: Any) -> Any:
        counts["ends"] += 1
        return real_end(**kwargs) if callable(real_end) else None

    monkeypatch.setattr(interceptor, "check_tool_start", _start)
    monkeypatch.setattr(interceptor, "on_tool_end", _end, raising=False)
    return counts


def test_explicit_handler_on_the_active_interceptor_is_checked_and_ended_once(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = init(decision="allow")
    adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))
    handler = adapter.get_callback_handler()
    counts = _count_interceptor_calls(handler._interceptor, monkeypatch)

    _write_tool(file_effect).invoke({"content": "x"}, config={"callbacks": [handler]})

    assert counts == {"starts": 1, "ends": 1}


def test_proxy_only_path_is_checked_and_ended_once(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = init(decision="allow")
    adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))
    counts = _count_interceptor_calls(adapter.get_callback_handler()._interceptor, monkeypatch)

    _write_tool(file_effect).invoke({"content": "x"})

    assert counts == {"starts": 1, "ends": 1}


def test_handler_subclass_that_swallows_its_own_deny_is_rechecked_by_the_proxy(
    init: Any, file_effect: FileSideEffect
) -> None:
    """A deny must not be recorded as "checked": a raise_error=False subclass swallows the block."""

    class _Swallowing(AssemblyCallbackHandler):
        raise_error = False

    context, _ = init(decision="deny")
    adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))
    swallowing = _Swallowing(adapter.get_callback_handler()._interceptor)

    with pytest.raises(ToolExecutionBlockedError, match=_DENY_REASON):
        _write_tool(file_effect).invoke({"content": "x"}, config={"callbacks": [swallowing]})

    assert file_effect.occurred() is False


def test_checked_run_registry_is_bounded_and_an_evicted_run_is_still_checked(
    init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from agent_assembly.adapters.langchain import callback_handler as ch
    from agent_assembly.adapters.langchain.configure_hook import ProcessGovernanceHandler

    context, _ = init(decision="allow")
    adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))
    handler = adapter.get_callback_handler()
    counts = _count_interceptor_calls(handler._interceptor, monkeypatch)
    ch._CHECKED_RUNS.clear()
    monkeypatch.setattr(ch, "_CHECKED_RUNS_MAX", 3)
    run_ids = [uuid4() for _ in range(5)]

    for run_id in run_ids:
        handler.on_tool_start({"name": "t"}, "", run_id=run_id)

    assert len(ch._CHECKED_RUNS) <= 3
    assert counts["starts"] == 5
    ProcessGovernanceHandler().on_tool_start({"name": "t"}, "", run_id=run_ids[0])  # evicted: re-checked
    assert counts["starts"] == 6
    ProcessGovernanceHandler().on_tool_start({"name": "t"}, "", run_id=run_ids[-1])  # still held: skipped
    assert counts["starts"] == 6


def test_registry_miss_never_equals_an_interceptor_even_none(init: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from uuid import uuid4

    from agent_assembly.adapters.langchain import runtime as lc_runtime
    from agent_assembly.adapters.langchain.callback_handler import CHECKED_RUN_MISS, consume_checked_run
    from agent_assembly.adapters.langchain.configure_hook import ProcessGovernanceHandler

    init(decision="allow")
    assert consume_checked_run(uuid4()) is CHECKED_RUN_MISS
    assert CHECKED_RUN_MISS is not None

    bare = AssemblyCallbackHandler(None)
    seen: list[object] = []
    monkeypatch.setattr(bare, "on_tool_start", lambda *a, **k: seen.append(k["run_id"]))
    monkeypatch.setattr(lc_runtime, "_ACTIVE_CALLBACK_HANDLER", bare)
    run_id = uuid4()

    ProcessGovernanceHandler().on_tool_start({"name": "t"}, "", run_id=run_id)

    assert seen == [run_id]


def test_runtime_failure_during_the_policy_query_blocks_the_tool(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runtime/gateway that cannot answer must not turn into an allow (enforce mode fails closed)."""
    _, runtime = init(decision="allow")

    def _unreachable(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise ConnectionError("runtime socket unreachable")

    monkeypatch.setattr(runtime, "query_policy", _unreachable)

    with pytest.raises(ToolExecutionBlockedError, match="runtime query failed"):
        _write_tool(file_effect).invoke({"content": "must not run"})

    assert file_effect.occurred() is False


def test_registering_the_hook_twice_registers_with_langchain_once(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langchain_core.tracers import context as lc_context

    from agent_assembly.adapters.langchain import configure_hook

    _, runtime = init(decision="allow")
    registered = configure_hook._registered_var
    registrations: list[object] = []
    monkeypatch.setattr(lc_context, "register_configure_hook", lambda *a, **k: registrations.append(a))

    assert configure_hook.ensure_configure_hook_registered() is True
    assert configure_hook.ensure_configure_hook_registered() is True
    _write_tool(file_effect).invoke({"content": "once"})

    assert registered is not None
    assert configure_hook._registered_var is registered
    assert registrations == []
    assert [call[2] for call in runtime.query_calls] == ["write_to_disk"]


def test_missing_langchain_core_leaves_the_hook_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from agent_assembly.adapters.langchain import configure_hook

    monkeypatch.setattr(configure_hook, "_registered_var", None)

    def _no_langchain(name: str, *args: Any, **kwargs: Any) -> Any:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", _no_langchain)

    assert configure_hook.ensure_configure_hook_registered() is False
    assert configure_hook._registered_var is None
