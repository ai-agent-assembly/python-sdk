"""Process-wide LangChain callback registration through a public extension point.

``langchain_core`` lets a library contribute a callback handler to every run's
configuration with ``register_configure_hook(ContextVar, inheritable=True)``. This is
how LangChain's own tracers are attached, and it is documented API, not patching.

Two properties of the registration matter for governance:

* The ``ContextVar`` carries a **default** (a process-wide proxy handler) instead of
  being ``set()`` by ``init_assembly``. A value ``set()`` in one context is invisible to
  a plain ``threading.Thread`` (threads start with an empty context), so a tool run
  there would silently skip the gate. A default is seen from every thread and task.
* The proxy skips a run only when an explicitly passed ``AssemblyCallbackHandler`` already
  allowed it with the *active* interceptor, so a tool is not checked twice. A handler built
  around a weaker or different interceptor never suppresses the active governance
  (registering with ``handle_class`` would have: LangChain then skips the proxy for any
  ``AssemblyCallbackHandler`` instance).

The hook cannot be unregistered, so the proxy resolves the active handler on every
call and does nothing once ``init_assembly``'s context is shut down.
"""

from __future__ import annotations

import importlib
from contextvars import ContextVar
from threading import Lock
from typing import Any

from agent_assembly.adapters.langchain.callback_handler import AssemblyCallbackHandler, consume_checked_run
from agent_assembly.adapters.langchain.runtime import get_active_callback_handler


class ProcessGovernanceHandler(AssemblyCallbackHandler):
    """Forwards LangChain tool callbacks to the currently active governance handler.

    ``raise_error`` stays True (inherited): LangChain swallows a callback's exception
    otherwise, so any failure here, including a runtime/gateway error raised by the
    interceptor, aborts the tool call instead of letting it run ungoverned.
    """

    def __init__(self) -> None:
        super().__init__(interceptor=None)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        active = get_active_callback_handler()
        if active is None:
            return
        run_id = kwargs.get("run_id")
        if run_id is not None and consume_checked_run(run_id) is active._interceptor:
            return
        active.on_tool_start(serialized, input_str, **kwargs)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        active = get_active_callback_handler()
        if active is not None:
            active.on_tool_end(output, **kwargs)


_REGISTER_LOCK = Lock()
_registered_var: ContextVar[Any] | None = None


def ensure_configure_hook_registered() -> bool:
    """Register the process-wide governance handler with LangChain once.

    Returns ``True`` when the hook is in place and ``False`` when ``langchain_core``
    is not installed (nothing to govern). Safe to call on every ``init_assembly``.
    """
    global _registered_var

    with _REGISTER_LOCK:
        if _registered_var is not None:
            return True
        try:
            context = importlib.import_module("langchain_core.tracers.context")
        except ImportError:
            return False
        # B039: the default is deliberately a shared mutable handler; a per-context
        # ``set()`` would not reach plain threads (see module docstring).
        var: ContextVar[Any] = ContextVar(
            "agent_assembly_langchain_governance",
            default=ProcessGovernanceHandler(),  # noqa: B039
        )
        context.register_configure_hook(var, True)
        _registered_var = var
        return True
