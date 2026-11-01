# Framework support

Agent Assembly governs *third-party* agent frameworks without those frameworks needing to be
aware of it. You call `init_assembly()` once; the SDK detects which supported frameworks are
importable in your process and installs governance hooks for each, in priority order (see
[Core Concepts](../concepts/index.md#the-adapter-pattern)).

This page is the adapter ↔ example status reference for the [Examples](index.md) section. For
the shared run instructions, see [Preparing the runtime environment](preparing-the-runtime-environment.md);
for a detailed, source-grounded walkthrough of each example, follow the per-framework pages
linked from the [Examples overview](index.md).

## The universal pattern

The integration is the same for every framework — there is no per-framework setup call:

```python
from agent_assembly import init_assembly

with init_assembly(
    gateway_url="http://localhost:7391",
    api_key="dev-key",
    agent_id="my-agent",
    mode="sdk-only",
):
    # Build and run your agent exactly as you normally would.
    # A call to a governed tool now passes through the policy gate, and its outcome is
    # offered to an audit hook. Over a connected runtime that hook forwards the
    # record to the runtime; without one nothing is recorded (AAASM-5750).
    ...
```

Inside the `with` block, each importable framework's adapter has registered its hooks. What a
hook governs depends on the framework: LangChain gets only a callback handler that you must pass
as `callbacks` (see the LangChain quick start below), so run your agent with that wiring. On exit,
all hooks are removed in reverse order.

## Supported frameworks

Each framework below ships an adapter under `agent_assembly/adapters/`. The
**Runnable example** column reflects whether a complete, validated example exists today —
either inline in this guide or as a curated script in the central
[`examples`](https://github.com/ai-agent-assembly/examples/tree/master/python)
repository.

| Framework | Adapter | Runnable example |
| --- | --- | --- |
| LangChain | `agent_assembly.adapters.langchain` | ✅ Validated — see [Quick start](#langchain-quick-start) below (runs offline against a mock LLM), plus the [LangChain basic agent](langchain-basic-agent.md) and [LangChain research agent](langchain-research-agent.md) pages. |
| LangGraph | `agent_assembly.adapters.langgraph` | ✅ Validated — see [LangGraph node-level governance](langgraph.md) (state-graph governance; wraps the compiled `StateGraph`). |
| CrewAI | `agent_assembly.adapters.crewai` | ✅ Validated — see [CrewAI research crew](crewai-research-crew.md) (multi-agent crew). |
| OpenAI Agents | `agent_assembly.adapters.openai_agents` | ✅ Validated — see [OpenAI Agents SDK](openai-agents-sdk.md). |
| Pydantic AI | `agent_assembly.adapters.pydantic_ai` | ✅ Validated — see [Pydantic AI](pydantic-ai.md). |
| Google ADK | `agent_assembly.adapters.google_adk` | ✅ Validated — see [Google ADK](google-adk.md). |
| Haystack | `agent_assembly.adapters.haystack` | ✅ Validated — governs `haystack.tools.Tool.invoke` (Haystack 2.x); see [Haystack](haystack.md). |
| LlamaIndex | `agent_assembly.adapters.llamaindex` | ✅ Validated — governs the concrete `FunctionTool.call` / `acall`; see [LlamaIndex](llamaindex-tool-policy.md). |
| Smolagents | `agent_assembly.adapters.smolagents` | ✅ Validated — governs `smolagents.tools.Tool.__call__`; see [Smolagents](smolagents.md). |
| Agno | `agent_assembly.adapters.agno` | ✅ Validated — governs `agno.tools.function.FunctionCall.execute` / `aexecute`; see [Agno](agno.md). |
| Microsoft Agent Framework | `agent_assembly.adapters.microsoft_agent_framework` | ✅ Validated — governs `agent_framework.FunctionTool.invoke`; see [Microsoft Agent Framework](microsoft-agent-framework.md). |
| MCP servers | `agent_assembly.adapters.mcp` | ⏳ Planned — adapter ships; a curated example is not yet vendored. |

!!! note "Adapter present vs. example present"
    Every framework above has an **adapter that is implemented and registered** — `init_assembly()`
    activates it whenever the framework is importable, regardless of the example status. What an
    adapter then governs differs per framework: for LangChain, `init_assembly()` only creates the
    callback handler and you must pass it as `callbacks` (see the quick start below).
    A ✅ row additionally has a curated, end-to-end runnable example you can clone and run. A ⏳ row
    (currently only MCP servers) means the adapter ships but a runnable example is not yet vendored.
    Contributions adding one are welcome; see
    [CONTRIBUTING.md](https://github.com/ai-agent-assembly/python-sdk/blob/master/CONTRIBUTING.md).

## LangChain quick start

A governed LangChain ReAct agent that runs **offline** against a mock LLM — no real API keys
and no network calls. Install the SDK plus LangChain:

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

Without `config={"callbacks": [handler]}` a LangChain tool runs regardless of policy. The wiring
mechanism is pinned by `test/unit/test_quickstart_langchain_denied_tool_wiring.py` (a bare
`langchain_core` tool; this exact `AgentExecutor` snippet has not been run). The same snippet
lives in the repository [README](https://github.com/ai-agent-assembly/python-sdk#quick-start). See the [`examples/`](https://github.com/ai-agent-assembly/python-sdk/tree/master/examples)
directory for additional in-repo runnable scripts and their status.

## More runnable examples

Curated, end-to-end examples for each framework live in the central
[`examples`](https://github.com/ai-agent-assembly/examples/tree/master/python)
repository. Each directory is a self-contained, cloneable project. This section documents each
one in detail — start with [Preparing the runtime environment](preparing-the-runtime-environment.md),
then follow the per-framework page:

- [LangChain — basic agent](langchain-basic-agent.md) — a governed LangChain agent.
- [LangChain — research agent](langchain-research-agent.md) — a governed LangChain ReAct research agent with a balanced policy.
- [LangGraph — node-level governance](langgraph.md) — LangGraph state-graph governance.
- [CrewAI — multi-agent research crew](crewai-research-crew.md) — a CrewAI multi-agent crew with delegation tracking.
- [OpenAI Agents SDK](openai-agents-sdk.md) — the OpenAI Agents SDK.
- [Pydantic AI](pydantic-ai.md) — Pydantic AI.
- [Google ADK](google-adk.md) — Google ADK.
- [LlamaIndex — manual tool policy](llamaindex-tool-policy.md) — the manual wrapper pattern for a framework with no native adapter.
- [Custom tool policy (no framework)](custom-tool-policy.md) — framework-free tool governance.
