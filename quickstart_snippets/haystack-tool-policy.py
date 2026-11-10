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
