"""LangChain patch orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_assembly.adapters.langchain.callback_handler import AssemblyCallbackHandler
from agent_assembly.adapters.langchain.configure_hook import ensure_configure_hook_registered
from agent_assembly.adapters.langchain.runtime import (
    _reset_runtime_state_for_tests,
    auto_inject_callback_handler,
)
from agent_assembly.adapters.pydantic_ai.patch import set_process_agent_id


@dataclass(slots=True)
class LangChainPatch:
    """Apply/remove LangChain callback handler injection."""

    interceptor: Any
    process_agent_id: str | None = None

    def apply(self) -> bool:
        auto_inject_callback_handler(
            self.interceptor,
            process_agent_id=self.process_agent_id,
        )
        ensure_configure_hook_registered()
        return True

    def revert(self) -> None:
        set_process_agent_id(None)
        _reset_runtime_state_for_tests()
        return None


__all__ = ["LangChainPatch", "AssemblyCallbackHandler"]
