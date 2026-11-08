"""Per-tenant routing policy: ``local_only`` (default, GDPR),
``local_first_cloud_fallback``,
``cloud_first``. Cloud is used only when the firm's license carries ``cloud_ai`` AND the firm opted in."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.pipeline.extract.merge import merge_results, min_header_confidence
from app.pipeline.types import DocumentText, ExtractionContext, ExtractionProvider, ExtractionResult

log = logging.getLogger("invoiceflow.pipeline.router")

POLICIES = ("local_only", "local_first_cloud_fallback", "cloud_first")


@dataclass(slots=True)
class RoutingDecision:
    policy: str
    cloud_allowed: bool
    used: list[str]
    skipped: list[str]


class ExtractionRouter:
    def __init__(
        self,
        rules: ExtractionProvider,
        local_llm: ExtractionProvider | None,
        cloud: ExtractionProvider | None,
    ) -> None:
        self.rules = rules
        self.local_llm = local_llm
        self.cloud = cloud

    async def run(
        self,
        text: DocumentText,
        ctx: ExtractionContext,
        *,
        policy: str,
        cloud_allowed: bool,
        threshold: float,
    ) -> tuple[ExtractionResult, RoutingDecision]:
        policy = policy if policy in POLICIES else "local_only"
        used: list[str] = []
        skipped: list[str] = []
        results: list[ExtractionResult] = []

        async def try_provider(p: ExtractionProvider | None, label: str) -> ExtractionResult | None:
            if p is None:
                skipped.append(f"{label}:not_configured")
                return None
            if not await p.available():
                skipped.append(f"{label}:unavailable")
                return None
            try:
                r = await p.extract(text, ctx)
            except Exception as e:  # noqa: BLE001 - a failing provider never fails the document
                log.warning("provider %s failed: %s", label, type(e).__name__)
                skipped.append(f"{label}:error")
                return None
            used.append(label)
            results.append(r)
            return r

        rules_result = await try_provider(self.rules, "rules")
        assert rules_result is not None  # the rules provider always runs
        cloud_ok = cloud_allowed and self.cloud is not None
        if policy == "cloud_first" and cloud_ok:
            await try_provider(self.cloud, "llm_cloud")
            if not results[1:]:
                await try_provider(self.local_llm, "llm_local")
        else:
            await try_provider(self.local_llm, "llm_local")
            merged = merge_results(results)
            if (
                policy == "local_first_cloud_fallback"
                and cloud_ok
                and min_header_confidence(merged.invoice) < threshold
            ):
                await try_provider(self.cloud, "llm_cloud")
            elif policy != "local_only" and not cloud_allowed:
                skipped.append("llm_cloud:not_entitled_or_not_opted_in")
            elif policy != "local_only" and self.cloud is None:
                skipped.append("llm_cloud:not_configured")
        return merge_results(results), RoutingDecision(policy, cloud_allowed, used, skipped)
