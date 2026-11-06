"""LangChain callback handler module."""

from __future__ import annotations

import importlib
from collections import OrderedDict
from collections.abc import Mapping
from threading import Lock
from typing import Any, Literal, NoReturn, cast
from uuid import UUID

from agent_assembly.adapters._shared.audit_record import record_denied_tool_result
from agent_assembly.core.audit_sink import (
    AUDIT_SINK_ABSENT,
    AUDIT_SINK_DISCARDED,
    AuditSinkDisposition,
    resolve_audit_sink,
)
from agent_assembly.exceptions import ToolExecutionBlockedError

_KNOWN_STATUSES: frozenset[str] = frozenset({"allow", "deny", "pending"})

# run_id -> interceptor that already allowed that tool run, so the process-wide
# configure-hook proxy (configure_hook.py) can skip a run an explicitly passed handler
# already checked *with the active interceptor*, instead of checking it twice. Keyed by
# interceptor so a handler built around a weaker or different one never suppresses the
# active governance. Bounded: entries are consumed by the proxy and the oldest are
# dropped if no proxy ever runs.
_CHECKED_RUNS: OrderedDict[UUID, object] = OrderedDict()
_CHECKED_RUNS_LOCK = Lock()
_CHECKED_RUNS_MAX = 4096


def consume_checked_run(run_id: UUID) -> object | None:
    """Pop and return the interceptor that already allowed ``run_id``, if any."""
    with _CHECKED_RUNS_LOCK:
        return _CHECKED_RUNS.pop(run_id, None)


class _FallbackBaseCallbackHandler:
    """Fallback base type when langchain-core is not installed."""


_CallbackHandlerBase: type[object] = _FallbackBaseCallbackHandler
try:  # pragma: no cover - import availability depends on installed extras.
    callbacks_module = importlib.import_module("langchain_core.callbacks")
    maybe_base = getattr(callbacks_module, "BaseCallbackHandler", _FallbackBaseCallbackHandler)
    if isinstance(maybe_base, type):
        _CallbackHandlerBase = cast(type[object], maybe_base)
except ImportError:  # pragma: no cover - fallback keeps runtime import-safe.
    pass


class AssemblyCallbackHandler(_CallbackHandlerBase):  # type: ignore[valid-type,misc]
    """Callback handler that delegates runtime events to governance interception."""

    # LangChain's CallbackManager LOGS-AND-SWALLOWS an exception raised inside a
    # callback when ``raise_error`` is False (its inherited default), then runs the
    # tool anyway. When a user wires this handler the idiomatic way
    # (``callbacks=[handler]``), that would let a policy DENY be silently bypassed —
    # ``on_tool_start`` raises ``ToolExecutionBlockedError`` but the denied tool still
    # executes, with only a log line as trace (AAASM-4658). Setting this True makes
    # LangChain propagate the block instead of swallowing it, so a DENY aborts the
    # tool call as governance requires.
    raise_error: bool = True

    _UNKNOWN_DECISION_REASON = "Unrecognized governance decision; denied under enforce."
    _MISSING_CHECK_TOOL_START_REASON = "Governance interceptor exposes no check_tool_start; denied under enforce."

    def __init__(self, interceptor: Any) -> None:
        self._interceptor = interceptor

    @property
    def audit_sink(self) -> AuditSinkDisposition:
        """What this handler does with the hook-layer audit record (AAASM-5731).

        Computed rather than declared, because it genuinely depends on what is
        wrapped, and this handler sits on the *other* side of the split from the
        interceptors it wraps. ``on_tool_end`` is defined here, so the adapters'
        audit-hook lookup **does** resolve on this object — the record is built
        and handed over — and it is then forwarded to the interceptor's own
        ``on_tool_end``.

        So the answer is the wrapped interceptor's, with one substitution: when
        the wrapped one resolves no hook at all, the record still reaches *this*
        object before stopping, which is ``discarded`` and not ``absent``. The
        distinction is not cosmetic — ``absent`` says nothing constructs the
        event, and here something does.

        A caller-supplied interceptor that really records is reported as such:
        this SDK does not claim anything about a handler it did not build, in
        either direction.
        """
        wrapped = resolve_audit_sink(self._interceptor)
        return AUDIT_SINK_DISCARDED if wrapped == AUDIT_SINK_ABSENT else wrapped

    def __getattr__(self, name: str) -> Any:
        """Delegate any attribute this handler does not define to the interceptor.

        When ``langchain`` is co-installed it registers first (registry priority
        0) and this handler is threaded to every subsequently-registered adapter
        as the governance interceptor (``core/assembly.py``). Those adapters look
        up governance entry points directly on the handed object — most notably
        ``getattr(handler, "check_tool_start", None)`` — which the LangChain
        callback contract implemented here does not expose. Without delegation
        that lookup returned ``None`` and the adapter fell back to allow, silently
        skipping pre-execution governance even under ``enforce`` (AAASM-4014).

        Forwarding missing attributes to the wrapped real interceptor (the
        ``RuntimeQueryInterceptor`` / ``_FailClosedInterceptor``) routes
        ``check_tool_start``, ``wait_for_tool_approval``, the approval-timeout
        provider, and event reporting to genuine governance. The explicit
        LangChain callback methods defined on this class are found by normal
        attribute lookup and are never delegated, so LangChain's own dispatch is
        unaffected. ``_interceptor`` is guarded to avoid unbounded recursion
        before ``__init__`` assigns it.
        """
        if name == "_interceptor":
            raise AttributeError(name)
        return getattr(self._interceptor, name)

    @property
    def _enforce(self) -> bool:
        """Whether the wired interceptor is in fail-closed ``enforce`` posture.

        The governance interceptor (``RuntimeQueryInterceptor`` /
        ``_FailClosedInterceptor``) carries ``_enforce`` set from
        ``enforcement_mode == "enforce"`` (AAASM-3106). A bare ``GatewayClient``
        — used when no native runtime authority is engaged — lacks it and
        defaults to fail-open. AAASM-3107 reuses this flag so an unknown / ``None``
        / malformed verdict denies under enforce instead of silently allowing.

        Compared strictly against ``True`` so a stub interceptor whose
        ``__getattr__`` synthesizes truthy values for missing attributes is not
        mistaken for the enforce posture; the real flag is always a ``bool``.
        """
        return getattr(self._interceptor, "_enforce", False) is True

    def _unknown_decision(self) -> tuple[Literal["allow", "deny", "pending"], str | None]:
        """Map an unrecognized / malformed verdict, failing closed under enforce.

        Under ``enforce`` an unknown, ``None``, or malformed verdict must not be
        silently allowed (AAASM-3107), so it denies. Under ``observe`` /
        ``disabled`` it proceeds (fail open).
        """
        if self._enforce:
            return "deny", self._UNKNOWN_DECISION_REASON
        return "allow", None

    @staticmethod
    def _coerce_known_status(value: str) -> Literal["allow", "deny", "pending"] | None:
        """Return the verdict literal for a recognized status string, else ``None``."""
        if value in _KNOWN_STATUSES:
            return cast("Literal['allow', 'deny', 'pending']", value)
        return None

    def _normalize_decision(
        self,
        decision: object,
    ) -> tuple[Literal["allow", "deny", "pending"], str | None]:
        if isinstance(decision, str):
            status = self._coerce_known_status(decision.strip().lower())
            if status is not None:
                return status, None
            return self._unknown_decision()

        if isinstance(decision, Mapping):
            reason_value = decision.get("reason")
            reason = str(reason_value) if reason_value is not None else None
            status = self._coerce_known_status(str(decision.get("status", "")).strip().lower())
            if status is not None:
                return status, reason
            unknown_status, unknown_reason = self._unknown_decision()
            return unknown_status, reason if reason is not None else unknown_reason

        return self._unknown_decision()

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        tool_name = self._tool_name(serialized)
        method = getattr(self._interceptor, "check_tool_start", None)
        if not callable(method):
            # Mirrors the other adapters' ``_missing_interceptor_decision``
            # fallback (AAASM-4790): a co-installed adapter can hand this
            # handler an interceptor that exposes no ``check_tool_start``.
            # Silently allowing there skipped pre-execution governance under
            # ``enforce``, so fail closed there and only fail open under
            # observe / disabled, consistent with ``_unknown_decision``.
            if self._enforce:
                self._deny(tool_name, run_id, self._MISSING_CHECK_TOOL_START_REASON)
            return None

        decision = method(
            serialized=serialized,
            input_str=input_str,
            run_id=run_id,
            **kwargs,
        )
        status, reason = self._normalize_decision(decision)
        if status == "deny":
            self._deny(tool_name, run_id, reason or "Tool execution blocked by governance.")
        if status == "pending":
            approval = self._resolve_pending_approval(
                serialized=serialized,
                input_str=input_str,
                run_id=run_id,
                **kwargs,
            )
            approval_status, approval_reason = self._normalize_decision(approval)
            if approval_status != "allow":
                self._deny(
                    tool_name,
                    run_id,
                    approval_reason or reason or "Tool execution was not approved by governance.",
                )

        with _CHECKED_RUNS_LOCK:
            _CHECKED_RUNS[run_id] = self._interceptor
            while len(_CHECKED_RUNS) > _CHECKED_RUNS_MAX:
                _CHECKED_RUNS.popitem(last=False)
        return None

    @staticmethod
    def _tool_name(serialized: dict[str, Any]) -> str:
        """The tool's name as LangChain reports it on the start callback.

        LangChain puts it in ``serialized["name"]``. It is absent for a tool
        constructed without one, and the record is worth more with an empty
        name than not at all, so this does not raise.
        """
        name = serialized.get("name")
        return name if isinstance(name, str) else ""

    def _deny(self, tool_name: str, run_id: UUID, reason: str) -> NoReturn:
        """Record the denial, then block the call by raising.

        AAASM-5787: ``on_tool_start`` raised straight out with nothing recorded,
        and ``on_tool_end`` — the hook AAASM-5750 wired to the sink — fires only
        after a tool has run, so a denied tool never reached it. This adapter
        therefore built no record on any of its three deny paths.

        The record is offered first and the raise is unconditional: the helper
        suppresses a raising audit hook so it cannot replace a decided deny with
        its own exception, and a caller matching on ``ToolExecutionBlockedError``
        keeps recognising the deny.
        """
        record_denied_tool_result(
            self._interceptor,
            tool_name=tool_name,
            result=reason,
            run_id=str(run_id),
        )
        raise ToolExecutionBlockedError(reason)

    def _resolve_pending_approval(
        self,
        *,
        serialized: dict[str, Any],
        input_str: str,
        run_id: UUID,
        **kwargs: Any,
    ) -> object:
        wait_method = getattr(self._interceptor, "wait_for_tool_approval", None)
        if not callable(wait_method):
            return "deny"

        return wait_method(
            serialized=serialized,
            input_str=input_str,
            run_id=run_id,
            **kwargs,
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        method = getattr(self._interceptor, "on_tool_end", None)
        if not callable(method):
            return None

        method(
            output=output,
            run_id=run_id,
            **kwargs,
        )
        return None

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        method = getattr(self._interceptor, "on_llm_start_scan", None)
        if not callable(method):
            return None

        method(
            serialized=serialized,
            prompts=prompts,
            run_id=run_id,
            **kwargs,
        )
        return None

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        method = getattr(self._interceptor, "on_llm_end", None)
        if not callable(method):
            return None

        method(
            response=response,
            run_id=run_id,
            **kwargs,
        )
        return None

    def on_graph_node_start(
        self,
        node_name: str,
        state: Any,
        *,
        agent_id: str | None = None,
        state_keys: list[str] | None = None,
        config: Any = None,
    ) -> None:
        method = getattr(self._interceptor, "on_graph_node_start", None)
        if not callable(method):
            return None
        method(
            node_name=node_name,
            agent_id=agent_id,
            state=state,
            state_keys=state_keys,
            config=config,
        )
        return None

    def on_graph_node_end(
        self,
        node_name: str,
        state: Any,
        result: Any,
        *,
        agent_id: str | None = None,
        state_delta: dict[str, Any] | None = None,
        config: Any = None,
    ) -> None:
        method = getattr(self._interceptor, "on_graph_node_end", None)
        if not callable(method):
            return None
        method(
            node_name=node_name,
            agent_id=agent_id,
            state=state,
            result=result,
            state_delta=state_delta,
            config=config,
        )
        return None
