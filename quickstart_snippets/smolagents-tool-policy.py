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
