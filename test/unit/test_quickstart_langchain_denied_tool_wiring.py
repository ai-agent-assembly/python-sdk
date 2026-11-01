"""A denied LangChain tool has no effect only when the callback is wired (AAASM-6415).

The README Quick Start used to say that ``init_assembly()`` alone sends "each tool
call the wrapped agent makes from now on" through the policy gate. For LangChain
that is false: ``init_assembly()`` registers the adapter and stores one
``AssemblyCallbackHandler`` (``get_callback_handler()``), but it installs no hook
into LangChain itself. LangChain only calls a handler it is handed through
``callbacks=[...]``, so an un-wired tool runs whatever the policy says. The
corrected Quick Start therefore passes the handler explicitly.

What runs here: the real ``init_assembly`` (real ``RuntimeQueryInterceptor``, fake
native runtime as the decision source, as in ``test_quickstart_negative_control.py``),
the handler taken from the langchain adapter on the returned context, and a bare
``langchain_core`` ``StructuredTool`` invoked through LangChain's own callback
manager. The tool body writes a file, so a deny is asserted as the absence of that
file. This proves the handler-wiring mechanism the Quick Start relies on; it does
NOT run ``AgentExecutor``/``create_react_agent``, so propagation of
``config={"callbacks": [...]}`` through an agent executor is not verified here.

* ``test_denied_tool_without_wiring_still_executes`` is the negative control and
  documents the gap: same init, same deny, handler not passed, file written.
* ``test_denied_tool_with_wiring_has_no_side_effect`` is the claim.
* ``test_allowed_tool_with_wiring_executes`` is the positive control; without it
  "no file" is equally explained by the tool never running at all.

``langchain`` itself is not a dependency of the test environment, and the adapter
is only selected when ``import langchain`` succeeds (``FrameworkAdapter.is_available``),
so a bare module is placed in ``sys.modules`` to make that probe pass. Only the
probe is satisfied; the adapter, handler and tool-invocation path are the real ones.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
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

_AGENT_ID = "quickstart-langchain-wiring-agent"
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


def _quickstart(monkeypatch: pytest.MonkeyPatch, *, decision: str) -> Iterator[tuple[Any, Any]]:
    install_fake_core(monkeypatch, FakeRuntimeClient(decision=decision, reason=_DENY_REASON))
    monkeypatch.setattr(
        core_assembly,
        "_start_network_layer",
        lambda **_kwargs: ("sdk-only", core_assembly._noop_shutdown),
    )
    context = init_assembly(
        gateway_url="https://gateway.test",
        api_key="test-key",
        agent_id=_AGENT_ID,
        mode="sdk-only",
        enforcement_mode="enforce",
    )
    try:
        adapter = next(a for a in context.adapters if isinstance(a, LangChainAdapter))
        yield context, adapter.get_callback_handler()
    finally:
        context.shutdown()


@pytest.fixture
def denied(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Any]]:
    yield from _quickstart(monkeypatch, decision="deny")


@pytest.fixture
def allowed(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Any]]:
    yield from _quickstart(monkeypatch, decision="allow")


def _write_tool(effect: FileSideEffect) -> StructuredTool:
    def write_to_disk(content: str) -> str:
        """Write ``content`` to disk."""
        return effect.write(content)

    return StructuredTool.from_function(write_to_disk, name="write_to_disk", description="Write to disk.")


def test_denied_tool_without_wiring_still_executes(denied: tuple[Any, Any], file_effect: FileSideEffect) -> None:
    """init_assembly() alone does not gate a LangChain tool: the gap the old README hid."""
    _, handler = denied
    assert handler is not None

    result = _write_tool(file_effect).invoke({"content": "ungated"})

    assert file_effect.occurred() is True
    assert file_effect.content() == "ungated"
    assert result == str(file_effect.path)


def test_denied_tool_with_wiring_has_no_side_effect(denied: tuple[Any, Any], file_effect: FileSideEffect) -> None:
    _, handler = denied

    with pytest.raises(ToolExecutionBlockedError, match=_DENY_REASON):
        _write_tool(file_effect).invoke({"content": "denied"}, config={"callbacks": [handler]})

    assert file_effect.occurred() is False
    assert file_effect.content() is None


def test_allowed_tool_with_wiring_executes(allowed: tuple[Any, Any], file_effect: FileSideEffect) -> None:
    _, handler = allowed

    result = _write_tool(file_effect).invoke({"content": "allowed"}, config={"callbacks": [handler]})

    assert result == str(file_effect.path)
    assert file_effect.content() == "allowed"
