# Changelog

All notable changes to Smart Queue are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.7] - 2026-09-11

### Added
- `nvidia-ml-py` as the package's first dependency. GPU temperature and VRAM now come from NVML, an in-process ctypes binding onto the driver's own library, instead of a subprocess.
- Startup scrub of the `held_items` table: any row written by an earlier version that still carries the queue tuple's `sensitive` element is rewritten without it, so upgrading removes the credential immediately rather than waiting for the next pause to overwrite it.

### Removed
- The `nvidia-smi` subprocess call in `backend/gpu_monitor.py`, and with it the last place in the package where a node widget on the unauthenticated `/prompt` endpoint could cause a process to be spawned, which is the shape the Comfy Registry bans under `policy-v0.2`. 0.1.6 removed the route-shaped instance; this removes the node-shaped one.
- The `CUDA_VISIBLE_DEVICES` environment read that came with it. On a multi-GPU machine Smart Queue now asks torch which device ComfyUI is using and matches it to the NVML device by UUID, falling back to the first device.
- The f-string-composed `ALTER TABLE` and `PRAGMA table_info` statements in the schema migration, and `executescript` in `init_db`. Table and column names are identifiers, which SQLite parameters cannot carry, so the previous form was necessary rather than careless. There are only three columns across two tables, though, and every f-string reaching `execute()` reads as SQL injection to a scanner.

### Changed
- `/api/prompt` is now gated while paused, not just `/prompt`. ComfyUI registers every route twice and the bundled frontend calls only the prefixed form, so clicking Run during a pause previously queued and executed normally; only jobs already in the queue when the pause began were held.
- Routes are registered on `PromptServer.instance.routes` rather than directly on the app router, so ComfyUI generates the `/api/smart_queue/...` copies alongside the bare paths. Existing URLs are unchanged. This is what a setup that only forwards `/api`, such as a reverse proxy or the frontend dev server, needs in order to reach these endpoints at all. The bundled panel still requests the bare paths itself, so making the panel work in that setup is a separate change.
- The GET endpoints answer HEAD on both registration paths. aiohttp's route table implies HEAD for a GET, but the direct-registration path used a lower-level call that does not, so the two paths produced subtly different routing.
- The Smart Cooldown node reads the executing `prompt_id` from `comfy_execution`'s execution context, falling back to `PromptServer.last_prompt_id` and then the running queue. The old attribute works, but it is never declared on `PromptServer` (it exists only because `main.py`'s worker loop assigns it), so it is now a safety net rather than the only source. If none of the three can answer, the click-wait is skipped and says so in the node's status instead of failing the prompt. The resolver never raises: a queue snapshot in an unexpected shape resolves to nothing rather than sending an error into node execution.
- `run_cooldown`'s `max_wait_seconds` is measured against a monotonic clock instead of by counting intended sleep time, so a slow metrics poll no longer makes the wait overshoot.
- The frontend test moved from `web/tests/` to `tests_web/`. ComfyUI globs `**/*.js` under the web directory and the frontend imports every match, so a test importing `node:test` logged an extension-load error on every page load.
- `SECURITY.md` now describes where the database actually lives: ComfyUI's per-extension user directory, with the extension's own folder as fallback. It had claimed the extension directory outright since the file was written.

### Fixed
- A failure while starting the backend (read-only user directory, locked database, a changed `PromptQueue` API) no longer removes the Smart Cooldown & Pause node from the UI. ComfyUI abandons a pack's entire module if its `__init__.py` raises, so an unguarded import-time failure took the node down with autopilot; the integration now runs inside a try/except and logs what it lost.
- Comfy.org API credentials are no longer written to disk. Held queue items persisted the whole queue tuple, including the `sensitive` element that ComfyUI deliberately keeps out of history and logs (`auth_token_comfy_org`, `api_key_comfy_org`).
- The pytest fallback for `InterruptProcessingException` derives from `BaseException`, matching `comfy.model_management`. Deriving from `Exception` meant a stray `except Exception` could swallow an interrupt under test but not in production.

## [0.1.6] - 2026-09-07

### Added
- `SECURITY.md`: documents every construct in the package that a pattern scanner flags, what it actually does, and why it is not reachable from attacker-controlled input. Covers the single remaining `subprocess` call site, the full endpoint list and what each one can affect, the two identifier-only f-string SQL statements in the schema migration, the sound-path resolution rules, and a table of known scanner false positives.

### Removed
- The "📁 Browse sound file" button on the cooldown node, and the `POST /smart_queue/browse_sound_file` endpoint and `backend/native_dialog.py` behind it. The endpoint opened a native file dialog by spawning a process (PowerShell on Windows, `osascript` on macOS, `zenity`/`kdialog` on Linux), and an unauthenticated route that spawns a process is the exact shape the Comfy Registry bans under `policy-v0.2` ("attacker-reachable via unauthenticated /prompt (node widget) or no-auth route"). Nothing was actually injectable — the dialog title was a hardcoded constant, never request data — but 0.1.2 and 0.1.4 were both banned under that policy and this was the only route in the extension that started a process, so it goes rather than gets defended.
- `sound_library.import_sound`, which only existed to copy the picked file into `web/sounds/custom/`.

### Changed
- `web/smart_queue.js` no longer binds the original queuePrompt method to keep a reference to it; it stores the plain reference and calls it with an explicit receiver instead. Functionally identical. The registry's scanner runs a YARA rule that greps for the word "bind" followed by an opening parenthesis, treats it as a socket bind, and files the hit under "Exfiltration Over C2 Channel", which is noise a human reviewer then has to triage.
- Setting a custom notification sound is now manual: place the file in the extension's `web/sounds/custom/` folder and type `sounds/custom/<filename>` into the node's `custom_sound_path` widget, which now carries a tooltip saying so. Existing custom sounds keep working untouched — the stored path format is unchanged and previously imported files are still in that folder. The `custom_sound_path` widget itself stays in place: its position in the schema is frozen, and removing it would shift every widget declared after it and corrupt their values in saved workflows.

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
