# Security

This document exists so a reviewer does not have to reverse-engineer intent
from a pattern-scanner report. It lists every construct in Smart Queue that a
security scanner flags, what it actually does, and why it is not reachable
from attacker-controlled input.

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
- **No HTTP endpoint spawns a process.** As of 0.1.6 there is exactly one
  process-spawning call site in the entire package, and it is not reachable
  from any route (see below).
- **All data stays local**, in one SQLite file inside the extension's own
  directory.

## Process execution: one call site

`backend/gpu_monitor.py`, in `poll_gpu_metrics()`:

```python
cmd = [
    "nvidia-smi",
    "--query-gpu=temperature.gpu,memory.used,memory.total",
    "--format=csv,noheader,nounits",
]
index = _target_gpu_index()
if index is not None:
    cmd += ["-i", str(index)]

result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
```

- The argument list is a hardcoded constant. Nothing from an HTTP request, a
  node widget, a workflow, or a file is interpolated into it.
- The only variable element is `-i <index>`, and `_target_gpu_index()` returns
  either `None` or an `int`: it reads `CUDA_VISIBLE_DEVICES`, takes the first
  comma-separated entry, and returns it only if `str.isdigit()` passes.
  Non-numeric values (for example the `GPU-<uuid>` form) return `None`.
- `shell=False` (the default). There is no shell, so shell metacharacters have
  no meaning even if one could get a value in.
- It is bounded by a timeout and wrapped in a blanket `except Exception` that
  returns empty metrics, so a machine without `nvidia-smi` degrades to
  "temperature and VRAM rules disabled" rather than erroring.

**Reachability:** called by the background autopilot poll loop, and during
execution of the `SmartCooldownNode` when its temperature-wait option is on.
A `/prompt` submission therefore causes it to run, but no widget value on that
node, and no field in the submitted workflow, can influence the command line.
The node's widgets are floats and booleans (target temperature, poll interval,
maximum wait) that are used as numeric comparisons against the returned
metrics.

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

None of them start a process, read or write a file outside the extension's own
SQLite database, accept a filesystem path, or return file contents. Their
worst-case effect is manipulation of queue ordering and pause state, which is
a subset of what ComfyUI's own unauthenticated queue endpoints already allow.

## SQL

`backend/persistence.py` uses parameterized queries (`?` placeholders)
everywhere that a value is involved.

Two statements use f-strings, in `_migrate_schema()`:

```python
existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
```

`table`, `name`, and `col_type` come from `_ADDED_COLUMNS`, a module-level
constant dict of literal strings. No caller passes anything in. SQLite does
not accept bound parameters for identifiers in `PRAGMA` or `ALTER TABLE`, so
this is the only available form.

## Filesystem

- The SQLite database lives inside the extension's own directory.
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
| `python_network_operations` on `backend/persistence.py`, "Exfiltration Over C2 Channel" | Matches the literal string `.connect(` in `sqlite3.connect(db_path)`. Opening a local SQLite file, not a socket. |
| `python_database_connections` on the same line | The same local SQLite file. |
| `python_environment_manipulation` on `backend/gpu_monitor.py` | `os.environ.get("CUDA_VISIBLE_DEVICES", "")`. A read, to poll the same GPU ComfyUI itself uses on a multi-GPU machine. Nothing is written to the environment anywhere in the package. |
| `python_command_injection_risk` on `backend/gpu_monitor.py` | The single `nvidia-smi` call documented above. |
| `python_network_operations` on `web/smart_queue.js` (in versions up to 0.1.5) | Matched the literal string `.bind(` in `app.queuePrompt.bind(app)`, a JavaScript function-binding call with no relation to sockets. Rewritten in 0.1.6 to avoid the pattern. |
| Any `urllib` import | `backend/queue_tracker.py` imports `urllib.parse.urlencode`, a pure string-formatting helper used to build the `filename=...&subfolder=...&type=output` query that ComfyUI's own thumbnail URLs use. `urllib.request` is never imported. |
