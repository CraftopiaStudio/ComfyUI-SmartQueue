from datetime import datetime, timedelta, timezone

from backend.queue_eta import estimate_remaining_seconds

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def _item(prompt_id, started_seconds_ago=None):
    started_at = None
    if started_seconds_ago is not None:
        started_at = (NOW - timedelta(seconds=started_seconds_ago)).isoformat()
    return {"prompt_id": prompt_id, "started_at": started_at}


def test_no_estimate_without_any_finished_job_to_learn_from():
    assert estimate_remaining_seconds([_item("a")], set(), [], NOW) is None


def test_no_estimate_when_nothing_is_queued():
    assert estimate_remaining_seconds([], set(), [60.0], NOW) is None


def test_waiting_jobs_each_count_as_the_median_duration():
    durations = [30.0, 60.0, 600.0]  # one slow outlier must not drag the median
    items = [_item("a"), _item("b")]

    assert estimate_remaining_seconds(items, set(), durations, NOW) == 120.0


def test_running_job_counts_only_its_expected_remainder():
    items = [_item("a", started_seconds_ago=20), _item("b")]

    assert estimate_remaining_seconds(items, {"a"}, [60.0], NOW) == 100.0


def test_running_job_past_the_median_counts_as_almost_done():
    items = [_item("a", started_seconds_ago=500)]

    assert estimate_remaining_seconds(items, {"a"}, [60.0], NOW) == 0.0


def test_running_job_without_a_start_time_counts_in_full():
    assert estimate_remaining_seconds([_item("a")], {"a"}, [60.0], NOW) == 60.0
