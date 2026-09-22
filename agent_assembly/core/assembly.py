"""Core assembly initialization module."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal, Protocol

from agent_assembly.adapters.base import FrameworkAdapter
from agent_assembly.adapters.langchain.adapter import LangChainAdapter
from agent_assembly.adapters.langchain.runtime import get_active_callback_handler
from agent_assembly.adapters.registry import AdapterRegistry
from agent_assembly.client.gateway import GatewayClient
from agent_assembly.core.audit_sink import (
    AUDIT_SINK_ABSENT,
    AUDIT_SINK_CALLER_SUPPLIED,
    AUDIT_SINK_DISCARDED,
    AuditSinkDisposition,
    resolve_audit_sink,
)
from agent_assembly.core.gateway_resolver import (
    resolve_api_key,
    resolve_gateway_grpc_endpoint,
    resolve_gateway_url,
)
from agent_assembly.core.runtime_interceptor import (
    ENFORCE_MODE,
    _local_posture_is_enforce,
    _native_core_available,
    build_governance_interceptor,
    connect_runtime_client,
    register_agent,
)
from agent_assembly.core.spawn import _SPAWN_CTX
from agent_assembly.core.transport_security import warn_if_insecure_http_url
from agent_assembly.exceptions import AssemblyError, ConfigurationError

RuntimeMode = Literal["auto", "ebpf", "proxy", "sdk-only"]
NetworkMode = Literal["ebpf", "proxy", "sdk-only"]

EnforcementMode = Literal["enforce", "observe", "disabled"]
"""Posture the governance gateway should apply to this agent's actions.

* ``"enforce"`` — default; deny blocks the action, redact strips secrets.
* ``"observe"`` — dry-run; the gateway records what *would* have happened
  but lets every action through. Surfaced by ``aa audit list --dry-run-only``.
* ``"disabled"`` — policy evaluation skipped entirely. Hermetic test only.

Mirrors ``aa_core::EnforcementMode`` on the wire; uses the same snake_case
tokens the gateway expects in the registration body."""

ENFORCEMENT_MODES: tuple[EnforcementMode, ...] = ("enforce", "observe", "disabled")
"""Canonical, ordered enforcement-mode tokens — the public parity surface.

Single source of truth for the enforcement-mode set: the private
``_VALID_ENFORCEMENT_MODES`` membership validator is derived from this tuple
(``frozenset(ENFORCEMENT_MODES)``) so the two can never drift. Ordering is
significant — the cross-SDK conformance cross-check (AAASM-4856) asserts the
Python/Node/Go SDKs each expose this exact sequence, matching the canonical
``aa_core::EnforcementMode`` order on the wire."""

ENV_GATEWAY_URL = "AA_GATEWAY_URL"
ENV_CONTROL_PLANE_URL = "AA_CONTROL_PLANE_URL"

_DEFAULT_AGENT_ID = "agent-assembly-default"
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_VALID_RUNTIME_MODES = {"auto", "ebpf", "proxy", "sdk-only"}
_VALID_ENFORCEMENT_MODES: frozenset[EnforcementMode] = frozenset(ENFORCEMENT_MODES)
_INIT_LOCK = Lock()
_ACTIVE_CONTEXT: AssemblyContext | None = None


def _validate_agent_id(agent_id: str) -> str:
    """Validate agent_id format before it reaches socket-path interpolation (AAASM-4301).

    Only allows [A-Za-z0-9_.-] up to 128 chars. Reject anything else with a clear ValueError
    so callers get a fast, actionable error instead of a downstream FileNotFoundError from
    a malformed socket path.
    """
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise ValueError(f"invalid agent_id: {agent_id!r}; must match {_AGENT_ID_RE.pattern}")
    return agent_id


class RuntimePatch(Protocol):
    """Internal monkey-patch mechanism used by framework adapters.

    This is the **internal mechanism layer** — not intended for SDK users
    or plugin authors.  Each ``RuntimePatch`` knows how to apply and
    revert a single monkey-patch on a specific framework class or function.

    A ``FrameworkAdapter``'s ``register_hooks()`` creates one or more
    ``RuntimePatch`` instances and calls ``apply()`` on each.  The
    adapter's ``unregister_hooks()`` calls ``revert()`` on each in
    reverse order.

    See Also:
        ``FrameworkAdapter`` in ``adapters/base.py`` — the public adapter
        API with ``register_hooks()`` / ``unregister_hooks()`` methods.
        ADR-0001 (``docs/adr/0001-hook-architecture.md``).
    """

    def apply(self) -> bool: ...

    def revert(self) -> None: ...


@dataclass(slots=True)
class AssemblyContext:
    """Represents an active assembly runtime session."""

    client: GatewayClient
    adapters: list[FrameworkAdapter]
    network_mode: NetworkMode
    _network_shutdown: Callable[[], None]
    # Whether this SDK actually registered the agent with the governance gateway
    # during init. False means the agent will NOT appear in the dashboard /
    # ``GET /api/v1/agents`` — the native extension was absent or registration
    # failed. Surfaced so callers can detect the unregistered state
    # programmatically rather than relying on the stderr warning alone
    # (AAASM-4547, mirroring the Node SDK's ``ctx.registered``).
    registered: bool = True
    # What the governance interceptor the adapters were handed does with the
    # hook-layer audit record for a governed tool call (AAASM-5731).
    # ``"forwarded"`` means the record is handed to the runtime's event channel
    # (a handoff, not evidence — the send is unacknowledged);
    # ``"absent"`` and ``"discarded"`` both mean governed actions produce NO
    # audit evidence from this SDK, so no claim of attributability or
    # after-the-fact review holds on that path; ``"caller-supplied"`` means this
    # SDK makes no claim. The programmatic counterpart of the stderr warning
    # ``_warn_audit_not_recorded`` emits for the two evidence-free values, so
    # which case a run is in is detectable in code and not only by reading
    # stderr.
    audit_sink: AuditSinkDisposition = AUDIT_SINK_ABSENT
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _is_shutdown: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> AssemblyContext:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
        del exc_type, exc, tb
        self.shutdown()
        return False

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._is_shutdown

    def shutdown(self) -> None:
        shutdown_errors: list[str] = []
        should_clear_active_context = False
        with self._lock:
            if self._is_shutdown:
                return None

            try:
                self._network_shutdown()
            except Exception as error:  # pragma: no cover - defensive guard
                shutdown_errors.append(f"network shutdown failed: {error}")

            for adapter in reversed(self.adapters):
                try:
                    adapter.unregister_hooks()
                except Exception as error:  # pragma: no cover - defensive guard
                    shutdown_errors.append(f"adapter hook removal failed: {error}")

            try:
                self.client.close()
            except Exception as error:  # pragma: no cover - defensive guard
                shutdown_errors.append(f"client close failed: {error}")

            self._is_shutdown = True
            should_clear_active_context = True

        if should_clear_active_context:
            _clear_active_context(self)
        if shutdown_errors:
            raise AssemblyError("; ".join(shutdown_errors))


def init_assembly(
    gateway_url: str | None = None,
    api_key: str | None = None,
    agent_id: str | None = None,
    mode: RuntimeMode = "auto",
    *,
    control_plane_url: str | None = None,
    parent_agent_id: str | None = None,
    team_id: str | None = None,
    delegation_reason: str | None = None,
    spawned_by_tool: str | None = None,
    depth: int | None = None,
    enforcement_mode: EnforcementMode | None = None,
    allow_insecure: bool = False,
) -> AssemblyContext:
    """Initialize the Agent Assembly SDK runtime for this process.

    Uses ``AdapterRegistry.get_available_adapters_by_priority()`` as the
    single detection path for framework adapters (see ADR-0001).

    With no ``gateway_url`` / ``api_key`` arguments the SDK falls back
    through the resolver chain (env → config file → local default with
    optional auto-start) per Epic 17 S-G — see
    ``agent_assembly.core.gateway_resolver``.

    Agent registration and the pre-execution policy check go through the native
    ``aa-sdk-client`` shim to the core over gRPC/UDS (ADR 0004): ``init_assembly``
    registers the agent on startup (storing the issued credential token on the
    native client) and a tool call is checked via the native ``query_policy`` so
    a ``deny`` blocks the tool before it runs. The SDK never calls a core HTTP
    endpoint directly for registration or policy checks.

    :param mode: Interception layer to activate (see :data:`RuntimeMode`).
        ``"sdk-only"`` is the in-process-only layer that starts no network
        sidecar, so it is also the one mode where a gateway that cannot be
        registered with only **warns** instead of failing init (AAASM-6155) —
        unless ``enforcement_mode="enforce"`` is passed explicitly, which asks
        for the fail-closed posture by name and aborts in every mode. Governed
        tool calls are unaffected either way: the interceptor still denies under
        enforce when no authoritative decision is available.
    :param control_plane_url: Optional URL of the control-plane HTTP API. When
        supplied, the SDK issues its remaining HTTP routes (topology edges,
        secret dispatch) against it instead of ``gateway_url``. When omitted it
        falls back to ``gateway_url`` — the backwards-compatible single-host OSS
        dev setup. Resolution order: explicit kwarg > ``AA_CONTROL_PLANE_URL``
        env-var > unset (falls back to ``gateway_url``).
    :param enforcement_mode: Per-agent governance posture applied to this
        agent's actions (see :data:`EnforcementMode`). Defaults to ``None``,
        which lets the gateway apply its server-side default (live ``enforce``).
        Because that default is ``enforce``, the SDK's *local* pre-execution
        fast path also takes the fail-closed posture under ``None`` (AAASM-4130):
        with the native runtime present an unreachable socket or an
        unauthoritative ``query_policy`` **denies** rather than silently
        proceeding, and on a pure-Python install (native extension absent) a loud
        one-time warning is emitted because no in-process deny can run — the
        gateway / proxy / eBPF layers remain authoritative, so init stays graceful
        and never hard-fails on a missing runtime. Pass ``"observe"`` to register
        the agent in dry-run / sandbox mode: every action proceeds (local checks
        fail open) and the gateway records would-be violations as shadow audit
        events.
    :param allow_insecure: Opt into a plaintext (non-TLS) native ``register``
        channel when the resolved gateway host is non-loopback (AAASM-4664).
        Defaults to ``False`` — secure-by-default (AAASM-4655): a derived
        plaintext ``http://`` register endpoint to a non-loopback host is
        refused, since the ``Register`` call carries the agent identity and must
        not travel unencrypted to a remote host. Set to ``True`` only on a
        trusted network (loopback dev / private link), mirroring op-control's
        ``connect(allow_insecure=...)`` semantics. Loopback and ``https://``
        targets always pass regardless of this flag.
    """
    gateway_url = resolve_gateway_url(gateway_url)
    api_key = resolve_api_key(api_key)
    # Warn early when the resolved gateway would carry the Bearer API key over
    # plaintext http:// to a non-loopback host (AAASM-3725). The control-plane
    # URL is the host the credential is actually sent to when set.
    warn_if_insecure_http_url(control_plane_url or gateway_url, has_api_key=bool(api_key))
    gateway_url, control_plane_url = _validate_inputs(
        gateway_url=gateway_url,
        mode=mode,
        control_plane_url=control_plane_url,
        enforcement_mode=enforcement_mode,
    )
    if delegation_reason is not None and len(delegation_reason) > 256:
        raise ValueError("delegation_reason must be <= 256 characters")

    # Auto-fill lineage from ambient spawn context when not passed explicitly.
    _spawn = _SPAWN_CTX.get()
    if _spawn is not None:
        if parent_agent_id is None:
            parent_agent_id = _spawn.parent_agent_id
        if depth is None:
            depth = _spawn.depth
        if spawned_by_tool is None:
            spawned_by_tool = _spawn.spawned_by_tool

    resolved_agent_id = _validate_agent_id(agent_id or _DEFAULT_AGENT_ID)

    global _ACTIVE_CONTEXT
    with _INIT_LOCK:
        if _ACTIVE_CONTEXT is not None and not _ACTIVE_CONTEXT.is_shutdown:
            _validate_active_context_compatibility(
                _ACTIVE_CONTEXT,
                gateway_url=gateway_url,
                api_key=api_key,
                agent_id=resolved_agent_id,
            )
            return _ACTIVE_CONTEXT

        client = GatewayClient(
            gateway_url=gateway_url,
            agent_id=resolved_agent_id,
            api_key=api_key,
            control_plane_url=control_plane_url,
            parent_agent_id=parent_agent_id,
            team_id=team_id,
            delegation_reason=delegation_reason,
            spawned_by_tool=spawned_by_tool,
            depth=depth,
            enforcement_mode=enforcement_mode,
        )

        registered_adapters: list[FrameworkAdapter] = []
        network_mode: NetworkMode = "sdk-only"
        network_shutdown: Callable[[], None] = _noop_shutdown
        registered = False
        audit_sink: AuditSinkDisposition = AUDIT_SINK_ABSENT
        try:
            native_available = _native_core_available()
            runtime_client = connect_runtime_client(resolved_agent_id) if native_available else None
            registered = _register_agent_with_gateway(
                runtime_client=runtime_client,
                agent_id=resolved_agent_id,
                enforcement_mode=enforcement_mode,
                mode=mode,
                gateway_endpoint=resolve_gateway_grpc_endpoint(gateway_url, allow_insecure=allow_insecure),
                native_available=native_available,
                team_id=team_id,
                parent_agent_id=parent_agent_id,
            )
            registered_adapters, audit_sink = _register_adapters(
                client=client,
                process_agent_id=resolved_agent_id,
                enforcement_mode=enforcement_mode,
                runtime_client=runtime_client,
                native_available=native_available,
            )
            network_mode, network_shutdown = _start_network_layer(client=client, mode=mode)
        except Exception as error:
            _unregister_adapters(registered_adapters)
            client.close()
            raise ConfigurationError(f"Failed to initialize assembly runtime: {error}") from error

        # AAASM-5731 — surface an audit path that retains nothing, on the
        # default path with nothing opted into. Emitted after registration so it
        # reflects the interceptor the adapters were actually handed.
        #
        # The condition enumerates the dispositions that warrant a warning rather
        # than excluding the one that does not (AAASM-5750). Written as
        # ``!= AUDIT_SINK_CALLER_SUPPLIED`` it warned about every value that was
        # not the caller's own, which silently included ``forwarded`` the moment
        # that value existed — telling a caller whose records do reach the runtime
        # that they produce none.
        if audit_sink in (AUDIT_SINK_ABSENT, AUDIT_SINK_DISCARDED):
            _warn_audit_not_recorded(audit_sink)

        context = AssemblyContext(
            client=client,
            adapters=registered_adapters,
            network_mode=network_mode,
            _network_shutdown=network_shutdown,
            registered=registered,
            audit_sink=audit_sink,
        )
        _ACTIVE_CONTEXT = context
        return context


def _validate_inputs(
    *,
    gateway_url: str,
    mode: RuntimeMode,
    control_plane_url: str | None = None,
    enforcement_mode: EnforcementMode | None = None,
) -> tuple[str, str | None]:
    """Validate inputs and apply env-var fallbacks.

    Resolution order for each URL is explicit kwarg > env-var > unset:
    ``gateway_url`` falls back to ``AA_GATEWAY_URL`` and ``control_plane_url``
    falls back to ``AA_CONTROL_PLANE_URL``. Returns the resolved
    ``(gateway_url, control_plane_url)`` pair.
    """
    if not gateway_url:
        gateway_url = os.environ.get(ENV_GATEWAY_URL, "")
    if control_plane_url is None:
        control_plane_url = os.environ.get(ENV_CONTROL_PLANE_URL) or None

    if not gateway_url:
        raise ConfigurationError("gateway_url is required")
    if mode not in _VALID_RUNTIME_MODES:
        raise ConfigurationError("mode must be one of: auto, ebpf, proxy, sdk-only")
    if enforcement_mode is not None and enforcement_mode not in _VALID_ENFORCEMENT_MODES:
        raise ConfigurationError(
            f"enforcement_mode must be one of: enforce, observe, disabled (got: {enforcement_mode!r})"
        )
    return gateway_url, control_plane_url


def _warn_agent_unregistered(detail: str) -> None:
    """Emit a loud, unconditional stderr warning that the agent is unregistered.

    Registration is what makes an agent visible to the gateway — it appears in
    the dashboard / ``GET /api/v1/agents`` and gets policy/budget tracking. When
    registration cannot happen (the native ``agent_assembly._core`` extension is
    absent on a pure-Python / unsupported-platform install, or the gateway gRPC
    endpoint is unreachable), a governance SDK must not report a clean init for an
    agent that never registered. Previously this failure was silent (the
    AAASM-4446 shape); now it mirrors the Node SDK's ``warnAgentUnregistered``
    (AAASM-4468): written straight to ``sys.stderr`` so ``logging`` configuration
    cannot silence it, once per ``init_assembly`` call (AAASM-4547).

    :param detail: A short clause explaining *why* registration did not happen,
        interpolated into the warning (must not contain credentials).
    """
    sys.stderr.write(
        "[agent-assembly] WARNING: the agent is NOT registered with the "
        f"governance gateway ({detail}). It will NOT appear in the dashboard or "
        "GET /api/v1/agents, and the gateway will not track its policy or budget "
        "state; the proxy / eBPF layers remain authoritative. Inspect the "
        "'registered' attribute on the returned assembly context to detect this "
        "programmatically (AAASM-4547).\n"
    )


def _warn_audit_not_recorded(disposition: AuditSinkDisposition) -> None:
    """Emit a loud stderr warning that no audit record is kept on this run.

    The framework adapters offer the outcome of every governed tool call to an
    audit hook on the interceptor they were handed. Over a connected runtime that
    hook resolves and forwards the record (AAASM-5750); without one it does not
    resolve at all, so nothing is emitted — for **allowed** calls as much as
    denied ones — and the caller had no way to learn that short of reading the
    interceptor. Enforcement is genuinely unaffected, which is exactly why the gap
    is easy to miss: denies still deny, and the governed call returns normally.

    Fires only for the dispositions that leave no evidence; see the enumeration at
    the call site in :func:`init_assembly`.

    Written straight to ``sys.stderr`` for the same reason as
    :func:`_warn_agent_unregistered`: ``logging`` configuration cannot silence it.
    Once per ``init_assembly`` rather than per governed call, so it cannot become
    steady-state noise. It does not fail init — a caller may not need SDK-side
    audit at all, and the proxy / eBPF layers are unaffected, so refusing to start
    over an evidence gap would trade a truthfulness fix for an availability
    regression.

    :param disposition: The resolved sink disposition. The mechanism clause MUST
        branch on it: ``"absent"`` and ``"discarded"`` fail differently and need
        different remedies, and a single unconditional sentence would be wrong in
        one direction or the other.
    """
    mechanism = (
        "the LangChain callback handler accepts the record and drops it, because "
        "the interceptor it forwards to exposes no on_tool_end"
        if disposition == AUDIT_SINK_DISCARDED
        else "no audit hook (record_result / on_tool_end) resolves on the governance "
        "interceptor this SDK built, because no runtime is reachable for it to "
        "send a record to, so no record is even attempted"
    )
    sys.stderr.write(
        "[agent-assembly] WARNING: hook-layer audit records are NOT retained "
        f"(audit sink '{disposition}'): {mechanism}. Governed tool calls — ALLOWED "
        "ones as well as denied ones — therefore produce NO audit evidence from "
        "this SDK, and nothing on this path can be attributed or reviewed after "
        "the fact. Enforcement is unaffected: a policy DENY still blocks a tool "
        "call, and the proxy / eBPF layers remain authoritative. Connect a runtime so "
        "the SDK's own sink resolves, or supply your own handler with a "
        "record_result or on_tool_end, and inspect the 'audit_sink' attribute on "
        "the returned assembly context to detect this programmatically "
        "(AAASM-5731, AAASM-5750).\n"
    )


def _register_failure_is_fatal(*, mode: RuntimeMode, enforcement_mode: EnforcementMode | None) -> bool:
    """Whether a failed ``register`` must abort init rather than warn (AAASM-6155).

    Two rules, in order:

    * An **explicit** ``enforce`` always aborts, in every ``mode``. The caller
      asked for the fail-closed posture by name, so a gateway it cannot register
      with is a misconfiguration and init must not come up.
    * Otherwise ``mode="sdk-only"`` warns and continues. That mode is documented
      as the in-process-only layer that starts no network sidecar and is "the best
      choice for deterministic, offline examples and tests"; the quick-start
      states the offline path *warns* that the agent is unregistered. Requiring a
      reachable gateway there contradicts the one mode whose purpose is to run
      without one.

    Every other ``mode`` keeps the :func:`_local_posture_is_enforce` posture, so an
    unset ``enforcement_mode`` of ``None`` still aborts (AAASM-4130): ``None``
    registers under the gateway's server-side default of live ``enforce``, and
    ``auto`` / ``proxy`` / ``ebpf`` all do bring up an interception layer that the
    gateway is expected to back.

    Relaxing init here does not make an ``sdk-only`` session run ungoverned. The
    registration warning is unconditional (see :func:`_warn_agent_unregistered`),
    ``AssemblyContext.registered`` reports ``False``, and the interceptor the
    adapters are handed keeps its own fail-closed posture under enforce — an
    unreachable runtime or an unauthoritative ``query_policy`` still **denies** the
    tool call (AAASM-3106, AAASM-4760). What changes is only whether ``init_assembly``
    raises instead of warning.
    """
    if enforcement_mode == ENFORCE_MODE:
        return True
    if mode == "sdk-only":
        return False
    return _local_posture_is_enforce(enforcement_mode)


def _register_agent_with_gateway(
    *,
    runtime_client: Any | None,
    agent_id: str,
    enforcement_mode: EnforcementMode | None,
    mode: RuntimeMode,
    gateway_endpoint: str,
    native_available: bool,
    team_id: str | None = None,
    parent_agent_id: str | None = None,
) -> bool:
    """Register the agent with the gateway over the native gRPC ``register``.

    Registration goes through the native runtime client (AAASM-3399) so the
    issued credential token is stored on the same client the
    ``RuntimeQueryInterceptor`` later uses for ``query_policy`` — the SDK never
    calls a core HTTP endpoint directly (ADR 0004).

    ``gateway_endpoint`` is the gateway's **gRPC** endpoint (:50051) the direct
    register call dials — distinct from the REST ``gateway_url`` (:7391) the HTTP
    client uses (AAASM-4547). It is resolved by
    :func:`~agent_assembly.core.gateway_resolver.resolve_gateway_grpc_endpoint`.

    ``team_id`` and ``parent_agent_id`` are forwarded to the native register so
    the gateway gets the agent's team-budget scoping and topology lineage on the
    native path, restoring what the legacy REST register sent (AAASM-3415).

    Returns whether the agent was registered. When there is no native runtime
    client to register through (``native_available`` False → extension missing on
    a pure-Python install; or True but the socket path was distrusted), or when
    ``register`` raises, the failure is no longer silent: a loud
    :func:`_warn_agent_unregistered` fires and ``False`` is returned (AAASM-4547).
    Init still proceeds so the proxy / eBPF layers stay authoritative — except
    where :func:`_register_failure_is_fatal` says the failure must abort, which is
    an explicit ``enforce`` in any mode, or the ``None`` default in any mode other
    than ``sdk-only`` (AAASM-6155).
    """
    if runtime_client is None:
        if native_available:
            detail = "the native runtime client could not be established"
        else:
            detail = (
                "the native agent_assembly._core extension is not installed, so this "
                "build cannot perform in-SDK gateway registration"
            )
        _warn_agent_unregistered(detail)
        return False
    framework = "python"
    try:
        register_agent(
            runtime_client,
            agent_id,
            framework,
            gateway_endpoint=gateway_endpoint,
            team_id=team_id,
            parent_agent_id=parent_agent_id,
        )
    except Exception as error:
        if _register_failure_is_fatal(mode=mode, enforcement_mode=enforcement_mode):
            raise
        _warn_agent_unregistered(f"registration failed: {error}")
        return False
    return True


def _warn_adapter_registration_failed(adapter: FrameworkAdapter, error: Exception) -> None:
    """Emit a loud, unconditional stderr warning that a framework adapter failed to attach.

    ``register_hooks`` failing was previously swallowed with a bare ``continue``
    (AAASM-4790), leaving a co-installed framework running fully ungoverned with
    no trace. Mirrors :func:`_warn_agent_unregistered`'s unconditional
    ``sys.stderr.write`` so ``logging`` configuration cannot silence it. Init
    still proceeds — other adapters may register fine — matching the existing
    fail-open-and-continue policy in :func:`_register_adapters`.
    """
    sys.stderr.write(
        "[agent-assembly] WARNING: framework adapter "
        f"{adapter.get_framework_name()!r} failed to register governance hooks "
        f"({error}); this framework will run UNGOVERNED by the SDK layer. The "
        "proxy / eBPF layers remain authoritative.\n"
    )


def _register_adapters(
    client: GatewayClient,
    process_agent_id: str,
    enforcement_mode: EnforcementMode | None = None,
    runtime_client: Any | None = None,
    native_available: bool = False,
) -> tuple[list[FrameworkAdapter], AuditSinkDisposition]:
    """Detect available frameworks via AdapterRegistry and register hooks.

    Adapters are returned in priority order.  LangChain is registered first
    so its ``AssemblyCallbackHandler`` can thread through to subsequent
    adapters as the governance interceptor.

    When the native runtime is reachable, the bare ``GatewayClient`` is wrapped
    in a ``RuntimeQueryInterceptor`` so a runtime ``deny`` blocks the tool via
    ``check_tool_start``. ``enforcement_mode`` decides the failure posture: under
    ``enforce`` an unreachable runtime or a failed query blocks (fail closed,
    AAASM-3106); under ``observe`` / ``disabled`` it proceeds (fail open).

    Returns the registered adapters and what the interceptor they were handed
    does with hook-layer audit records (AAASM-5731). The disposition is returned
    rather than re-derived by the caller because building a second interceptor to
    ask it would re-emit the one-time native-missing warning.
    """
    registry = AdapterRegistry()
    adapters = registry.get_available_adapters_by_priority()

    registered: list[FrameworkAdapter] = []
    interceptor: Any = build_governance_interceptor(
        client,
        process_agent_id,
        enforcement_mode,
        runtime_client=runtime_client,
        native_available=native_available,
    )
    # AAASM-5731 — read off the interceptor the SDK itself builds, before the
    # LangChain hand-over below. That one is the object this SDK can speak for;
    # the handler that may replace it declares its own disposition, and the
    # worst of the two is what the caller is told.
    audit_sink = resolve_audit_sink(interceptor)

    for adapter in adapters:
        adapter.set_process_agent_id(process_agent_id)

        try:
            adapter.register_hooks(interceptor)
        except Exception as error:
            _warn_adapter_registration_failed(adapter, error)
            continue

        registered.append(adapter)

        # After LangChain registers, its callback handler becomes the
        # interceptor for all subsequent adapters.
        if isinstance(adapter, LangChainAdapter):
            callback_handler = get_active_callback_handler()
            if callback_handler is not None:
                interceptor = callback_handler
                handler_sink = resolve_audit_sink(callback_handler)
                # Only ever narrow away from "no claim": the handler accepts the
                # record and drops it where the wrapped interceptor never sees
                # one at all, and reporting the milder of the two would
                # understate what the later adapters actually get.
                if handler_sink != AUDIT_SINK_CALLER_SUPPLIED:
                    audit_sink = handler_sink

    return registered, audit_sink


def _unregister_adapters(adapters: list[FrameworkAdapter]) -> None:
    for adapter in reversed(adapters):
        try:
            adapter.unregister_hooks()
        except Exception:
            continue


def _start_network_layer(*, client: GatewayClient, mode: RuntimeMode) -> tuple[NetworkMode, Callable[[], None]]:
    if mode == "sdk-only":
        return "sdk-only", _noop_shutdown

    if mode == "ebpf":
        if not _platform_supports_ebpf():
            raise ConfigurationError("eBPF mode is not supported on this platform.")
        return "ebpf", _start_ebpf_probes(client)

    if mode == "proxy":
        return "proxy", _start_mitm_proxy(client)

    if _platform_supports_ebpf():
        return "ebpf", _start_ebpf_probes(client)
    return "proxy", _start_mitm_proxy(client)


def _platform_supports_ebpf() -> bool:
    return sys.platform.startswith("linux")


def _start_ebpf_probes(client: GatewayClient) -> Callable[[], None]:
    del client
    return _noop_shutdown


def _start_mitm_proxy(client: GatewayClient) -> Callable[[], None]:
    del client
    return _noop_shutdown


def _noop_shutdown() -> None:
    return None


def _clear_active_context(context: AssemblyContext) -> None:
    global _ACTIVE_CONTEXT
    with _INIT_LOCK:
        if _ACTIVE_CONTEXT is context:
            _ACTIVE_CONTEXT = None


def _validate_active_context_compatibility(
    context: AssemblyContext,
    *,
    gateway_url: str,
    api_key: str,
    agent_id: str,
) -> None:
    if context.client.gateway_url != gateway_url.rstrip("/"):
        raise ConfigurationError("init_assembly already initialized with a different gateway_url.")
    if context.client.api_key != api_key:
        raise ConfigurationError("init_assembly already initialized with a different api_key.")
    if context.client.agent_id != agent_id:
        raise ConfigurationError("init_assembly already initialized with a different agent_id.")
