"""Rough time-left estimate for the whole queue, shown in the panel status."""

from __future__ import annotations

from datetime import datetime
from statistics import median


def estimate_remaining_seconds(
    items: list[dict],
    running_ids: set[str],
    recent_durations: list[float],
    now: datetime,
) -> float | None:
    """Every unfinished job is assumed to take the median of recent jobs.

    Median rather than mean: one overnight upscale in the history would
    otherwise inflate every estimate for days. A running job only counts the
    part of that median it has not spent yet, floored at zero, so a job that
    runs long reads as "almost done" instead of making the total go negative.
    """
    if not items or not recent_durations:
        return None

    typical = median(recent_durations)
    remaining = 0.0
    for item in items:
        started_at = item.get("started_at")
        if item["prompt_id"] in running_ids and started_at:
            elapsed = (now - datetime.fromisoformat(started_at)).total_seconds()
            remaining += max(typical - elapsed, 0.0)
        else:
            remaining += typical
    return remaining
