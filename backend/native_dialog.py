"""Windows native file/folder picker, invoked from a browser-facing endpoint.

Ported from ComfyUI-CraftKit's own browse_folder endpoint (__init__.py) —
same IFileDialog COM interop via a hidden PowerShell/WinForms host, just
parameterized so the same dialog can pick either a folder or a single file.
"""

import asyncio
import concurrent.futures
import subprocess
import sys
import threading
from typing import Callable

from aiohttp import web

_dialog_lock = threading.Lock()
# Must match the literal 'SMARTQUEUE_DIALOG_ERROR:' prefix written by the
# PowerShell catch block below (kept literal there to avoid brace-escaping
# a dense here-string).
_DIALOG_ERROR_PREFIX = "SMARTQUEUE_DIALOG_ERROR:"


def _run_dialog(title: str, pick_folder: bool) -> str:
    if sys.platform != "win32":
        return ""
    # FOS_FORCEFILESYSTEM [| FOS_PICKFOLDERS] — plain PowerShell hex/bitwise
    # syntax, not C#'s "0x40u" literal suffix (that parses fine inside the
    # Add-Type C# block above, but this flags value is substituted into the
    # PowerShell call site further down, where "u" is a syntax error).
    options_flags = "0x40" if not pick_folder else "(0x20 -bor 0x40)"
    ps = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class SmartQueueFilePicker
{
    [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
    private class FileOpenDialogRCW { }

    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileDialog
    {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint cFileTypes, IntPtr rgFilterSpec);
        void SetFileTypeIndex(uint iFileType);
        void GetFileTypeIndex(out uint piFileType);
        void Advise(IntPtr pfde, out uint pdwCookie);
        void Unadvise(uint dwCookie);
        void SetOptions(uint fos);
        void GetOptions(out uint fos);
        void SetDefaultFolder(IntPtr psi);
        void SetFolder(IntPtr psi);
        void GetFolder(out IntPtr ppsi);
        void GetCurrentSelection(out IntPtr ppsi);
        void SetFileName(string pszName);
        void GetFileName(out IntPtr pszName);
        void SetTitle(string pszTitle);
        void SetOkButtonLabel(string pszText);
        void SetFileNameLabel(string pszLabel);
        void GetResult(out IShellItemLocal ppsi);
        void AddPlace(IntPtr psi, uint fdap);
        void SetDefaultExtension(string pszDefaultExtension);
        void Close(int hr);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr pFilter);
    }

    [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItemLocal
    {
        void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
        void GetParent(out IShellItemLocal ppsi);
        void GetDisplayName(uint sigdnName, out IntPtr ppszName);
        void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
        void Compare(IShellItemLocal psi, uint hint, out int piOrder);
    }

    public static string ShowDialog(IntPtr owner, string title, uint options)
    {
        const uint ERROR_CANCELLED = 0x800704C7;
        var dialog = (IFileDialog)new FileOpenDialogRCW();
        dialog.SetOptions(options);
        if (!string.IsNullOrEmpty(title)) dialog.SetTitle(title);
        int hr = dialog.Show(owner);
        if (hr != 0)
        {
            if ((uint)hr == ERROR_CANCELLED) return null;
            throw new System.Runtime.InteropServices.COMException("IFileDialog.Show failed", hr);
        }
        IShellItemLocal item;
        dialog.GetResult(out item);
        IntPtr pszPath = IntPtr.Zero;
        try
        {
            item.GetDisplayName(0x80058000u, out pszPath); // SIGDN_FILESYSPATH
            return Marshal.PtrToStringUni(pszPath);
        }
        finally
        {
            if (pszPath != IntPtr.Zero) Marshal.FreeCoTaskMem(pszPath);
        }
    }
}
"@

$r = ''
$o = New-Object System.Windows.Forms.Form
$o.TopMost = $true
$o.ShowInTaskbar = $false
$o.FormBorderStyle = 'None'
$o.Width = 1; $o.Height = 1; $o.Opacity = 0
$o.StartPosition = 'CenterScreen'
$o.Add_Shown({
    $o.Activate()
    try {
        $path = [SmartQueueFilePicker]::ShowDialog($o.Handle, '__TITLE__', __OPTIONS__)
        if ($path) { $script:r = $path }
    } catch {
        # Must match _DIALOG_ERROR_PREFIX in native_dialog.py.
        $script:r = 'SMARTQUEUE_DIALOG_ERROR:' + $_.Exception.Message
    }
    $o.Close()
})
$o.ShowDialog() | Out-Null
$r
""".replace("__TITLE__", title.replace("'", "''")).replace("__OPTIONS__", options_flags)

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=300
        )
    except subprocess.TimeoutExpired:
        return ""
    return result.stdout.strip()


async def browse_path(
    request: web.Request,
    *,
    pick_folder: bool,
    title: str,
    transform: Callable[[str], str] | None = None,
) -> web.Response:
    """Shared handler for a native folder/file picker endpoint.

    `transform` post-processes the picked path before it's returned (used by
    the sound picker to copy the file into the extension's web dir and hand
    back a browser-loadable relative path). A ValueError from it becomes a 400
    with its message, so the UI can show why the pick was rejected.
    """
    if request.remote not in ("127.0.0.1", "::1"):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    origin = request.headers.get("Origin")
    if origin is not None:
        host = request.headers.get("Host", "")
        origin_host = origin.split("://", 1)[-1]
        if origin_host != host:
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    if not _dialog_lock.acquire(blocking=False):
        return web.json_response({"ok": False, "error": "A file dialog is already open."}, status=409)
    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            path = await loop.run_in_executor(pool, _run_dialog, title, pick_folder)
    finally:
        _dialog_lock.release()

    if path.startswith(_DIALOG_ERROR_PREFIX):
        return web.json_response({"ok": False, "error": path.removeprefix(_DIALOG_ERROR_PREFIX)}, status=500)

    import os
    is_valid = os.path.isdir(path) if pick_folder else os.path.isfile(path)
    if path and is_valid:
        if transform is not None:
            try:
                path = transform(path)
            except ValueError as exc:
                return web.json_response({"ok": False, "error": str(exc)}, status=400)
            except OSError as exc:
                return web.json_response({"ok": False, "error": f"Could not import file: {exc}"}, status=500)
        return web.json_response({"ok": True, "path": path})
    return web.json_response({"ok": False, "cancelled": True})
