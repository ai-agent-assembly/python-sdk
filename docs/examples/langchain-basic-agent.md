# LangChain — basic agent

Integrate Agent Assembly with LangChain to enforce governance policy on tool calls before they execute.

## What this example demonstrates

This example wires Agent Assembly governance into a LangChain agent so that each tool call the agent makes is checked against policy *before* the tool runs. The example calls the `AssemblyCallbackHandler` explicitly; LangChain does not govern a tool unless the handler is passed in `callbacks`. It covers:

- Initializing Agent Assembly with `init_assembly()`.
- Wrapping LangChain tools with `AssemblyCallbackHandler` + a governance interceptor.
- Running an **allowed** tool call (`get_weather`).
- Running a **denied** tool call (`delete_files` — blocked by a policy rule).
- Running a **pending** tool call (`send_email` — requires human approval; auto-denied in offline mode).
- How `ToolExecutionBlockedError` is raised when a tool is blocked.

The governance point: a safe tool is allowed, a destructive tool is denied, and a tool that requires human approval is held pending — and because the demo runs offline with no approver available, the pending call is auto-denied.

## The framework / library

This example uses [LangChain](https://python.langchain.com/), specifically its `langchain_core.tools` `@tool` decorator and the callback-handler hook surface.

Dependency version pins from `pyproject.toml`:

| Dependency | Pin |
|---|---|
| `agent-assembly` | `>=0.0.1a2` |
| `langchain-core` | `>=0.2.0` |
| `pytest` (dev) | `>=8.0.0` |
| `pytest-mock` (dev) | `>=3.14.0` |

Python requirement: `>=3.12`.

## How it works

The adapter flow ties LangChain's tool-call lifecycle to Agent Assembly policy:

1. **`init_assembly()`** creates an Agent Assembly context. In this example it is called with `gateway_url`, `api_key`, `agent_id="langchain-demo-agent"`, and `mode="sdk-only"`, and is used as a context manager so the assembly context shuts down cleanly on exit.
2. **`AssemblyCallbackHandler`** (from `agent_assembly.adapters.langchain`) hooks LangChain tool calls. It is constructed with an `interceptor` — here `LocalPolicyEngine` — and its `on_tool_start(...)` is invoked before each tool runs.
3. **The interceptor decides** allow / deny / pending. `LocalPolicyEngine.check_tool_start()` returns a decision dict that mirrors the gateway wire format:
   - `delete_files` / `write_file` → `{"status": "deny", ...}`
   - `send_email` → `{"status": "pending", ...}`
   - everything else → `{"status": "allow"}`
4. **Pending resolution**: when a tool is pending, the handler consults `wait_for_tool_approval()`. In offline mode no approver is available, so that method returns a `deny` decision.
5. **A blocked call surfaces as `ToolExecutionBlockedError`.** When the decision is deny (or an unresolved pending), the handler raises `ToolExecutionBlockedError`; `main.py` catches it and prints the block reason. Only an `allow` decision lets the LangChain tool actually `invoke`.

In production, policy is enforced by the gateway server-side; `LocalPolicyEngine` is a local simulation of that governance layer so the demo runs without a running gateway.

## Prerequisites & running it

See [Preparing the runtime environment](preparing-the-runtime-environment.md) for the shared prerequisites.

This example runs fully offline — **no running Agent Assembly gateway is required** for the demo.

Example-specific requirements:

| Requirement | Version |
|---|---|
| Python | `>= 3.12` |
| [uv](https://github.com/astral-sh/uv) | latest |
| Agent Assembly Python SDK | `>= 0.0.1a2` |

Setup and run:

```bash
cd python/langchain-basic-agent
uv sync --extra dev
uv run python src/main.py
```

### Production mode (optional)

To run against a real gateway or SaaS workspace, copy `.env.example` to `.env`, fill in credentials, and pass the gateway environment variables. The relevant variables from `.env.example` are:

```bash
AGENT_ASSEMBLY_GATEWAY_URL=http://localhost:8080 \
AGENT_ASSEMBLY_API_KEY=your-key \
uv run python src/main.py
```

`.env.example` also exposes an optional `OPENAI_API_KEY` for a LangChain LLM provider — the example runs with mock tools by default, so it is not required. In production, remove the `mode="sdk-only"` argument from `init_assembly()` and replace `LocalPolicyEngine` with the gateway-backed interceptor; the SDK then enforces the policy rules configured in the gateway automatically.

## Code walkthrough

**Tools** (`src/tools.py`) — three LangChain `@tool` functions, one per policy outcome:

```python
@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city.

    Returns mock data in offline / demo mode.
    """
    return f"🌤  Weather in {city}: 22°C, partly cloudy (mock response)"
```

`get_weather` is the safe, allowed tool. `delete_files` (destructive, denied) and `send_email` (requires approval) are defined alongside it.

**Policy** (`src/policy.py`) — `DENIED_TOOLS` and `PENDING_TOOLS` are the local rule sets that the interceptor checks against:

```python
DENIED_TOOLS: frozenset[str] = frozenset({
    "delete_files",
    "write_file",
})

PENDING_TOOLS: frozenset[str] = frozenset({
    "send_email",
})
```

`check_tool_start()` maps a tool name to an allow / deny / pending decision:

```python
def check_tool_start(self, serialized, input_str, run_id=None, **kwargs):
    tool_name = serialized.get("name", "")
    if tool_name in DENIED_TOOLS:
        return {"status": "deny", "reason": (
            f"Tool '{tool_name}' is blocked by policy rule "
            "'deny_destructive_operations'.")}
    if tool_name in PENDING_TOOLS:
        return {"status": "pending",
                "reason": f"Tool '{tool_name}' requires human approval before execution."}
    return {"status": "allow"}
```

For pending tools, `wait_for_tool_approval()` returns a `deny` because no approver exists in offline mode.

**Governed call flow** (`src/main.py`) — each demo call routes through `on_tool_start` before the tool is invoked, and a block is caught as `ToolExecutionBlockedError`:

```python
try:
    handler.on_tool_start(
        serialized={"name": tool_name, "type": "tool"},
        input_str=input_str,
        run_id=run_id,
    )
    tool_map = {t.name: t for t in _TOOLS}
    result = tool_map[tool_name].invoke(input_str)
    print(f"     ✅ ALLOWED  — {result}")
except ToolExecutionBlockedError as exc:
    print(f"     ❌ BLOCKED  — {exc}")
```

The handler is constructed with the policy engine as its interceptor: `handler = AssemblyCallbackHandler(interceptor=policy)`.

## Notes & caveats

!!! note "Offline / sdk-only mode"
    The demo runs with `mode="sdk-only"` and requires no running gateway. `LocalPolicyEngine` simulates the gateway's policy enforcement locally; in production the gateway enforces policy server-side and the SDK applies it automatically.

!!! warning "Pending tools are auto-denied offline"
    `send_email` is a pending (approval-required) tool. In offline mode there is no approver, so `wait_for_tool_approval()` returns a deny decision and the call is blocked.

!!! tip "Troubleshooting"
    - `ModuleNotFoundError: agent_assembly` → run `uv sync` first.
    - `ModuleNotFoundError: langchain_core` → run `uv sync` — `langchain-core` is a required dependency.
    - `ToolExecutionBlockedError` in tests → expected; the deny/pending policy rules are intentional.

## Expected behavior

```
==============================================================
  Agent Assembly — LangChain Basic Agent Demo
==============================================================

Initializing Agent Assembly (gateway: http://localhost:8080, sdk-only mode)...
  Agent:    langchain-demo-agent
  Gateway:  http://localhost:8080
  Mode:     sdk-only (offline demo)

Policy rules (local simulation of gateway policy):
  DENY    — delete_files, write_file  (destructive operations)
  PENDING — send_email                (requires human approval)
  ALLOW   — everything else

Running governed tool calls:
--------------------------------------------
  → get_weather({"city": "London"})
     ✅ ALLOWED  — 🌤  Weather in {"city": "London"}: 22°C, partly cloudy (mock response)

  → delete_files({"path": "/etc/passwd"})
     ❌ BLOCKED  — Tool 'delete_files' is blocked by policy rule 'deny_destructive_operations'.

  → send_email({"to": "all@company.com", "subject": "Hello", "body": "World"})
     ❌ BLOCKED  — Tool 'send_email' requires approval, but no approver is available in offline mode.

Assembly context shut down.
```

## Links

- Example directory: <https://github.com/ai-agent-assembly/examples/tree/master/python/langchain-basic-agent>
- Example README: <https://github.com/ai-agent-assembly/examples/blob/master/python/langchain-basic-agent/README.md>
- LangChain documentation: <https://python.langchain.com/>
