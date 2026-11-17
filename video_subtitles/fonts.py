"""Утилиты поиска доступных системных шрифтов (включая поддержку кириллицы)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

PREFERRED_FONTS = (
    "DejaVu Sans",
    "Liberation Sans",
    "Noto Sans",
    "Arial",
    "Helvetica",
    "Ubuntu",
    "FreeSans",
)


def list_system_fonts() -> list[str]:
    """Возвращает список доступных семейств шрифтов через fc-list (если установлен)."""
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

    # Fallback: проверяем популярные файлы шрифтов на диске
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
