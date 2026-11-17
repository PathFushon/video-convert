"""Утилиты поиска доступных системных шрифтов (включая поддержку кириллицы)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

PREFERRED_FONTS = (
    "DejaVu Sans",
    "Arial",
    "Liberation Sans",
    "Noto Sans",
    "Segoe UI",
    "Tahoma",
    "Helvetica",
    "Ubuntu",
    "FreeSans",
)


def _list_windows_fonts() -> list[str]:
    """Читает установленные шрифты из реестра Windows."""
    try:
        import winreg  # type: ignore
    except ImportError:
        return []

    families: set[str] = set()
    reg_paths = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    for hive, subkey in reg_paths:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                count = winreg.QueryInfoKey(key)[1]
                for i in range(count):
                    name, _, _ = winreg.EnumValue(key, i)
                    # Убираем суффиксы вида " (TrueType)" / " (OpenType)"
                    clean = name.split(" (")[0].strip()
                    # Убираем начертания в конце вроде Bold / Italic для базового имени
                    for suffix in (
                        " Bold Italic",
                        " Bold",
                        " Italic",
                        " Regular",
                        " Semibold",
                        " Light",
                        " Medium",
                    ):
                        if clean.endswith(suffix) and len(clean) > len(suffix):
                            clean = clean[: -len(suffix)].strip()
                    if clean:
                        families.add(clean)
        except OSError:
            continue
    return sorted(families)


def list_system_fonts() -> list[str]:
    """Возвращает список доступных семейств шрифтов."""
    if sys.platform == "win32":
        win_fonts = _list_windows_fonts()
        if win_fonts:
            return win_fonts

    fc_list = shutil.which("fc-list")
    if fc_list:
        try:
            res = subprocess.run(
                [fc_list, ":", "family"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                families: set[str] = set()
                for line in res.stdout.splitlines():
                    for part in line.split(","):
                        name = part.strip()
                        if name:
                            families.add(name)
                return sorted(families)
        except OSError:
            pass
    return []


def find_default_font_name() -> Optional[str]:
    """Подбирает подходящий шрифт по умолчанию с поддержкой кириллицы и латиницы."""
    available = set(list_system_fonts())
    if available:
        for candidate in PREFERRED_FONTS:
            if candidate in available:
                return candidate

    font_paths = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in font_paths:
        if path.exists():
            if "DejaVu" in path.name:
                return "DejaVu Sans"
            if "Liberation" in path.name:
                return "Liberation Sans"
            if "Noto" in path.name:
                return "Noto Sans"
            if "Arial" in path.name or "arial" in path.name:
                return "Arial"

    return None
