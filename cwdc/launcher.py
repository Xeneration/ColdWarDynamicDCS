"""One-click start and stop (`cwdc start` / `cwdc stop`, used by the desktop shortcuts).

Starts the web UI in the background if it isn't already running, opens it in the browser and,
with dcs=True, also starts DCS World unless a (non-server) DCS is already running. The server
keeps running after the launcher exits; its log is Saved Games\\DCS\\CWDC\\server.log.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8076
URL = f"http://127.0.0.1:{PORT}/"
DETACHED = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW


def server_up(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(URL + "api/campaigns", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def _log_path() -> Path:
    from cwdc import store
    p = store.campaigns_root().parent / "server.log"            # Saved Games\DCS\CWDC
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def start_server() -> bool:
    """Start `cwdc serve` detached. Returns True once it answers (up to 30 s)."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    log = _log_path().open("a", encoding="utf-8")
    subprocess.Popen([str(pyw if pyw.exists() else exe), "-m", "cwdc", "serve", "--no-browser",
                      "--port", str(PORT)], cwd=str(Path(__file__).resolve().parents[1]),
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     creationflags=DETACHED if sys.platform == "win32" else 0, close_fds=True)
    for _ in range(60):
        if server_up():
            return True
        time.sleep(0.5)
    return False


def dcs_running() -> bool:
    """Is a normal (player) DCS running? Soak servers (DCS.exe --server) don't count."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='DCS.exe'\" | "
                          "ForEach-Object { $_.CommandLine }"],
                         capture_output=True, text=True, timeout=30).stdout
    return any(line.strip() and "--server" not in line for line in out.splitlines())


def start_dcs() -> str:
    from cwdc.soak.runner import dcs_exe
    exe = dcs_exe()
    if not exe.is_file():
        return f"DCS not found at {exe}"
    if dcs_running():
        return "DCS is already running"
    subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=DETACHED if sys.platform == "win32" else 0,
                     close_fds=True)
    return "DCS is starting"


def start(dcs: bool = False) -> list[str]:
    notes = []
    if server_up():
        notes.append("web UI already running")
    elif start_server():
        notes.append("web UI started")
    else:
        notes.append(f"web UI did not start - see {_log_path()}")
    if dcs:
        notes.append(start_dcs())
    webbrowser.open(URL)
    return notes


def server_pids() -> list[int]:
    """Processes running the web UI (`-m cwdc serve`), however they were started."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                          "Where-Object { $_.CommandLine -match '-m\\s+cwdc\\s+serve' } | "
                          "ForEach-Object { $_.ProcessId }"],
                         capture_output=True, text=True, timeout=30).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def stop() -> list[str]:
    """Stop the web UI. Campaign saves are atomic, so nothing is lost; DCS is left alone."""
    pids = server_pids()
    if not pids and not server_up():
        return ["web UI was not running"]
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)
    for _ in range(20):
        if not server_up():
            return ["web UI stopped"]
        time.sleep(0.5)
    return [f"web UI is still answering on port {PORT} - was it started some other way?"]


def popup(title: str, lines: list[str]) -> None:
    """A message box, for the shortcuts (pythonw has no console). No-op off Windows."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "\n".join(lines), title, 0x40)   # MB_ICONINFORMATION


SHORTCUTS = [
    # name, arguments after the venv python, use a console window, icon
    ("ColdWarDynamic", "-m cwdc start", False, None),
    ("ColdWarDynamic + DCS", "-m cwdc start --dcs", False, "dcs"),
    ("Stop ColdWarDynamic", "-m cwdc stop --popup", False, "stop"),
    ("ColdWarDynamic overnight soak", "-m cwdc soak --hours 8 --instances 2", True, None),
]


def make_shortcuts(folder: Path | None = None) -> list[Path]:
    """Desktop shortcuts for the one-click start (re-run after moving the project)."""
    import os
    from cwdc.soak.runner import dcs_exe
    repo = Path(__file__).resolve().parents[1]
    scripts = Path(sys.executable).parent
    pyw = scripts / "pythonw.exe" if (scripts / "pythonw.exe").exists() else Path(sys.executable)
    if folder is None:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        folder = Path(out or os.path.expanduser("~/Desktop"))
    made = []
    for name, args, console, icon in SHORTCUTS:
        lnk = folder / f"{name}.lnk"
        target = scripts / "python.exe" if console else pyw
        if icon == "dcs" and dcs_exe().is_file():
            icon_loc = f"{dcs_exe()},0"
        elif icon == "stop":
            icon_loc = "%SystemRoot%\\System32\\shell32.dll,27"     # the shut-down icon
        else:
            icon_loc = "%SystemRoot%\\System32\\shell32.dll,13"
        ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
              f"$s.TargetPath = '{target}'; $s.Arguments = '{args}'; $s.WorkingDirectory = '{repo}'; "
              f"$s.IconLocation = '{icon_loc}'; $s.Description = 'ColdWarDynamicDCS: {name}'; $s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=30)
        made.append(lnk)
    return made
