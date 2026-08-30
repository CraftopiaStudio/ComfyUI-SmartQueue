# Changelog

All notable changes to Smart Queue are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
