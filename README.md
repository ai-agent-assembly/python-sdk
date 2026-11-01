# Agent Assembly Python SDK

[![CI](https://img.shields.io/github/actions/workflow/status/ai-agent-assembly/python-sdk/ci.yaml?branch=main&logo=githubactions&logoColor=white&label=CI)](https://github.com/ai-agent-assembly/python-sdk/actions/workflows/ci.yaml)
[![Docs](https://img.shields.io/github/actions/workflow/status/ai-agent-assembly/python-sdk/documentation.yaml?branch=main&logo=readthedocs&logoColor=white&label=docs)](https://github.com/ai-agent-assembly/python-sdk/actions/workflows/documentation.yaml)
[![PyPI version](https://img.shields.io/pypi/v/agent-assembly?logo=pypi&logoColor=white)](https://pypi.org/project/agent-assembly/)
[![GitHub release](https://img.shields.io/github/v/release/ai-agent-assembly/python-sdk?include_prereleases&sort=semver&label=release&logo=github)](https://github.com/ai-agent-assembly/python-sdk/releases)
[![Python versions](https://img.shields.io/pypi/pyversions/agent-assembly?logo=python&logoColor=white)](https://pypi.org/project/agent-assembly/)
[![Coverage](https://img.shields.io/codecov/c/github/ai-agent-assembly/python-sdk?logo=codecov&logoColor=white)](https://codecov.io/gh/ai-agent-assembly/python-sdk)
[![Quality Gate](https://img.shields.io/sonar/quality_gate/ai-agent-assembly_python-sdk?server=https%3A%2F%2Fsonarcloud.io&logo=sonarcloud)](https://sonarcloud.io/project/overview?id=ai-agent-assembly_python-sdk)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow?logo=opensourceinitiative&logoColor=white)](./LICENSE)
[![Code style: ruff](https://img.shields.io/badge/style-ruff-261230?logo=ruff&logoColor=white)](https://github.com/astral-sh/ruff)
[![Type-checked: mypy](https://img.shields.io/badge/types-mypy-2a6db2?logo=python&logoColor=white)](https://mypy-lang.org/)

Python SDK for **AI Agent Assembly** — a governance-native runtime for AI agents. `init_assembly()` registers your agent with the policy gateway and installs the framework adapters; the adapters apply pre-execution allow/deny on tool calls. **For LangChain, `init_assembly()` alone does not govern tool calls: you must pass the SDK's callback handler** (see [Quick Start](#quick-start)). For other frameworks, check that framework's adapter page for what it requires.

> **The SDK hands records to the runtime; it does not give you an audit trail.** The framework adapters offer a governed call's outcome to an audit hook on the governance interceptor, and over a connected runtime that hook writes it to the native event channel — the same one agent registration uses. That is a handoff, **not** evidence: the send is unacknowledged, so this SDK cannot tell you the record arrived, and does not claim it did. Downstream, [AAASM-5783](https://lightning-dust-mite.atlassian.net/browse/AAASM-5783) is open on `report_event` payloads reaching neither the live stream nor the durable entry — until it lands, no SDK can claim ADR 0033 §6 *Observed*. Without a reachable runtime there is no channel at all and nothing is emitted.
>
> **Denied calls are covered too.** The eleven governing adapters build a record on the denied path as well as the allowed one, marked so a reader can tell a blocked call from a tool that ran and returned the denial text ([AAASM-5787](https://lightning-dust-mite.atlassian.net/browse/AAASM-5787)); eight of them used to return or raise before their record helper. `langgraph` instruments nodes for lineage and has no tool-call gate, so it records neither. Enforcement is unaffected either way: a policy DENY still blocks the tool. `init_assembly()` warns when no record can be sent and reports `audit_sink` on the returned context ([AAASM-5750](https://lightning-dust-mite.atlassian.net/browse/AAASM-5750)).

## Why use it

- **Framework adapters** for LangChain, LangGraph, CrewAI, OpenAI Agents, Pydantic AI, Google ADK, Haystack, Smolagents, Agno, LlamaIndex, Microsoft Agent Framework, and MCP servers — drop in, no SDK rewrites required.
- **Pre-execution policy enforcement** via the `FrameworkAdapter` ABC — block disallowed tool calls before they hit the LLM.
- **Agent lineage** — parent / root / team identity is registered with the gateway and carried on every policy check. (An audit *trail* from this SDK layer depends on a reachable runtime — see the note above.)
- **Native PyO3 fast path** (optional) — drop into a Rust runtime client when you need sub-millisecond policy checks.
- **Typed throughout** — Pydantic models for every gateway payload, mypy strict on adapter base and registry.

## Framework compatibility

The SDK governs these AI-agent frameworks; install whichever one your agent already
uses alongside `agent-assembly` and the matching adapter activates automatically (the
frameworks are **not** runtime dependencies of the SDK — you pin the framework, within
the supported range). **Tested version** is the exact line exercised by the live smoke
suite (AAASM-3525) and the integration tests; older releases within the supported range
are expected to work but are not continuously tested.

| Framework | Import name | Supported range | Tested version |
| --- | --- | --- | --- |
| LangChain | `langchain` | `>=0.1.0` | `0.3.x` line |
| LangGraph | `langgraph` | `>=0.1.0` | `0.2.x` line |
| Pydantic AI | `pydantic_ai` | `>=0.1.0` | `>=0.3.0` |
| CrewAI | `crewai` | `>=0.1.0` | `1.14.x` |
| Google ADK | `google.adk` | `>=1.0.0,<2.0` | `1.x` line |
| Haystack | `haystack` | `>=2.0.0,<3.0` | `2.30.x` |
| LlamaIndex | `llama_index.core` | `>=0.10.0` | `0.14.x` |
| MCP | `mcp` | `>=1.0.0` | `1.27.x` |
| OpenAI Agents | `agents` | `>=0.1.0` | `0.17.x` |
| Smolagents | `smolagents` | `>=1.0.0,<2.0.0` | `1.26.x` |
| Agno | `agno` | `>=2.0.0` | `2.6.x` |
| Microsoft Agent Framework | `agent_framework` | `>=1.0.0,<2.0` | `1.9.x` |

Python framework compatibility is documented **authoritatively in the SDK docs** —
[Framework compatibility](./docs/compatibility/frameworks.md) — because the Python
adapters and the ranges they advertise via `get_supported_versions()` are the source of
truth (Python-specific detail, version-sync policy, and the Pydantic AI hook-point note
live there). The core docs provide a cross-SDK
[index/hub](https://docs.agent-assembly.com/core/stable/reference/framework-compatibility.html)
that links out to each SDK's own page (Python here, plus Node and Go) — an index, not the
canonical matrix (a `/stable/` link — it 404s until the first GA release, by design).

## Project status

**Pre-1.0 (`0.x`) — published and usable, API not yet frozen.** The SDK is released to
PyPI from the `0.0.x` line (the version badge above reflects the current release). Until
`1.0.0`, minor versions may introduce breaking changes to the public surface; pin an exact
version (`agent-assembly==0.0.x`) if you need a stable contract.

- **Releases** — [PyPI release history](https://pypi.org/project/agent-assembly/#history) · [GitHub releases](https://github.com/ai-agent-assembly/python-sdk/releases)
- **Changelog** — tracked via [commits to `master`](https://github.com/ai-agent-assembly/python-sdk/commits/master) until the first tagged `1.0` release; see the [release notes](https://docs.agent-assembly.com/python-sdk/latest/release-notes/) page.
- **Stability** — the `init_assembly()` entry point and the exception hierarchy are the most stable surface; framework adapters and the native fast path may evolve faster.

## Requirements

- **Python** `>=3.12,<4.0` (3.12, 3.13, 3.14 are tested in CI)
- **Rust toolchain** (stable channel) — only required for building the optional native extension via `maturin develop`. Pure-Python users do not need Rust.
- **uv** ≥ 0.4 — recommended for managing the dev environment. (`pip` works for plain installs.)

## Installation

### Use the SDK in your project

The package is published on PyPI as [`agent-assembly`](https://pypi.org/project/agent-assembly/):

```bash
pip install --pre agent-assembly            # pure-Python SDK
pip install --pre 'agent-assembly[runtime]' # SDK + bundled aasm runtime binary (platform wheel)
```

> **`--pre` is required for now.** Agent Assembly is currently published only as a
> pre-release on PyPI, and `pip` skips pre-releases unless you pass `--pre`. Drop the
> flag once a stable (non-pre-release) version is published.

`agent-assembly[runtime]` pulls a platform wheel (`manylinux`, `macosx`) that bundles the
`aasm` sidecar binary, so you don't need a separate runtime install. Plain `agent-assembly`
is the pure-Python client and expects an `aasm` runtime to be reachable some other way.

Confirm the `aasm` CLI is on your `PATH`:

```bash
aasm --version   # e.g. aasm 0.0.1rc6
```

> **Supply-chain verification.** The only official PyPI package is `agent-assembly`
> (anything else is a typosquat). Every release ships PEP 740 attestations on its
> [PyPI files page](https://pypi.org/project/agent-assembly/#files) and a CycloneDX
> SBOM attached to its [GitHub Release](https://github.com/ai-agent-assembly/python-sdk/releases).
> See [`SECURITY.md`](./SECURITY.md) for how to verify what you installed.

With [`uv`](https://docs.astral.sh/uv/):

```bash
uv add --prerelease=allow agent-assembly
```

> **`--prerelease=allow` is required for now.** Unlike `pip`, `uv` refuses to
> resolve pre-releases unless you opt in. Agent Assembly is currently published only
> as a pre-release on PyPI, so `uv add agent-assembly` fails to resolve without it.
> Drop the flag once a stable (non-pre-release) version is published.

To track unreleased changes, install from the `main` branch:

```bash
pip install git+https://github.com/ai-agent-assembly/python-sdk.git
```

### Develop on the SDK

Clone the repo and sync the dev environment with `uv`:

```bash
git clone https://github.com/ai-agent-assembly/python-sdk.git
cd python-sdk
uv sync
```

To build the optional PyO3 extension locally (requires Rust):

```bash
uv tool run maturin develop --manifest-path native/aa-ffi-python/Cargo.toml --release
```

The pure-Python SDK works without the native extension — `maturin develop` is only needed if you want the sub-millisecond `RuntimeClient` fast path.

## Quick Start

A governed LangChain ReAct agent that runs offline against a mock LLM. The example imports LangChain in addition to the SDK, so install both:

```bash
pip install --pre agent-assembly langchain langchain-classic langchain-community
```

```python
from langchain_classic.agents import AgentExecutor, create_react_agent
from langchain_classic.tools import Tool
from langchain_community.llms import FakeListLLM
from langchain_core.prompts import PromptTemplate

from agent_assembly import init_assembly

with init_assembly(
    gateway_url="http://localhost:7391",
    api_key="dev-key",
    agent_id="quickstart-agent",
    mode="sdk-only",
) as ctx:
    # init_assembly() does NOT hook LangChain by itself. LangChain only calls a
    # callback handler it is given, so pass the SDK's handler on every run.
    adapter = next((a for a in ctx.adapters if a.get_framework_name() == "langchain"), None)
    if adapter is None:
        raise RuntimeError("LangChain adapter is not active: is `langchain` importable?")
    handler = adapter.get_callback_handler()
    llm = FakeListLLM(responses=[
        "Thought: I should look up the user.\nAction: whoami\nAction Input: alice\n",
        "Thought: I have the answer.\nFinal Answer: alice is in engineering\n",
    ])
    tools = [Tool(name="whoami", func=lambda name: f"{name} is in engineering", description="who")]
    prompt = PromptTemplate.from_template(
        "Use the tools.\n{tools}\nTool names: {tool_names}\nQ: {input}\n{agent_scratchpad}"
    )
    executor = AgentExecutor(agent=create_react_agent(llm, tools, prompt), tools=tools, max_iterations=2)
    result = executor.invoke(
        {"input": "Which team is alice on?"},
        config={"callbacks": [handler]},  # <- this is what puts the policy gate in front of each tool
    )
    print(result["output"])
```

What this does:

1. `init_assembly()` registers the agent with the gateway and activates the LangChain adapter, which creates one `AssemblyCallbackHandler` (`adapter.get_callback_handler()`). It installs **no** hook into LangChain itself.
2. `config={"callbacks": [handler]}` is what routes each tool call through the policy gate. The handler runs before the tool and raises `ToolExecutionBlockedError` on a deny, so a denied tool's body does not run. **Without this argument a LangChain tool runs regardless of policy**, even though `init_assembly()` succeeded and reports the adapter as active.
3. The `FakeListLLM` replays canned responses so the example runs **offline** with no real LLM.
4. The `with` block tears down the gateway connection and unwinds adapter hooks on exit.

> **What `init_assembly()` alone does not enforce.** It does not govern LangChain tool calls (pass the handler as above), and an offline `sdk-only` run has no gateway policy authority to ask unless a native runtime is reachable. `test/unit/test_quickstart_langchain_denied_tool_wiring.py` pins the wiring: a denied tool run with the handler leaves no side effect, and the same tool run without it executes.

## Public API

- `init_assembly(gateway_url, api_key, agent_id=None, mode="auto", *, control_plane_url=None, allow_insecure=False) -> AssemblyContext`
- Exceptions: `AssemblyError`, `AgentError`, `PolicyError`, `GatewayError`, `ConfigurationError`
- Data models: `AgentConfig`, `AgentState`, `PolicyEvaluation`

Agent **registration** and the **pre-execution policy check** are not REST
calls: the SDK goes through the native `aa-sdk-client` shim to the core over
gRPC/UDS (ADR 0004) — it never calls a core HTTP endpoint directly for those.
`init_assembly` registers the agent on startup, and a tool call that reaches an
adapter hook (for LangChain, only when the callback handler is passed in
`callbacks`) is checked via the native `query_policy` so a `deny` blocks it
before the tool runs.

### Control-plane routing

By default the SDK issues its remaining HTTP routes (topology edges, secret
dispatch) against `gateway_url` — the single-host OSS dev setup. Pass
`control_plane_url` to route those HTTP calls to a separate control-plane host
while `gateway_url` continues to serve the gRPC data path:

```python
init_assembly(
    gateway_url="http://gateway:7391",
    control_plane_url="http://control-plane:9000",
    api_key="dev-key",
)
```

Both URLs also resolve from the environment when their kwargs are omitted.
Resolution order is **explicit kwarg > env-var > unset**:

| Argument | Env-var fallback |
|---|---|
| `gateway_url` | `AA_GATEWAY_URL` |
| `control_plane_url` | `AA_CONTROL_PLANE_URL` |

### Insecure transport opt-in

Agent registration dials the gateway's gRPC port over the native `register`
call. By default a **plaintext `http://` register channel to a non-loopback
host is refused** — the `Register` call carries the agent identity, so it must
not travel unencrypted to a remote host. Loopback (local dev) and `https://`
targets always pass.

Pass `allow_insecure=True` to opt into plaintext to a non-loopback host, on a
**trusted network only** (e.g. a private link where TLS terminates elsewhere).
This mirrors the operator-control `connect(allow_insecure=...)` opt-in:

```python
init_assembly(
    gateway_url="http://gateway.internal:7391",
    allow_insecure=True,  # trusted-network only; leave False in production
)
```

## Error Handling

```python
from agent_assembly import init_assembly
from agent_assembly.exceptions import ConfigurationError

try:
    # An invalid mode fails validation up front and raises ConfigurationError.
    # (An empty/unset gateway_url is not an error — it triggers local
    # auto-discovery; a gateway that can't be reached raises GatewayError.)
    context = init_assembly(gateway_url="http://localhost:7391", mode="invalid-mode")
except ConfigurationError as exc:
    print(f"Invalid configuration: {exc}")
```

> **Auto-discovery caveat.** Local auto-discovery tries to auto-start a gateway via
> the `aasm` CLI. If `aasm` is **not** on your `PATH`, discovery can't start one and
> raises `ConfigurationError` immediately (rather than the `GatewayError` you'd get
> after the ~5 s health-check timeout when `aasm` is present but the gateway never
> becomes ready). Install the runtime with `pip install --pre 'agent-assembly[runtime]'`.

## Development

Run tests:

```bash
uv run pytest
```

Run integration tests:

```bash
uv run pytest -m integration
```

Lint and type-check:

```bash
uv run ruff check .
uv run mypy agent_assembly
```

## Native Core Extension

Build and install the PyO3 extension locally:

```bash
uv tool run maturin develop --manifest-path native/aa-ffi-python/Cargo.toml --release
```

Validate native module import:

```python
from agent_assembly._core import RuntimeClient, GovernanceEvent
```

Run opt-in native integration tests:

```bash
AAASM_RUN_NATIVE_CORE_TESTS=1 uv run pytest test/integration/test_native_core_runtime.py
AAASM_RUN_MATURIN_TESTS=1 uv run pytest test/integration/test_native_core_maturin.py
```

## Documentation

- **Project docs (rendered)** — https://docs.agent-assembly.com/python-sdk/ *(versioned via `mike`; pick `latest` or `stable` from the version selector)*
- **Quick Start** — [source](./docs/quick-start.md) — install and govern your first agent in five minutes (offline LangChain example).
- **Core Concepts** — [source](./docs/concepts/index.md) — the adapter pattern, native FFI vs. pure-Python, the `init_assembly()` lifecycle, and modes/enforcement. Deep dive in [Architecture](./docs/concepts/architecture.md).
- **Guides** — [Framework examples](./docs/guides/framework-examples.md), [Handling allow/deny decisions](./docs/guides/handling-decisions.md), [Type checking](./docs/guides/type-checking.md).
- **Configuration** — [source](./docs/configuration.md) — gateway URL / API-key resolution, runtime modes, enforcement modes.
- **API reference** — [rendered](https://docs.agent-assembly.com/python-sdk/latest/api-reference/) / [source](./docs/api-reference/index.md) — auto-generated from package docstrings via `mkdocstrings`. Per-module pages: [Client](./docs/api-reference/client.md), [Exceptions](./docs/api-reference/exceptions.md), [Models](./docs/api-reference/models.md).
- **Compatibility & Versioning** — [source](./docs/compatibility/index.md) — Python versions, core-runtime tracking, [release process](./docs/compatibility/release-process.md), and [ADRs](./docs/development/adr/).
- **Troubleshooting** — [source](./docs/troubleshooting.md) — common integration errors and fixes.

## Ecosystem

This SDK is one piece of the **AI Agent Assembly** project. Start from the
[organization profile](https://github.com/ai-agent-assembly) to discover every repo, or jump
directly:

| Project | What it is |
| --- | --- |
| [agent-assembly](https://github.com/ai-agent-assembly/agent-assembly) | **Core runtime** — gateway, policy engine, eBPF, proxy, CLI. Also home of the protocol specification. |
| [Documentation site](https://docs.agent-assembly.com/) | Canonical, cross-repo documentation hub for the whole project. |
| [python-sdk](https://github.com/ai-agent-assembly/python-sdk) | **This repo** — the Python SDK. |
| [node-sdk](https://github.com/ai-agent-assembly/node-sdk) · [go-sdk](https://github.com/ai-agent-assembly/go-sdk) | Sibling SDKs for TypeScript/Node and Go. |
| [homebrew-tap](https://github.com/ai-agent-assembly/homebrew-tap) | Homebrew tap for installing the `aasm` runtime CLI. |
| [examples](https://github.com/ai-agent-assembly/examples) | Runnable examples — learn by running small, framework-specific Python (and Node/Go) samples covering policy enforcement, approvals, audit, trace, and runtime workflows. |

The protocol specification and gateway behaviour the SDK targets live in the core runtime
monorepo; see its [README](https://github.com/ai-agent-assembly/agent-assembly#readme) for the
spec and architecture. For how this SDK stays in sync with the core runtime, see the
[Compatibility & Versioning](https://docs.agent-assembly.com/python-sdk/latest/compatibility/)
docs.

## Contributing

Please read [**CONTRIBUTING.md**](./CONTRIBUTING.md) before opening a PR — it covers dev environment setup, framework adapter authoring, the test/lint command list, branch naming, and the PR checklist.

## Support

- **Bugs & feature requests** — open a [GitHub issue](https://github.com/ai-agent-assembly/python-sdk/issues).
- **Questions & usage help** — start with the [documentation site](https://docs.agent-assembly.com/python-sdk/) and the [Troubleshooting](https://docs.agent-assembly.com/python-sdk/latest/troubleshooting/) guide, then open an issue if you're still stuck.
- **Security** — please **do not** file public issues for vulnerabilities. Report them privately via [GitHub Security Advisories](https://github.com/ai-agent-assembly/python-sdk/security/advisories/new).

## License

[MIT License](./LICENSE)
