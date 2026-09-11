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

- **One dependency.** `pyproject.toml` declares `nvidia-ml-py`, carrying an
  environment marker that excludes aarch64 so it is not installed on Jetson
  and other ARM boards, and nothing else. That package is NVIDIA's own ctypes
  binding onto the NVML library that ships with the driver; it is what
  replaced this package's last subprocess call in 0.1.7. Beyond it, nothing
  is installed, downloaded, or imported except Python's standard library,
  `aiohttp` (already required by ComfyUI itself), and ComfyUI's own modules.
  Versions up to 0.1.6 declared no dependencies at all.
- **No outbound network traffic.** This package never opens a socket, makes
  an HTTP request, or contacts any host. There is no telemetry, no update
  check, and no analytics. Since 0.1.7 that statement covers one dependency
  as well as this package's own source, which is worth saying plainly: NVML
  reads local driver state through a library already on the machine, and the
  binding is maintained by NVIDIA, but the guarantee now rests on that
  package too and not only on the code in this repository.
- **No `eval`, `exec`, `compile`, `pickle`, `marshal`, or `__import__()`.**
- **The package starts no processes at all**, as of 0.1.7. This took two
  releases: 0.1.6 removed the only HTTP route that spawned one, and 0.1.7
  removed the only remaining call, which was reachable from a node widget
  (see below). 0.1.6 was therefore free of route-reachable spawns but not of
  node-reachable ones.
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

### Removed in 0.1.7

Up to and including 0.1.6, GPU temperature and VRAM were read by running the
driver's command-line query utility (`nvidia-smi`) as a child process and
parsing its CSV output. No part of that command line came from a request, a
widget, or a file: it was a fixed argument list invoked without a shell, and
the only values that crossed the boundary were the two numbers it printed
back. It was still a process spawn that a workflow could cause, because the
cooldown node's temperature-wait option runs the poll during execution of a
prompt, and prompts arrive on an unauthenticated endpoint. That is the shape
`policy-v0.2` describes, so it is gone rather than defended. NVML answers the
same two questions in-process.

The environment-variable read that selected which GPU to report on went with
it. Device selection now goes through torch and a UUID match, as described
above.

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

As of 0.1.7 they are registered through ComfyUI's own route table rather than
on the app router directly, so ComfyUI generates its usual `/api/`-prefixed
duplicate of each one, exactly as it does for its own endpoints. The set of
routes and what each can affect is unchanged; there is now a second URL that
reaches each of them.

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

## The one ComfyUI endpoint Smart Queue interposes on

Pausing has to stop new submissions, so the package installs an aiohttp
middleware on ComfyUI's app. It reads two fields of each request, the path and
the method, and returns HTTP 423 with a short reason when a POST arrives at
the prompt endpoint while the queue is paused. Every other request goes
straight to ComfyUI's own handler untouched. The middleware never reads a
request body, never modifies one, and never replaces ComfyUI's prompt handler.
`backend/queue_middleware.py` is the whole of it, and it is under thirty lines.

As of 0.1.7 the gate covers both the bare and the `/api`-prefixed form of that
endpoint. Earlier versions named only the bare path, which the bundled
frontend never calls, so a pause did not in fact hold new submissions. That
was a broken product feature rather than a security weakness, but it changes
what this middleware does, so it is recorded here.

## SQL

`backend/persistence.py` uses parameterized queries (`?` placeholders)
everywhere that a value is involved.

As of 0.1.7 no statement in the package is composed with an f-string, and the
schema is applied one statement at a time instead of as a single script. Up to
0.1.6 the migration built three column-adding statements and a table-inspection
pragma by interpolating table and column names, which SQLite parameters cannot
carry. Those names were hardcoded constants and never came from input, but the
construct reads as injection to a scanner and there are only three of them, so
they are now written out literally.

## What the database holds, and one thing it used to

The SQLite file holds queue and history rows (prompt ids, job names,
timestamps, status, output filenames) and the autopilot's own settings.

While the queue is paused, each held job is stored together with the queue
entry ComfyUI handed over, so it can be put back afterwards. Up to 0.1.6 that
entry was stored whole, and a ComfyUI queue entry carries a final element
holding the values ComfyUI deliberately keeps out of its own history and logs:
the Comfy.org authentication token and API key that API nodes use. Those were
therefore written to disk. This was found by reviewing this package's own
persistence path, not reported from outside.

As of 0.1.7 that element is dropped before the row is written and an empty one
is substituted when the job is restored, and startup rewrites any row an
earlier version left behind, so upgrading clears them instead of waiting for
the next pause to overwrite them. A job released after a restart no longer
carries its API-node token and the user signs in again, which is the right
trade.

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

## Tests

What the registry receives is the runtime code and nothing else: `.comfyignore`
keeps the test suite out of the published archive, so it is not part of what a
scanner has to read. The suite is in the repository, 230 tests as of 0.1.7,
covering the persistence layer, the pause middleware, route registration, the
GPU metrics poll, the prompt-id resolver, the sound-path rules, and the node's
own behaviour:

<https://github.com/CraftopiaStudio/ComfyUI-SmartQueue/tree/master/tests>

Several of them exist specifically to hold the claims on this page in place.
Among others: that a held queue row is written without the element carrying
the Comfy.org token and key, that the pause gate covers both forms of the
prompt endpoint, that the metrics poll returns empty values instead of raising
when NVML is absent, and that a failure while starting the backend still
leaves the node registered.

## Known scanner findings and why they are false positives

| Finding | Reality |
| --- | --- |
| `python_network_operations` on `backend/persistence.py`, "Exfiltration Over C2 Channel" | Matches on the sqlite3 connect call, because the rule greps for the word "connect" followed by an opening parenthesis. It opens a local SQLite file, not a socket. |
| `python_database_connections` on the same line | The same local SQLite file. |
| `python_network_operations` on `web/smart_queue.js` (in versions up to 0.1.5) | Matched on a JavaScript function-binding call, because the rule greps for the word "bind" followed by an opening parenthesis and reads it as a socket bind. Rewritten in 0.1.6 to avoid the pattern. |
| Any `urllib` import | `backend/queue_tracker.py` imports `urllib.parse.urlencode`, a pure string-formatting helper used to build the `filename=...&subfolder=...&type=output` query that ComfyUI's own thumbnail URLs use. `urllib.request` is never imported. |
| A newly introduced dependency, or one that reaches a native library through ctypes (from 0.1.7) | `nvidia-ml-py` is NVIDIA's own binding onto NVML, the management library that ships with the driver. It is this package's only dependency, it arrived in 0.1.7, and it exists to replace a subprocess call, which is a trade this document considers worth making explicit rather than quiet. It is deliberately not version-pinned: the four NVML functions called here (device handle by index, UUID, temperature, memory info) are long-standing parts of that API, and pinning a package that tracks the installed driver tends to break installs rather than protect them. The only constraint on it is the aarch64 exclusion described above. |
