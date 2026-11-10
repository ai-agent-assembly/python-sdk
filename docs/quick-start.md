# Quick Start

Govern your first agent in about five minutes. By the end you'll have an agent — in whichever
framework you already use — whose tool calls pass through an Agent Assembly adapter, and it
runs **offline** against a local policy, so you need no API keys and no network access to the
outside world.

## 1. Install

The package is published on PyPI as
[`{{ aa.python_sdk.package_name }}`]({{ aa.urls.pypi }}) (current version:
`{{ aa.python_sdk.version }}`).

=== "pip"

    ```bash
    {{ aa.commands.install_pip }}            # pure-Python SDK
    {{ aa.commands.install_pip_runtime }} # SDK + bundled aasm runtime binary (platform wheel)
    ```

=== "uv"

    ```bash
    {{ aa.commands.install_uv }}
    ```

=== "poetry"

    ```bash
    {{ aa.commands.install_poetry }}            # pure-Python SDK
    {{ aa.commands.install_poetry_runtime }} # SDK + bundled aasm runtime binary (platform wheel)
    ```

=== "conda"

    `{{ aa.python_sdk.package_name }}` is not published on conda-forge or the
    Anaconda default channel — create a conda environment, then install from
    PyPI with `pip` inside it:

    ```bash
    {{ aa.commands.install_conda }}
    {{ aa.commands.install_conda_pip }}
    ```

!!! note "`--pre` is required for now"
    Agent Assembly is currently published only as a pre-release on PyPI, and `pip`
    skips pre-releases unless you pass `--pre` (already included above). Drop the flag
    once a stable (non-pre-release) version is published.

`{{ aa.python_sdk.package_name }}` is the pure-Python client.
`{{ aa.python_sdk.package_name }}[runtime]` additionally pulls a platform wheel
(`manylinux`, `macosx`) that bundles the `{{ aa.python_sdk.cli_name }}`
gateway/runtime binary, so a local gateway is available without a separate install.

## 2. Point the SDK at a gateway

`init_assembly()` needs to reach a **gateway** — the policy brain that returns allow/deny
decisions. You have three options:

- **Let the SDK auto-start one.** Call `init_assembly()` with no `gateway_url`; the SDK probes
  `http://localhost:7391` and, if nothing answers, runs `aasm start --mode local --foreground`
  for you. This needs the `aasm` binary on your `PATH` (the `agent-assembly[runtime]` extra
  provides it).
- **Run one yourself** with `aasm start --mode local --foreground` in a separate terminal. For
  a full gateway walkthrough, see the core
  [Run the gateway](https://docs.agent-assembly.com/core/latest/quick-start/first-run.html) guide.
- **Pass an explicit URL**, as the example below does.

See [Configuration](configuration.md) for the full URL/key resolution chain (`7391` is the
local default port).

!!! note "Local-mode transports: `:7391` REST + `:50051` gRPC"
    Starting local mode binds **two** loopback surfaces in one process:

    ```bash
    aasm start --mode local
    ```

    This runs the REST/dashboard API on `http://localhost:7391` (what `gateway_url`
    points to, and what the SDK probes and auto-starts) **and** the gRPC
    `AgentLifecycleService` on `127.0.0.1:50051`, which is the endpoint the native SDK
    uses to **register** your agent. You don't configure `:50051` yourself —
    registration dials it automatically — so a no-argument `init_assembly()` both
    connects and shows the agent in the dashboard. `:8080` is **not** the local gateway
    port; ignore older docs or examples that point registration there.

    To confirm both surfaces are actually up rather than guessing from the SDK's
    behavior, check them directly:

    ```bash
    curl http://localhost:7391/healthz   # REST — real JSON: mode, storage, version, uptime_secs
    nc -z localhost 50051 && echo "gRPC port open"   # gRPC has no health endpoint yet; this only confirms the port accepts connections
    ```

## 3. Govern your first agent

Agent Assembly governs whichever agent framework you already use. Pick your framework below —
each tab is the **governance-wiring slice** (`init_assembly()` plus that framework's adapter
hookup) taken verbatim from that framework's runnable example in the
[examples repo](https://github.com/ai-agent-assembly/examples/tree/master/python). Copy the
full, runnable script — imports, tools, and the agent run — from the linked example; the slice
below is the part that wires in governance.

Every example runs **offline** in `mode="sdk-only"` against a local policy, so you can try it
with no API keys and no outbound network.

These offline snippets also pass `enforcement_mode="observe"`, a demo posture for running
offline against a local policy. Before you copy a snippet into a real service, remove
`enforcement_mode="observe"` and `mode="sdk-only"`, and point the SDK at your gateway.

<!-- BEGIN GENERATED: quickstart-framework-tabs -->

=== "Agno"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.agno import AgnoPatch

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="agno-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()

        print("Policy rules (local simulation of gateway policy):")
        print("  DENY   — execute_sql, run_shell_command  (arbitrary execution)")
        print("  ALLOW  — everything else")
        print()

        # In production init_assembly() auto-detects Agno and wires the live
        # runtime as the interceptor automatically. In this offline sdk-only demo
        # there is no live runtime, so init_assembly() installs a no-op hook; we
        # revert it and re-apply the hook wired to our local policy so the demo
        # shows real allow/deny decisions without a gateway. (The patch is
        # idempotent, so we must revert the no-op hook before installing ours.)
        AgnoPatch(policy).revert()
        patch = AgnoPatch(policy)
        assert patch.apply(), (
            "Agno governance hook did not install — is agno importable?"
        )
    ```

    !!! note "Version compatibility"
        Agno was previously published as **Phidata**; the rename replaced every `phi.*` import with `agno.*`.

        - Before (Phidata): `from phi.agent import Agent`
        - After (Agno): `from agno.agent import Agent`

        Source: [Agno's official Phidata → Agno migration guide](https://docs.agno.com/how-to/phidata-to-agno).

=== "AutoGen"

    ```python
    from agent_assembly import init_assembly

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="autogen-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()
    ```

    !!! note "Version compatibility"
        AutoGen's `v0.4` rewrite (2024) replaced the single `pyautogen` package's `autogen.agentchat` namespace with separate `autogen-agentchat` / `autogen-core` / `autogen-ext` packages, and `llm_config` with an explicit `model_client`.

        - Before (v0.2, `pyautogen`): `from autogen.agentchat import AssistantAgent`
        - After (v0.4+): `from autogen_agentchat.agents import AssistantAgent`

        Source: [AutoGen's official v0.2 → v0.4 migration guide](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/migration-guide.html).

=== "CrewAI"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.langchain import AssemblyCallbackHandler

    from src.crew import CREW
    from src.policy import DAILY_BUDGET_USD, CrewPolicyEngine, MockApprover

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="crewai-research-crew",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} ({mode_label})")
        print()

        print("Crew members:")
        for member in CREW:
            print(f"  • {member.name:<11} — {member.role}")
        print()

        print("Crew policy (local simulation of gateway policy):")
        print("  APPROVAL — any agent attempting a file write must be approved")
        print(f"  BUDGET   — ${DAILY_BUDGET_USD:.2f} / day, shared across all agents")
        print("  TRACK    — every call recorded with its delegation call stack")
        print()

        policy = CrewPolicyEngine(approver=MockApprover(auto_approve=False))
        handler = AssemblyCallbackHandler(interceptor=policy)
    ```

=== "Custom (no framework)"

    ```python
    from agent_assembly import init_assembly

    from src.policy import LocalPolicyEngine, governed
    from src.tools import (
        compute_sum,
        fetch_stock_price,
        send_http_request,
        write_to_disk,
    )

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="custom-tool-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()

        raw_fns = {
            "compute_sum": compute_sum,
            "fetch_stock_price": fetch_stock_price,
            "send_http_request": send_http_request,
            "write_to_disk": write_to_disk,
        }
        tools = {name: governed(name, fn, policy) for name, fn in raw_fns.items()}
    ```

=== "Google ADK"

    ```python
    from agent_assembly import init_assembly

    from src.governance import govern_tool_class, ungovern_tool_class
    from src.policy import LocalPolicyEngine
    from src.tools import DemoTool

    # Govern the concrete demo tool class BEFORE init_assembly so the offline
    # LocalPolicyEngine stays wired as the interceptor (the patch is idempotent).
    govern_tool_class(DemoTool, LocalPolicyEngine())

    try:
        with init_assembly(
            gateway_url=gateway_url,
            api_key=api_key,
            agent_id="google-adk-demo-agent",
            mode="sdk-only",
            # No gateway here: the default posture fails closed when registration
            # can't reach one, so an offline demo must name its dry-run posture.
            enforcement_mode="observe",
        ) as ctx:
            print(f"  Agent:    {ctx.client.agent_id}")
            print(f"  Gateway:  {ctx.client.gateway_url}")
            print(f"  Mode:     {ctx.network_mode} (offline demo)")
            print()

            print("Policy rules (local simulation of gateway policy):")
            print("  DENY    — delete_records, write_file  (destructive operations)")
            print("  PENDING — send_email                  (requires human approval)")
            print("  ALLOW   — everything else")
            print()
    finally:
        ungovern_tool_class(DemoTool)
    ```

=== "Haystack"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.haystack import HaystackPatch

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="haystack-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        print("Policy rules (local simulation of gateway policy):")
        print("  DENY   — execute_sql, run_shell_command  (arbitrary execution)")
        print("  ALLOW  — everything else")
        print()

        # init_assembly() has already auto-detected Haystack and patched
        # Tool.invoke — but in offline sdk-only mode it wires a no-op interceptor
        # (there is no live gateway/runtime to answer policy). For this *offline*
        # demo we revert that and re-install the same native adapter against a
        # LocalPolicyEngine so a real allow/deny is visible without a gateway. In
        # production you would instead point init_assembly() at a gateway and let
        # its auto-detected adapter enforce real policy — no manual re-install.
        print("Installing the native Haystack adapter against the demo policy...")
        HaystackPatch(LocalPolicyEngine()).revert()  # drop the auto-applied no-op patch
        patch = HaystackPatch(LocalPolicyEngine())
        installed = patch.apply()
    ```

    !!! note "Version compatibility"
        Haystack 2.0 replaced the `farm-haystack` package with `haystack-ai` and flattened node imports into `haystack.components.*`; the two package versions cannot coexist in one environment.

        - Before (Haystack 1.x, `farm-haystack`): `from haystack.nodes import BM25Retriever`
        - After (Haystack 2.x, `haystack-ai`): `from haystack.components.retrievers.in_memory import InMemoryBM25Retriever`

        Source: [Haystack's official migration guide](https://docs.haystack.deepset.ai/docs/migration).

=== "LangChain"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.langchain import AssemblyCallbackHandler

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="langchain-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()
        handler = AssemblyCallbackHandler(interceptor=policy)
    ```

    !!! note "Version compatibility"
        LangChain's import surface moved twice: `langchain-core` split out of `langchain` across the `0.1` → `0.3` series (2024), and the `1.0` rewrite (2025) moved legacy chains/agents/tools out of `langchain` entirely into `langchain-classic`.

        - Before (`<1.0`): `from langchain.agents import AgentExecutor, create_react_agent`
        - After (`>=1.0`): `from langchain_classic.agents import AgentExecutor, create_react_agent` (requires the separate `langchain-classic` package)

        This SDK's own quick-start sample hit exactly this break — see AAASM-4451. Sources: [LangChain's official v1 migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1) and the [LangChain v0.3 announcement](https://www.langchain.com/blog/announcing-langchain-v0-3).

=== "LangChain (Research Agent)"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.langchain import AssemblyCallbackHandler

    from src.policy import DAILY_BUDGET_USD, BalancedPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="langchain-research-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} ({mode_label})")
        print()

        policy = BalancedPolicyEngine(daily_budget_usd=DAILY_BUDGET_USD)
        handler = AssemblyCallbackHandler(interceptor=policy)
    ```

    !!! note "Version compatibility"
        LangChain's import surface moved twice: `langchain-core` split out of `langchain` across the `0.1` → `0.3` series (2024), and the `1.0` rewrite (2025) moved legacy chains/agents/tools out of `langchain` entirely into `langchain-classic`.

        - Before (`<1.0`): `from langchain.agents import AgentExecutor, create_react_agent`
        - After (`>=1.0`): `from langchain_classic.agents import AgentExecutor, create_react_agent` (requires the separate `langchain-classic` package)

        This SDK's own quick-start sample hit exactly this break — see AAASM-4451. Sources: [LangChain's official v1 migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1) and the [LangChain v0.3 announcement](https://www.langchain.com/blog/announcing-langchain-v0-3).

=== "LangGraph"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.langchain import AssemblyCallbackHandler
    from agent_assembly.adapters.langgraph import LangGraphAdapter

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="langgraph-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()
        handler = AssemblyCallbackHandler(interceptor=policy)

        # Install LangGraph node-level governance hooks. The adapter wraps the
        # compiled graph's nodes so tool calls inside each node are governed.
        adapter = LangGraphAdapter()
        adapter.set_process_agent_id(ctx.client.agent_id)
        adapter.register_hooks(handler)
    ```

    !!! note "Version compatibility"
        LangGraph `1.0` deprecated `langgraph.prebuilt.create_react_agent` in favor of LangChain's own agent constructor.

        - Before (`<1.0`): `from langgraph.prebuilt import create_react_agent`
        - After (`>=1.0`): `from langchain.agents import create_agent`

        Source: [LangGraph's official v1 migration guide](https://docs.langchain.com/oss/python/migrate/langgraph-v1).

=== "LlamaIndex"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.llamaindex import LlamaIndexAdapter, LlamaIndexPatch

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="llamaindex-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        print("Policy rules (local simulation of gateway policy):")
        print("  DENY   — execute_sql, run_shell_command  (arbitrary execution)")
        print("  ALLOW  — everything else")
        print()

        # Register the native LlamaIndex adapter against the local policy engine.
        # This patches FunctionTool.call so every tool call below is governed
        # automatically — no per-tool wrapper needed.
        #
        # init_assembly() in sdk-only mode already auto-detected LlamaIndex and
        # patched FunctionTool.call against a no-op interceptor (there is no
        # gateway offline). Revert that first so this example's LocalPolicyEngine
        # is the live interceptor; in production init_assembly wires the adapter
        # to the gateway and this manual step is unnecessary.
        print("Registering the native LlamaIndex governance adapter...")
        LlamaIndexPatch(callback_handler=None).revert()
        adapter = LlamaIndexAdapter()
        adapter.register_hooks(LocalPolicyEngine())
    ```

    !!! note "Version compatibility"
        LlamaIndex `v0.10.0` (February 2024) split the monolithic `llama_index` package into a slim `llama-index-core` plus versioned per-provider packages (`llama-index-llms-openai`, etc.). An automated `llamaindex-cli upgrade` tool is provided for the migration.

        - Before (`<0.10`): `from llama_index.llms import OpenAI`
        - After (`>=0.10`): `from llama_index.llms.openai import OpenAI` (from the separate `llama-index-llms-openai` package)

        Source: [LlamaIndex's official v0.10 migration guide](https://www.llamaindex.ai/blog/llamaindex-v0-10-838e735948f8).

=== "Microsoft Agent Framework"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.microsoft_agent_framework import (
        MicrosoftAgentFrameworkAdapter,
    )

    from src.policy import LocalPolicyEngine

    policy = LocalPolicyEngine()

    # Live path: install the governance hooks BEFORE init_assembly. The adapter
    # patches `agent_framework.FunctionTool.invoke`; because the patch is
    # idempotent, registering first makes init_assembly's auto-detection a no-op
    # and keeps the offline `LocalPolicyEngine` wired as the interceptor (rather
    # than the no-op interceptor auto-detection would install).
    adapter: MicrosoftAgentFrameworkAdapter | None = None
    if not mock:
        adapter = MicrosoftAgentFrameworkAdapter()
        adapter.set_process_agent_id("microsoft-agent-framework-demo-agent")
        adapter.register_hooks(policy)

    try:
        with init_assembly(
            gateway_url=gateway_url,
            api_key=api_key,
            agent_id="microsoft-agent-framework-demo-agent",
            mode="sdk-only",
            # No gateway here: the default posture fails closed when registration
            # can't reach one, so an offline demo must name its dry-run posture.
            enforcement_mode="observe",
        ) as ctx:
            print(f"  Agent:    {ctx.client.agent_id}")
            print(f"  Gateway:  {ctx.client.gateway_url}")
            print(f"  Mode:     {ctx.network_mode} (offline demo)")
            print()

            print("Policy rules (local simulation of gateway policy):")
            print("  DENY    — delete_records, write_file  (destructive operations)")
            print("  PENDING — send_email                  (requires human approval)")
            print("  ALLOW   — everything else")
            print()
    finally:
        if adapter is not None:
            adapter.unregister_hooks()
    ```

=== "OpenAI Agents SDK"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.langchain import AssemblyCallbackHandler

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="openai-agents-demo",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()
        handler = AssemblyCallbackHandler(interceptor=policy)
    ```

=== "Pydantic AI"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.pydantic_ai import PydanticAIAdapter

    from src.policy import LocalPolicyEngine

    adapter = PydanticAIAdapter()
    adapter.set_process_agent_id("pydantic-ai-demo-agent")
    adapter.register_hooks(LocalPolicyEngine())

    try:
        with init_assembly(
            gateway_url=gateway_url,
            api_key=api_key,
            agent_id="pydantic-ai-demo-agent",
            mode="sdk-only",
            # No gateway here: the default posture fails closed when registration
            # can't reach one, so an offline demo must name its dry-run posture.
            enforcement_mode="observe",
        ) as ctx:
            print(f"  Agent:    {ctx.client.agent_id}")
            print(f"  Gateway:  {ctx.client.gateway_url}")
            print(f"  Mode:     {ctx.network_mode} (offline demo)")
            print()

            print("Policy rules (local simulation of gateway policy):")
            print("  DENY    — delete_records, write_file  (destructive operations)")
            print("  PENDING — send_email                  (requires human approval)")
            print("  ALLOW   — everything else")
            print()
    finally:
        adapter.unregister_hooks()
    ```

=== "Semantic Kernel"

    ```python
    from agent_assembly import init_assembly

    from src.policy import LocalPolicyEngine
    from src.tools import build_kernel

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="semantic-kernel-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()
        kernel = build_kernel()
    ```

=== "smolagents"

    ```python
    from agent_assembly import init_assembly
    from agent_assembly.adapters.smolagents import SmolagentsPatch

    from src.policy import LocalPolicyEngine

    policy = LocalPolicyEngine()
    patch = SmolagentsPatch(policy)
    patch.apply()

    print(f"Initializing Agent Assembly (gateway: {gateway_url}, sdk-only mode)...")

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="smolagents-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        print("Policy rules (local simulation of gateway policy):")
        print("  DENY   — run_shell_command, delete_records  (destructive ops)")
        print("  ALLOW  — everything else")
        print()
    ```

    !!! note "Version compatibility"
        smolagents `v1.14.0` (April 2025) renamed `HfApiModel` to `InferenceClientModel` to reflect that it wraps any Hugging Face Inference Provider, not just the HF Hub; backward-compatible re-export was restored in `v1.24.0`.

        - Before (`<1.14`): `from smolagents import HfApiModel`
        - After (`>=1.14`): `from smolagents import InferenceClientModel`

        Source: [smolagents releases](https://github.com/huggingface/smolagents/releases).

=== "Strands Agents"

    ```python
    from agent_assembly import init_assembly

    from src.policy import LocalPolicyEngine

    with init_assembly(
        gateway_url=gateway_url,
        api_key=api_key,
        agent_id="strands-demo-agent",
        mode="sdk-only",
        # No gateway here: the default posture fails closed when registration
        # can't reach one, so an offline demo must name its dry-run posture.
        enforcement_mode="observe",
    ) as ctx:
        print(f"  Agent:    {ctx.client.agent_id}")
        print(f"  Gateway:  {ctx.client.gateway_url}")
        print(f"  Mode:     {ctx.network_mode} (offline demo)")
        print()

        policy = LocalPolicyEngine()
    ```

<!-- END GENERATED: quickstart-framework-tabs -->

## What just happened

1. **`init_assembly()` installed the governance hook.** It attempted to register the agent with
   the gateway and auto-loaded the adapter for your framework, which patches that framework's
   tool-invocation path.
2. **`mode="sdk-only"` kept it offline.** No network sidecar starts in that mode, so the example
   runs deterministically with no real LLM or gateway round-trip.
3. **Tool calls went through the adapter.** The adapter intercepts the framework's
   tool-invocation path and, when a policy authority is reachable, asks it for an allow/deny
   verdict before the tool actually runs.
4. **The `with` block tore everything down on exit** — adapter hooks were unwound and the
   gateway connection closed, leaving the process exactly as it was before.

If a tool call raises a `ToolExecutionBlockedError`, that is not a bug — something refused the
call before it ran. Read the exception's reason to see what refused it: a policy rule that denied
the call, or — as in this offline example — an SDK that had no authority to ask and refused
rather than run ungoverned. That's the product working. See
[Handling allow/deny decisions](guides/handling-decisions.md) for how to catch and respond to
those, and [Troubleshooting](troubleshooting.md) if `init_assembly()` itself raised.

## What this offline example evaluates

The example passes a `gateway_url`, and running it offline means nothing is listening there.
A pure-Python `{{ aa.commands.install_pip }}` carries no native `agent_assembly._core`
extension either.

`init_assembly()` reaches no policy authority in that configuration, so it evaluates no policy —
under
[ADR 0033 §6](https://github.com/ai-agent-assembly/agent-assembly/blob/master/docs/src/adr/0033-canonical-governance-and-enforcement-architecture.md)
the term for that state is **Degraded**, not *Evaluated*.
Under the default enforce posture the SDK takes its fail-closed branch instead: a governed tool
call is **denied before execution**, carrying a reason that names the absent extension rather
than a policy rule.
`init_assembly()` says as much at startup — it warns that the agent is unregistered, and that no
in-process policy decision can be made.

That is why several framework tabs above revert the hook `init_assembly()` installed and
re-apply one wired to the example's own `LocalPolicyEngine`.
The local engine, not the SDK, is what returns allow and deny in the offline demo.

Getting a decision from the SDK instead needs the native `agent_assembly._core` extension this
example lacks; install it with `{{ aa.commands.install_pip_runtime }}`.
[Point the SDK at a gateway](#2-point-the-sdk-at-a-gateway) above covers the other half of that
setup.

## `mode="sdk-only"` — why this example uses it

`mode="sdk-only"` is the in-process-only interception layer: the framework adapter enforces on
tool calls against a reachable policy authority, and starts no network sidecar. It's the most
portable mode and the best choice for deterministic, offline examples and tests. The other modes (`auto`, `proxy`, `ebpf`) add
network/kernel interception — see [Core Concepts → Modes](concepts/index.md#runtime-modes).

## Next steps

- **[Core Concepts](concepts/index.md)** — the adapter pattern, the `init_assembly()`
  lifecycle, and the modes/enforcement model.
- **[Examples](examples/index.md)** — wire the SDK into the framework you actually use.
- **[Configuration](configuration.md)** — drop the hard-coded URL and key; let the resolver
  chain find them.
