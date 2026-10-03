"""Start the agent automatically when the user signs in - for the downloaded
binary as well as a source checkout, and without administrator rights.

Windows: a per-user "Run" registry value (HKCU), which needs no elevation.
It launches the agent through `conhost.exe --headless`, so no console window
opens at sign-in - the binary is a console app, and on Windows 11 a plain
launch would open a Windows Terminal tab that a user might simply close.

Linux: a systemd user service with Restart=on-failure, enabled and started.

Either way the agent is first copied to a stable place inside its data
directory. A downloaded file usually sits in Downloads, and an auto-start
entry pointing there breaks the moment the user tidies that folder.

macOS is not supported: no macOS binary is published.
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from agent_paths import data_dir, is_frozen

TASK_NAME = "NetSentinel Agent"          # the Windows Run value's name
SERVICE_NAME = "netsentinel-agent"       # the systemd user unit
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _bin_dir() -> Path:
    return data_dir() / "bin"


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_executable() -> Path:
    """The binary copied into the data directory (frozen builds only)."""
    current = Path(sys.executable).resolve()
    target = _bin_dir() / current.name
    if target.exists() and target.resolve() == current:
        return target  # already running from the stable copy
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or _file_digest(target) != _file_digest(current):
        try:
            shutil.copy2(current, target)
        except PermissionError:
            # The stable copy is running (an older version started at sign-in)
            # and Windows will not overwrite a running executable. Keep it.
            if not target.exists():
                raise
    if sys.platform != "win32":
        target.chmod(target.stat().st_mode | 0o111)
    return target


def launch_command() -> List[str]:
    """The background command an auto-start entry runs."""
    if is_frozen():
        return [str(stable_executable()), "--start", "--background"]
    script = str(Path(__file__).resolve().parent / "agent.py")
    python = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = python.with_name("pythonw.exe")
        python = pythonw if pythonw.exists() else python
    return [str(python), script, "--start", "--background"]


# --- Windows -------------------------------------------------------------------

def _conhost() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "conhost.exe")


def _windows_value() -> str:
    cmd = launch_command()
    if cmd[0].lower().endswith("pythonw.exe"):
        return subprocess.list2cmdline(cmd)  # pythonw has no console at all
    return subprocess.list2cmdline([_conhost(), "--headless"] + cmd)


def _win_install() -> Tuple[bool, str]:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, _windows_value())
    return True, "It will start automatically, in the background, whenever you sign in to Windows."


def _win_uninstall() -> Tuple[bool, str]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, TASK_NAME)
    except FileNotFoundError:
        return True, "Automatic start was not set up."
    return True, "It will no longer start automatically when you sign in."


def _win_registered_command() -> str:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, TASK_NAME)[0]
    except FileNotFoundError:
        return ""


# --- Linux ---------------------------------------------------------------------

def _unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _unit_text() -> str:
    exec_start = " ".join(f'"{part}"' if " " in part else part for part in launch_command())
    return (
        "[Unit]\n"
        "Description=NetSentinel agent - reports this machine's network health\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
        # on-failure, not always: a second copy exits cleanly when one is
        # already running, and that must not become a restart loop.
        "Restart=on-failure\n"
        "RestartSec=30\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def _linux_install() -> Tuple[bool, str]:
    if not shutil.which("systemctl"):
        return False, ("This system has no systemd, so automatic start could not be set up. "
                       f"Add this line with `crontab -e` instead:  @reboot {' '.join(launch_command())}")
    unit = _unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(_unit_text())
    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", SERVICE_NAME)
    if r.returncode != 0:
        return False, f"systemd refused to enable the service: {(r.stderr or r.stdout).strip()}"
    return True, ("It is running in the background now and will start whenever you sign in. "
                  "To keep it reporting while nobody is signed in, run once:  loginctl enable-linger $USER")


def _linux_uninstall() -> Tuple[bool, str]:
    unit = _unit_path()
    if shutil.which("systemctl"):
        _systemctl("disable", "--now", SERVICE_NAME)
    if unit.exists():
        unit.unlink()
        if shutil.which("systemctl"):
            _systemctl("daemon-reload")
        return True, "It will no longer start automatically."
    return True, "Automatic start was not set up."


# --- Public --------------------------------------------------------------------

def install() -> Tuple[bool, str]:
    if sys.platform == "win32":
        return _win_install()
    if sys.platform.startswith("linux"):
        return _linux_install()
    return False, "Automatic start is supported on Windows and Linux only."


def uninstall() -> Tuple[bool, str]:
    if sys.platform == "win32":
        return _win_uninstall()
    if sys.platform.startswith("linux"):
        return _linux_uninstall()
    return True, "Automatic start is supported on Windows and Linux only."


def is_installed() -> bool:
    if sys.platform == "win32":
        return bool(_win_registered_command())
    if sys.platform.startswith("linux"):
        if not _unit_path().exists() or not shutil.which("systemctl"):
            return False
        return _systemctl("is-enabled", SERVICE_NAME).stdout.strip() == "enabled"
    return False


def start_in_background() -> None:
    """Start a detached background copy now, so a user who just set up
    automatic start does not have to sign out and in to get reporting."""
    cmd = launch_command()
    if sys.platform == "win32":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
        subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(cmd, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
