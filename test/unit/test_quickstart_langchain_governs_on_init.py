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
from agent_assembly.adapters.langchain import LangChainAdapter  # noqa: E402
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
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            _write_tool(file_effect).invoke({"content": "denied"})
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
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


def test_runtime_query_failure_under_enforce_blocks_the_tool(
    init: Any, file_effect: FileSideEffect, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, runtime = init(decision="allow")

    def _unreachable(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise ConnectionError("runtime unreachable")

    monkeypatch.setattr(runtime, "query_policy", _unreachable)

    with pytest.raises(Exception):  # noqa: B017, PT011 - any raise is a block; the tool must not run
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
