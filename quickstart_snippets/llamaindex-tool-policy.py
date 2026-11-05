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
