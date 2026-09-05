"""Reveals a rendered output file in the OS file manager, from a history row's
stored thumbnail_path (e.g. "filename=a.png&subfolder=&type=output").

Path resolution mirrors ComfyUI's own view_image handler (server.py) so the
same query string that already renders a thumbnail resolves to the same file.
"""

import asyncio
import logging
import os
import subprocess
import sys
from typing import Callable
from urllib.parse import parse_qs

from aiohttp import web

logger = logging.getLogger(__name__)


def _resolve_output_path(query: str, get_directory_by_type: Callable[[str], str | None] | None = None) -> str | None:
    if get_directory_by_type is None:
        import folder_paths  # type: ignore[import-not-found]
        get_directory_by_type = folder_paths.get_directory_by_type

    params = parse_qs(query)
    filename = params.get("filename", [""])[0]
    if not filename or filename[0] == "/" or ".." in filename:
        return None

    output_dir = get_directory_by_type(params.get("type", ["output"])[0])
    if output_dir is None:
        return None

    subfolder = params.get("subfolder", [""])[0]
    if subfolder:
        full_dir = os.path.join(output_dir, subfolder)
        if os.path.commonpath((os.path.abspath(full_dir), output_dir)) != output_dir:
            return None
        output_dir = full_dir

    return os.path.join(output_dir, os.path.basename(filename))


# PowerShell script (same COM/P-Invoke style as native_dialog.py's file
# picker): finds the Explorer window actually showing our target folder via
# Shell.Application's Windows() collection — matching on the real folder
# path rather than guessing from window title text — and foregrounds it.
#
# SetForegroundWindow alone would silently no-op here: Windows enforces a
# "foreground lock" that refuses it for a background process (this Python
# server owns no window of its own) and just flashes the taskbar icon
# instead. A synthetic Alt keypress is the standard, minimal way to reset
# that lock's timer so the very next SetForegroundWindow call is honored.
_FOREGROUND_EXPLORER_PS = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class SmartQueueForeground {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@

$target = '__FOLDER__'
$shell = New-Object -ComObject Shell.Application
$deadline = (Get-Date).AddSeconds(2)
$hwnd = [IntPtr]::Zero
while ((Get-Date) -lt $deadline -and $hwnd -eq [IntPtr]::Zero) {
    foreach ($w in @($shell.Windows())) {
        try {
            if ($w.Document.Folder.Self.Path -eq $target) { $hwnd = [IntPtr]$w.HWND; break }
        } catch {}
    }
    if ($hwnd -eq [IntPtr]::Zero) { Start-Sleep -Milliseconds 150 }
}

if ($hwnd -ne [IntPtr]::Zero) {
    [SmartQueueForeground]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)  # Alt down
    [SmartQueueForeground]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)  # Alt up
    [SmartQueueForeground]::ShowWindow($hwnd, 9) | Out-Null  # SW_RESTORE
    [SmartQueueForeground]::SetForegroundWindow($hwnd) | Out-Null
}
"""


def _bring_explorer_to_foreground(folder: str) -> None:
    script = _FOREGROUND_EXPLORER_PS.replace("__FOLDER__", folder.replace("'", "''"))
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=5,
    )


def _reveal(path: str) -> None:
    if sys.platform == "win32":
        # Passed as a raw command-line string (not a list) so only the path
        # itself is quoted, matching Explorer's documented "/select,<quoted
        # path>" syntax exactly. A list argument here would go through
        # Python's own list2cmdline quoting, which wraps the *entire*
        # "/select,<path with spaces>" token in an outer pair of quotes —
        # Explorer parses that malformed form inconsistently and can open a
        # second, unselected fallback window alongside the real one.
        subprocess.Popen(f'explorer /select,"{path}"')
        # Best-effort UI polish only — the file is already selected in
        # Explorer at this point regardless of whether this succeeds, so a
        # failure here must never turn into an error response for the user.
        try:
            _bring_explorer_to_foreground(os.path.dirname(path))
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("[Smart Queue] could not bring Explorer to the foreground")
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        # No universal "select this file" affordance on Linux file managers;
        # opening the containing folder is the best portable fallback.
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


async def post_reveal_in_explorer(request: web.Request) -> web.Response:
    if request.remote not in ("127.0.0.1", "::1"):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    payload = await request.json()
    query = str(payload.get("thumbnail_path", ""))
    if not query:
        return web.json_response({"ok": False, "error": "no file for this item"}, status=400)

    path = _resolve_output_path(query)
    if path is None or not os.path.isfile(path):
        return web.json_response({"ok": False, "error": "file not found on disk"}, status=404)

    try:
        # _reveal briefly polls for the Explorer window on win32 (up to
        # ~2s) — keep that off the event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _reveal, path)
    except OSError as exc:
        logger.error("[Smart Queue] failed to open file manager: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)

    return web.json_response({"ok": True})
