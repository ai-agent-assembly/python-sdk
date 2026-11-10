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
