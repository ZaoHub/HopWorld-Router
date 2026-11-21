"""Rank node probe results using the target priority selected by the user."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeResult:
    node_id: str
    title: str
    passed_targets: frozenset[str]
    latency_ms: int | None


def rank_candidates(results: list[ProbeResult], target_order: list[str]) -> list[ProbeResult]:
    """Keep nodes passing any selected target; prioritize earlier targets, then latency."""
    selected = set(target_order)
    candidates = [result for result in results if result.passed_targets & selected]
    return sorted(
        candidates,
        key=lambda result: (
            tuple(target not in result.passed_targets for target in target_order),
            result.latency_ms if result.latency_ms is not None else float("inf"),
            result.title.casefold(),
            result.node_id,
        ),
    )
