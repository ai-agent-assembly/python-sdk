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
