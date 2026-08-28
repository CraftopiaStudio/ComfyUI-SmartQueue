# Smart Queue v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build v1 of Smart Queue — a ComfyUI custom node pack combining (a) a queue-level "autopilot" that pauses/resumes the render queue based on live GPU temperature/VRAM/job-count, and (b) a consolidated "Smart Cooldown & Pause" in-graph node that folds in cooldown timing, GPU temp waiting, manual pause, notifications, and model unloading.

**Architecture:** A non-intrusive aiohttp middleware gates ComfyUI's native `/prompt` endpoint with a 423 response while an in-memory `AutopilotState` says "paused"; a background asyncio loop periodically polls GPU metrics via `nvidia-smi` subprocess calls and updates that state through a pure, independently-testable rule-engine function. SQLite persists queue order/history across restarts. The node is built on ComfyUI's V3 (`io.ComfyNode`) schema. No third-party Python dependencies.

**Tech Stack:** Python 3.12+ (stdlib `sqlite3`, `asyncio`, `subprocess`), `aiohttp` (already a ComfyUI dependency, not added by this pack), `pytest` for tests, vanilla JS for the frontend (no build step, matching ComfyUI extension conventions).

**Spec:** `docs/specs/2026-08-28-smart-queue-design.md`

## Global Constraints

- **Fail-open always**: any exception in GPU polling, the rule engine, or the middleware disables the affected feature and logs a warning — it must never block the queue or crash the server. (Spec §4, §9)
- **Never interrupt a running job**: autopilot only blocks the *next* `/prompt` call, never the currently executing one. (Spec §4)
- **No hijacking**: the middleware only intercepts `/prompt` while paused and returns HTTP 423; it never overrides or replaces a native ComfyUI route. (Spec §5)
- **No third-party Python dependencies**: GPU data comes only from an `nvidia-smi` subprocess call, same pattern as the existing `rubz-gpu-cooldown` node. (Spec §2, §3)
- **Node backward compatibility**: the consolidated node's internal type ID must stay `RubzGpuCooldownNode` (display name is "Smart Cooldown & Pause") so existing saved workflows keep resolving. (Spec §8)
- **Node built on V3 schema**: `io.ComfyNode` + `define_schema()`, not the legacy `INPUT_TYPES` dict pattern. (Spec §11)
- **Settings default ON**: the autopilot master toggle and all three rule toggles default to enabled; thresholds default to conservative values that rarely trigger unless genuinely needed. (Spec §7)
- **No hard dependency on Crystools**: Smart Queue always polls its own GPU metrics; Crystools detection only changes what the panel displays, never the autopilot logic. (Spec §6, §12)

---

## File Structure

```
comfyui-smart-queue/
├── __init__.py                        # modify — wires everything together
├── backend/
│   ├── __init__.py                    # new — empty, makes backend a package
│   ├── gpu_monitor.py                 # new — Task 1
│   ├── autopilot.py                   # new — Task 2
│   ├── autopilot_state.py             # new — Task 3
│   ├── persistence.py                 # new — Task 4
│   ├── queue_middleware.py            # new — Task 5
│   ├── autopilot_loop.py              # new — Task 6
│   ├── routes.py                      # new — Task 8
│   └── nodes/
│       ├── __init__.py                # new — empty
│       └── cooldown.py                # new — Task 7
├── web/
│   ├── smart_queue.js                 # new — Task 10 (manual verification)
│   ├── smart_queue.css                # new — Task 10 (manual verification)
│   └── smart_queue_node.js            # new — Task 11 (manual verification)
└── tests/
    ├── test_smoke.py                  # existing, untouched
    ├── test_gpu_monitor.py            # new — Task 1
    ├── test_autopilot.py              # new — Task 2
    ├── test_autopilot_state.py        # new — Task 3
    ├── test_persistence.py            # new — Task 4
    ├── test_queue_middleware.py       # new — Task 5
    ├── test_autopilot_loop.py         # new — Task 6
    ├── test_cooldown_node.py          # new — Task 7
    └── test_routes.py                 # new — Task 8
```

---

### Task 1: GPU metrics polling

**Files:**
- Create: `backend/__init__.py` (empty)
- Create: `backend/gpu_monitor.py`
- Test: `tests/test_gpu_monitor.py`

**Interfaces:**
- Produces: `GpuMetrics` dataclass with fields `temp_c: float | None`, `vram_used_mb: float | None`, `vram_total_mb: float | None`, `util_pct: float | None`, and a `vram_free_mb` property. `poll_gpu_metrics(timeout: float = 5.0) -> GpuMetrics`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_gpu_monitor.py
import subprocess
from unittest.mock import patch, MagicMock

from backend.gpu_monitor import GpuMetrics, poll_gpu_metrics


def test_vram_free_mb_computed_from_used_and_total():
    metrics = GpuMetrics(temp_c=60.0, vram_used_mb=2000.0, vram_total_mb=8000.0, util_pct=10.0)
    assert metrics.vram_free_mb == 6000.0


def test_vram_free_mb_none_when_data_missing():
    metrics = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None, util_pct=None)
    assert metrics.vram_free_mb is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_parses_nvidia_smi_csv_output(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="62, 2048, 8192, 15\n",
    )
    metrics = poll_gpu_metrics()
    assert metrics.temp_c == 62.0
    assert metrics.vram_used_mb == 2048.0
    assert metrics.vram_total_mb == 8192.0
    assert metrics.util_pct == 15.0


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_returns_all_none_when_nvidia_smi_missing(mock_run):
    mock_run.side_effect = FileNotFoundError("nvidia-smi not found")
    metrics = poll_gpu_metrics()
    assert metrics.temp_c is None
    assert metrics.vram_free_mb is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_returns_all_none_on_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    metrics = poll_gpu_metrics()
    assert metrics.temp_c is None


@patch("backend.gpu_monitor.subprocess.run")
def test_poll_returns_all_none_on_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=5.0)
    metrics = poll_gpu_metrics()
    assert metrics.temp_c is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gpu_monitor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend'`

- [x] **Step 3: Write the implementation**

```python
# backend/gpu_monitor.py
"""Polls NVIDIA GPU metrics via nvidia-smi. No third-party dependencies."""

import subprocess
from dataclasses import dataclass


@dataclass
class GpuMetrics:
    temp_c: float | None
    vram_used_mb: float | None
    vram_total_mb: float | None
    util_pct: float | None

    @property
    def vram_free_mb(self) -> float | None:
        if self.vram_used_mb is None or self.vram_total_mb is None:
            return None
        return self.vram_total_mb - self.vram_used_mb


_EMPTY = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None, util_pct=None)


def poll_gpu_metrics(timeout: float = 5.0) -> GpuMetrics:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return _EMPTY

        first_line = result.stdout.strip().splitlines()[0]
        temp_s, used_s, total_s, util_s = (p.strip() for p in first_line.split(","))
        return GpuMetrics(
            temp_c=float(temp_s),
            vram_used_mb=float(used_s),
            vram_total_mb=float(total_s),
            util_pct=float(util_s),
        )
    except Exception:
        return _EMPTY
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gpu_monitor.py -v`
Expected: 6 passed

- [x] **Step 5: Commit**

```bash
git add backend/__init__.py backend/gpu_monitor.py tests/test_gpu_monitor.py
git commit -m "Add GPU metrics polling via nvidia-smi"
```

---

### Task 2: Autopilot rule engine (pure function)

**Files:**
- Create: `backend/autopilot.py`
- Test: `tests/test_autopilot.py`

**Interfaces:**
- Consumes: `GpuMetrics` from Task 1 (`backend.gpu_monitor`).
- Produces: `AutopilotSettings` dataclass, `Decision` dataclass (`should_pause: bool`, `reasons: tuple[str, ...]`), and `evaluate(metrics: GpuMetrics, jobs_since_resume: int, currently_paused: bool, settings: AutopilotSettings) -> Decision`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_autopilot.py
from backend.gpu_monitor import GpuMetrics
from backend.autopilot import AutopilotSettings, evaluate


def make_metrics(temp_c=50.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=5.0):
    return GpuMetrics(temp_c=temp_c, vram_used_mb=vram_used_mb, vram_total_mb=vram_total_mb, util_pct=util_pct)


def test_no_rules_triggered_returns_no_pause():
    settings = AutopilotSettings()
    decision = evaluate(make_metrics(), jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is False
    assert decision.reasons == ()


def test_temp_above_pause_threshold_triggers_pause():
    settings = AutopilotSettings(pause_temp_c=80.0, resume_temp_c=72.0)
    decision = evaluate(make_metrics(temp_c=85.0), jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is True
    assert "85" in decision.reasons[0]


def test_temp_hysteresis_keeps_paused_until_resume_threshold():
    settings = AutopilotSettings(pause_temp_c=80.0, resume_temp_c=72.0)
    # already paused, temp dropped below pause_temp_c but not yet below resume_temp_c
    decision = evaluate(make_metrics(temp_c=75.0), jobs_since_resume=0, currently_paused=True, settings=settings)
    assert decision.should_pause is True


def test_temp_hysteresis_resumes_once_below_resume_threshold():
    settings = AutopilotSettings(pause_temp_c=80.0, resume_temp_c=72.0)
    decision = evaluate(make_metrics(temp_c=70.0), jobs_since_resume=0, currently_paused=True, settings=settings)
    assert decision.should_pause is False


def test_temp_rule_disabled_never_pauses_on_temp():
    settings = AutopilotSettings(temp_rule_enabled=False, pause_temp_c=80.0)
    decision = evaluate(make_metrics(temp_c=99.0), jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is False


def test_low_free_vram_triggers_pause():
    settings = AutopilotSettings(min_free_vram_mb=1024.0)
    decision = evaluate(
        make_metrics(vram_used_mb=7800.0, vram_total_mb=8000.0),
        jobs_since_resume=0, currently_paused=False, settings=settings,
    )
    assert decision.should_pause is True
    assert "VRAM" in decision.reasons[0]


def test_vram_rule_disabled_never_pauses_on_vram():
    settings = AutopilotSettings(vram_rule_enabled=False, min_free_vram_mb=1024.0)
    decision = evaluate(
        make_metrics(vram_used_mb=7999.0, vram_total_mb=8000.0),
        jobs_since_resume=0, currently_paused=False, settings=settings,
    )
    assert decision.should_pause is False


def test_job_count_at_or_above_max_triggers_pause():
    settings = AutopilotSettings(max_jobs_before_pause=20)
    decision = evaluate(make_metrics(), jobs_since_resume=20, currently_paused=False, settings=settings)
    assert decision.should_pause is True
    assert "20" in decision.reasons[0]


def test_job_count_rule_disabled_never_pauses_on_count():
    settings = AutopilotSettings(job_count_rule_enabled=False, max_jobs_before_pause=1)
    decision = evaluate(make_metrics(), jobs_since_resume=999, currently_paused=False, settings=settings)
    assert decision.should_pause is False


def test_missing_gpu_data_does_not_trigger_temp_or_vram_rules():
    settings = AutopilotSettings()
    metrics = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None, util_pct=None)
    decision = evaluate(metrics, jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is False


def test_update_from_dict_mutates_matching_fields_only():
    settings = AutopilotSettings()
    settings.update_from_dict({"pause_temp_c": 90.0, "not_a_real_field": 123})
    assert settings.pause_temp_c == 90.0
    assert not hasattr(settings, "not_a_real_field")


def test_multiple_reasons_all_reported():
    settings = AutopilotSettings(pause_temp_c=80.0, min_free_vram_mb=1024.0)
    decision = evaluate(
        make_metrics(temp_c=90.0, vram_used_mb=7800.0, vram_total_mb=8000.0),
        jobs_since_resume=0, currently_paused=False, settings=settings,
    )
    assert len(decision.reasons) == 2
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_autopilot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.autopilot'`

- [x] **Step 3: Write the implementation**

```python
# backend/autopilot.py
"""Pure rule-engine: GPU metrics + queue state -> pause/resume decision.

No I/O here. This module never touches the network, the filesystem, or a
subprocess — that keeps it trivially unit-testable and lets the fail-open
guarantee live entirely in the caller (backend.autopilot_loop).
"""

from dataclasses import dataclass

from .gpu_monitor import GpuMetrics


@dataclass
class AutopilotSettings:
    master_enabled: bool = True

    temp_rule_enabled: bool = True
    pause_temp_c: float = 80.0
    resume_temp_c: float = 72.0

    vram_rule_enabled: bool = True
    min_free_vram_mb: float = 1024.0

    job_count_rule_enabled: bool = True
    max_jobs_before_pause: int = 20

    def update_from_dict(self, values: dict) -> None:
        """Mutates in place so callers holding a reference (the background
        loop, the middleware's is_enabled closure) see updates immediately."""
        for key, value in values.items():
            if hasattr(self, key):
                setattr(self, key, value)


@dataclass
class Decision:
    should_pause: bool
    reasons: tuple[str, ...]


def evaluate(
    metrics: GpuMetrics,
    jobs_since_resume: int,
    currently_paused: bool,
    settings: AutopilotSettings,
) -> Decision:
    reasons: list[str] = []

    if settings.temp_rule_enabled and metrics.temp_c is not None:
        threshold = settings.resume_temp_c if currently_paused else settings.pause_temp_c
        if metrics.temp_c > threshold:
            reasons.append(f"GPU at {metrics.temp_c:.0f}C, target {threshold:.0f}C")

    if settings.vram_rule_enabled and metrics.vram_free_mb is not None:
        if metrics.vram_free_mb < settings.min_free_vram_mb:
            reasons.append(
                f"Only {metrics.vram_free_mb:.0f}MB VRAM free, below {settings.min_free_vram_mb:.0f}MB"
            )

    if settings.job_count_rule_enabled and jobs_since_resume >= settings.max_jobs_before_pause:
        reasons.append(f"{jobs_since_resume} jobs run since last pause, cooldown break")

    return Decision(should_pause=len(reasons) > 0, reasons=tuple(reasons))
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_autopilot.py -v`
Expected: 12 passed

- [x] **Step 5: Commit**

```bash
git add backend/autopilot.py tests/test_autopilot.py
git commit -m "Add autopilot rule engine as a pure, testable function"
```

---

### Task 3: Autopilot state machine

**Files:**
- Create: `backend/autopilot_state.py`
- Test: `tests/test_autopilot_state.py`

**Interfaces:**
- Consumes: `Decision` from Task 2 (`backend.autopilot`).
- Produces: `AutopilotState` class with `is_paused: bool`, `jobs_since_resume: int`, `last_reasons: tuple[str, ...]` attributes, and a method `apply(decision: Decision) -> None` that mutates state (resets `jobs_since_resume` to 0 on transition from paused to resumed) plus a method `record_job_started() -> None` that increments `jobs_since_resume`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_autopilot_state.py
from backend.autopilot import Decision
from backend.autopilot_state import AutopilotState


def test_initial_state_is_not_paused():
    state = AutopilotState()
    assert state.is_paused is False
    assert state.jobs_since_resume == 0


def test_apply_pause_decision_sets_paused_and_stores_reasons():
    state = AutopilotState()
    state.apply(Decision(should_pause=True, reasons=("too hot",)))
    assert state.is_paused is True
    assert state.last_reasons == ("too hot",)


def test_apply_resume_decision_after_pause_resets_job_counter():
    state = AutopilotState()
    state.record_job_started()
    state.record_job_started()
    state.apply(Decision(should_pause=True, reasons=("cooldown",)))
    state.apply(Decision(should_pause=False, reasons=()))
    assert state.is_paused is False
    assert state.jobs_since_resume == 0


def test_record_job_started_increments_counter():
    state = AutopilotState()
    state.record_job_started()
    state.record_job_started()
    assert state.jobs_since_resume == 2


def test_apply_resume_when_already_resumed_does_not_reset_counter():
    state = AutopilotState()
    state.record_job_started()
    state.apply(Decision(should_pause=False, reasons=()))
    assert state.jobs_since_resume == 1
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_autopilot_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.autopilot_state'`

- [x] **Step 3: Write the implementation**

```python
# backend/autopilot_state.py
"""In-memory autopilot state, mutated by the polling loop and read by the middleware."""

from .autopilot import Decision


class AutopilotState:
    def __init__(self) -> None:
        self.is_paused: bool = False
        self.jobs_since_resume: int = 0
        self.last_reasons: tuple[str, ...] = ()

    def apply(self, decision: Decision) -> None:
        was_paused = self.is_paused
        self.is_paused = decision.should_pause
        self.last_reasons = decision.reasons
        if was_paused and not self.is_paused:
            self.jobs_since_resume = 0

    def record_job_started(self) -> None:
        self.jobs_since_resume += 1
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_autopilot_state.py -v`
Expected: 5 passed

- [x] **Step 5: Commit**

```bash
git add backend/autopilot_state.py tests/test_autopilot_state.py
git commit -m "Add autopilot state machine with job-counter reset on resume"
```

---

### Task 4: SQLite persistence

**Files:**
- Create: `backend/persistence.py`
- Test: `tests/test_persistence.py`

**Interfaces:**
- Produces: `init_db(db_path: str) -> sqlite3.Connection`, `add_queue_item(conn, prompt_id: str, name: str) -> int`, `list_queue_items(conn) -> list[dict]`, `reorder_queue_items(conn, ordered_prompt_ids: list[str]) -> None`, `remove_queue_item(conn, prompt_id: str) -> None`, `mark_completed(conn, prompt_id: str, thumbnail_path: str | None = None) -> None`, `list_history(conn, limit: int = 50) -> list[dict]`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_persistence.py
from backend.persistence import (
    init_db,
    add_queue_item,
    list_queue_items,
    reorder_queue_items,
    remove_queue_item,
    mark_completed,
    list_history,
)


def test_init_db_creates_empty_tables(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    assert list_queue_items(conn) == []
    assert list_history(conn) == []


def test_add_queue_item_appears_in_order(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="Cyberpunk Cat")
    add_queue_item(conn, prompt_id="b", name="Sunset Beach")
    items = list_queue_items(conn)
    assert [item["prompt_id"] for item in items] == ["a", "b"]
    assert items[0]["name"] == "Cyberpunk Cat"
    assert items[0]["status"] == "pending"


def test_reorder_queue_items_changes_returned_order(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")
    add_queue_item(conn, prompt_id="b", name="Second")
    reorder_queue_items(conn, ordered_prompt_ids=["b", "a"])
    items = list_queue_items(conn)
    assert [item["prompt_id"] for item in items] == ["b", "a"]


def test_remove_queue_item_deletes_it(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")
    remove_queue_item(conn, prompt_id="a")
    assert list_queue_items(conn) == []


def test_mark_completed_moves_item_into_history(tmp_path):
    conn = init_db(str(tmp_path / "test.sqlite3"))
    add_queue_item(conn, prompt_id="a", name="First")
    mark_completed(conn, prompt_id="a", thumbnail_path="/thumbs/a.png")
    assert list_queue_items(conn) == []
    history = list_history(conn)
    assert len(history) == 1
    assert history[0]["prompt_id"] == "a"
    assert history[0]["thumbnail_path"] == "/thumbs/a.png"


def test_persistence_survives_reopening_the_same_file(tmp_path):
    db_path = str(tmp_path / "test.sqlite3")
    conn1 = init_db(db_path)
    add_queue_item(conn1, prompt_id="a", name="First")
    conn1.close()

    conn2 = init_db(db_path)
    items = list_queue_items(conn2)
    assert len(items) == 1
    assert items[0]["prompt_id"] == "a"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_persistence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.persistence'`

- [x] **Step 3: Write the implementation**

```python
# backend/persistence.py
"""SQLite-backed queue order + history. Survives ComfyUI restarts."""

import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    order_index INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id TEXT NOT NULL,
    name TEXT NOT NULL,
    thumbnail_path TEXT,
    completed_at TEXT NOT NULL
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def add_queue_item(conn: sqlite3.Connection, prompt_id: str, name: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(order_index), -1) + 1 AS next FROM queue_items").fetchone()
    next_index = row["next"]
    cursor = conn.execute(
        "INSERT INTO queue_items (prompt_id, name, status, order_index, created_at) VALUES (?, ?, 'pending', ?, ?)",
        (prompt_id, name, next_index, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


def list_queue_items(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM queue_items ORDER BY order_index ASC").fetchall()
    return [dict(row) for row in rows]


def reorder_queue_items(conn: sqlite3.Connection, ordered_prompt_ids: list[str]) -> None:
    for index, prompt_id in enumerate(ordered_prompt_ids):
        conn.execute("UPDATE queue_items SET order_index = ? WHERE prompt_id = ?", (index, prompt_id))
    conn.commit()


def remove_queue_item(conn: sqlite3.Connection, prompt_id: str) -> None:
    conn.execute("DELETE FROM queue_items WHERE prompt_id = ?", (prompt_id,))
    conn.commit()


def mark_completed(conn: sqlite3.Connection, prompt_id: str, thumbnail_path: str | None = None) -> None:
    row = conn.execute("SELECT name FROM queue_items WHERE prompt_id = ?", (prompt_id,)).fetchone()
    name = row["name"] if row else prompt_id
    conn.execute(
        "INSERT INTO history (prompt_id, name, thumbnail_path, completed_at) VALUES (?, ?, ?, ?)",
        (prompt_id, name, thumbnail_path, datetime.now(timezone.utc).isoformat()),
    )
    conn.execute("DELETE FROM queue_items WHERE prompt_id = ?", (prompt_id,))
    conn.commit()


def list_history(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM history ORDER BY completed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_persistence.py -v`
Expected: 6 passed

- [x] **Step 5: Commit**

```bash
git add backend/persistence.py tests/test_persistence.py
git commit -m "Add SQLite persistence for queue order and history"
```

---

### Task 5: Non-intrusive queue middleware

**Files:**
- Create: `backend/queue_middleware.py`
- Test: `tests/test_queue_middleware.py`

**Interfaces:**
- Consumes: `AutopilotState` from Task 3 (reads `.is_paused` and `.last_reasons`).
- Produces: `create_queue_middleware(state: AutopilotState, is_enabled: Callable[[], bool]) -> Callable` — an aiohttp middleware factory.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_queue_middleware.py
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from backend.autopilot_state import AutopilotState
from backend.autopilot import Decision
from backend.queue_middleware import create_queue_middleware


async def _make_app(state: AutopilotState, enabled: bool = True):
    middleware = create_queue_middleware(state, is_enabled=lambda: enabled)
    app = web.Application(middlewares=[middleware])

    async def handle_prompt(request):
        return web.json_response({"ok": True})

    app.router.add_post("/prompt", handle_prompt)
    return app


class TestQueueMiddlewarePassthrough(AioHTTPTestCase):
    async def get_application(self):
        self.state = AutopilotState()
        return await _make_app(self.state, enabled=True)

    @unittest_run_loop
    async def test_passes_through_when_not_paused(self):
        resp = await self.client.post("/prompt", json={})
        assert resp.status == 200


class TestQueueMiddlewareBlocking(AioHTTPTestCase):
    async def get_application(self):
        self.state = AutopilotState()
        self.state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
        return await _make_app(self.state, enabled=True)

    @unittest_run_loop
    async def test_blocks_with_423_when_paused(self):
        resp = await self.client.post("/prompt", json={})
        assert resp.status == 423
        body = await resp.json()
        assert "GPU too hot" in body["error"]


class TestQueueMiddlewareDisabled(AioHTTPTestCase):
    async def get_application(self):
        self.state = AutopilotState()
        self.state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
        return await _make_app(self.state, enabled=False)

    @unittest_run_loop
    async def test_passes_through_when_master_toggle_disabled_even_if_paused(self):
        resp = await self.client.post("/prompt", json={})
        assert resp.status == 200
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_queue_middleware.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.queue_middleware'`

- [x] **Step 3: Write the implementation**

```python
# backend/queue_middleware.py
"""Non-intrusive middleware: never replaces /prompt, only gates it while paused."""

from typing import Callable

from aiohttp import web

from .autopilot_state import AutopilotState


def create_queue_middleware(state: AutopilotState, is_enabled: Callable[[], bool]):
    @web.middleware
    async def queue_middleware(request: web.Request, handler):
        if is_enabled() and state.is_paused and request.path == "/prompt" and request.method == "POST":
            reason = "; ".join(state.last_reasons) or "Queue paused"
            return web.json_response({"error": f"Queue paused: {reason}"}, status=423)
        return await handler(request)

    return queue_middleware
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queue_middleware.py -v`
Expected: 3 passed

- [x] **Step 5: Commit**

```bash
git add backend/queue_middleware.py tests/test_queue_middleware.py
git commit -m "Add non-intrusive middleware that gates /prompt while paused"
```

---

### Task 6: Background autopilot polling loop

**Files:**
- Create: `backend/autopilot_loop.py`
- Test: `tests/test_autopilot_loop.py`

**Interfaces:**
- Consumes: `poll_gpu_metrics` (Task 1, injected as a callable so tests can fake it), `evaluate` (Task 2), `AutopilotState` (Task 3), `AutopilotSettings` (Task 2).
- Produces: `async def run_autopilot_tick(state: AutopilotState, settings: AutopilotSettings, metrics_provider: Callable[[], GpuMetrics]) -> None` — runs exactly one poll-evaluate-apply cycle, fail-open on any exception from `metrics_provider`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_autopilot_loop.py
import pytest

from backend.gpu_monitor import GpuMetrics
from backend.autopilot import AutopilotSettings
from backend.autopilot_state import AutopilotState
from backend.autopilot_loop import run_autopilot_tick


@pytest.mark.asyncio
async def test_tick_pauses_state_when_metrics_exceed_threshold():
    state = AutopilotState()
    settings = AutopilotSettings(pause_temp_c=80.0)

    async def fake_metrics():
        return GpuMetrics(temp_c=90.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=10.0)

    await run_autopilot_tick(state, settings, fake_metrics)
    assert state.is_paused is True


@pytest.mark.asyncio
async def test_tick_leaves_state_unpaused_when_metrics_are_fine():
    state = AutopilotState()
    settings = AutopilotSettings(pause_temp_c=80.0)

    async def fake_metrics():
        return GpuMetrics(temp_c=50.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=10.0)

    await run_autopilot_tick(state, settings, fake_metrics)
    assert state.is_paused is False


@pytest.mark.asyncio
async def test_tick_fails_open_when_metrics_provider_raises():
    state = AutopilotState()
    settings = AutopilotSettings(pause_temp_c=80.0)

    async def broken_metrics():
        raise RuntimeError("nvidia-smi exploded")

    await run_autopilot_tick(state, settings, broken_metrics)
    assert state.is_paused is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_autopilot_loop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.autopilot_loop'`

(Note: this task needs `pytest-asyncio`. Add it to CI in Step 5 alongside the commit — see the CI update below.)

- [x] **Step 3: Write the implementation**

```python
# backend/autopilot_loop.py
"""Background polling: one tick = poll GPU metrics, evaluate rules, apply decision.

Fail-open: if metrics_provider raises, the tick is a no-op — it never leaves
or forces the queue into a paused state due to an internal error.
"""

import logging
from typing import Awaitable, Callable

from .autopilot import AutopilotSettings, evaluate
from .autopilot_state import AutopilotState
from .gpu_monitor import GpuMetrics

logger = logging.getLogger(__name__)

MetricsProvider = Callable[[], Awaitable[GpuMetrics]]


async def run_autopilot_tick(
    state: AutopilotState,
    settings: AutopilotSettings,
    metrics_provider: MetricsProvider,
) -> None:
    try:
        metrics = await metrics_provider()
    except Exception:
        logger.warning("Smart Queue: GPU metrics poll failed, skipping this tick", exc_info=True)
        return

    decision = evaluate(
        metrics,
        jobs_since_resume=state.jobs_since_resume,
        currently_paused=state.is_paused,
        settings=settings,
    )
    state.apply(decision)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pip install pytest-asyncio && pytest tests/test_autopilot_loop.py -v`
Expected: 3 passed

- [x] **Step 5: Update CI to install pytest-asyncio, then commit**

```yaml
# .github/workflows/ci.yml — change the "pip install pytest" line to:
      - run: pip install pytest pytest-asyncio
```

Also add to the top of `tests/test_autopilot_loop.py` a pytest marker config, or add this to a new `pytest.ini` in the repo root:

```ini
# pytest.ini
[pytest]
asyncio_mode = auto
```

```bash
git add backend/autopilot_loop.py tests/test_autopilot_loop.py .github/workflows/ci.yml pytest.ini
git commit -m "Add background autopilot polling tick with fail-open error handling"
```

---

### Task 7: Smart Cooldown & Pause node (V3 schema)

**Files:**
- Create: `backend/nodes/__init__.py` (empty)
- Create: `backend/nodes/cooldown.py`
- Test: `tests/test_cooldown_node.py`

**Interfaces:**
- Consumes: `poll_gpu_metrics` from Task 1.
- Produces: `SmartCooldownNode` class (V3 schema, node type ID `RubzGpuCooldownNode`) and a plain function `run_cooldown(fixed_delay_seconds, wait_for_temp, target_temp_c, poll_interval_seconds, max_wait_seconds, unload_models_before_wait, sleep_fn, metrics_fn, unload_fn) -> str` (the testable core logic, wrapped by the V3 node class so tests don't need a real ComfyUI runtime).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_cooldown_node.py
from backend.gpu_monitor import GpuMetrics
from backend.nodes.cooldown import run_cooldown


def test_fixed_delay_is_applied():
    sleep_calls = []
    status = run_cooldown(
        fixed_delay_seconds=10.0,
        wait_for_temp=False,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=False,
        sleep_fn=sleep_calls.append,
        metrics_fn=lambda: GpuMetrics(70.0, 1000.0, 8000.0, 10.0),
        unload_fn=lambda: None,
    )
    assert sleep_calls == [10.0]
    assert "Fixed delay: 10s" in status


def test_unload_models_called_before_wait_when_enabled():
    unload_calls = []
    run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=False,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=True,
        sleep_fn=lambda s: None,
        metrics_fn=lambda: GpuMetrics(70.0, 1000.0, 8000.0, 10.0),
        unload_fn=lambda: unload_calls.append(True),
    )
    assert unload_calls == [True]


def test_waits_for_temp_until_below_target():
    temps = iter([90.0, 80.0, 60.0])
    sleep_calls = []
    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=False,
        sleep_fn=sleep_calls.append,
        metrics_fn=lambda: GpuMetrics(next(temps), 1000.0, 8000.0, 10.0),
        unload_fn=lambda: None,
    )
    assert sleep_calls == [5.0, 5.0]
    assert "60" in status


def test_temp_unavailable_skips_temp_wait():
    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=65.0,
        poll_interval_seconds=5.0,
        max_wait_seconds=300.0,
        unload_models_before_wait=False,
        sleep_fn=lambda s: None,
        metrics_fn=lambda: GpuMetrics(None, None, None, None),
        unload_fn=lambda: None,
    )
    assert "unavailable" in status.lower()


def test_max_wait_seconds_caps_the_temp_wait():
    elapsed = {"total": 0.0}

    def fake_sleep(seconds):
        elapsed["total"] += seconds

    status = run_cooldown(
        fixed_delay_seconds=0.0,
        wait_for_temp=True,
        target_temp_c=10.0,  # unreachably low, forces the cap
        poll_interval_seconds=5.0,
        max_wait_seconds=12.0,
        unload_models_before_wait=False,
        sleep_fn=fake_sleep,
        metrics_fn=lambda: GpuMetrics(90.0, 1000.0, 8000.0, 10.0),
        unload_fn=lambda: None,
        clock_fn=_make_fake_clock(elapsed),
    )
    assert "Max wait" in status


def _make_fake_clock(elapsed):
    def clock():
        return elapsed["total"]
    return clock
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cooldown_node.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.nodes.cooldown'`

- [x] **Step 3: Write the implementation**

```python
# backend/nodes/cooldown.py
"""Smart Cooldown & Pause node (V3 schema). Node type ID stays RubzGpuCooldownNode
for backward compatibility with workflows saved against the old rubz-gpu-cooldown pack.
"""

from typing import Callable

from comfy_api.latest import io  # type: ignore[import-not-found]

from ..gpu_monitor import GpuMetrics, poll_gpu_metrics


def run_cooldown(
    fixed_delay_seconds: float,
    wait_for_temp: bool,
    target_temp_c: float,
    poll_interval_seconds: float,
    max_wait_seconds: float,
    unload_models_before_wait: bool,
    sleep_fn: Callable[[float], None],
    metrics_fn: Callable[[], GpuMetrics],
    unload_fn: Callable[[], None],
    clock_fn: Callable[[], float] | None = None,
) -> str:
    log: list[str] = []

    if unload_models_before_wait:
        unload_fn()
        log.append("Unloaded all models")

    if fixed_delay_seconds > 0:
        sleep_fn(fixed_delay_seconds)
        log.append(f"Fixed delay: {fixed_delay_seconds:.0f}s")

    if wait_for_temp:
        metrics = metrics_fn()
        if metrics.temp_c is None:
            log.append("GPU temp unavailable (nvidia-smi not found or failed) — skipping temp wait.")
        else:
            elapsed = 0.0
            log.append(f"Start temp: {metrics.temp_c:.0f}C, target: {target_temp_c:.0f}C")
            while metrics.temp_c is not None and metrics.temp_c > target_temp_c:
                if elapsed >= max_wait_seconds:
                    log.append(f"Max wait ({max_wait_seconds:.0f}s) reached at {metrics.temp_c:.0f}C — continuing anyway.")
                    break
                sleep_fn(poll_interval_seconds)
                elapsed += poll_interval_seconds
                metrics = metrics_fn()
                if metrics.temp_c is not None:
                    log.append(f"  -> {metrics.temp_c:.0f}C")
            else:
                if metrics.temp_c is not None:
                    log.append(f"Reached target: {metrics.temp_c:.0f}C")

    return " | ".join(log) if log else "No wait configured."


class SmartCooldownNode(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="RubzGpuCooldownNode",
            display_name="Smart Cooldown & Pause",
            category="utils",
            inputs=[
                io.Float.Input("fixed_delay_seconds", default=30.0, min=0.0, max=3600.0, step=1.0),
                io.Boolean.Input("wait_for_temp", default=True),
                io.Float.Input("target_temp_c", default=65.0, min=30.0, max=100.0, step=1.0),
                io.Float.Input("poll_interval_seconds", default=5.0, min=1.0, max=60.0, step=1.0),
                io.Float.Input("max_wait_seconds", default=300.0, min=0.0, max=3600.0, step=10.0),
                io.Boolean.Input("unload_models_before_wait", default=False),
                io.Boolean.Input("wait_for_click", default=False),
                io.Boolean.Input("notify_sound", default=False),
                io.Combo.Input("notify_sound_choice", options=["Default", "Chime", "Alert", "Custom..."], default="Default"),
                io.String.Input("custom_sound_path", default="", optional=True),
                io.Boolean.Input("notify_toast", default=False),
                io.AnyType.Input("passthrough", optional=True),
            ],
            outputs=[
                io.AnyType.Output("passthrough"),
                io.String.Output("status"),
            ],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, **kwargs) -> io.NodeOutput:
        import comfy.model_management as model_management

        status = run_cooldown(
            fixed_delay_seconds=kwargs["fixed_delay_seconds"],
            wait_for_temp=kwargs["wait_for_temp"],
            target_temp_c=kwargs["target_temp_c"],
            poll_interval_seconds=kwargs["poll_interval_seconds"],
            max_wait_seconds=kwargs["max_wait_seconds"],
            unload_models_before_wait=kwargs["unload_models_before_wait"],
            sleep_fn=__import__("time").sleep,
            metrics_fn=poll_gpu_metrics,
            unload_fn=model_management.unload_all_models,
        )
        return io.NodeOutput(kwargs.get("passthrough"), status)
```

**Note for the implementer:** `comfy_api.latest.io` is only importable inside a running ComfyUI installation. `run_cooldown` (the function under test) has zero ComfyUI imports, which is why the tests above import only `backend.nodes.cooldown.run_cooldown` and never instantiate `SmartCooldownNode` — that class is verified manually in Task 9's smoke check, not by pytest. If the exact `io.*` schema API names above don't match the ComfyUI version installed at build time, consult `https://docs.comfy.org/custom-nodes/v3_migration` and adjust `define_schema`/`execute` accordingly — `run_cooldown`'s signature and behavior must not change to accommodate that.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cooldown_node.py -v`
Expected: 5 passed
(This will fail at import time if `comfy_api.latest` isn't installed — if so, wrap the `from comfy_api.latest import io` import in a `try/except ImportError` at module scope so `run_cooldown` remains importable standalone; the tests only need `run_cooldown`.)

- [x] **Step 5: Commit**

```bash
git add backend/nodes/__init__.py backend/nodes/cooldown.py tests/test_cooldown_node.py
git commit -m "Add Smart Cooldown & Pause node (V3 schema, testable core logic)"
```

---

### Task 8: HTTP routes for the queue panel

**Files:**
- Create: `backend/routes.py`
- Test: `tests/test_routes.py`

**Interfaces:**
- Consumes: `persistence` module (Task 4), `AutopilotState` (Task 3), `AutopilotSettings` (Task 2).
- Produces: `register_routes(app: web.Application, conn: sqlite3.Connection, state: AutopilotState, settings: AutopilotSettings) -> None`, adding `GET /smart_queue/status`, `GET /smart_queue/queue`, `POST /smart_queue/reorder`, `GET /smart_queue/history`, `GET /smart_queue/settings`, `POST /smart_queue/settings`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_routes.py
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from backend.persistence import init_db, add_queue_item
from backend.autopilot_state import AutopilotState
from backend.autopilot import AutopilotSettings, Decision
from backend.routes import register_routes


class TestSmartQueueRoutes(AioHTTPTestCase):
    async def get_application(self):
        self.conn = init_db(":memory:")
        self.state = AutopilotState()
        self.settings = AutopilotSettings()
        app = web.Application()
        register_routes(app, self.conn, self.state, self.settings)
        return app

    @unittest_run_loop
    async def test_status_reports_not_paused_initially(self):
        resp = await self.client.get("/smart_queue/status")
        body = await resp.json()
        assert body["is_paused"] is False

    @unittest_run_loop
    async def test_status_reports_paused_with_reasons(self):
        self.state.apply(Decision(should_pause=True, reasons=("GPU too hot",)))
        resp = await self.client.get("/smart_queue/status")
        body = await resp.json()
        assert body["is_paused"] is True
        assert "GPU too hot" in body["reasons"]

    @unittest_run_loop
    async def test_queue_lists_items_in_order(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        resp = await self.client.get("/smart_queue/queue")
        body = await resp.json()
        assert [item["prompt_id"] for item in body["items"]] == ["a", "b"]

    @unittest_run_loop
    async def test_reorder_updates_queue_order(self):
        add_queue_item(self.conn, prompt_id="a", name="First")
        add_queue_item(self.conn, prompt_id="b", name="Second")
        resp = await self.client.post("/smart_queue/reorder", json={"ordered_prompt_ids": ["b", "a"]})
        assert resp.status == 200
        resp2 = await self.client.get("/smart_queue/queue")
        body = await resp2.json()
        assert [item["prompt_id"] for item in body["items"]] == ["b", "a"]

    @unittest_run_loop
    async def test_history_is_empty_initially(self):
        resp = await self.client.get("/smart_queue/history")
        body = await resp.json()
        assert body["items"] == []

    @unittest_run_loop
    async def test_get_settings_returns_current_values(self):
        resp = await self.client.get("/smart_queue/settings")
        body = await resp.json()
        assert body["master_enabled"] is True
        assert body["pause_temp_c"] == 80.0

    @unittest_run_loop
    async def test_post_settings_updates_shared_settings_object(self):
        resp = await self.client.post("/smart_queue/settings", json={"pause_temp_c": 90.0, "master_enabled": False})
        assert resp.status == 200
        assert self.settings.pause_temp_c == 90.0
        assert self.settings.master_enabled is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.routes'`

- [x] **Step 3: Write the implementation**

```python
# backend/routes.py
"""HTTP endpoints for the Smart Queue panel. Additive only — never overrides
a native ComfyUI route."""

import sqlite3
from dataclasses import asdict

from aiohttp import web

from .autopilot import AutopilotSettings
from .autopilot_state import AutopilotState
from .persistence import list_history, list_queue_items, reorder_queue_items


def register_routes(
    app: web.Application,
    conn: sqlite3.Connection,
    state: AutopilotState,
    settings: AutopilotSettings,
) -> None:
    async def get_status(request: web.Request) -> web.Response:
        return web.json_response({"is_paused": state.is_paused, "reasons": list(state.last_reasons)})

    async def get_queue(request: web.Request) -> web.Response:
        return web.json_response({"items": list_queue_items(conn)})

    async def post_reorder(request: web.Request) -> web.Response:
        payload = await request.json()
        reorder_queue_items(conn, payload["ordered_prompt_ids"])
        return web.json_response({"ok": True})

    async def get_history(request: web.Request) -> web.Response:
        return web.json_response({"items": list_history(conn)})

    async def get_settings(request: web.Request) -> web.Response:
        return web.json_response(asdict(settings))

    async def post_settings(request: web.Request) -> web.Response:
        payload = await request.json()
        settings.update_from_dict(payload)
        return web.json_response({"ok": True})

    app.router.add_get("/smart_queue/status", get_status)
    app.router.add_get("/smart_queue/queue", get_queue)
    app.router.add_post("/smart_queue/reorder", post_reorder)
    app.router.add_get("/smart_queue/history", get_history)
    app.router.add_get("/smart_queue/settings", get_settings)
    app.router.add_post("/smart_queue/settings", post_settings)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_routes.py -v`
Expected: 7 passed

- [x] **Step 5: Commit**

```bash
git add backend/routes.py tests/test_routes.py
git commit -m "Add HTTP routes for the Smart Queue panel"
```

---

### Task 9: Wire everything into `__init__.py`

**Files:**
- Modify: `__init__.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `SmartCooldownNode` (Task 7), `create_queue_middleware` (Task 5), `register_routes` (Task 8), `run_autopilot_tick` (Task 6), `AutopilotSettings` (Task 2), `init_db` (Task 4).
- Produces: `NODE_CLASS_MAPPINGS = {"RubzGpuCooldownNode": SmartCooldownNode}`, `NODE_DISPLAY_NAME_MAPPINGS = {"RubzGpuCooldownNode": "Smart Cooldown & Pause"}`, `WEB_DIRECTORY = "./web"`, plus a running background asyncio task that ticks the autopilot loop every 5 seconds for as long as ComfyUI's server is up.

- [x] **Step 1: Write the failing test**

```python
# tests/test_smoke.py — replace the existing body with:
"""Smoke test: the package's __init__.py loads cleanly and registers the node."""

import importlib.util
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _load_init_module():
    spec = importlib.util.spec_from_file_location("smart_queue_init", PACKAGE_ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_init_exposes_node_mappings():
    module = _load_init_module()
    assert isinstance(module.NODE_CLASS_MAPPINGS, dict)
    assert isinstance(module.NODE_DISPLAY_NAME_MAPPINGS, dict)


def test_cooldown_node_registered_under_backward_compatible_id():
    module = _load_init_module()
    assert "RubzGpuCooldownNode" in module.NODE_CLASS_MAPPINGS
    assert module.NODE_DISPLAY_NAME_MAPPINGS["RubzGpuCooldownNode"] == "Smart Cooldown & Pause"


def test_web_directory_is_declared():
    module = _load_init_module()
    assert module.WEB_DIRECTORY == "./web"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_smoke.py -v`
Expected: FAIL — `RubzGpuCooldownNode` not in `NODE_CLASS_MAPPINGS` (currently empty dict)

- [x] **Step 3: Write the implementation**

```python
# __init__.py
"""Smart Queue — GPU-aware queue autopilot + cooldown/pause node for ComfyUI."""

import asyncio
import logging
from pathlib import Path

try:
    from server import PromptServer  # type: ignore[import-not-found]
    _HAS_COMFY_SERVER = True
except ImportError:
    _HAS_COMFY_SERVER = False

from .backend.autopilot import AutopilotSettings
from .backend.autopilot_loop import run_autopilot_tick
from .backend.autopilot_state import AutopilotState
from .backend.gpu_monitor import poll_gpu_metrics
from .backend.nodes.cooldown import SmartCooldownNode
from .backend.persistence import init_db
from .backend.queue_middleware import create_queue_middleware
from .backend.routes import register_routes

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS = {
    "RubzGpuCooldownNode": SmartCooldownNode,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "RubzGpuCooldownNode": "Smart Cooldown & Pause",
}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_autopilot_state = AutopilotState()
_autopilot_settings = AutopilotSettings()

TICK_INTERVAL_SECONDS = 5.0


async def _async_poll_gpu_metrics():
    # poll_gpu_metrics is a blocking subprocess call; keep it off the event loop.
    return await asyncio.to_thread(poll_gpu_metrics)


async def _autopilot_background_loop():
    while True:
        if _autopilot_settings.master_enabled:
            await run_autopilot_tick(_autopilot_state, _autopilot_settings, _async_poll_gpu_metrics)
        await asyncio.sleep(TICK_INTERVAL_SECONDS)


def _is_autopilot_enabled() -> bool:
    return _autopilot_settings.master_enabled


if _HAS_COMFY_SERVER:
    _db_path = str(Path(__file__).parent / "smart_queue.sqlite3")
    _conn = init_db(_db_path)

    _server = PromptServer.instance
    _server.app.middlewares.append(create_queue_middleware(_autopilot_state, _is_autopilot_enabled))
    register_routes(_server.app, _conn, _autopilot_state, _autopilot_settings)

    async def _start_autopilot_loop(app):
        app["smart_queue_autopilot_task"] = asyncio.create_task(_autopilot_background_loop())

    _server.app.on_startup.append(_start_autopilot_loop)

    logger.info("[Smart Queue] Loaded — autopilot + Smart Cooldown & Pause node registered.")
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -v`
Expected: all tests pass (the `_HAS_COMFY_SERVER` guard means `__init__.py` degrades gracefully outside a real ComfyUI process, which is exactly the environment pytest runs in)

- [x] **Step 5: Commit**

```bash
git add __init__.py tests/test_smoke.py
git commit -m "Wire autopilot, middleware, routes, and node into package init"
```

---

### Task 10: Queue panel frontend (manual verification)

**Files:**
- Create: `web/smart_queue.js`
- Create: `web/smart_queue.css`

This task has no automated test — it requires a running ComfyUI instance (per spec §9, the panel/theme/Nodes-2.0 checks are explicitly manual). Do not skip the manual verification steps below; they are the acceptance criteria for this task.

- [x] **Step 1: Write `web/smart_queue.js`**

```javascript
import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "SmartQueue.Panel",
    settings: [
        {
            id: "SmartQueue.EnableAutopilot",
            name: "Enable Autopilot Queue Panel",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.TempRuleEnabled",
            name: "Autopilot: pause on high temperature",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.VramRuleEnabled",
            name: "Autopilot: pause on low free VRAM",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.PauseTempC",
            name: "Autopilot: pause temperature (C)",
            type: "number",
            defaultValue: 80.0,
        },
        {
            id: "SmartQueue.ResumeTempC",
            name: "Autopilot: resume temperature (C)",
            type: "number",
            defaultValue: 72.0,
        },
        {
            id: "SmartQueue.JobCountRuleEnabled",
            name: "Autopilot: pause after N jobs",
            type: "boolean",
            defaultValue: true,
        },
        {
            id: "SmartQueue.MinFreeVramMb",
            name: "Autopilot: minimum free VRAM (MB)",
            type: "number",
            defaultValue: 1024,
        },
        {
            id: "SmartQueue.MaxJobsBeforePause",
            name: "Autopilot: jobs before forced cooldown",
            type: "number",
            defaultValue: 20,
        },
    ],
    async setup() {
        async function syncSettingsToBackend() {
            const payload = {
                master_enabled: app.extensionManager.setting.get("SmartQueue.EnableAutopilot"),
                temp_rule_enabled: app.extensionManager.setting.get("SmartQueue.TempRuleEnabled"),
                pause_temp_c: app.extensionManager.setting.get("SmartQueue.PauseTempC"),
                resume_temp_c: app.extensionManager.setting.get("SmartQueue.ResumeTempC"),
                vram_rule_enabled: app.extensionManager.setting.get("SmartQueue.VramRuleEnabled"),
                min_free_vram_mb: app.extensionManager.setting.get("SmartQueue.MinFreeVramMb"),
                job_count_rule_enabled: app.extensionManager.setting.get("SmartQueue.JobCountRuleEnabled"),
                max_jobs_before_pause: app.extensionManager.setting.get("SmartQueue.MaxJobsBeforePause"),
            };
            try {
                await fetch("/smart_queue/settings", {
                    method: "POST",
                    body: JSON.stringify(payload),
                    headers: { "Content-Type": "application/json" },
                });
            } catch (err) {
                console.error("[Smart Queue] settings sync failed:", err);
            }
        }

        await syncSettingsToBackend();
        setInterval(syncSettingsToBackend, 10000);

        const enabled = app.extensionManager.setting.get("SmartQueue.EnableAutopilot");
        if (!enabled) {
            console.log("[Smart Queue] Autopilot panel disabled via settings.");
            return;
        }

        const hasCrystools = app.extensions?.some((ext) => ext.name?.toLowerCase().includes("crystools"));

        const panel = document.createElement("div");
        panel.id = "smart-queue-panel";
        panel.innerHTML = `
            <div class="smart-queue-status">Smart Queue: idle</div>
            ${hasCrystools ? "" : '<div class="smart-queue-gpu-readout"></div>'}
            <ul class="smart-queue-list" id="smart-queue-list"></ul>
        `;

        const sidebar = document.querySelector(".comfyui-body-bottom") || document.querySelector(".comfy-menu");
        if (sidebar) {
            sidebar.appendChild(panel);
        }

        async function refreshStatus() {
            try {
                const res = await fetch("/smart_queue/status");
                const data = await res.json();
                const statusEl = panel.querySelector(".smart-queue-status");
                statusEl.textContent = data.is_paused
                    ? `Smart Queue: paused — ${data.reasons.join("; ")}`
                    : "Smart Queue: running";
            } catch (err) {
                console.error("[Smart Queue] status fetch failed:", err);
            }
        }

        async function refreshQueueList() {
            try {
                const res = await fetch("/smart_queue/queue");
                const data = await res.json();
                const listEl = panel.querySelector("#smart-queue-list");
                listEl.innerHTML = "";
                for (const item of data.items) {
                    const li = document.createElement("li");
                    li.draggable = true;
                    li.dataset.promptId = item.prompt_id;
                    li.textContent = item.name;
                    listEl.appendChild(li);
                }
            } catch (err) {
                console.error("[Smart Queue] queue fetch failed:", err);
            }
        }

        setInterval(refreshStatus, 3000);
        setInterval(refreshQueueList, 5000);
        await refreshStatus();
        await refreshQueueList();
    },
});
```

- [x] **Step 2: Write `web/smart_queue.css`**

```css
#smart-queue-panel {
    background: var(--comfy-menu-bg, var(--p-content-background, #202020));
    color: var(--fg-color, var(--p-text-color, #ddd));
    border: 1px solid var(--border-color, var(--p-content-border-color, #444));
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 12px;
}

#smart-queue-panel .smart-queue-status {
    font-weight: 600;
    margin-bottom: 6px;
}

#smart-queue-panel .smart-queue-list {
    list-style: none;
    margin: 0;
    padding: 0;
}

#smart-queue-panel .smart-queue-list li {
    padding: 4px 6px;
    border-bottom: 1px solid var(--border-color, var(--p-content-border-color, #444));
    cursor: grab;
}
```

- [x] **Step 3: Manual verification against a running ComfyUI instance** *(items 1,2,3,5,6 verified 2026-08-29; item 4 — Nodes 2.0 toggle — still pending; item 7 verified via the master-toggle-off code path, not re-tested live after the toolbar-button rework)*

Use the `run` skill to launch ComfyUI with this pack installed, then check:

1. [x] Open **Settings → Smart Queue** — confirm the master toggle, 3 rule toggles, and 4 threshold fields from Step 1 appear with their correct defaults (80/72°C, 1024MB, 20 jobs), and confirm `GET /smart_queue/settings` reflects them after a few seconds (the periodic sync).
2. [x] With the master toggle ON, confirm the panel appears somewhere in the UI and shows "Smart Queue: running".
3. [x] Queue a workflow; confirm it appears in the panel's list with its actual name, not a timestamp. *(Required building `backend/queue_tracker.py` — this wasn't wired up at all before manual testing; see spec §13.)*
4. [ ] Switch **Settings → Comfy → Modern Node Design (Nodes 2.0)** OFF, reload, repeat steps 2-3. Then switch it back ON, reload, repeat again. Both must work identically. **Not yet done.**
5. [x] Switch **Settings → Appearance** to a different theme (e.g. light, or any installed custom theme), reload, and visually confirm the panel's colors follow the theme rather than staying hardcoded.
6. [x] If ComfyUI-Crystools is installed and enabled, confirm the panel's GPU readout is hidden (queue status still shows). If not installed, confirm the GPU readout is shown.
7. [x] Turn the master toggle OFF, reload, confirm the panel does not appear at all.

- [x] **Step 4: Commit**

```bash
git add web/smart_queue.js web/smart_queue.css
git commit -m "Add queue panel UI with theme-aware styling and Crystools detection"
```

---

### Task 11: Node continue-button, toast, and sound (manual verification)

**Files:**
- Create: `web/smart_queue_node.js`
- Create: `web/sounds/default.wav`, `web/sounds/chime.wav`, `web/sounds/alert.wav` (three short, freely-licensed or self-generated tones — generate with any offline tone generator; exact audio content is a production detail left to the implementer, but all three files must exist and be under 50KB each)

This task also has no automated test (browser audio/DOM only) — manual verification is the acceptance criteria, per spec §9.

**Blocked (2026-08-29):** `RubzGpuCooldownNode` is registered by both this
package and the old `rubz-gpu-cooldown` pack under the same node-type ID;
the old V1 node currently wins (loads later alphabetically, overwrites the
mapping) so the new node with sound/toast/continue-button support can't be
placed on a canvas to test. Old folder renamed to
`rubz-gpu-cooldown.disabled` to unblock the next verification pass — see
spec §13. Once a restart confirms the new node loads, resume at Step 2.

- [x] **Step 1: Write `web/smart_queue_node.js`** *(also wired the Python side — `execute()` in `backend/nodes/cooldown.py` dispatches the `smart_queue.cooldown_notify` and `smart_queue.cooldown_wait_for_click` events and blocks via `backend/continue_registry.py` — beyond what this step's snippet below shows.)*

```javascript
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SOUND_FILES = {
    Default: "sounds/default.wav",
    Chime: "sounds/chime.wav",
    Alert: "sounds/alert.wav",
};

app.registerExtension({
    name: "SmartQueue.CooldownNode",
    async setup() {
        api.addEventListener("smart_queue.cooldown_notify", (event) => {
            const { notify_sound, notify_sound_choice, custom_sound_path, notify_toast, status } = event.detail;

            if (notify_sound) {
                let src = SOUND_FILES[notify_sound_choice] ?? SOUND_FILES.Default;
                if (notify_sound_choice === "Custom..." && custom_sound_path) {
                    src = custom_sound_path;
                }
                const audio = new Audio(new URL(src, import.meta.url).href);
                audio.play().catch(() => {
                    // Custom file missing/unplayable — fall back to the default tone.
                    new Audio(new URL(SOUND_FILES.Default, import.meta.url).href).play().catch(() => {});
                });
            }

            if (notify_toast && app.extensionManager?.toast) {
                app.extensionManager.toast.add({ severity: "info", summary: "Smart Cooldown & Pause", detail: status });
            }
        });
    },
});
```

**Note for the implementer:** the Python side of `wait_for_click` and the `smart_queue.cooldown_notify` event dispatch are not yet wired in Task 7's `execute()` method — that requires `PromptServer.instance.send_sync(...)` for the notify event and a small polling loop against a `/smart_queue/continue/<prompt_id>` endpoint for `wait_for_click`. Add both directly to `backend/nodes/cooldown.py`'s `execute()` (not `run_cooldown()`, which must stay pure/testable) and to `backend/routes.py` before running the manual verification below. Keep `run_cooldown()`'s signature and existing tests unchanged.

- [ ] **Step 2: Manual verification against a running ComfyUI instance**

1. Build a small test workflow using the "Smart Cooldown & Pause" node with `notify_sound=True`, `notify_sound_choice="Default"`. Run it — confirm a tone plays when the node finishes.
2. Set `notify_sound_choice="Custom..."` with an empty `custom_sound_path`. Run it — confirm it falls back to the default tone instead of erroring.
3. Set `notify_toast=True`. Run it — confirm a toast appears with the status text.
4. Set `wait_for_click=True`. Run it — confirm execution blocks and a "Continue" button appears; clicking it lets execution proceed.
5. Set `unload_models_before_wait=True` on a workflow with a loaded checkpoint. Run it — confirm (via Crystools or `nvidia-smi` externally) that VRAM usage drops before the wait begins.

- [ ] **Step 3: Commit**

```bash
git add web/smart_queue_node.js web/sounds/
git commit -m "Add node notification UI: continue button, toast, sound playback"
```

---

## Post-implementation

Once all 11 tasks are verified (automated tests green in CI for Tasks 1-9, manual checks passed for Tasks 10-11): delete `D:\AI\ComfyUI-AGAIN\ComfyUI\custom_nodes\rubz-gpu-cooldown` per spec §10, and update the spec's "Status" line from "Draft" to "v1 implemented".

**Status as of 2026-08-29:** Tasks 1-9 done (70 automated tests green — up
from the original plan's count, four bugs found only by manual testing
required new code: `backend/queue_tracker.py`, the manual-pause additions to
`autopilot_state.py`/`queue_middleware.py`/`routes.py`, and CSS-loading /
toolbar-button fixes in `web/`). Task 10 verified except the Nodes 2.0
toggle check. Task 11 blocked on the `rubz-gpu-cooldown` name collision —
old folder renamed to `.disabled`, not yet deleted; do that only after Task
11 passes and the toggle check is done. See spec §13 for full detail.

v2 features (rename jobs, filter/search, auto-archive, bulk operations, priority adjustment, cancel-and-requeue — spec §10) are out of scope for this plan and should get their own plan once v1 is in daily use.
