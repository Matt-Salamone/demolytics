from __future__ import annotations

import configparser
import ctypes
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk
from tkinter import messagebox

from demolytics.config.rocket_league import DEFAULT_STATS_API_RELATIVE_PATH

if TYPE_CHECKING:
    from customtkinter import CTkBaseClass

LOGGER = logging.getLogger(__name__)

PACKET_SEND_RATE_TARGET = 20

_PROTECTED_FOLDER_EXPLANATION = (
    "Rocket League appears to be installed under a protected Windows folder "
    "(for example Program Files). Changing files there requires administrator "
    "permission. Demolytics could not save the Stats API configuration without "
    "that access."
)

_RESTART_RL_NOTE = (
    "If Rocket League is running, you must restart it after this change for the "
    "Stats API settings to take effect."
)


def _ini_path_for_install(install_dir: str | Path) -> Path:
    return Path(install_dir) / DEFAULT_STATS_API_RELATIVE_PATH


def _patch_default_stats_api_ini(ini_path: Path, packet_send_rate: int = PACKET_SEND_RATE_TARGET) -> None:
    """Read and write DefaultStatsAPI.ini with configparser; raises OSError subclasses on failure."""
    ini_path = ini_path.resolve()
    ini_path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    parser.optionxform = str
    if ini_path.exists():
        parser.read(ini_path, encoding="utf-8-sig")

    if not parser.has_section("StatsAPI"):
        parser.add_section("StatsAPI")

    parser.set("StatsAPI", "PacketSendRate", str(packet_send_rate))

    with open(ini_path, "w", encoding="utf-8", newline="") as handle:
        parser.write(handle)


def _manual_steps_text(ini_path: Path) -> str:
    path_str = str(ini_path.resolve())
    return (
        f"File path (copy as needed):\n{path_str}\n\n"
        "Step-by-step:\n"
        "1. Close Rocket League if it is running (recommended before editing).\n"
        "2. Open the file above in a text editor. If Windows blocks saving, open "
        "the editor with Run as administrator, or copy the file to your Desktop, "
        "edit it, then copy it back and allow the UAC prompt.\n"
        "3. Ensure there is a [StatsAPI] section. Under it, set or change exactly:\n"
        f"   PacketSendRate={PACKET_SEND_RATE_TARGET}\n"
        "4. Save the file.\n"
        "5. Start Rocket League again.\n\n"
        + _RESTART_RL_NOTE
    )


def _resolve_python_for_elevated_helper() -> str | None:
    for cmd in ("py", "python", "python3"):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def _elevated_patch_script_body(ini_path: Path, packet_send_rate: int) -> str:
    """Single-file Python source run elevated; paths embedded with repr for safety."""
    return (
        "import configparser\n"
        "from pathlib import Path\n"
        f"path = Path({repr(str(ini_path.resolve()))})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "parser = configparser.ConfigParser()\n"
        "parser.optionxform = str\n"
        "if path.exists():\n"
        "    parser.read(path, encoding='utf-8-sig')\n"
        "if not parser.has_section('StatsAPI'):\n"
        "    parser.add_section('StatsAPI')\n"
        f"parser.set('StatsAPI', 'PacketSendRate', {repr(str(packet_send_rate))})\n"
        "with open(path, 'w', encoding='utf-8', newline='') as handle:\n"
        "    parser.write(handle)\n"
    )


def _shell_execute_runas(executable: str, parameters: str | None) -> int:
    """Returns result from ShellExecuteW; values > 32 indicate success."""
    shell32 = ctypes.windll.shell32
    rc = shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        parameters,
        None,
        1,
    )
    return int(rc)


def _launch_elevated_ini_patch(ini_path: Path, packet_send_rate: int = PACKET_SEND_RATE_TARGET) -> bool:
    """
    Start a short-lived elevated subprocess that only patches the INI file.
    Does not restart the main PyInstaller GUI.
    """
    if os.name != "nt":
        return False

    python_exe = _resolve_python_for_elevated_helper()
    if not python_exe:
        messagebox.showerror(
            "Demolytics",
            "Could not find Python on PATH (py / python). Use the manual steps, "
            "or install the Python launcher and try again.",
        )
        return False

    script_body = _elevated_patch_script_body(ini_path, packet_send_rate)
    fd, tmp_path = tempfile.mkstemp(prefix="demolytics_stats_api_", suffix=".py")
    os.close(fd)
    tmp_file = Path(tmp_path)
    try:
        tmp_file.write_text(script_body, encoding="utf-8")
    except OSError as exc:
        LOGGER.exception("Could not write temporary patch script")
        messagebox.showerror("Demolytics", f"Could not prepare elevated helper:\n{exc}")
        return False

    is_py_launcher = Path(python_exe).name.lower() in ("py.exe", "py")
    if is_py_launcher:
        params = f'-3 "{tmp_path}"'
    else:
        params = f'"{tmp_path}"'

    rc = _shell_execute_runas(python_exe, params)
    if rc <= 32:
        messagebox.showerror(
            "Demolytics",
            f"Could not start elevated helper (ShellExecute returned {rc}). "
            "Try the manual steps instead.",
        )
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not delete temp script %s", tmp_file)
        return False

    messagebox.showinfo(
        "Demolytics",
        "If you approved the UAC prompt, the configuration file should be updated.\n\n"
        f"{_RESTART_RL_NOTE}\n\n"
        "Click Retry Detection on the setup screen after the file is saved.",
    )
    return True


class StatsApiPermissionModal(ctk.CTkToplevel):
    """Shown when writing DefaultStatsAPI.ini fails with PermissionError on Windows."""

    def __init__(self, master: CTkBaseClass, ini_path: Path) -> None:
        super().__init__(master)
        self.title("Administrator permission needed")
        self.geometry("560x420")
        self.minsize(480, 360)
        self.grab_set()

        self._ini_path = ini_path

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(
            body,
            text="Cannot save Stats API settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkLabel(
            body,
            text=_PROTECTED_FOLDER_EXPLANATION,
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkLabel(
            body,
            text=_RESTART_RL_NOTE,
            wraplength=500,
            justify="left",
            anchor="w",
            text_color=("gray30", "gray70"),
        ).pack(anchor="w", pady=(0, 12))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(anchor="w", pady=(0, 12))

        ctk.CTkButton(
            buttons,
            text="Elevate Permissions",
            width=180,
            command=self._on_elevate,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            buttons,
            text="I'll do it manually",
            width=180,
            command=self._on_show_manual,
        ).pack(side="left")

        self._manual_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._manual_visible = False

        self._manual_text = ctk.CTkTextbox(self._manual_frame, height=200, wrap="word")
        self._manual_text.pack(fill="both", expand=True)
        self._manual_text.insert("1.0", _manual_steps_text(ini_path))
        self._manual_text.configure(state="disabled")

        ctk.CTkButton(body, text="Close", command=self.destroy).pack(anchor="e", pady=(12, 0))

    def _on_elevate(self) -> None:
        _launch_elevated_ini_patch(self._ini_path)
        self.destroy()

    def _on_show_manual(self) -> None:
        if not self._manual_visible:
            self._manual_frame.pack(fill="both", expand=True, pady=(8, 0))
            self._manual_visible = True
            self.geometry("560x620")


def enable_stats_api(install_dir: str, *, parent: CTkBaseClass | None = None) -> bool:
    """
    Set PacketSendRate in ``<install_dir>/TAGame/Config/DefaultStatsAPI.ini`` using configparser.

    Attempts a normal write first. On ``PermissionError``, Windows shows a ``CTkToplevel``
    with elevation and manual options when ``parent`` is provided; otherwise the error is re-raised.
    Returns True if the file was written successfully.
    """
    ini_path = _ini_path_for_install(install_dir)

    try:
        _patch_default_stats_api_ini(ini_path)
        return True
    except PermissionError:
        if os.name == "nt" and parent is not None:
            StatsApiPermissionModal(parent, ini_path)
            return False
        raise
