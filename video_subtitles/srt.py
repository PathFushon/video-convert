"""Работа с субтитрами формата SRT и JSON-списками реплик."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, Union

_TIMESTAMP_RE = re.compile(
    r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?\s*$"
)
_SRT_TIMING_LINE_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?$"
)


@dataclass(frozen=True)
class SubtitleCue:
    """Одна реплика субтитров с началом, концом (в секундах) и текстом."""

    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"Время начала не может быть отрицательным: {self.start}")
        if self.end <= self.start:
            raise ValueError(
                f"Время окончания ({self.end}) должно быть больше начала ({self.start})"
            )
        if not self.text.strip():
            raise ValueError("Текст реплики не может быть пустым")


def format_timestamp_srt(seconds: float) -> str:
    """Форматирует секунды в таймкод SRT вида HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    sec = total_sec % 60
    total_min = total_sec // 60
    minutes = total_min % 60
    hours = total_min // 60
    return f"{hours:02d}:{minutes:02d}:{sec:02d},{ms:03d}"


def format_timestamp_ass(seconds: float) -> str:
    """Форматирует секунды в таймкод ASS вида H:MM:SS.cc."""
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_sec = total_cs // 100
    sec = total_sec % 60
    total_min = total_sec // 60
    minutes = total_min % 60
    hours = total_min // 60
    return f"{hours}:{minutes:02d}:{sec:02d}.{cs:02d}"


def parse_timestamp(value: Union[str, int, float]) -> float:
    """Преобразует число секунд или строку вида HH:MM:SS,mmm / MM:SS.mmm в float."""
    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value).strip()
    try:
        return float(raw)
    except ValueError:
        pass

    match = _TIMESTAMP_RE.match(raw)
    if not match:
        raise ValueError(f"Некорректный формат таймкода: {value!r}")

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    ms_str = match.group(4) or "0"
    ms = int(ms_str.ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + ms / 1000.0


def format_srt(cues: Sequence[SubtitleCue]) -> str:
    """Собирает содержимое SRT-файла из последовательности SubtitleCue."""
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        start_str = format_timestamp_srt(cue.start)
        end_str = format_timestamp_srt(cue.end)
        text = cue.text.strip("\r\n")
        blocks.append(f"{index}\n{start_str} --> {end_str}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_srt(cues: Sequence[SubtitleCue], path: Union[str, Path]) -> Path:
    """Записывает список реплик в SRT-файл (UTF-8)."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(format_srt(cues), encoding="utf-8")
    return out_path


def parse_srt(content: str) -> list[SubtitleCue]:
    """Парсит текст в формате SRT в список SubtitleCue."""
    clean = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    raw_blocks = re.split(r"\n\s*\n", clean.strip())
    cues: list[SubtitleCue] = []

    for block in raw_blocks:
        lines = [line for line in block.split("\n") if line is not None]
        if not lines:
            continue

        timing_idx = 0
        if not _SRT_TIMING_LINE_RE.match(lines[0]):
            if len(lines) >= 2 and _SRT_TIMING_LINE_RE.match(lines[1]):
                timing_idx = 1
            else:
                continue

        timing_match = _SRT_TIMING_LINE_RE.match(lines[timing_idx])
        if not timing_match:
            continue

        start = parse_timestamp(timing_match.group(1))
        end = parse_timestamp(timing_match.group(2))
        text = "\n".join(lines[timing_idx + 1 :]).strip()
        if text and end > start:
            cues.append(SubtitleCue(start=start, end=end, text=text))

    return cues


def cues_from_json(path: Union[str, Path]) -> list[SubtitleCue]:
    """Загружает реплики из JSON-файла ([{start, end, text}, ...] или {cues: [...]})."""
    data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "cues" in data:
        items = data["cues"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError(
            "JSON должен содержать список объектов [{start, end, text}] или ключ 'cues'"
        )

    cues: list[SubtitleCue] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Элемент #{idx} в JSON должен быть объектом")
        try:
            start = parse_timestamp(item["start"])
            end = parse_timestamp(item["end"])
            text = str(item["text"])
        except KeyError as exc:
            raise ValueError(f"В элементе #{idx} отсутствует обязательное поле {exc}") from exc
        cues.append(SubtitleCue(start=start, end=end, text=text))
    return cues


def shift_cues(cues: Sequence[SubtitleCue], offset_seconds: float) -> list[SubtitleCue]:
    """Сдвигает тайминги всех реплик на offset_seconds (может быть отрицательным)."""
    shifted: list[SubtitleCue] = []
    for cue in cues:
        new_start = max(0.0, cue.start + offset_seconds)
        new_end = cue.end + offset_seconds
        if new_end > new_start:
            shifted.append(SubtitleCue(start=new_start, end=new_end, text=cue.text))
    return shifted
