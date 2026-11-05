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
