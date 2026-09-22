# Core Concepts

This section explains the four ideas that make the SDK work. Read it once and the rest of the
docs — and the package source — will make sense.

- [The adapter pattern](#the-adapter-pattern) — how one `init_assembly()` call governs a
  framework that knows nothing about Agent Assembly.
- [Native FFI vs. pure-Python](#native-ffi-vs-pure-python) — the optional Rust fast path and
  why the SDK works without it.
- [The `init_assembly()` lifecycle](#the-init_assembly-lifecycle) — what happens at startup
  and shutdown.
- [Modes and enforcement](#modes-and-enforcement) — *where* policy is enforced and *how
  hard* it bites.

For the full, contributor-grade walkthrough of the internals — including the per-framework
monkey-patch layer and the exact bootstrap/teardown order — see
[Architecture](architecture.md).

The SDK is the in-process (fastest) layer of a three-layer interception model. For how it
fits alongside the sidecar proxy and eBPF layers, and the trust boundary the gateway
enforces, see the core
[Architecture](https://docs.agent-assembly.com/core/latest/architecture/system-architecture.html) and
[Security Model](https://docs.agent-assembly.com/core/latest/security/overview.html) docs.

## The adapter pattern

The SDK governs *third-party* agent frameworks (LangChain, LangGraph, CrewAI, OpenAI Agents,
Pydantic AI, Google ADK, MCP servers) **without those frameworks needing to be aware of Agent
Assembly**. It does this with a three-layer pattern:

1. **`FrameworkAdapter` (the public ABC)** — `agent_assembly.adapters.base.FrameworkAdapter`
   is the abstract contract every adapter implements. It declares four lifecycle methods:
   `get_framework_name()`, `get_supported_versions()`, `register_hooks(interceptor)`, and
   `unregister_hooks()`. Adapter authors target this and nothing else.
2. **`AdapterRegistry` (auto-discovery + priority)** — enumerates the built-in adapters, probes
   each one's `is_available()` to see if its framework is importable in this process, and
   returns the available ones in priority order.
3. **Per-framework patches** — each adapter's `register_hooks()` installs one or more
   `RuntimePatch` objects that monkey-patch the framework's actual tool-call entry points
   (e.g. LangChain's `BaseTool._run`).

`init_assembly()` calls `AdapterRegistry.get_available_adapters_by_priority()` exactly once at
startup — this is the **single detection path** (see [ADR-0001](../development/adr/0001-hook-architecture.md)).

### Why priority order matters

Two frameworks can coexist in one process (e.g. a LangGraph graph that contains a LangChain
tool). The registry assigns each a fixed rank so hooks install in a deterministic order and
adapters don't double-emit events. The order is defined in
`agent_assembly.adapters.registry`:

| Priority | Framework key | Why this rank |
| --- | --- | --- |
| 0 | `langchain` | **First** — its callback handler threads through to every adapter that follows. |
| 1 | `langgraph` | Framework-specific adapter; ranked after LangChain since a LangGraph graph commonly wraps LangChain tools. |
| 2 | `crewai` | Framework-specific adapter; fixed mid-rank so its hooks install deterministically. |
| 3 | `pydantic_ai` | Framework-specific adapter; fixed mid-rank so its hooks install deterministically. |
| 4 | `openai` | OpenAI Agents; framework-specific adapter at a fixed mid-rank. |
| 5 | `google_adk` | Google ADK; framework-specific adapter, the last fixed rank before third-party and MCP. |
| 99 | `mcp` | **Last** — backstops any tool-dispatch path the framework-specific adapters didn't claim. |

Third-party adapters discovered via the `agent_assembly.adapters` entry-point group get the
default priority `50` (between Google ADK and MCP) unless they appear in the table above.

## Native FFI vs. pure-Python

The SDK has two interchangeable client paths:

- **Pure-Python `GatewayClient`** (the default) — talks to the gateway over HTTP. No
  compilation, no Rust, works everywhere. This is what every example in these docs uses.
- **Native PyO3 fast path** (optional) — a Rust runtime client exposed as the private
  `agent_assembly._core` module, built from `native/aa-ffi-python/` with `maturin`. It ships a
  `RuntimeClient` (a thin shim over the shared `aa-sdk-client` crate) for sub-millisecond,
  fire-and-forget event reporting under heavy multi-tenant load, plus a `GovernanceEvent`
  type.

`agent_assembly/__init__.py` imports the native symbols inside a `try / except ImportError`.
**If the native extension was never built, the SDK still works** — the pure-Python client is
the fallback, and `RuntimeClient` / `GovernanceEvent` simply aren't present in
`agent_assembly.__all__`. You only need the native path when policy-check latency is your
bottleneck; see [Architecture → PyO3 FFI layer](architecture.md#pyo3-ffi-layer) to build it.

## The `init_assembly()` lifecycle

`agent_assembly.init_assembly()` is the single entry point. Its contract is **atomic**: either
everything succeeds and your agent is governed, or nothing changed and you get an exception —
it never leaves the process half-patched.

At a glance:

1. **Validate** `gateway_url`, `mode`, and `enforcement_mode` — bad arguments raise
   `ConfigurationError` *before* any side effect, so retrying is safe.
2. **Create the gateway client** and register the agent.
3. **Discover adapters** via the registry; frameworks that aren't importable are silently
   skipped.
4. **Install hooks** for each available adapter, in priority order.
5. **Start the network layer** (for `proxy` / `ebpf` modes).
6. **Register the active context** under a lock — `init_assembly()` is **idempotent per
   process**: a second compatible call returns the same context instead of double-patching.

The returned `AssemblyContext` is also a **context manager**. On exit (or explicit
`shutdown()`) the steps reverse: stop the network layer, `unregister_hooks()` on every adapter
in reverse order, close the client, and clear the global slot. Teardown errors are aggregated
into a single `AssemblyError` so no patch is ever left installed. Full step-by-step order is in
[Architecture → lifecycle](architecture.md#init_assembly-lifecycle).

```python
from agent_assembly import init_assembly

with init_assembly(gateway_url="http://localhost:7391", api_key="dev-key", mode="sdk-only"):
    ...  # your agent runs governed here; hooks come off on exit
```

## Modes and enforcement

Two independent knobs control governance. It's worth keeping them straight:

- **`mode`** answers *"where is policy enforced?"* — which interception layer is active.
- **`enforcement_mode`** answers *"how hard does a deny bite?"* — the posture the gateway
  applies.

### Runtime modes

`mode` selects the interception layer. Passing an unknown value raises `ConfigurationError`.

| Mode | Where it enforces |
| --- | --- |
| `auto` (default) | Picks the best available layer for the current platform (eBPF on Linux, else proxy). |
| `sdk-only` | In-process only — framework adapters enforce on tool calls; no network sidecar. Most portable; best for tests. |
| `proxy` | Routes outbound traffic through the `aasm` sidecar proxy — network-egress policy without modifying the agent's source. |
| `ebpf` | Kernel-level interception via eBPF. **Linux only** — raises `ConfigurationError` elsewhere. |

The two knobs are independent with one exception: what a **failed agent registration** does to
`init_assembly()`. Under `sdk-only` — the mode that starts no sidecar and is documented as
needing no gateway — an unreachable gateway **warns** and init continues. Every other mode, and
any mode where you pass `enforcement_mode="enforce"` explicitly, treats it as a misconfiguration
and fails init closed (AAASM-6155). Only whether `init_assembly()` raises differs: in both cases
`ctx.registered` reports `False`, the warning is unconditional, and a governed tool call with no
authoritative decision behind it is still **denied** under enforce.

### Enforcement modes

`enforcement_mode` is the governance posture sent to the gateway at registration. Leaving it
`None` omits the field, so the gateway applies its server-side default (live `enforce`).

| Value | What a policy decision does |
| --- | --- |
| `enforce` | Default. A `deny` decision **blocks** the action; `redact` strips secrets. |
| `observe` | Dry-run — every action **proceeds**, but the gateway records would-be violations as shadow audit events. Ideal for safely rolling out new policy. |
| `disabled` | Policy evaluation **skipped** entirely. Hermetic tests only. |

These tokens are the same snake_case strings the gateway expects on the wire — `mode` mirrors
the SDK's interception layers and `enforcement_mode` mirrors `aa_core::EnforcementMode`. The
full parameter reference is in [Configuration](../configuration.md).
