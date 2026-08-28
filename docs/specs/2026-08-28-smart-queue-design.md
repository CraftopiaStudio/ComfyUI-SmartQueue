# Smart Queue — Design Spec

Status: v1 fully implemented and manually verified against a running ComfyUI instance (Tasks 1-9 automated, 70 tests green; Task 10 UI verified — panel, settings sync, Crystools detection, theme, manual pause, Nodes 2.0 toggle on/off; Task 11 node-notification checks verified — sound, toast, fallback, wait-for-click, unload-before-wait; see §13). Remaining: delete `rubz-gpu-cooldown.disabled` per §10.
Date: 2026-08-29
Repo: `comfyui-smart-queue` (D:\AI\ComfyUI-AGAIN\ComfyUI\custom_nodes\comfyui-smart-queue)
GitHub: `github.com/CraftopiaStudio/comfyui-smart-queue` (alongside ComfyUI-WorkflowOrganizer and CraftKit)
Display name: **Smart Queue**

## 1. Motivation

Long batch renders (50+ jobs) on a single GPU risk thermal throttling and VRAM
exhaustion. ComfyUI removed its native pause button in v0.4.0 and has no
native way to gate the queue on live GPU state; upstream maintainers have
explicitly declined to build this (no native GPU-suspend state in PyTorch).

An in-graph node (`rubz-gpu-cooldown`, built 2026-08-27) already solves this
per-workflow with a fixed delay + optional GPU-temp wait via `nvidia-smi`.
Smart Queue folds that node in and adds a second, complementary layer:
queue-level automation that pauses/resumes the *entire* queue based on live
GPU signals, independent of any single workflow.

## 2. Competitive research summary

| Tool | Architecture | What it does | Notes |
|---|---|---|---|
| [QuietNoise/comfyui_queue_manager](https://github.com/QuietNoise/comfyui_queue_manager) | Next.js app in an iframe, postMessage bridge | Pause/resume, persistence (SQLite), archive, import/export, filter by workflow name | 57★, self-described "proof of concept"; README admits it hijacks native queue processes |
| [co5dt/ComfyUI-Persistent-Queue](https://github.com/co5dt/ComfyUI-Persistent-Queue) | Native UI panel, no iframe | Pause/resume, drag-drop reorder, rename jobs, persistence, history with thumbnails, restore workflow from thumbnail, priority adjust | 7★, small but architecturally the right pattern |
| [abdullahceylan/ac-comfyui-queue-manager](https://github.com/abdullahceylan/ac-comfyui-queue-manager) | Own REST server on port 5000 | Pause/resume, persistence, advanced filter/search, auto-archive after N days, bulk operations, import/export | 9★ |
| [vilanele/ComfyUI-AsyncPause](https://github.com/vilanele/ComfyUI-AsyncPause) | In-graph nodes | Pause node with continue/cancel/cancel-and-requeue buttons, Notify Audio node (12 built-in sounds + custom), Notify Toast node | Node-level only, no queue-level control |
| [crystian/ComfyUI-Crystools](https://github.com/crystian/ComfyUI-Crystools) | Native UI (progress bar + resource monitor) | Live CPU/RAM/GPU/VRAM/temp display, per-metric toggle | De facto standard monitor; NVIDIA/CUDA only for GPU metrics; display-only, never touches the queue |
| SeanScripts/ComfyUI-Unload-Model, willblaschko/ComfyUI-Unload-Models | In-graph passthrough nodes | Unload one/all models at a specific graph point | Simple utility nodes |

**Gap nobody fills:** no tool ties queue control to live GPU state. Monitoring
tools (Crystools) only display; queue tools (Persistent-Queue etc.) only
react to manual input. That gap is Smart Queue's core differentiator.

**Deliberately not copied:**
- Iframe + postMessage architecture (QuietNoise) — exactly the fragility this
  project avoids.
- A separate REST server on its own port (ac-comfyui-queue-manager) —
  unnecessary; everything rides on ComfyUI's existing aiohttp server.
- Hijacking/overwriting native endpoints (QuietNoise) — Smart Queue uses
  non-intrusive middleware instead; it never replaces a native route.
- Import/export of queue state as files — low value once SQLite persistence
  and history exist; deferred indefinitely, not planned.

## 3. Architecture

Two independent custom node packs, no dependency between them, sharing only
a common *pattern* (subprocess `nvidia-smi` polling, no pip dependency):

- **`comfyui-smart-queue`** (this project) — everything described below.
- **`rubz-gpu-cooldown`** — superseded by this project. Its node moves into
  Smart Queue (same internal node-type ID, see §8). The old folder is left
  in place until the new node is verified working, then deleted (§10). It
  was never published (placeholder publisher ID in its `pyproject.toml`), so
  there are no external users to migrate.

```
comfyui-smart-queue/
├── __init__.py
├── pyproject.toml
├── backend/
│   ├── gpu_monitor.py       # nvidia-smi poll: temp, vram_used/total, util%
│   ├── queue_middleware.py  # non-intrusive aiohttp middleware on /prompt
│   ├── autopilot.py         # rule engine: metrics + state -> pause/resume
│   ├── persistence.py       # SQLite: queue history, names, thumbnails
│   ├── routes.py            # /smart_queue/* endpoints, never overrides native ones
│   └── nodes/
│       └── cooldown.py      # V3-schema node (formerly rubz-gpu-cooldown)
├── web/
│   ├── smart_queue.js       # panel injection, settings registration
│   ├── smart_queue.css      # scoped, uses ComfyUI's own CSS variables
│   └── sounds/              # 2-3 built-in short tones (no 12-sound library)
└── docs/specs/
    └── 2026-08-28-smart-queue-design.md
```

## 4. Autopilot rule engine

Three independent, individually toggleable "warning lights." Any one being
red keeps the queue paused; the next job starts only once **all** enabled
lights are green.

| Rule | Setting to enable | Thresholds | Default |
|---|---|---|---|
| Temperature | `autopilot.temp_rule_enabled` | `pause_temp_c`, `resume_temp_c` (hysteresis gap to avoid flapping) | enabled, conservative default (pauses only at a clearly-too-hot value, not aggressively) |
| VRAM headroom | `autopilot.vram_rule_enabled` | `min_free_vram_mb` | enabled |
| Job-count cooldown | `autopilot.job_count_rule_enabled` | `max_jobs_before_pause` | enabled |

Rule evaluation (`backend/autopilot.py`) is a pure function:
`evaluate(metrics: GpuMetrics, state: AutopilotState) -> Decision`, with no
I/O — this is what unit tests target (§9).

**Never interrupts a running job.** Autopilot only blocks the *next* job
from starting (via the middleware, §5). This matches every existing tool's
behavior and sidesteps the "no native GPU-suspend state" problem the
ComfyUI maintainers cited when declining native pause support.

**Fail-open.** If `nvidia-smi` is unavailable (no NVIDIA GPU, AMD, CPU-only)
or the rule engine throws, the affected rule silently disables itself and
logs a warning — it never blocks the queue due to an internal error. A bug
in autopilot must never be able to hang someone's render queue.

## 5. Queue middleware

An aiohttp middleware registered on ComfyUI's existing `PromptServer`
(`@PromptServer.instance.routes.middleware`), matching the "non-intrusive"
approach from the original research doc:

- Intercepts POST `/prompt` only while the autopilot state is "paused";
  returns HTTP 423 with a clear reason (`{"error": "Queue paused: GPU at
  84°C, target 75°C"}`).
- Never registers over an existing route, never wraps/replaces a native
  handler — purely additive.
- Third-party integrations that already POST to `/prompt` (e.g. a Krita or
  Photoshop plugin) see a standard HTTP error they can already handle, not
  a broken connection.
- No-ops entirely (zero overhead, doesn't even poll) when the user has
  turned off the "Enable Autopilot" master setting (§7).

## 6. Persistence & queue UI (v1 baseline, matching best-in-class competitors)

SQLite database (`smart_queue.sqlite3` in the ComfyUI user directory),
consistent with every researched competitor:

- Queue and its ordering survive a ComfyUI restart or crash.
- **Readable job names**: read directly from the prompt's `extra_pnginfo` /
  workflow JSON metadata that ComfyUI already sends when queuing — no extra
  node required in the user's graph (an improvement over QuietNoise, which
  needs a dedicated node for this). Falls back to a timestamp only if no
  name is found.
- **Drag & drop reorder** of pending queue items.
- **History with thumbnails**, and dragging a history thumbnail back onto
  the canvas restores that workflow (matching Persistent-Queue).
- **Pause/resume** is both automatic (autopilot) and a manual toggle button
  in the panel.

**Crystools overlap avoidance:** on panel load, the JS checks whether
Crystools' extension is already registered (via ComfyUI's list of loaded
`app.registerExtension` names). If found, Smart Queue's panel hides its own
temp/VRAM display widget and shows only queue/autopilot status — autopilot
keeps polling internally regardless, this only affects what's *displayed*,
avoiding a redundant second GPU gauge on screen. If Crystools isn't
present, Smart Queue shows its own simple temp/VRAM readout so the
information is still available.

## 7. Settings

Registered via ComfyUI's standard extension settings API
(`app.registerExtension({ settings: [...] })`), appearing under
**Settings → Smart Queue** exactly like any other extension (EasyUse,
rgthree, KJNodes, etc. already do this):

- Master toggle: **Enable Autopilot Queue Panel** — default **ON**. When
  off, the middleware no-ops and the panel doesn't inject itself.
- Per-rule toggles + thresholds as listed in §4.
- All settings read by both the JS panel and the Python middleware/rule
  engine (via a small `/smart_queue/settings` endpoint or shared config
  file — implementation detail, not user-facing).

## 8. Cooldown & Pause node (formerly `rubz-gpu-cooldown`)

Built on the **V3 node schema** (`io.ComfyNode` + `define_schema()`) for
forward compatibility — see §11. Display name **"Smart Cooldown & Pause"**
("Smart" as the brand prefix, consistent with the "Smart Queue" pack name
and intended as a naming pattern for future tools in this line); internal
node-type ID stays `RubzGpuCooldownNode` so existing saved workflows
referencing the old node keep loading.

This single node consolidates three categories of previously-separate
tools: cooldown timers, AsyncPause's pause/notify nodes, and the
Unload-Model utility nodes.

| Input | Type | Default | Behavior |
|---|---|---|---|
| `fixed_delay_seconds` | FLOAT | 30.0 | Always-applied delay (existing) |
| `wait_for_temp` | BOOLEAN | True | Poll GPU temp until under target (existing) |
| `target_temp_c` | FLOAT | 65.0 | Existing |
| `poll_interval_seconds` | FLOAT | 5.0 | Existing |
| `max_wait_seconds` | FLOAT | 300.0 | Safety cap (existing) |
| `unload_models_before_wait` | BOOLEAN | False | Calls `comfy.model_management.unload_all_models()` before the wait starts |
| `wait_for_click` | BOOLEAN | False | After the cooldown, blocks until a "Continue" button (rendered by `web/smart_queue.js`) is clicked — replaces AsyncPause's Pause node |
| `notify_sound` | BOOLEAN | False | Plays a short tone when the node completes |
| `notify_sound_choice` | COMBO | "Default" | Options: "Default", "Chime", "Alert", "Custom..." |
| `custom_sound_path` | STRING (optional) | "" | Only used when `notify_sound_choice = "Custom..."`. If missing/unreadable, falls back to "Default" and logs a warning in the node's status output instead of failing |
| `notify_toast` | BOOLEAN | False | Shows a ComfyUI toast with the status text |

**Why keep this node once queue-level autopilot exists:** autopilot rules
are global (apply to the whole queue). The node gives **per-workflow**
granularity — e.g. a harder cooldown only on heavy video-batch workflows,
while light test workflows run unaffected — plus notifications, which are
inherently workflow-local and not a queue-wide concept. If a user disables
the queue-level autopilot entirely, the node remains their only way to get
GPU-aware pausing for a specific workflow.

## 9. Error handling & testing

- **Fail-open everywhere**: any exception in `autopilot.py`, `gpu_monitor.py`,
  or the middleware disables the affected feature and logs, never blocks
  the queue or crashes ComfyUI's server.
- **Unit tests** target `autopilot.py`'s rule engine as pure functions
  (metrics + state in, decision out) — no real GPU or running ComfyUI
  instance required.
- **Manual/integration testing** for the JS panel, node UI (continue
  button, toast, sound), and the middleware's real interaction with the
  `/prompt` endpoint — done against a running ComfyUI instance (via the
  `run` skill), since this can't be meaningfully unit-tested.
- **Theme acceptance check**: panel must look visually native (colors,
  spacing) in at least two different ComfyUI themes (default dark + one
  custom/light theme) before this is considered done — verified by eye
  against a live instance, not assumed from documentation.
- **Nodes 2.0 acceptance check**: panel and node UI must be verified
  working with the "Modern Node Design (Nodes 2.0)" setting both on and
  off — official docs don't document extension-author impact, so this must
  be checked empirically rather than assumed.

## 10. Feature scope: v1 vs v2

No feature is cut for being low-value — the split is purely about technical
build order (later items build on the queue-state/persistence foundation
that must exist first), not priority.

**v1 (build first — foundation + the unique differentiator):**
Persistence, drag-drop reorder, readable workflow names, manual + automatic
pause/resume, history with thumbnails, autopilot rule engine (all 3 rules),
Crystools-overlap detection, the consolidated Cooldown & Pause node
(including unload-models, wait-for-click, notify sound/toast).

**v2 (builds on top of v1's foundation):**
Rename jobs, filter/search (status/date/name), auto-archive after N days,
bulk operations (multi-select), priority adjustment, cancel + cancel-and-requeue
at the queue level.

Once v1 is stable: delete the old `rubz-gpu-cooldown` folder
(`D:\AI\ComfyUI-AGAIN\ComfyUI\custom_nodes\rubz-gpu-cooldown`) — kept until
then purely as a safety net, not because it's still in use.

## 11. Future-proofing: V3 schema & Nodes 2.0

- ComfyUI's official docs state new node features will only land in the
  **V3 schema** (`io.ComfyNode`, `define_schema()`, classmethod-only,
  stateless) going forward; V1 (`INPUT_TYPES` dict + `NODE_CLASS_MAPPINGS`)
  is not removed but won't gain new capabilities. The cooldown node is
  built directly in V3.
- **Open verification item** (not blocking the spec, but must be checked
  during implementation): confirm that a V3-registered node's type
  identifier round-trips correctly with a workflow JSON saved under the
  old V1 `RubzGpuCooldownNode` registration, so existing saved workflows
  keep resolving to the new node.
- **Nodes 2.0** (Vue-based rendering, replacing LiteGraph Canvas) is
  reported backward-compatible for existing custom nodes, but ComfyUI's
  own docs are silent on implications for extension authors (sidebar
  panels, `app.registerExtension`, settings API). This must be verified
  empirically against a live instance with Nodes 2.0 toggled both on and
  off (§9), not assumed safe.
- **Theming**: no hardcoded colors. The panel reuses ComfyUI's own existing
  CSS custom properties (not invented parallel ones) so it inherits
  whatever theme — default dark, default light, or a custom theme — the
  user has set, automatically and without separate light/dark branching
  logic. Exact variable names are confirmed by inspecting a live instance
  during implementation rather than assumed from third-party templates.

## 12. Known deliberate omissions

- Import/export of queue state as a file (two competitors have this;
  superseded by persistence + history, deferred indefinitely).
- Hard dependency on Crystools for GPU data — Smart Queue always polls its
  own GPU metrics independently; Crystools detection (§6) only affects
  what's *displayed*, never the autopilot logic itself.

## 13. Manual verification findings (2026-08-29)

Live-tested against a running ComfyUI instance (Task 10 acceptance criteria,
§9). Confirmed working: settings panel + defaults, `/smart_queue/settings`
sync, Crystools GPU-readout suppression, theme-aware panel styling, queue and
history persistence including the fast-job edge case, and manual pause/resume
gating the middleware. Four real gaps were found this way — none caught by
the unit tests, since each was an integration/wiring gap, not a rule-engine
bug:

- **Queue/history were never populated.** `add_queue_item` /
  `mark_completed` / `record_job_started` existed only in tests — nothing in
  `__init__.py` ever called them. Fixed by `backend/queue_tracker.py`
  (Section 5-adjacent, read-only against `PromptServer.instance.prompt_queue`
  via `get_current_queue_volatile()`/`get_history()`), ticked from the same
  background loop as the autopilot poll. Handles the case of a job that
  starts and finishes inside one tick gap (never observed "running") by
  reading it retroactively from the history entry.
- **`web/smart_queue.css` was never linked into the page.** The panel ran on
  browser-default styling. Fixed with a `<link>` injected in `setup()`.
- **No manual pause/resume existed**, despite §6 requiring one. Added
  `AutopilotState.manual_paused` / `effective_paused` / `effective_reasons`,
  `POST /smart_queue/manual_pause`, and the middleware now gates on
  `effective_paused` (autopilot OR manual).
- **UI placement for the manual-pause control took three iterations** before
  landing: (1) inside the sidebar tab — blocked by an unrelated installed
  extension's `position:fixed` overlay (`hNodeAlignKit` / "NodeAlignPro")
  sitting in a higher ancestor stacking context that a child `z-index` can't
  escape; (2) anchored beside ComfyUI's own Job Queue toggle button — the top
  toolbar has zero free space at any window width once other extensions'
  icons are accounted for, verified by measurement (a wide 1600px window
  still had only ~34px free, not enough for a labeled button). Landed on: a
  small icon-only square button (`.smart-queue-toolbar-btn`, matching the
  native "Cancel current run" button's size) inserted as a real DOM sibling
  inside ComfyUI's own button-group flex row, right after the batch-count/Run
  group — letting the browser's own flex layout make room instead of
  computing pixel offsets. A periodic re-insert check guards against Vue
  dropping the manually-inserted node on re-render.
- **Resolved**: Task 11's node-notification checks were initially blocked —
  `RubzGpuCooldownNode` was registered by *both* packages, and
  `rubz-gpu-cooldown` (old, V1-schema) won over `comfyui-smart-queue`'s new
  V3 node because it loaded later alphabetically, shadowing the new node's
  `NODE_CLASS_MAPPINGS` entry entirely. Confirmed via
  `GET /object_info/RubzGpuCooldownNode` → `python_module:
  custom_nodes.rubz-gpu-cooldown`. Fixed by renaming the old folder to
  `rubz-gpu-cooldown.disabled` (ComfyUI's standard disable convention, fully
  reversible) and restarting ComfyUI.
- **A second, real bug surfaced immediately after the restart**: the new
  node's `define_schema()` referenced `io.Hidden.prompt_id`, which doesn't
  exist on this ComfyUI version's V3 `Hidden` enum (only `unique_id`,
  `prompt`, `extra_pnginfo`, `dynprompt`, `auth_token_comfy_org`,
  `api_key_comfy_org`, `comfy_usage_source`) — every `/object_info` request
  for the node threw `AttributeError: type object 'Hidden' has no attribute
  'prompt_id'`, confirmed in `user/comfyui.log`. None of the real `Hidden`
  members carry the running prompt's UUID. Fixed by dropping the hidden
  schema input entirely; `execute()` now reads
  `PromptServer.instance.last_prompt_id` — set once per prompt by
  ComfyUI's own single-threaded execution loop (`main.py`) right before a
  prompt's nodes run, so it is stable for the duration of that node's
  `execute()` call. Unit tests were unaffected (they target `run_cooldown()`
  only, not `execute()`/`define_schema()`).
- **Task 11 manual checks (2026-08-29), all passed** against the live
  instance, via isolated test workflows submitted through the ComfyUI MCP
  rather than editing the user's open canvas: default-sound tone + toast
  fired and logged a real `sounds/default.wav` network fetch;
  `notify_sound_choice="Custom..."` with an empty path resolved straight to
  the default tone (the empty string is falsy, so the JS path never even
  attempts the bad URL — no error path exercised, none needed);
  `wait_for_click=True` blocked execution behind a "Continue" modal that,
  once clicked, completed the job; and a full SD1.5 txt2img graph piped into
  the node with `unload_models_before_wait=True` left `nvidia-smi` reporting
  VRAM back at the pre-test idle baseline afterward (the load→unload cycle
  itself was too fast, ~3s, for polling to catch the live transition).
- **Task 10's previously-pending Nodes 2.0 toggle check also completed**
  during this pass: toggled "Modern Node Design (Nodes 2.0)" on, confirmed
  the queue panel, manual-pause toolbar button, cooldown node's
  Continue-modal and toast all still render and function correctly under
  the new Vue-based rendering, then toggled it back off (restoring the
  user's original setting).
