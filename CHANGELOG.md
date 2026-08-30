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

### Changed
- `nvidia-smi` polling now respects `CUDA_VISIBLE_DEVICES` on multi-GPU machines instead of always reading the first GPU.
- Queue/history state (`smart_queue.sqlite3`) now lives under ComfyUI's own `user/` directory instead of inside the extension folder, with automatic one-time migration from the old location.
