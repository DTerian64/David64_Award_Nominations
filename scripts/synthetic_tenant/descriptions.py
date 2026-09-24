"""Deterministic, category-grounded v5 nomination stories.

Each category has 3,000 distinct story combinations before intentional
three-person exact-reuse groups are applied. Names and award amounts come from
the nomination row; no label or scenario metadata is an input to this module.
"""

from __future__ import annotations

import hashlib


# Each entry has ten compatible contexts, actions, and outcomes. Keeping the
# language category-specific avoids a generic sentence shared by every award.
STORIES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "Innovation & Problem Solving": (
        (
            "during a handoff review", "while investigating recurring delays",
            "during a release retrospective", "after a service interruption",
            "while simplifying intake", "during a capacity review",
            "while testing a new workflow", "during a data-quality review",
            "after a process bottleneck surfaced", "while preparing a pilot",
        ),
        (
            "mapped the root cause and tested a smaller approval path",
            "built a reusable check for inconsistent records",
            "prototyped an alternative to manual reconciliation",
            "redesigned the escalation steps with the affected teams",
            "removed duplicate work from the intake sequence",
            "created a clearer way to track exceptions",
            "tested a lightweight automation with frontline feedback",
            "translated a recurring issue into a practical checklist",
            "compared several approaches before changing the process",
            "documented a repeatable solution for an intermittent failure",
        ),
        (
            "colleagues could resolve similar cases with fewer handoffs",
            "the team gained a more reliable way to spot exceptions",
            "follow-up work became easier to prioritize",
            "the pilot gave other teams a concrete pattern to reuse",
            "the revised process reduced avoidable rework",
            "service owners had a clearer path for unusual cases",
            "the next cycle ran with fewer manual corrections",
            "the change made the workflow easier to explain and maintain",
            "the team could address the issue before it spread",
            "the improvement left a useful record for future releases",
        ),
    ),
    "Teamwork & Collaboration": (
        (
            "during a cross-team launch", "while coordinating a shared deadline",
            "during a difficult handoff", "while supporting a new partner team",
            "during a multi-department review", "while resolving ownership gaps",
            "during a busy delivery week", "while aligning competing priorities",
            "during a customer-facing rollout", "while bringing two teams together",
        ),
        (
            "brought the right contributors into a working session",
            "clarified ownership and kept the shared plan current",
            "made space for different teams to surface risks early",
            "connected colleagues who had complementary expertise",
            "coordinated a practical handoff across functions",
            "shared timely updates that kept dependencies visible",
            "helped the group agree on an achievable sequence",
            "resolved a misunderstanding before it delayed delivery",
            "organized a common view of open decisions",
            "supported another team without losing sight of the joint goal",
        ),
        (
            "the teams completed their shared work with clearer ownership",
            "colleagues had fewer surprises at the next handoff",
            "the group maintained momentum through a complex milestone",
            "contributors could make decisions using the same information",
            "the joint delivery was easier to coordinate",
            "team members knew where to ask for help",
            "the work moved forward without leaving a partner team behind",
            "a common plan reduced duplicated effort",
            "the handoff became more dependable for everyone involved",
            "the collaboration produced a result neither team could deliver alone",
        ),
    ),
    "Leadership & Mentorship": (
        (
            "while onboarding a new colleague", "during a skills-sharing session",
            "while a teammate learned a new system", "during a project transition",
            "while coaching a first-time lead", "during regular peer reviews",
            "while preparing a succession handoff", "during a team learning cycle",
            "while supporting a developing specialist", "during a demanding milestone",
        ),
        (
            "explained the reasoning behind a difficult decision",
            "paired with a colleague on a real work example",
            "offered specific feedback and room to practice",
            "created a short guide from lessons learned",
            "invited quieter contributors into the discussion",
            "helped a teammate plan the next steps independently",
            "shared context that made unfamiliar work approachable",
            "modeled a calm response to a complex question",
            "followed up after training to answer practical questions",
            "gave a colleague ownership with constructive support",
        ),
        (
            "the colleague could take on similar work with more confidence",
            "the team retained knowledge that might otherwise have been lost",
            "new contributors had a clearer route to independence",
            "the group developed a stronger shared practice",
            "the handoff left both teams better prepared",
            "colleagues had an example they could apply again",
            "the teammate gained confidence without being left alone",
            "the team could spread responsibility more fairly",
            "the learning carried into later assignments",
            "the support improved both delivery and professional growth",
        ),
    ),
    "Customer Excellence": (
        (
            "during a difficult customer handoff", "after a service concern was raised",
            "while preparing a customer rollout", "during an urgent support request",
            "while reviewing customer feedback", "during an implementation call",
            "after an unexpected delivery change", "while clarifying a customer need",
            "during a renewal conversation", "while following up on a complex case",
        ),
        (
            "listened carefully and clarified the customer's priority",
            "coordinated a clear response with the delivery team",
            "kept the customer informed while the issue was investigated",
            "translated technical constraints into useful options",
            "followed through on the details of a promised solution",
            "identified the next practical step and its owner",
            "brought the right specialist into the conversation",
            "checked that the proposed fix met the actual need",
            "documented the resolution for future support",
            "reframed a difficult request into a workable plan",
        ),
        (
            "the customer had a clearer path to resolution",
            "the response restored confidence in the team's follow-through",
            "the implementation moved ahead with fewer open questions",
            "the customer could make an informed decision",
            "the issue was resolved without losing important context",
            "the service team had a better record for future cases",
            "the customer received a timely and understandable update",
            "the next handoff was smoother for the customer",
            "the outcome addressed the underlying concern, not just the symptom",
            "the relationship benefited from consistent communication",
        ),
    ),
    "Going Above & Beyond": (
        (
            "during an unusually busy week", "when a teammate needed extra help",
            "after an unexpected schedule change", "during a time-sensitive request",
            "while another team faced a backlog", "during a critical handoff",
            "when a recurring task became urgent", "during a resource gap",
            "while preparing for an important deadline", "after a late issue surfaced",
        ),
        (
            "volunteered to coordinate the remaining work",
            "made time to help colleagues finish a difficult task",
            "took ownership of a neglected follow-up",
            "checked the details that might otherwise be missed",
            "stepped in to keep a handoff moving",
            "offered practical support outside their usual assignment",
            "stayed engaged until the outstanding questions were answered",
            "helped divide the work into manageable next steps",
            "covered an urgent gap while preserving quality",
            "followed through after the immediate pressure had passed",
        ),
        (
            "the team met its commitment without leaving loose ends",
            "colleagues had the support they needed to complete the work",
            "the extra effort prevented a small issue from becoming a delay",
            "the handoff remained dependable under pressure",
            "the group could focus on the highest-priority work",
            "the deadline was met with fewer last-minute surprises",
            "the team gained breathing room to finish carefully",
            "the contribution helped another person succeed",
            "the outstanding work received timely attention",
            "the team could close the loop on a difficult request",
        ),
    ),
}


def description_for_nomination(
    category: str, ordinal: int, seed: int, beneficiary_name: str, amount: int,
) -> str:
    """Generate a category-grounded story for the actual nominee and amount."""
    contexts, actions, outcomes = STORIES[category]
    if (len(contexts), len(actions), len(outcomes)) != (10, 10, 10):
        raise ValueError(f"Incomplete description bank for {category}")
    if ordinal < 0 or ordinal >= 3000:
        raise ValueError(f"Category ordinal outside v5 corpus: {ordinal}")
    # Multiplication by 997 permutes 0..2999; intentional exact-reuse groups
    # are applied later to whole, consistent nomination records.
    offset = int.from_bytes(
        hashlib.sha256(f"v5:text:{seed}:{category}".encode()).digest()[:4], "big"
    ) % 3000
    variant = (ordinal * 997 + offset) % 3000
    context = contexts[variant % 10]
    action = actions[(variant // 10) % 10]
    outcome = outcomes[(variant // 100) % 10]
    style = variant // 1000
    if style == 0:
        return (
            f"{context.capitalize()}, {beneficiary_name} {action}. "
            f"{outcome.capitalize()}. This ${amount:,} award recognizes that work."
        )
    if style == 1:
        return (
            f"We nominate {beneficiary_name} for a ${amount:,} award: "
            f"they {action} {context}, and {outcome}."
        )
    return (
        f"The ${amount:,} nomination for {beneficiary_name} reflects how "
        f"they {action} {context}. {outcome.capitalize()}."
    )
