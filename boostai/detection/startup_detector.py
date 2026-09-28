"""Startup load analysis. Only OPTIONAL / HIGH_IMPACT items are ever proposed for disabling."""

from __future__ import annotations

from boostai.actions.action_models import ActionID
from boostai.core.issues import ActionProposal, Confidence, Issue, IssueType, RootCause, Severity
from boostai.detection.base import DetectionContext
from boostai.knowledge.startup_catalog import StartupCategory, assess


def detect(ctx: DetectionContext) -> list[Issue]:
    enabled = [i for i in ctx.startup_items if i.enabled]
    if not enabled:
        return []
    assessed = [(item, assess(item, ctx.snapshot.processes)) for item in enabled]
    heavy = [(i, a) for i, a in assessed if a.category == StartupCategory.HIGH_IMPACT and i.item_id not in ctx.startup_keep_ids]
    optional = [(i, a) for i, a in assessed if a.category == StartupCategory.OPTIONAL and i.item_id not in ctx.startup_keep_ids]
    unknown = [(i, a) for i, a in assessed if a.category == StartupCategory.UNKNOWN]
    if not heavy and len(optional) < 3:
        return []

    proposals = [
        ActionProposal(
            action_id=ActionID.DISABLE_STARTUP_ITEM.value, params={"item_id": item.item_id},
            label=f"Disable {item.display_name} at startup",
            rationale=f"{a.reason} Estimated impact: {a.impact} ({a.impact_basis}).",
        )
        for item, a in heavy + optional
    ]
    evidence = [f"{len(enabled)} programs start automatically when you sign in."]
    if heavy:
        evidence.append("High impact: " + ", ".join(f"{i.display_name} ({a.impact_basis})" for i, a in heavy))
    if optional:
        evidence.append("Optional: " + ", ".join(i.display_name for i, _ in optional))
    if unknown:
        evidence.append("Unrecognised (manual review required): " + ", ".join(i.display_name for i, _ in unknown))
    return [Issue(
        key=IssueType.STARTUP_LOAD.value, type=IssueType.STARTUP_LOAD,
        title=f"{len(heavy) + len(optional)} optional programs start with Windows",
        severity=Severity.MEDIUM if len(heavy) >= 3 else Severity.LOW,
        confidence=Confidence.MEDIUM, root_cause=RootCause.CONFIGURATION, component="Startup programs",
        evidence=evidence,
        metrics={"enabled": len(enabled), "high_impact": len(heavy), "optional": len(optional)},
        explanation=("Programs that start automatically slow down sign-in and keep using memory and CPU in the "
                     "background. Disabling them does not uninstall anything; you can still open them normally, and "
                     "BoostAI can re-enable them at any time."),
        proposals=proposals,
        manual_steps=["Unrecognised items are never disabled automatically; review them in the Startup page."]
        if unknown else [],
        expected_effect="Faster sign-in and less background memory use from the next sign-in onward.",
    )]
