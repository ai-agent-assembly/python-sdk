"""Drift gate binding the quick-start's claims to the controls that prove them.

AAASM-5529, Epic AAASM-5526.

Every sentence in ``docs/quick-start.md`` must be either **bound** to a control
that proves it, or **explicitly allow-listed** as making no capability claim.
There is no third state and no keyword filter.

Why the default is inverted
---------------------------

Earlier revisions only scanned sentences matching an enforcement vocabulary.
Review appended three plain sentences that used none of the 21 terms — the last
of them, *"Tool bodies always execute; the policy result is recorded alongside
them"*, is the negation of the product — and all three gates stayed green.
Widening 3 → 21 terms closed the instance and not the class: **a keyword
allow-list cannot be completed, because whoever adds the claim picks the words
after reading the list.**

So the vocabulary no longer gates anything. It survives only as a *severity
hint* in the failure message, and as the trigger for a stricter allow-list rule
(:data:`_ALLOWED` entries whose sentence matches it need a bespoke written
justification, not a category).

There are no section-level exclusions either. An excluded section was a black
hole: the guard checked the heading still existed and said nothing about its
contents, so a claim inserted into ``## Next steps`` was never scanned at all.

What this gate proves
---------------------

#. **Every sentence in the document is accounted for.** Add a sentence anywhere
   — any section, any wording — and it fails until someone binds it or
   allow-lists it by exact text.
#. **A binding matches a whole sentence, exactly** (``==``, never ``in``), and
   exactly one binding may match a sentence. Substring containment let a
   sentence carry extra unbound claims, including its own negation.
#. **Every control a binding names still exists**, extracted from the control
   modules' ASTs rather than transcribed.
#. **Every claim is proven or openly unproven**, with no exempt category, and an
   unproven claim must name a ticket that is *not* the ticket this module
   implements — a pointer at one's own ticket resolves to a closed issue the
   moment that ticket merges.
#. **Comments are stripped before scanning**, because a reader cannot see them.
   Leaving them in let a bound claim be commented out of the rendered page while
   the gate still counted it.

What this gate does **not** prove
---------------------------------

It does not execute, import or type-check a quick-start snippet.
``quickstart_snippets/`` is a vendored verbatim copy of regions from the
``examples`` repository (``ruff.toml`` excludes it), and the snippets reference
names they never define. The ``quickstart-tabs-check`` drift job round-trips
them as *text* only. Neither job runs a snippet.

Nor does binding a claim make it true. A binding records which control stands
behind a sentence; where none does, it says so and names the ticket.
"""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# NOTE: SDK symbols a claim names are resolved lazily, by module path and
# attribute name, rather than imported here. Importing them at module scope
# makes a rename a *collection* error, aborting before the assertion meant to
# catch it can run.

#: Tickets whose work lands in this module. An unproven claim may not name any of
#: them — see test_an_unproven_reason_does_not_name_an_implementing_ticket.
#:
#: A tuple rather than one string (AAASM-5661). As a single value the rule caught
#: only the module's original author: AAASM-5661 edited these bindings and pointed
#: three unproven claims at *itself*, and the gate stayed green through the whole
#: PR because the banned name was still 5529. Every ticket that edits this module
#: appends itself here, because every one of them closes on merge and none may be
#: a referent afterwards.
IMPLEMENTING_TICKETS: tuple[str, ...] = ("AAASM-5529", "AAASM-5661")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUICK_START = _REPO_ROOT / "docs" / "quick-start.md"

#: Modules a binding may name a control from.
#:
#: AAASM-5661 added the third. The first proves what a *reachable* runtime does
#: and installs a fake native core to get one; the third proves what the
#: documented configuration does, which is to have none. Keeping them in separate
#: modules keeps that difference visible at the import line rather than buried in
#: a fixture.
_CONTROL_MODULES = (
    Path(__file__).with_name("test_quickstart_negative_control.py"),
    Path(__file__).with_name("test_quickstart_documented_configuration.py"),
    Path(__file__).with_name("test_assembly.py"),
)

#: NOT a gate. A severity hint in the failure message, and the trigger for the
#: stricter allow-list rule below. See the module docstring for why gating on a
#: keyword list is unsound.
_ENFORCEMENT_VOCABULARY = re.compile(
    r"(?i)\bdenie[sd]\b|\bdeny\b|\bblocked\b|\bblocking\b|\bnever runs?\b"
    r"|\bbefore execution\b|\bchecked against\b|\benforces?\b|\benforced\b"
    r"|\bpassthrough\b|\bdiscards?\b|\bdiscarded\b|\bthrows?\b|\brejects?\b"
    r"|\brouted\b|\bintercepts?\b|\binterception\b|\bgovern(s|ed|ance)?\b"
    r"|\bverified\b|\bprotection\b|\bunprotected\b|\bbypass(ed|es)?\b"
)

#: Allow-list categories. A category is only permitted for a sentence that does
#: NOT match the vocabulary above; anything that does needs a written reason.
#: The ONLY bare constant. Permitted solely for structurally non-prose lines —
#: tab labels, table rows, migration import pairs, bare link-list items — which
#: are matched by _STRUCTURAL_LINE below. Every other entry carries a written
#: justification unique to that sentence.
#:
#: The previous rule required a justification only when the sentence matched the
#: enforcement vocabulary, which is backwards: the sentences that most need
#: explaining are the ones that EVADE the vocabulary, since evading it is the
#: whole reason the scan was inverted.
_STRUCTURAL = "Structurally non-prose: a bare mkdocs tab label, which renders as a tab caption and carries no sentence."

#: Lines that may use the bare constant.
#:
#: Fully anchored, and deliberately narrow. The previous pattern asked whether
#: a sentence STARTED with structure, not whether it was ONLY structure — so a
#: link item, a table row or a bold link was waved through on its first few
#: characters while its anchor text, which a reader sees as prose, went
#: unexamined. A payload as plain as
#:   [Every tool request is permitted to proceed and its outcome captured](x.md)
#: passed. Only a bare tab label qualifies now; everything else is justified.
_STRUCTURAL_LINE = re.compile(r'^===\s+"[^"]*"$')

#: A sentence that turns mid-way can under-claim and over-claim at once:
#: "Network-layer interception is not enabled by default, because the in-process
#: adapter already verifies every outbound request before it leaves the host."
#: The first clause is a limitation; the second is a fabrication riding along
#: under it. Rather than judge each case, the shape is rejected: an allow-listed
#: sentence may not contain a contrastive conjunction. It costs nothing on
#: genuine non-claims, because a sentence containing " but " should be split
#: regardless of what its justification says.
#:
#: Applied to EVERY entry, not only ones whose justification calls itself a
#: disclaimer. Keying off a marker phrase made the rule opt-in by the author it
#: constrains — capitalising the phrase, or omitting it, evaded the check.
#: "so" is deliberately NOT in this list. It is consequential ("therefore"),
#: not adversative, and both sentences it flagged here — "…against a local
#: policy, so you can try it with no API keys" and "…bundles the binary, so a
#: local gateway is available" — turn in the same direction they started.
#: Both attack payloads are still caught: the reviewer's used "because" and
#: the live Go case used "but". Controls for both are in the test below.
_CONTRASTIVE_CONJUNCTION = re.compile(r"(?i)\s(?:but|because|though|although|however|whereas|while)\s")

#: "so" is handled separately, because the risk it carries is not
#: adversativeness but POLARITY CHANGE. "We don't do X but Y" is a concession;
#: "we don't do X so Y covers it" is a REASSURANCE, and reassurance is the
#: register documentation over-claims in. A negated clause followed by an
#: un-negated one is the shape that hides an affirmative capability claim
#: behind a limitation.
#:
#: Flagging "so" flat would catch six live sentences across the three repos,
#: every one of which turns the way it started. This form catches none of them
#: and still catches the payload, which is the only negative-to-positive case.
_NEGATION = re.compile(r"(?i)\b(?:not|no|never|cannot|can't|without)\b")
_SO = re.compile(r"(?i)\sso\s")

#: A justification must be at least this long. Not a real check — no gate can
#: tell a justification from noise — but it makes reason="x" visible.
_MIN_JUSTIFICATION = 40


@dataclass(frozen=True)
class ClaimBinding:
    """One documented claim and the controls that stand behind it."""

    claim_id: str
    #: The claim as a WHOLE sentence, flattened. Compared with ==, not `in`.
    quote: str
    #: ``ClassName::test_name`` or ``test_name`` ids from _CONTROL_MODULES.
    controls: tuple[str, ...] = ()
    #: Set when no control proves the claim. Must name a ticket, and must not
    #: name any of IMPLEMENTING_TICKETS.
    unproven_reason: str = ""
    #: Backticked SDK identifiers the claim names -> the module they live in.
    symbols: dict[str, str] = field(default_factory=dict)


_DENY_CONTROLS = (
    "TestFilesystemSideEffect::test_negative_control_denied_write_leaves_no_file",
    "TestNetworkSideEffect::test_negative_control_denied_egress_never_reaches_the_listener",
)
_ALLOW_AND_DENY_CONTROLS = (
    "TestFilesystemSideEffect::test_positive_control_allowed_write_creates_the_file",
    *_DENY_CONTROLS,
    "TestNetworkSideEffect::test_positive_control_allowed_egress_reaches_the_listener",
)

#: AAASM-5661 measured the documented configuration: it reaches no gateway and
#: installs a deny-all fail-closed interceptor. Every control in
#: test_quickstart_negative_control.py calls install_fake_core(), supplying an
#: authoritative runtime the documented path does not have, so none of them
#: exercises what these sentences describe. Binding one would launder that gap
#: into evidence.
#:
#: AAASM-5661 also closed the gap for the sentences it could: the ones about what
#: happens *after* init now name controls in
#: test_quickstart_documented_configuration.py, which runs the page's arguments
#: with no fake core. What is left is what that module cannot reach — claims that
#: need a runtime or a gateway actually standing up.
#:
#: The shared body carries NO ticket. It used to, and the ticket it carried was
#: AAASM-5661 — the one that closes on this merge, which is precisely the
#: stale-referent shape AAASM-5750 exists to eliminate: a pointer aimed at a
#: ticket that finishes without delivering the capability, so a reader who
#: follows it finds completed work that never intended to. Each claim below now
#: names the ticket that will actually resolve *it*, prefixed to this body.
_DOCUMENTED_PATH_UNMEASURED = (
    "the documented configuration was measured and does not behave as this sentence "
    "says. No control covers it — every control in test_quickstart_negative_control.py "
    "installs a fake native core the documented path does not have, and the "
    "documented-configuration controls cannot stand up a live gateway to prove what one "
    "would return."
)

#: The documented configuration, measured end to end through Agno's own tool
#: path: a hook is installed, the body does not run, and the refusal names the
#: absent authority rather than a policy rule.
_DOCUMENTED_CONFIGURATION_CONTROLS = (
    "TestTheConfigurationTheQuickStartHandsAReader::test_a_governance_hook_is_installed_on_agnos_own_tool_path",
    "TestTheConfigurationTheQuickStartHandsAReader"
    "::test_the_refusal_is_the_fail_closed_posture_rather_than_a_policy_decision",
    "TestTheConfigurationTheQuickStartHandsAReader::test_falsification_the_same_agno_tool_ungoverned_writes_the_file",
)

BINDINGS: tuple[ClaimBinding, ...] = (
    ClaimBinding(
        claim_id="tool-calls-pass-through-the-policy-gate",
        quote=(
            "By the end you'll have an agent — in whichever framework you already use — whose "
            "tool calls pass through an Agent Assembly adapter, and it runs **offline** "
            "against a local policy, so you need no API keys and no network access to the "
            "outside world."
        ),
        # Found by inverting the default. It states the page's central promise
        # and matches no enforcement keyword, so every earlier revision of this
        # gate was blind to it.
        #
        # AAASM-5661 narrowed it from "the Agent Assembly policy gate" to "an
        # Agent Assembly adapter", which is what the documented configuration
        # actually puts on the tool path — measured through Agno's own
        # FunctionCall.execute, so the adapter's presence is observed rather than
        # inferred from init returning cleanly. The page's *policy gate* is the
        # example's own LocalPolicyEngine, and the section added by that ticket
        # says so.
        controls=_DOCUMENTED_CONFIGURATION_CONTROLS,
    ),
    ClaimBinding(
        claim_id="governs-whichever-framework-you-use",
        quote="Agent Assembly governs whichever agent framework you already use.",
        # A breadth claim with no boundary. The controls prove the shared
        # governed-tool chain, not "whichever framework"; per ADR 0033 §6 a
        # claim like this needs a named boundary or a qualification.
        unproven_reason=(
            "AAASM-5768: an unbounded breadth claim. The controls prove the shared "
            "governed-tool chain behind two adapter tabs, not every framework the page "
            "offers, and no control enumerates them. AAASM-5768 owns bounding or "
            "qualifying it."
        ),
    ),
    ClaimBinding(
        claim_id="auto-start-probes-and-starts-a-gateway",
        quote=(
            "Call `init_assembly()` with no `gateway_url`; the SDK probes "
            "`http://localhost:7391` and, if nothing answers, runs `aasm start --mode local "
            "--foreground` for you."
        ),
        # Measured during this ticket's scoping pass and reported: the
        # [project.scripts] aasm console script shadows the bundled Rust binary
        # on PATH, so find_aasm_binary() resolves the Python one, which has no
        # `start` subcommand. The documented auto-start therefore cannot work
        # from a clean install.
        #
        # Owned by AAASM-5760, whose description carries this exact measurement
        # as its defect #1. It previously named AAASM-5661, which measured the
        # defect but does not fix it — a packaging change, not a documentation
        # one — so that pointer would have resolved to closed work.
        unproven_reason=(
            "AAASM-5760: no control covers the documented auto-start path, and it was "
            "measured not to work from a clean install — the [project.scripts] aasm "
            "console script shadows the bundled binary, and the shadowing one has no "
            "'start' subcommand. AAASM-5760 owns resolving the binary or naming the "
            "command that exists."
        ),
    ),
    ClaimBinding(
        claim_id="no-arg-init-connects-and-appears-in-dashboard",
        quote=(
            "You don't configure `:50051` yourself — registration dials it automatically — so a "
            "no-argument `init_assembly()` both connects and shows the agent in the dashboard."
        ),
        # Owned by AAASM-5760's defect #2, "a gateway-less call raises instead of
        # degrading", whose AC is that the gateway-less path either degrades with
        # a stated posture or the documentation says it raises. That is this
        # sentence: measured, a no-argument init_assembly() with the native core
        # present and no gateway raises ConfigurationError, and without the
        # native core registration never runs, so the agent does not appear.
        unproven_reason=(
            f"AAASM-5760: {_DOCUMENTED_PATH_UNMEASURED} Neither half of 'connects and "
            "shows the agent in the dashboard' holds on the documented path, and "
            "AAASM-5760 owns making the gateway-less path degrade or saying that it "
            "raises."
        ),
    ),
    ClaimBinding(
        claim_id="gateway-returns-allow-deny-decisions",
        quote=("`init_assembly()` needs to reach a **gateway** — the policy brain that returns allow/deny decisions."),
        # What a *live gateway* returns cannot be shown by any unit control in
        # this repo; it needs the documented path standing up end to end. That is
        # AAASM-5758, which runs each documented quick-start from published
        # artifacts only — and which lists AAASM-5661 among its blockers, so it
        # cannot be the ticket that closes first.
        unproven_reason=(
            f"AAASM-5758: {_DOCUMENTED_PATH_UNMEASURED} AAASM-5758 owns running the "
            "documented quick-start from published artifacts against a real gateway, "
            "which is the only place this sentence can be shown true."
        ),
    ),
    ClaimBinding(
        claim_id="init-routes-every-tool-call",
        quote=(
            "It attempted to register the agent with the gateway and auto-loaded the adapter "
            "for your framework, which patches that framework's tool-invocation path."
        ),
        # AAASM-5661. The previous wording — "every tool call from this point on
        # is routed through the policy gate" — was measured false on the
        # documented path (there is no policy gate to route to) and used a banned
        # absolute besides. What survives is what the controls observe: the
        # adapter patches the framework's tool path, and registration is
        # *attempted*, which on this configuration does not succeed.
        controls=(
            *_DOCUMENTED_CONFIGURATION_CONTROLS,
            "TestTheConfigurationTheQuickStartHandsAReader::test_the_agent_is_not_registered_in_this_configuration",
        ),
    ),
    ClaimBinding(
        claim_id="sdk-only-enforces-on-tool-calls",
        quote=(
            "No network sidecar starts in that mode, so the example runs deterministically "
            "with no real LLM or gateway round-trip."
        ),
        # AAASM-5661, second pass. This said "the in-process adapter enforces on
        # tool calls with no network sidecar" — the same claim as the
        # `mode="sdk-only"` section further down, which that pass qualified while
        # leaving this one unconditional, and rested on _DENY_CONTROLS, both of
        # which call install_fake_core(). Two sentences making one claim is one
        # sentence too many; the enforcement half now lives only in the qualified
        # one, and what is left here is what `sdk-only` actually buys the example.
        # Bound to the control that runs the real _start_network_layer rather than
        # monkeypatching it away.
        controls=("TestTheConfigurationTheQuickStartHandsAReader::test_no_network_sidecar_starts_in_sdk_only_mode",),
    ),
    ClaimBinding(
        claim_id="verdict-precedes-execution",
        quote=(
            "The adapter intercepts the framework's tool-invocation path and, when a policy "
            "authority is reachable, asks it for an allow/deny verdict before the tool "
            "actually runs."
        ),
        # AAASM-5661 added the condition. The controls below supply a reachable
        # authority via install_fake_core(); unconditionally, the sentence
        # described a configuration this page's own example does not produce.
        # Both halves. The negative controls prove the "before" by absence of
        # the side effect; the positive controls prove the probe would have seen
        # that effect had it happened. Either alone is vacuous.
        controls=_ALLOW_AND_DENY_CONTROLS,
    ),
    ClaimBinding(
        claim_id="init-wired-in-governance-label",
        # Split out from the bullet it leads, once the splitter learned to keep
        # closing markup with its sentence. Short, but still a claim: it asserts
        # an outcome.
        #
        # AAASM-5661 replaced "wired in governance" — an undifferentiated verb of
        # exactly the kind ADR 0033 §6 rules out — with the narrower outcome the
        # controls observe: a hook on the framework's tool path.
        quote="**`init_assembly()` installed the governance hook.**",
        controls=_DOCUMENTED_CONFIGURATION_CONTROLS,
    ),
    ClaimBinding(
        claim_id="tool-calls-were-governed-label",
        quote="**Tool calls went through the adapter.**",
        # AAASM-5661, second pass. Found by sweeping the page for the claim class
        # rather than the sites review named — nobody flagged this one. "were
        # governed" is a bare past-tense assertion about *this* example, in the
        # undifferentiated register ADR 0033 §6 rules out, and it rested on
        # controls that install a fake native core. On the configuration the page
        # hands a reader the calls were refused without a policy governing them,
        # so the label now says the part that held: they reached the adapter.
        controls=_DOCUMENTED_CONFIGURATION_CONTROLS,
    ),
    ClaimBinding(
        claim_id="with-block-tears-everything-down",
        quote=(
            "**The `with` block tore everything down on exit** — adapter hooks were unwound and "
            "the gateway connection closed, leaving the process exactly as it was before."
        ),
        # Previously exempt under the removed `kind` field. Under the inverted
        # default it is a claim like any other and needs a control.
        controls=("test_context_manager_shutdown_calls_adapter_unregister_hooks",),
    ),
    ClaimBinding(
        claim_id="deny-surfaces-as-tool-execution-blocked",
        quote=(
            "If a tool call raises a `ToolExecutionBlockedError`, that is not a bug — something "
            "refused the call before it ran."
        ),
        # AAASM-5661, second pass. This read "— the policy denied the call",
        # which a reader meets at the moment their tool blocks. On the documented
        # configuration every governed call raises this and no policy denied any
        # of them. The tell was in the binding: it already named
        # test_an_unavailable_native_runtime_denies_rather_than_silently_allowing,
        # a control whose whole point is that there is no verdict source — the
        # named evidence was the counter-example to the clause.
        #
        # "something refused" is the claim both halves support: a policy deny
        # (the fake-core controls) and a no-authority refusal (the documented
        # ones) each raise it.
        controls=(
            *_DENY_CONTROLS,
            *_DOCUMENTED_CONFIGURATION_CONTROLS,
            "TestDegradedRuntimeCannotLookProtected"
            "::test_an_unavailable_native_runtime_denies_rather_than_silently_allowing",
        ),
        symbols={"ToolExecutionBlockedError": "agent_assembly.exceptions"},
    ),
    ClaimBinding(
        claim_id="the-exception-reason-says-what-refused",
        quote=(
            "Read the exception's reason to see what refused it: a policy rule that denied the "
            "call, or — as in this offline example — an SDK that had no authority to ask and "
            "refused rather than run ungoverned."
        ),
        # AAASM-5661, second pass. The replacement for what "the policy denied the
        # call" used to assert, and stronger than it: each disjunct has its own
        # control reading the reason string. The deny controls assert the policy
        # text reaches it ("policy forbids disk writes"); the documented-path
        # control asserts the other branch names the absent extension. Binding
        # only one of the two would leave the sentence half-evidenced in exactly
        # the direction that misleads.
        controls=(
            *_DENY_CONTROLS,
            "TestTheConfigurationTheQuickStartHandsAReader"
            "::test_the_refusal_is_the_fail_closed_posture_rather_than_a_policy_decision",
        ),
    ),
    ClaimBinding(
        claim_id="sdk-only-is-the-in-process-interception-layer",
        quote=(
            '`mode="sdk-only"` is the in-process-only interception layer: the framework adapter '
            "enforces on tool calls against a reachable policy authority, and starts no network "
            "sidecar."
        ),
        # AAASM-5661 named the precondition the deny controls actually satisfy.
        # Without it the sentence read as unconditional and was falsified by the
        # page's own example, which reaches no authority at all.
        controls=_DENY_CONTROLS,
    ),
    ClaimBinding(
        claim_id="other-modes-add-network-kernel-interception",
        quote=(
            "The other modes (`auto`, `proxy`, `ebpf`) add network/kernel interception — see "
            "[Core Concepts → Modes](concepts/index.md#runtime-modes)."
        ),
        # This previously named AAASM-5529 — the ticket this module implements —
        # which would have resolved to a closed issue the moment this merged.
        # No ticket currently owns *proving* it: nothing in this SDK starts or
        # probes a proxy or eBPF layer, so the honest resolution is
        # qualification rather than proof, which AAASM-5766 owns.
        unproven_reason=(
            "AAASM-5766: no control in the Python SDK starts or probes a proxy or eBPF "
            "layer, so nothing here distinguishes 'the mode adds interception' from 'the "
            "mode is selected'. AAASM-5766 owns proving or qualifying it."
        ),
    ),
    # ---------------------------------------------------------------- AAASM-5661
    # "What this offline example evaluates". The section exists because the
    # sentences above it were measured false on the page's own configuration, so
    # every sentence in it is bound to the module that made that measurement.
    ClaimBinding(
        claim_id="offline-example-has-no-native-extension",
        quote=(
            "A pure-Python `{{ aa.commands.install_pip }}` carries no native `agent_assembly._core` extension either."
        ),
        # The premise the rest of the section rests on, pinned by the control
        # that refuses to run if the premise stops holding.
        controls=(
            "TestTheConfigurationTheQuickStartHandsAReader::test_the_environment_under_test_has_no_native_authority",
        ),
    ),
    ClaimBinding(
        claim_id="offline-example-evaluates-no-policy",
        quote=(
            "`init_assembly()` reaches no policy authority in that configuration, so it "
            "evaluates no policy — under [ADR 0033 §6](https://github.com/ai-agent-assembly/"
            "agent-assembly/blob/master/docs/src/adr/"
            "0033-canonical-governance-and-enforcement-architecture.md) the term for that "
            "state is **Degraded**, not *Evaluated*."
        ),
        # A negative capability claim, and the one that replaces the page's
        # central over-claim. The control reads the refusal's reason: it names
        # the absent extension, which a policy verdict could not.
        controls=_DOCUMENTED_CONFIGURATION_CONTROLS,
    ),
    ClaimBinding(
        claim_id="offline-example-denies-before-execution",
        quote=(
            "Under the default enforce posture the SDK takes its fail-closed branch instead: a "
            "governed tool call is **denied before execution**, carrying a reason that names "
            "the absent extension rather than a policy rule."
        ),
        controls=_DOCUMENTED_CONFIGURATION_CONTROLS,
    ),
    ClaimBinding(
        claim_id="offline-example-warns-at-startup",
        quote=(
            "`init_assembly()` says as much at startup — it warns that the agent is "
            "unregistered, and that no in-process policy decision can be made."
        ),
        controls=(
            "TestTheConfigurationTheQuickStartHandsAReader"
            "::test_startup_reports_both_the_registration_and_the_enforcement_gap",
            "TestTheConfigurationTheQuickStartHandsAReader::test_the_agent_is_not_registered_in_this_configuration",
        ),
    ),
    ClaimBinding(
        claim_id="framework-tabs-revert-the-installed-hook",
        quote=(
            "That is why several framework tabs above revert the hook `init_assembly()` "
            "installed and re-apply one wired to the example's own `LocalPolicyEngine`."
        ),
        # About the page's own generated tabs. Bound rather than allow-listed
        # because the tabs are vendored from another repo: an upstream edit that
        # drops the workaround would otherwise leave this sentence quietly false.
        controls=(
            "TestTheWorkaroundTheFrameworkTabsCarry::test_the_tabs_still_revert_the_hook_init_assembly_installed",
        ),
    ),
    ClaimBinding(
        claim_id="the-local-engine-decides-in-the-offline-demo",
        quote="The local engine, not the SDK, is what returns allow and deny in the offline demo.",
        controls=_DOCUMENTED_CONFIGURATION_CONTROLS,
    ),
    ClaimBinding(
        claim_id="native-extension-is-required-for-an-sdk-decision",
        quote=(
            "Getting a decision from the SDK instead needs the native `agent_assembly._core` "
            "extension this example lacks; install it with "
            "`{{ aa.commands.install_pip_runtime }}`."
        ),
        # A necessity claim, and only that. The controls show the SDK returns no
        # decision without the extension; nothing here shows that installing it
        # is sufficient, and the sentence deliberately does not say so.
        controls=_DOCUMENTED_CONFIGURATION_CONTROLS,
    ),
)

#: Every sentence in the quick-start that makes no capability claim, keyed
#: exactly. A category is only permitted where the sentence does not match
#: _ENFORCEMENT_VOCABULARY; anything that does needs a written justification.
_ALLOWED: dict[str, str] = {
    "Govern your first agent in about five minutes.": (
        "The page title line. An imperative naming what the reader is about to do; the substantive promise is the next sentence, which is bound."
    ),
    "The package is published on PyPI as [`{{ aa.python_sdk.package_name }}`]({{ aa.urls.pypi }}) (current version: `{{ aa.python_sdk.version }}`).": (
        "States where the package is distributed and at what version. Distribution, not behaviour."
    ),
    '=== "pip"': _STRUCTURAL,
    '=== "uv"': _STRUCTURAL,
    '=== "poetry"': _STRUCTURAL,
    '=== "conda"': _STRUCTURAL,
    "`{{ aa.python_sdk.package_name }}` is not published on conda-forge or the Anaconda default channel — create a conda environment, then install from PyPI with `pip` inside it:": (
        "A packaging-channel fact plus the workaround. Says nothing about runtime behaviour."
    ),
    '!!! note "`--pre` is required for now" Agent Assembly is currently published only as a pre-release on PyPI, and `pip` skips pre-releases unless you pass `--pre` (already included above).': (
        "Explains a pip flag required by the pre-release channel. Installer mechanics only."
    ),
    "Drop the flag once a stable (non-pre-release) version is published.": (
        "Forward-looking install instruction tied to the --pre note above it."
    ),
    "`{{ aa.python_sdk.package_name }}` is the pure-Python client.": (
        "Names what the base distribution contains. Packaging composition, not capability."
    ),
    "`{{ aa.python_sdk.package_name }}[runtime]` additionally pulls a platform wheel (`manylinux`, `macosx`) that bundles the `{{ aa.python_sdk.cli_name }}` gateway/runtime binary, so a local gateway is available without a separate install.": (
        "Names what the [runtime] extra adds to the install. Packaging composition; whether that binary is actually reachable is a separate, bound claim."
    ),
    "You have three options:": ("A list lead-in with no predicate of its own."),
    'These offline snippets also pass `enforcement_mode="observe"`, a demo posture for running without a gateway.': (
        "Names the argument the offline snippets pass and the setting they are meant for. States no behaviour of observe mode; what that mode does is documented and bound elsewhere."
    ),
    'Before you copy a snippet into a real service, remove `enforcement_mode="observe"` and `mode="sdk-only"`, and point the SDK at your gateway.': (
        "An instruction to change two demo arguments and add a gateway endpoint when moving off the offline setup. An imperative about configuration, with no claim about what the SDK or gateway then does."
    ),
    "**Let the SDK auto-start one.**": (
        "The bold label of a bullet. The behavioural claim it introduces is the next sentence, which is bound and registered unproven."
    ),
    "This needs the `aasm` binary on your `PATH` (the `agent-assembly[runtime]` extra provides it).": (
        "States a prerequisite for the auto-start option. A precondition, not a claim that anything is enforced."
    ),
    "**Run one yourself** with `aasm start --mode local --foreground` in a separate terminal.": (
        "One of the three gateway options, given as a command to run."
    ),
    "For a full gateway walkthrough, see the core [Run the gateway](https://docs.agent-assembly.com/core/latest/quick-start/first-run.html) guide.": (
        "A cross-reference to the Core docs; the gateway's own claims are gated there."
    ),
    "**Pass an explicit URL**, as the example below does.": (
        "The third gateway option, pointing at the example below."
    ),
    "See [Configuration](configuration.md) for the full URL/key resolution chain (`7391` is the local default port).": (
        "A cross-reference plus the default port number."
    ),
    '!!! note "Local-mode transports: `:7391` REST + `:50051` gRPC" Starting local mode binds **two** loopback surfaces in one process:': (
        "Describes which ports local mode binds. Transport topology, not enforcement."
    ),
    "This runs the REST/dashboard API on `http://localhost:7391` (what `gateway_url` points to, and what the SDK probes and auto-starts) **and** the gRPC `AgentLifecycleService` on `127.0.0.1:50051`, which is the endpoint the native SDK uses to **register** your agent.": (
        "Maps each local-mode port to the consumer that dials it. Transport topology, not enforcement."
    ),
    "`:8080` is **not** the local gateway port; ignore older docs or examples that point registration there.": (
        "Corrects a wrong port number that appears in older material."
    ),
    "To confirm both surfaces are actually up rather than guessing from the SDK's behavior, check them directly:": (
        "Tells the reader to verify the ports themselves; the commands follow in a fenced block."
    ),
    "Pick your framework below — each tab is the **governance-wiring slice** (`init_assembly()` plus that framework's adapter hookup) taken verbatim from that framework's runnable example in the [examples repo](https://github.com/ai-agent-assembly/examples/tree/master/python).": (
        "Describes the provenance of the tab content. 'governance-wiring slice' names the excerpt, not an enforcement outcome."
    ),
    "Copy the full, runnable script — imports, tools, and the agent run — from the linked example; the slice below is the part that wires in governance.": (
        "An instruction about which lines to copy. Identifies the excerpt without claiming what the wiring achieves."
    ),
    'Every example runs **offline** in `mode="sdk-only"` against a local policy, so you can try it with no API keys and no outbound network.': (
        "A claim about network usage and credentials, not about whether a denied call is stopped. The enforcement claim for this mode is bound separately."
    ),
    '=== "Agno"': _STRUCTURAL,
    '!!! note "Version compatibility" Agno was previously published as **Phidata**; the rename replaced every `phi.*` import with `agno.*`.': (
        "Third-party framework rename note, for Agno."
    ),
    "Before (Phidata): `from phi.agent import Agent`": (
        "The pre-rename Agno import, shown as the before half of a two-line pair."
    ),
    "After (Agno): `from agno.agent import Agent`": (
        "The post-rename Agno import, shown as the after half of a two-line pair."
    ),
    "Source: [Agno's official Phidata → Agno migration guide](https://docs.agno.com/how-to/phidata-to-agno).": (
        "Attribution for the Agno rename note, linking that framework's own migration guide."
    ),
    '=== "AutoGen"': _STRUCTURAL,
    "!!! note \"Version compatibility\" AutoGen's `v0.4` rewrite (2024) replaced the single `pyautogen` package's `autogen.agentchat` namespace with separate `autogen-agentchat` / `autogen-core` / `autogen-ext` packages, and `llm_config` with an explicit `model_client`.": (
        "Third-party framework migration note, for AutoGen v0.2 to v0.4."
    ),
    "Before (v0.2, `pyautogen`): `from autogen.agentchat import AssistantAgent`": (
        "The AutoGen v0.2 import, shown as the before half of a two-line pair."
    ),
    "After (v0.4+): `from autogen_agentchat.agents import AssistantAgent`": (
        "The AutoGen v0.4 import, shown as the after half of a two-line pair."
    ),
    "Source: [AutoGen's official v0.2 → v0.4 migration guide](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/migration-guide.html).": (
        "Attribution for the AutoGen rewrite note, linking that framework's own migration guide."
    ),
    '=== "CrewAI"': _STRUCTURAL,
    '=== "Custom (no framework)"': _STRUCTURAL,
    '=== "Google ADK"': _STRUCTURAL,
    '=== "Haystack"': _STRUCTURAL,
    '!!! note "Version compatibility" Haystack 2.0 replaced the `farm-haystack` package with `haystack-ai` and flattened node imports into `haystack.components.*`; the two package versions cannot coexist in one environment.': (
        "Third-party framework migration note, for Haystack 1.x to 2.x."
    ),
    "Before (Haystack 1.x, `farm-haystack`): `from haystack.nodes import BM25Retriever`": (
        "The Haystack 1.x import, shown as the before half of a two-line pair."
    ),
    "After (Haystack 2.x, `haystack-ai`): `from haystack.components.retrievers.in_memory import InMemoryBM25Retriever`": (
        "The Haystack 2.x import, shown as the after half of a two-line pair."
    ),
    "Source: [Haystack's official migration guide](https://docs.haystack.deepset.ai/docs/migration).": (
        "Attribution for the Haystack packaging note, linking that framework's own migration guide."
    ),
    '=== "LangChain"': _STRUCTURAL,
    '!!! note "Version compatibility" LangChain\'s import surface moved twice: `langchain-core` split out of `langchain` across the `0.1` → `0.3` series (2024), and the `1.0` rewrite (2025) moved legacy chains/agents/tools out of `langchain` entirely into `langchain-classic`.': (
        "Third-party framework migration note, for LangChain's two import moves."
    ),
    "Before (`<1.0`): `from langchain.agents import AgentExecutor, create_react_agent`": (
        "The pre-1.0 LangChain agents import, shown as the before half of a two-line pair."
    ),
    "After (`>=1.0`): `from langchain_classic.agents import AgentExecutor, create_react_agent` (requires the separate `langchain-classic` package)": (
        "The post-1.0 LangChain agents import, naming the extra package it now needs."
    ),
    "This SDK's own quick-start sample hit exactly this break — see AAASM-4451.": (
        "A historical note recording that this repo was affected by the LangChain break above."
    ),
    "Sources: [LangChain's official v1 migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1) and the [LangChain v0.3 announcement](https://www.langchain.com/blog/announcing-langchain-v0-3).": (
        "Attribution for the LangChain note, linking both upstream announcements it draws on."
    ),
    '=== "LangChain (Research Agent)"': _STRUCTURAL,
    '=== "LangGraph"': _STRUCTURAL,
    '!!! note "Version compatibility" LangGraph `1.0` deprecated `langgraph.prebuilt.create_react_agent` in favor of LangChain\'s own agent constructor.': (
        "Third-party framework deprecation note, for LangGraph 1.0."
    ),
    "Before (`<1.0`): `from langgraph.prebuilt import create_react_agent`": (
        "The pre-1.0 LangGraph prebuilt import, shown as the before half of a two-line pair."
    ),
    "After (`>=1.0`): `from langchain.agents import create_agent`": (
        "The post-1.0 replacement for the LangGraph prebuilt constructor."
    ),
    "Source: [LangGraph's official v1 migration guide](https://docs.langchain.com/oss/python/migrate/langgraph-v1).": (
        "Attribution for the LangGraph deprecation note, linking that project's own migration guide."
    ),
    '=== "LlamaIndex"': _STRUCTURAL,
    '!!! note "Version compatibility" LlamaIndex `v0.10.0` (February 2024) split the monolithic `llama_index` package into a slim `llama-index-core` plus versioned per-provider packages (`llama-index-llms-openai`, etc.).': (
        "Third-party framework packaging-split note, for LlamaIndex v0.10."
    ),
    "An automated `llamaindex-cli upgrade` tool is provided for the migration.": (
        "Names the upstream tool that performs the LlamaIndex migration described above."
    ),
    "Before (`<0.10`): `from llama_index.llms import OpenAI`": (
        "The pre-0.10 LlamaIndex LLM import, shown as the before half of a two-line pair."
    ),
    "After (`>=0.10`): `from llama_index.llms.openai import OpenAI` (from the separate `llama-index-llms-openai` package)": (
        "The post-0.10 LlamaIndex LLM import, naming the provider package it moved into."
    ),
    "Source: [LlamaIndex's official v0.10 migration guide](https://www.llamaindex.ai/blog/llamaindex-v0-10-838e735948f8).": (
        "Attribution for the LlamaIndex split note, linking that project's own release post."
    ),
    '=== "Microsoft Agent Framework"': _STRUCTURAL,
    '=== "OpenAI Agents SDK"': _STRUCTURAL,
    '=== "Pydantic AI"': _STRUCTURAL,
    '=== "Semantic Kernel"': _STRUCTURAL,
    '=== "smolagents"': _STRUCTURAL,
    '!!! note "Version compatibility" smolagents `v1.14.0` (April 2025) renamed `HfApiModel` to `InferenceClientModel` to reflect that it wraps any Hugging Face Inference Provider, not just the HF Hub; backward-compatible re-export was restored in `v1.24.0`.': (
        "Third-party framework rename note, for smolagents v1.14 and its v1.24 re-export."
    ),
    "Before (`<1.14`): `from smolagents import HfApiModel`": (
        "The pre-1.14 smolagents model import, shown as the before half of a two-line pair."
    ),
    "After (`>=1.14`): `from smolagents import InferenceClientModel`": (
        "The post-1.14 smolagents model import, after the class was renamed."
    ),
    "Source: [smolagents releases](https://github.com/huggingface/smolagents/releases).": (
        "Attribution for the smolagents rename note, linking that project's release list."
    ),
    '=== "Strands Agents"': _STRUCTURAL,
    '**`mode="sdk-only"` kept it offline.**': (
        "The bold label of a bullet, claiming only that no network was used. The enforcement half of the bullet is the next sentence, which is bound to the deny controls."
    ),
    "That's the product working.": (
        "A one-clause reassurance attached to the ToolExecutionBlockedError sentence before it, which is bound."
    ),
    "See [Handling allow/deny decisions](guides/handling-decisions.md) for how to catch and respond to those, and [Troubleshooting](troubleshooting.md) if `init_assembly()` itself raised.": (
        "A cross-reference. It reads like a claim only through the linked page's title; the claims live on that page."
    ),
    "It's the most portable mode and the best choice for deterministic, offline examples and tests.": (
        "A recommendation about which mode to pick for examples. A preference, not a capability."
    ),
    "**[Core Concepts](concepts/index.md)** — the adapter pattern, the `init_assembly()` lifecycle, and the modes/enforcement model.": (
        "A Next-steps link item. Its anchor text lists topics covered elsewhere; the claims live on the Core Concepts page and are gated there."
    ),
    "**[Examples](examples/index.md)** — wire the SDK into the framework you actually use.": (
        "A Next-steps link item pointing at the examples index. An invitation to read further, asserting nothing about enforcement."
    ),
    "**[Configuration](configuration.md)** — drop the hard-coded URL and key; let the resolver chain find them.": (
        "A Next-steps link item about configuration ergonomics: where the URL and key come from, not what governance does."
    ),
    "The example passes a `gateway_url`, and running it offline means nothing is listening there.": (
        "AAASM-5661. Describes the example's own setup — an argument it passes and a listener the reader was told not to start. A premise for the bound sentences that follow, asserting nothing the SDK does with it."
    ),
    "[Point the SDK at a gateway](#2-point-the-sdk-at-a-gateway) above covers the other half of that setup.": (
        "AAASM-5661. A cross-reference to §2 of this same page. It names where the gateway setup is written down and asserts nothing about what the SDK evaluates, denies or observes once one is running."
    ),
}


_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCE = re.compile(r"```.*?```", re.DOTALL)
#: Comments are invisible to a reader, so a bound claim commented out of the
#: rendered page must not still satisfy this gate. Stripped for the same reason
#: fences are. Go's CommonMark HTML blocks and MDX's {/* */} form are covered
#: too, so the three gates strip the same things.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_MDX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.DOTALL)
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*+]|\d+\.)\s+")
_UNIT_SPLIT = re.compile(r"(?m)^(?=\s*(?:[-*+]|\d+\.)\s|\|)")
#: '.' and '?' only. '!' is not a terminator here because mkdocs admonitions
#: open with '!!! note', which would otherwise split into a bare '!!!' unit.
#:
#: Closing markup between the terminator and the space is consumed WITH the
#: sentence, so "**Tool calls were governed.**" is its own unit instead of
#: running into the sentence after it. Without that, a bold lead-in label and
#: the claim it introduces were one string, and binding the pair covered both.
#: A backtick is deliberately NOT in the trailing class: inline code such as
#: `phi.*` would otherwise be read as a sentence end and split mid-sentence.
_SENTENCE_END = re.compile(r"[.?][*)\]_\"']*(?=\s)")


def _split_sentences(unit: str) -> list[str]:
    """Split on terminators, keeping the terminator and its markup attached."""
    out: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(unit):
        out.append(unit[start : match.end()])
        start = match.end()
    out.append(unit[start:])
    return out


def _document() -> str:
    """Read the quick-start with line endings normalised to LF.

    Without this the paragraph split never fires on a CRLF checkout and the
    whole section collapses into one sentence. Node's Windows CI legs caught
    exactly that; this repo's CI is Linux-only, so it is latent here.
    """
    return _QUICK_START.read_text(encoding="utf-8").replace("\r\n", "\n")


def _scanned_occurrences() -> list[tuple[str, str]]:
    """Return every ``(sentence, section)`` OCCURRENCE in the whole document.

    A list, not a dict. Keying by sentence collapsed duplicates before anything
    counted them, so ``matched == 1`` could only ever be 0 or 1 and a bound true
    sentence could be pasted into a section that inverts its meaning — an
    observe-mode block, a "what not to do" block — and still count once. Section
    attribution was last-write-wins for the same reason.

    No section is skipped. A section-level exclusion was a black hole: the guard
    checked the heading still existed and said nothing about its contents, so a
    claim inserted into an excluded section was never scanned.
    """
    body = _document()
    body = _FRONT_MATTER.sub("", body)
    for pattern in (_FENCE, _HTML_COMMENT, _MDX_COMMENT):
        body = pattern.sub("\n\n", body)

    occurrences: list[tuple[str, str]] = []
    section = "(preamble)"
    for chunk in re.split(r"(?m)^(#{1,6} .*)$", body):
        if chunk is None:
            continue
        if re.match(r"^#{1,6} ", chunk):
            section = chunk.strip()
            continue
        for paragraph in chunk.split("\n\n"):
            for unit in _UNIT_SPLIT.split(paragraph):
                for raw in _split_sentences(_LIST_MARKER.sub("", unit)):
                    flat = re.sub(r"\s+", " ", raw).strip()
                    if flat:
                        occurrences.append((flat, section))
    return occurrences


def _control_node_ids() -> set[str]:
    """Extract control ids from the control modules' ASTs, not transcribed."""
    node_ids: set[str] = set()
    for module in _CONTROL_MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name.startswith("test_"):
                        node_ids.add(f"{node.name}::{child.name}")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                node_ids.add(node.name)
    return node_ids


class TestTheGateCanSeeWhatItGates:
    """Positive controls. An empty parse and a clean result look identical."""

    def test_the_whole_document_is_read_and_split(self) -> None:
        assert len(_scanned_occurrences()) > 60, "too few sentences parsed from the whole quick-start"

    def test_the_scan_covers_every_section_including_the_last(self) -> None:
        sections = {section for _, section in _scanned_occurrences()}
        assert len(sections) >= 6, f"the scan reached only {len(sections)} sections: {sections}"
        assert "## Next steps" in sections, (
            "'## Next steps' is not in the scan. It used to be excluded by name, which made it "
            "a black hole: a claim inserted there was never seen. It must be scanned."
        )

    def test_comments_are_stripped_before_scanning(self) -> None:
        """A commented-out sentence must not satisfy a binding.

        Positive control for the strip: the document contains HTML comments, and
        none of their content may appear in the scan.
        """
        assert "<!--" in _document(), (
            "this control assumes the quick-start still contains an HTML comment; if it no "
            "longer does, re-point it rather than deleting it"
        )
        joined = " ".join(sentence for sentence, _ in _scanned_occurrences())
        assert "BEGIN GENERATED" not in joined, "HTML comment content leaked into the scan"

    def test_the_ast_extraction_finds_controls_in_every_control_module(self) -> None:
        node_ids = _control_node_ids()
        assert "TestFilesystemSideEffect::test_negative_control_denied_write_leaves_no_file" in node_ids
        assert "test_context_manager_shutdown_calls_adapter_unregister_hooks" in node_ids


class TestEverySentenceIsAccountedFor:
    def test_no_sentence_is_unbound_and_unallowed(self) -> None:
        """Every sentence must be bound or allow-listed. There is no third state.

        This is the inversion. A keyword filter cannot be completed, because
        whoever adds a claim picks the words after reading the filter — review
        appended three plain sentences, one of them the negation of the product,
        and every keyword-gated revision of this gate stayed green.
        """
        quotes = {binding.quote for binding in BINDINGS}
        loose = {
            sentence: section
            for sentence, section in _scanned_occurrences()
            if sentence not in quotes and sentence not in _ALLOWED
        }
        rendered = "\n".join(
            f"  [{'CLAIM-LIKE' if _ENFORCEMENT_VOCABULARY.search(s) else 'prose'}] [{sec}] {s}"
            for s, sec in loose.items()
        )
        assert not loose, (
            f"{len(loose)} sentence(s) in {_QUICK_START.name} are neither bound nor "
            f"allow-listed:\n{rendered}\n\n"
            "CLAIM-LIKE marks sentences matching the enforcement vocabulary — a severity "
            "hint only; a sentence marked 'prose' can still be a claim, which is exactly "
            "why this gate does not filter on the vocabulary.\n\n"
            "For each: add a ClaimBinding whose quote is the WHOLE sentence and which names "
            "the control that proves it (or an unproven_reason naming a ticket), or add it "
            "to _ALLOWED with a category. A sentence matching the vocabulary needs a written "
            "justification in _ALLOWED, not a category."
        )

    def test_nothing_is_both_bound_and_allowed(self) -> None:
        overlap = {binding.quote for binding in BINDINGS} & set(_ALLOWED)
        assert not overlap, f"these sentences are both bound and allow-listed: {overlap}"

    @pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.claim_id)
    def test_each_binding_matches_exactly_one_whole_sentence(self, binding: ClaimBinding) -> None:
        matches = [(text, section) for text, section in _scanned_occurrences() if text == binding.quote]
        assert len(matches) == 1, (
            f"ClaimBinding {binding.claim_id!r} must match exactly one whole sentence occurrence "
            f"in {_QUICK_START.name}; it matched {len(matches)} "
            f"(sections: {[section for _, section in matches]}).\n"
            f"Its quote is:\n  {binding.quote!r}\n"
            "0 means the claim was reworded, split, merged, or commented out — update the quote "
            "and re-check the named controls.\n"
            "More than 1 means the same sentence now appears in more than one place. That is not "
            "harmless: a true claim pasted into a section that inverts its meaning — an "
            "observe-mode block, a 'what not to do' block — would otherwise be counted by this "
            "binding as though it were still the sentence that was proven."
        )

    def test_no_two_bindings_claim_the_same_sentence(self) -> None:
        quotes = [b.quote for b in BINDINGS]
        duplicates = {q for q in quotes if quotes.count(q) > 1}
        assert not duplicates, f"more than one ClaimBinding quotes the same sentence: {duplicates}"


class TestTheAllowListCannotBecomeABypass:
    def test_every_allowed_sentence_is_still_present_verbatim(self) -> None:
        """A stale entry cannot silently exempt a reworded claim."""
        scanned = {text for text, _ in _scanned_occurrences()}
        stale = [s for s in _ALLOWED if s not in scanned]
        assert not stale, (
            "_ALLOWED contains sentences that no longer appear in the quick-start:\n"
            + "\n".join(f"  {s!r}" for s in stale)
            + "\nThey were reworded or removed. Delete the stale entries, and if a replacement "
            "makes a capability claim, bind it."
        )

    def test_only_structural_lines_may_use_the_bare_constant(self) -> None:
        """Every prose entry needs a written justification — not only claim-like ones.

        The previous rule keyed off the enforcement vocabulary, which is exactly
        backwards. A sentence that *matches* the vocabulary has already warned
        the author; a sentence that *evades* it has not, and evading it is the
        whole reason this scan was inverted. So the bare constant is now
        available only to structurally non-prose lines.
        """
        offenders = {
            sentence
            for sentence, reason in _ALLOWED.items()
            if reason == _STRUCTURAL and not _STRUCTURAL_LINE.match(sentence)
        }
        assert not offenders, (
            "These allow-listed sentences are prose but are waved through with the bare "
            "_STRUCTURAL constant:\n" + "\n".join(f"  {s!r}" for s in offenders) + "\n"
            "Replace it with a written justification saying why this particular sentence makes "
            "no capability claim, or bind it."
        )

    def test_no_allow_listed_sentence_turns_mid_way(self) -> None:
        """An allow-listed sentence may not contain a contrastive conjunction.

        A sentence can under-claim and over-claim at once, and a justification
        arguing "this only says what the product does NOT do" cannot be trusted
        for one that turns. Split the affirmative clause out and bind it.
        """
        offenders = {sentence for sentence in _ALLOWED if _CONTRASTIVE_CONJUNCTION.search(sentence)}
        assert not offenders, (
            "These allow-listed sentences contain a contrastive conjunction, so part of each "
            "may be an affirmative capability claim riding along under the justification:\n"
            + "\n".join(f"  {s!r}" for s in offenders)
            + "\nSplit the affirmative clause into its own sentence and bind it to the controls "
            "that prove it."
        )

    def test_no_allow_listed_sentence_reassures_across_a_negation(self) -> None:
        """A negated clause followed by an un-negated one, joined by "so".

        "Network-layer interception is not enabled by default, so the in-process
        adapter verifies every outbound request before it leaves the host
        instead." reads as a limitation and asserts a capability the product
        does not have. The contrastive list above does not catch it, because
        "so" is not adversative — it is the reassurance that follows a denial,
        which is precisely where an unbacked claim hides.
        """
        offenders = []
        for sentence in _ALLOWED:
            for match in _SO.finditer(sentence):
                before, after = sentence[: match.start()], sentence[match.end() :]
                if _NEGATION.search(before) and not _NEGATION.search(after):
                    offenders.append(sentence)
                    break
        assert not offenders, (
            'These allow-listed sentences negate something and then say "so ..." without a '
            "second negation — the shape of a limitation followed by a reassurance, where the "
            "reassurance may be an unbacked capability claim:\n"
            + "\n".join(f"  {s!r}" for s in offenders)
            + '\nSplit the clause after "so" into its own sentence and bind it to the controls '
            "that prove it."
        )

    def test_written_justifications_are_substantial_and_distinct(self) -> None:
        """A cheap partial, and it is only that.

        No gate can tell a justification from noise — ``reason="x"`` is prose to
        a computer. Requiring length and uniqueness does not fix that; it only
        makes the two cheapest ways of waving something through, an empty
        gesture and a copy-paste, visible in review.
        """
        written = {s: r for s, r in _ALLOWED.items() if r != _STRUCTURAL}
        too_short = {s: r for s, r in written.items() if len(r) < _MIN_JUSTIFICATION}
        assert not too_short, f"These justifications are shorter than {_MIN_JUSTIFICATION} characters:\n" + "\n".join(
            f"  {r!r} for {s!r}" for s, r in too_short.items()
        )
        seen: dict[str, str] = {}
        duplicates: list[str] = []
        for sentence, reason in written.items():
            if reason in seen:
                duplicates.append(f"{reason!r}\n    used by {seen[reason]!r}\n    and by {sentence!r}")
            seen[reason] = sentence
        assert not duplicates, (
            "The same justification is reused for different sentences:\n"
            + "\n".join(f"  {d}" for d in duplicates)
            + "\nA justification explains one specific sentence. Reuse is copy-paste waving."
        )

    def test_every_allow_list_reason_is_non_empty(self) -> None:
        empty = [s for s, reason in _ALLOWED.items() if not reason.strip()]
        assert not empty, f"allow-list entries with no reason: {empty}"


class TestEveryBindingNamesSomethingReal:
    @pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.claim_id)
    def test_named_controls_exist(self, binding: ClaimBinding) -> None:
        available = _control_node_ids()
        missing = [c for c in binding.controls if c not in available]
        assert not missing, (
            f"ClaimBinding {binding.claim_id!r} names controls that do not exist: {missing}\n"
            "The control was renamed or removed. Re-point the binding, or mark the claim "
            "unproven and name the ticket."
        )

    @pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.claim_id)
    def test_a_claim_is_either_proven_or_openly_unproven(self, binding: ClaimBinding) -> None:
        assert binding.controls or binding.unproven_reason, (
            f"Claim {binding.claim_id!r} names no control and gives no unproven_reason. A "
            "documented claim with neither is the unbacked assertion AAASM-5526 exists to "
            "eliminate."
        )
        if not binding.controls:
            assert re.search(r"AAASM-\d+", binding.unproven_reason), (
                f"Claim {binding.claim_id!r} is unproven but names no ticket. Reason given: {binding.unproven_reason!r}"
            )

    @pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.claim_id)
    def test_an_unproven_reason_does_not_name_an_implementing_ticket(self, binding: ClaimBinding) -> None:
        """An unproven claim may not point at a ticket that closes it.

        A reason naming one of this module's own tickets resolves to a *closed*
        issue the moment that work merges, and nothing would notice: the
        ticket-shaped check above is satisfied by any AAASM-nnnn, open or not.

        Checked against every entry, not only the first (AAASM-5661). A
        single-value rule bans the author who wrote the rule and nobody after
        them, which is the one author who was never going to break it.
        """
        named = [ticket for ticket in IMPLEMENTING_TICKETS if ticket in binding.unproven_reason]
        assert not named, (
            f"Claim {binding.claim_id!r} is registered unproven against {named}, which this "
            "module implements. On merge that pointer resolves to a closed issue and the "
            "claim is silently orphaned. Name the ticket that will actually resolve it, or "
            "file one."
        )

    @pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.claim_id)
    def test_named_sdk_symbols_resolve_to_that_name(self, binding: ClaimBinding) -> None:
        for documented_name, module_path in binding.symbols.items():
            module = importlib.import_module(module_path)
            resolved = getattr(module, documented_name, None)
            assert resolved is not None, (
                f"{_QUICK_START.name} names `{documented_name}` but that symbol no longer "
                f"exists in {module_path}. The documented quick-start now points at something "
                "a reader cannot import."
            )
            assert resolved.__name__ == documented_name, (
                f"{_QUICK_START.name} names {documented_name!r} but the resolved symbol "
                f"reports __name__ == {resolved.__name__!r}."
            )
