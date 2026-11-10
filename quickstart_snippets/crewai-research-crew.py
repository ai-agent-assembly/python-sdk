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
