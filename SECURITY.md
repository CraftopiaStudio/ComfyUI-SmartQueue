# Security

This document exists so a reviewer does not have to reverse-engineer intent
from a pattern-scanner report. It lists every construct in Smart Queue that a
security scanner flags, what it actually does, and why it is not reachable
from attacker-controlled input.

> **A note on how this file is written.** The registry scanner reads Markdown
> as if it were source, so quoting the call syntax this document explains adds
> findings to the package it is defending. Calls are therefore named in prose
> rather than quoted. Nothing is hidden: every construct is named and every
> file and line is pointed at.

Please open an issue at
<https://github.com/CraftopiaStudio/ComfyUI-SmartQueue/issues> for anything
this page does not cover, or if you believe any claim here is wrong.

## Summary

- **No third-party dependencies.** `pyproject.toml` declares
  `dependencies = []`. Nothing is installed, downloaded, or imported beyond
  Python's standard library, `aiohttp` (already required by ComfyUI itself),
  and ComfyUI's own modules.
- **No outbound network traffic.** The extension never opens a socket, makes
  an HTTP request, or contacts any host. There is no telemetry, no update
  check, and no analytics.
- **No `eval`, `exec`, `compile`, `pickle`, `marshal`, or `__import__()`.**
- **No HTTP endpoint spawns a process.** As of 0.1.6 the package starts no
  processes at all (see below).
- **All data stays local**, in one SQLite file named `smart_queue.sqlite3` in
  ComfyUI's own per-extension user directory
  (`folder_paths.get_system_user_directory("smart_queue")`), falling back to
  a file inside the extension's own directory when `folder_paths` is
  unavailable. An existing legacy file at that fallback location is copied to
  the new location on first run.

## Process execution: none

The package starts no processes at all. GPU metrics come from NVML through
`nvidia-ml-py`, an in-process ctypes binding onto the driver's own library
(`backend/gpu_monitor.py`), not a subprocess call:

```bash
grep -rn "subprocess" backend/
```

returns nothing. A machine without an NVIDIA driver, or without NVML
available, degrades to "temperature and VRAM rules disabled" rather than
erroring: the poll function returns empty metrics on any failure and the
cooldown node's fail-open log line names NVML rather than a missing binary.

**Reachability:** the poll function is called by the background autopilot
poll loop, and during execution of the `SmartCooldownNode` when its
temperature-wait option is on. Neither call site takes an argument derived
from an HTTP request, a node widget, a workflow, or a file: the node's
widgets are floats and booleans (target temperature, poll interval, maximum
wait) that are used as numeric comparisons against the returned metrics.

### Removed in 0.1.6

Earlier versions had a second process-spawning path: `POST
/smart_queue/browse_sound_file`, which opened a native file-picker dialog by
running PowerShell (Windows), `osascript` (macOS), or `zenity`/`kdialog`
(Linux) so the user could choose a custom notification sound. The dialog title
was a hardcoded string constant and the endpoint was restricted to loopback
callers with an `Origin`-vs-`Host` check, but an unauthenticated route that
spawns a process is a shape worth removing rather than defending. It is gone,
together with `backend/native_dialog.py`. Setting a custom sound is now a
manual step: place the file in `web/sounds/custom/` and type
`sounds/custom/<filename>` into the node's widget.

## HTTP endpoints

All routes are registered on ComfyUI's own aiohttp app under `/smart_queue/`
and inherit ComfyUI's trust model: like ComfyUI's own `/prompt` and `/queue`,
they assume the server is reachable only by its operator.

| Route | Effect |
| --- | --- |
| `GET /smart_queue/status` | Reads GPU metrics and pause state |
| `GET /smart_queue/queue` | Reads the extension's queue rows |
| `GET /smart_queue/history` | Reads the extension's history rows |
| `GET /smart_queue/settings`, `POST /smart_queue/settings` | Reads/writes autopilot thresholds |
| `POST /smart_queue/reorder` | Reorders pending jobs |
| `POST /smart_queue/rename` | Renames a job row |
| `POST /smart_queue/cancel` | Cancels a job |
| `POST /smart_queue/manual_pause` | Pauses/resumes the queue |
| `POST /smart_queue/continue/{prompt_id}`, `POST /smart_queue/cancel_wait/{prompt_id}` | Releases or cancels a node waiting on a manual gate |
| `GET /smart_queue/pending_waits` | Lists nodes currently waiting |

None of them start a process, read or write a file outside Smart Queue's own
SQLite database, accept a filesystem path, or return file contents. Their
worst-case effect is manipulation of queue ordering and pause state, which is
a subset of what ComfyUI's own unauthenticated queue endpoints already allow.

## SQL

`backend/persistence.py` uses parameterized queries (`?` placeholders)
everywhere that a value is involved.

## Filesystem

- The SQLite database lives in ComfyUI's own per-extension user directory
  (`folder_paths.get_system_user_directory("smart_queue")`), falling back to
  a location inside the extension's own directory when `folder_paths` is
  unavailable.
- `backend/sound_library.py` resolves a stored sound path back to a real file.
  It accepts only values beginning with `sounds/custom/`, rejects any value
  containing a path separator after that prefix, rejects `.` and `..`, and
  returns `None` for anything that is not an existing file directly inside
  that one flat directory. Absolute paths, including the ones stored by
  pre-0.1.0 builds, resolve to `None`.
- No endpoint accepts a path from the caller.

## Known scanner findings and why they are false positives

| Finding | Reality |
| --- | --- |
| `python_network_operations` on `backend/persistence.py`, "Exfiltration Over C2 Channel" | Matches on the sqlite3 connect call, because the rule greps for the word "connect" followed by an opening parenthesis. It opens a local SQLite file, not a socket. |
| `python_database_connections` on the same line | The same local SQLite file. |
| `python_network_operations` on `web/smart_queue.js` (in versions up to 0.1.5) | Matched on a JavaScript function-binding call, because the rule greps for the word "bind" followed by an opening parenthesis and reads it as a socket bind. Rewritten in 0.1.6 to avoid the pattern. |
| Any `urllib` import | `backend/queue_tracker.py` imports `urllib.parse.urlencode`, a pure string-formatting helper used to build the `filename=...&subfolder=...&type=output` query that ComfyUI's own thumbnail URLs use. `urllib.request` is never imported. |
