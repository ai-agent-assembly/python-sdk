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
