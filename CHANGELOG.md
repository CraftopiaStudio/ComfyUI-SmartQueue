# Changelog

All notable changes to Smart Queue are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- "Show in Explorer" on the history panel's right-click menu: resolves the row's stored thumbnail path back to the real output file (mirroring ComfyUI's own `/view` resolution) and opens it selected in the OS file manager. Windows also force-brings the Explorer window to the foreground, since a background process is normally blocked from stealing focus (`SetForegroundWindow`'s "foreground lock") — worked around with a synthetic Alt keypress plus matching the window by its actual folder path via `Shell.Application`, not by guessing from window title text.

### Fixed
- On Windows, revealing a file whose path contained spaces could pop a second, unselected Explorer window alongside the real one: the `/select,<path>` argument was passed as a list element, which Python's own command-line quoting wraps in an extra outer pair of quotes that Explorer parses inconsistently. Now passed as a single raw command-line string so only the path itself is quoted, matching Explorer's documented syntax.

## [0.1.5] - 2026-09-04

### Fixed
- Pending rows in the sidebar panel could survive forever, even across a page refresh, once their job was removed from ComfyUI's live queue by anything other than Smart Queue's own Cancel button — most commonly ComfyUI's native "Clear Queue" button, which bypasses `/smart_queue/cancel` entirely. `queue_tracker`'s sync tick now prunes any pending row whose prompt_id is no longer in the live running/queued lists and never reached history, since that only happens when it was cleared or cancelled out from under Smart Queue. Held rows (manual pause) are exempt, since they're deliberately outside the live queue.
- History sidebar thumbnails were silently blank for video outputs (e.g. `SaveVideo`'s `.mp4`) — `refreshHistory` always rendered an `<img>`, which browsers can't use to preview a video file. Now renders a `<video muted loop>` for video filenames and `<img>` for everything else, matching ComfyUI's own native queue panel.

## [0.1.4] - 2026-08-31

### Added
- Test that pins `SmartCooldownNode`'s widget declaration order and output socket order (parses `define_schema()` via `ast` since `comfy_api` isn't importable under pytest) — turns the "FROZEN ORDER, do not reorder" code comments into an enforced check instead of a comment someone could miss.
- Tooltip on the "Turn on autopilot" setting explaining that turning it off removes the sidebar panel/pause button entirely and stops all background queue tracking, not just hides them.

### Changed
- Turning autopilot off is now a true node-only mode: the background loop skips its per-5-second SQLite queue-tracking write (previously ran unconditionally even with the panel hidden and nothing reading it). Manual-pause release logic keeps running unconditionally as a safety net so a held job from before the toggle flip can't get stuck.
- `pyproject.toml` description and README clarify which features are NVIDIA-only (temperature/VRAM autopilot) versus GPU-agnostic (manual pause, panel, job-count autopilot) — moved out of the buried "Compatibility" section into a callout near the top.
- Replaced `__import__("time").sleep` with a normal `import time` in `backend/nodes/cooldown.py` — functionally identical, but `__import__(...)` is a pattern registry security scanners flag, and removing it is a no-cost step in narrowing down why 0.1.2/0.1.3 came back `NodeVersionStatusFlagged` on the registry.

### Fixed
- README test count updated to 212 (210 backend + 2 new schema-order tests).

### Fixed
- Registry `DisplayName` set to `ComfyUI-SmartQueue` (was `Smart Queue`) so the registry listing title/subtitle match the naming convention used by ComfyUI-CraftKit and ComfyUI-WorkflowOrganizer.

## [0.1.2] - 2026-08-30

### Fixed
- Package name changed from `comfyui-smartqueue` to `ComfyUI-SmartQueue` to match registry naming convention (required deleting and re-registering the node, since the node ID is locked after first publish).

## [0.1.1] - 2026-08-30

### Fixed
- Registry publish workflow: `REGISTRY_ACCESS_TOKEN` secret was never set, so publishing silently failed since it was added.

## [0.1.0] - 2026-08-30

### Added
- Autopilot: opt-in temperature/VRAM/job-count rules that pause and resume the queue automatically, with hysteresis.
- Manual pause with persistence across restarts.
- Persistent queue & history sidebar panel: drag-reorder, rename, search, bulk actions, history thumbnails with click-to-restore.
- Smart Cooldown & Pause node: fixed delay, temperature-wait, VRAM unload/cache-clear, wait-for-click gate, sound/popup notifications, dual passthrough lanes.
- Startup check that warns clearly if ComfyUI's internal queue API shape changes, instead of failing silently.
- Native sound/file picker now works on macOS (`osascript`) and Linux (`zenity`/`kdialog`), not just Windows.
- Automated frontend smoke tests (`node --test`) alongside the backend unit test suite.
- Public README with real screenshots and two ready-to-load workflow templates.
- README "Use Cases" section: six concrete scenarios the pack is meant for, including pausing a running batch to slip an urgent render in front of it.
- GitHub Actions workflow to publish to the Comfy Registry on a `pyproject.toml` version bump.

### Changed
- `nvidia-smi` polling now respects `CUDA_VISIBLE_DEVICES` on multi-GPU machines instead of always reading the first GPU.
- Queue/history state (`smart_queue.sqlite3`) now lives under ComfyUI's own `user/` directory instead of inside the extension folder, with automatic one-time migration from the old location.
- Smart Cooldown & Pause node outputs reordered to `passthrough`, `passthrough_2`, `status`, so the two passthrough sockets sit together instead of being split by the status output. Done before the first registry release on purpose: ComfyUI links outputs by position, so this order is fixed from 0.1.0 onward and anything new goes on the end.
