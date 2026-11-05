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
