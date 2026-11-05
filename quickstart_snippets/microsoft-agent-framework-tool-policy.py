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
