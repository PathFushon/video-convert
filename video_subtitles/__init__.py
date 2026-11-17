"""Библиотека и CLI для добавления субтитров на видео на Python."""

from .core import (
    FFmpegNotFoundError,
    SubtitleError,
    SubtitleMode,
    SubtitleResult,
    SubtitleStyle,
    VideoInfo,
    add_subtitles,
    color_to_ass,
    find_ffmpeg,
    probe_video,
)
from .fonts import find_default_font_name, list_system_fonts
from .srt import (
    SubtitleCue,
    cues_from_json,
    format_srt,
    format_timestamp_srt,
    parse_srt,
    parse_timestamp,
    shift_cues,
    write_srt,
)

__all__ = [
    "FFmpegNotFoundError",
    "SubtitleCue",
    "SubtitleError",
    "SubtitleMode",
    "SubtitleResult",
    "SubtitleStyle",
    "VideoInfo",
    "add_subtitles",
    "color_to_ass",
    "cues_from_json",
    "find_default_font_name",
    "find_ffmpeg",
    "format_srt",
    "format_timestamp_srt",
    "list_system_fonts",
    "parse_srt",
    "parse_timestamp",
    "probe_video",
    "shift_cues",
    "write_srt",
]
