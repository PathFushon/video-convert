"""Основная логика добавления субтитров на видео через FFmpeg."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence, Union

from .fonts import find_default_font_name
from .srt import (
    SubtitleCue,
    cues_from_json,
    format_timestamp_ass,
    parse_srt,
    shift_cues,
    write_srt,
)

SubtitleMode = Literal["hard", "soft"]

_NAMED_COLORS: dict[str, str] = {
    "white": "#FFFFFF",
    "black": "#000000",
    "yellow": "#FFFF00",
    "red": "#FF0000",
    "green": "#00FF00",
    "blue": "#0000FF",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
    "gray": "#808080",
    "grey": "#808080",
    "orange": "#FFA500",
}

_ALIGNMENT_MAP: dict[str, int] = {
    "bottom-left": 1,
    "bottom": 2,
    "bottom-center": 2,
    "bottom-right": 3,
    "middle-left": 4,
    "center": 5,
    "middle": 5,
    "middle-center": 5,
    "middle-right": 6,
    "top-left": 7,
    "top": 8,
    "top-center": 8,
    "top-right": 9,
}


class SubtitleError(RuntimeError):
    """Базовая ошибка при обработке субтитров или видео."""


class FFmpegNotFoundError(SubtitleError):
    """Возникает, если не удалось найти исполняемый файл ffmpeg."""


@dataclass(frozen=True)
class VideoInfo:
    """Краткая информация о видеофайле, полученная от FFmpeg."""

    duration: Optional[float]
    width: Optional[int]
    height: Optional[int]
    has_video: bool
    has_audio: bool
    subtitle_streams: int


@dataclass
class SubtitleStyle:
    """Параметры оформления вжённых (hardsub) субтитров."""

    font_name: Optional[str] = None
    font_size: int = 24
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    back_color: str = "#00000080"
    outline: float = 2.0
    shadow: float = 1.0
    bold: bool = False
    italic: bool = False
    alignment: Union[int, str] = 2
    margin_v: int = 24
    margin_l: int = 20
    margin_r: int = 20
    border_style: int = 1

    def resolved_alignment(self) -> int:
        if isinstance(self.alignment, int):
            if 1 <= self.alignment <= 9:
                return self.alignment
            raise ValueError(f"Alignment должен быть от 1 до 9, получено: {self.alignment}")
        key = str(self.alignment).strip().lower()
        if key in _ALIGNMENT_MAP:
            return _ALIGNMENT_MAP[key]
        try:
            num = int(key)
        except ValueError as exc:
            raise ValueError(f"Неизвестное выравнивание субтитров: {self.alignment!r}") from exc
        if 1 <= num <= 9:
            return num
        raise ValueError(f"Alignment должен быть от 1 до 9, получено: {self.alignment}")

    def to_ass_force_style(self) -> str:
        """Формирует строку force_style для фильтра FFmpeg subtitles."""
        font = self.font_name if self.font_name is not None else find_default_font_name()
        parts: list[str] = []
        if font:
            parts.append(f"FontName={font}")
        parts.extend(
            [
                f"FontSize={int(self.font_size)}",
                f"PrimaryColour={color_to_ass(self.primary_color)}",
                f"OutlineColour={color_to_ass(self.outline_color)}",
                f"BackColour={color_to_ass(self.back_color)}",
                f"BorderStyle={int(self.border_style)}",
                f"Outline={self.outline:g}",
                f"Shadow={self.shadow:g}",
                f"Bold={-1 if self.bold else 0}",
                f"Italic={-1 if self.italic else 0}",
                f"Alignment={self.resolved_alignment()}",
                f"MarginV={int(self.margin_v)}",
                f"MarginL={int(self.margin_l)}",
                f"MarginR={int(self.margin_r)}",
            ]
        )
        return ",".join(parts)


@dataclass(frozen=True)
class SubtitleResult:
    """Результат добавления субтитров."""

    output_path: Path
    mode: SubtitleMode
    command: list[str]
    ffmpeg_path: str


def color_to_ass(color: str) -> str:
    """Конвертирует цвет (#RRGGBB, #RRGGBBAA, имя цвета или &HAABBGGRR) в формат ASS."""
    raw = color.strip()
    if raw.upper().startswith("&H"):
        return raw.upper()

    lower = raw.lower()
    if lower in _NAMED_COLORS:
        raw = _NAMED_COLORS[lower]

    hex_str = raw.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(ch * 2 for ch in hex_str)

    if len(hex_str) == 6:
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        ass_alpha = "00"
    elif len(hex_str) == 8:
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        css_alpha = int(hex_str[6:8], 16)
        ass_alpha = f"{255 - css_alpha:02X}"
    else:
        raise ValueError(
            f"Некорректный цвет {color!r}. Используйте #RRGGBB, #RRGGBBAA, имя цвета или &HAABBGGRR."
        )

    # Валидация hex-символов
    int(r + g + b, 16)
    return f"&H{ass_alpha}{b.upper()}{g.upper()}{r.upper()}"


def find_ffmpeg(ffmpeg_path: Optional[Union[str, Path]] = None) -> str:
    """Находит бинарник ffmpeg: явный путь -> PATH -> imageio-ffmpeg."""
    if ffmpeg_path is not None:
        candidate = Path(ffmpeg_path)
        if candidate.is_file():
            return str(candidate)
        found = shutil.which(str(ffmpeg_path))
        if found:
            return found
        raise FFmpegNotFoundError(f"Указанный ffmpeg не найден: {ffmpeg_path}")

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass

    raise FFmpegNotFoundError(
        "FFmpeg не найден. Установите пакет 'imageio-ffmpeg' (pip install imageio-ffmpeg) "
        "или установите системный ffmpeg и добавьте его в PATH."
    )


def _subprocess_no_window_kwargs() -> dict[str, object]:
    """Скрывает консольное окно FFmpeg при запуске из GUI на Windows."""
    if sys.platform == "win32":
        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flag:
            return {"creationflags": flag}
    return {}


def probe_video(
    video_path: Union[str, Path],
    ffmpeg_path: Optional[Union[str, Path]] = None,
) -> VideoInfo:
    """Возвращает сведения о потоках и длительности видео через ffmpeg -i."""
    video = Path(video_path)
    if not video.is_file():
        raise FileNotFoundError(f"Видеофайл не найден: {video}")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video)],
        capture_output=True,
        text=True,
        check=False,
        **_subprocess_no_window_kwargs(),  # type: ignore[arg-type]
    )
    info_text = proc.stderr or ""

    duration: Optional[float] = None
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info_text)
    if dur_match:
        h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), float(dur_match.group(3))
        duration = h * 3600 + m * 60 + s

    width: Optional[int] = None
    height: Optional[int] = None
    has_video = False
    has_audio = False
    subtitle_streams = 0

    for line in info_text.splitlines():
        if "Stream #" not in line:
            continue
        if "Video:" in line:
            has_video = True
            if width is None:
                res_match = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
                if res_match:
                    width = int(res_match.group(1))
                    height = int(res_match.group(2))
        elif "Audio:" in line:
            has_audio = True
        elif "Subtitle:" in line:
            subtitle_streams += 1

    return VideoInfo(
        duration=duration,
        width=width,
        height=height,
        has_video=has_video,
        has_audio=has_audio,
        subtitle_streams=subtitle_streams,
    )


def escape_ffmpeg_filter_value(value: str) -> str:
    """Двухуровневое экранирование значения для опции фильтра FFmpeg (например filename=...)."""
    # Уровень 1: экранирование внутри значения опции фильтра
    level1 = (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )
    # Уровень 2: экранирование разделителей filtergraph (-vf)
    level2 = (
        level1.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    return level2


def _default_output_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}_subtitled{video_path.suffix}")


def _pick_softsub_codec(output_path: Path, subtitle_ext: str) -> str:
    ext = output_path.suffix.lower()
    sub_ext = subtitle_ext.lower()
    if ext in {".mp4", ".m4v", ".mov"}:
        return "mov_text"
    if ext == ".webm":
        return "webvtt"
    if ext == ".mkv":
        return "ass" if sub_ext in {".ass", ".ssa"} else "srt"
    return "mov_text" if ext == "" else "srt"


def _prepare_subtitle_file(
    subtitle_source: Union[str, Path, Sequence[SubtitleCue]],
    shift_seconds: float,
    tmp_dir: Path,
) -> tuple[Path, str]:
    """Подготавливает файл субтитров, при необходимости конвертируя JSON/список и сдвигая тайминги."""
    if isinstance(subtitle_source, (str, Path)):
        src_path = Path(subtitle_source)
        if not src_path.is_file():
            raise FileNotFoundError(f"Файл субтитров не найден: {src_path}")

        ext = src_path.suffix.lower()
        if ext == ".json":
            cues = cues_from_json(src_path)
            if shift_seconds != 0.0:
                cues = shift_cues(cues, shift_seconds)
            if not cues:
                raise SubtitleError("Список субтитров пуст после обработки.")
            out_srt = tmp_dir / "subtitles.srt"
            write_srt(cues, out_srt)
            return out_srt, ".srt"

        if ext == ".srt" and shift_seconds != 0.0:
            cues = parse_srt(src_path.read_text(encoding="utf-8-sig"))
            cues = shift_cues(cues, shift_seconds)
            if not cues:
                raise SubtitleError("После сдвига времени в файле не осталось реплик.")
            out_srt = tmp_dir / "shifted.srt"
            write_srt(cues, out_srt)
            return out_srt, ".srt"

        if ext not in {".srt", ".ass", ".ssa", ".vtt"}:
            raise ValueError(
                f"Неподдерживаемый формат субтитров {ext!r}. "
                "Поддерживаются: .srt, .ass, .ssa, .vtt, .json"
            )
        return src_path.resolve(), ext

    cues = list(subtitle_source)
    if shift_seconds != 0.0:
        cues = shift_cues(cues, shift_seconds)
    if not cues:
        raise SubtitleError("Список субтитров пуст.")
    out_srt = tmp_dir / "cues.srt"
    write_srt(cues, out_srt)
    return out_srt, ".srt"


def _compute_play_res(width: Optional[int], height: Optional[int]) -> tuple[int, int]:
    """Подбирает эталонное разрешение PlayRes (на базе 720p), чтобы шрифт масштабировался одинаково на HD/FHD/4K."""
    if not width or not height or width <= 0 or height <= 0:
        return 1280, 720
    if width >= height:
        play_y = 720
        play_x = max(2, int(round(720 * width / height)))
        return play_x, play_y
    play_x = 720
    play_y = max(2, int(round(720 * height / width)))
    return play_x, play_y


def _write_styled_ass(
    cues: Sequence[SubtitleCue],
    style: SubtitleStyle,
    out_path: Path,
    width: Optional[int],
    height: Optional[int],
) -> Path:
    """Генерирует ASS-файл с корректным PlayResX/PlayResY и заданным стилем."""
    play_x, play_y = _compute_play_res(width, height)
    font = style.font_name if style.font_name is not None else (find_default_font_name() or "Arial")
    primary = color_to_ass(style.primary_color)
    outline = color_to_ass(style.outline_color)
    back = color_to_ass(style.back_color)
    bold_flag = -1 if style.bold else 0
    italic_flag = -1 if style.italic else 0
    align = style.resolved_alignment()

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_x}",
        f"PlayResY: {play_y}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{int(style.font_size)},{primary},&H000000FF,{outline},{back},"
        f"{bold_flag},{italic_flag},0,0,100,100,0,0,{int(style.border_style)},"
        f"{style.outline:g},{style.shadow:g},{align},{int(style.margin_l)},{int(style.margin_r)},"
        f"{int(style.margin_v)},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        start_ass = format_timestamp_ass(cue.start)
        end_ass = format_timestamp_ass(cue.end)
        text_ass = (
            cue.text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "\\N")
        )
        lines.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text_ass}")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def add_subtitles(
    video_path: Union[str, Path],
    subtitle_source: Union[str, Path, Sequence[SubtitleCue]],
    output_path: Optional[Union[str, Path]] = None,
    *,
    mode: SubtitleMode = "hard",
    style: Optional[SubtitleStyle] = None,
    apply_style_to_ass: bool = False,
    shift_seconds: float = 0.0,
    language: str = "rus",
    title: Optional[str] = None,
    default_track: bool = True,
    video_codec: str = "libx264",
    crf: int = 18,
    preset: str = "medium",
    audio_codec: str = "copy",
    fonts_dir: Optional[Union[str, Path]] = None,
    overwrite: bool = True,
    ffmpeg_path: Optional[Union[str, Path]] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> SubtitleResult:
    """Добавляет субтитры к видео в режиме hardsub (вживление) или softsub (дорожка).

    Args:
        video_path: Путь к исходному видео.
        subtitle_source: Путь к .srt/.ass/.ssa/.vtt/.json или список SubtitleCue.
        output_path: Путь к итоговому видео (по умолчанию <name>_subtitled.<ext>).
        mode: 'hard' — вжечь субтитры в картинку; 'soft' — встроить как дорожку без перекодирования.
        style: Стиль оформления для режима 'hard'.
        apply_style_to_ass: Применять ли force_style к .ass/.ssa (по умолчанию сохраняется стиль ASS).
        shift_seconds: Сдвиг таймингов субтитров в секундах (для .srt / JSON / SubtitleCue, или softsub).
        language: ISO 639-2 код языка дорожки в режиме 'soft' (например, 'rus', 'eng').
        title: Название дорожки субтитров в режиме 'soft'.
        default_track: Пометить дорожку как основную (default) в режиме 'soft'.
        video_codec: Видеокодек в режиме 'hard' (по умолчанию 'libx264').
        crf: Качество кодирования CRF в режиме 'hard' (меньше = выше качество, обычно 18–23).
        preset: Пресет скорости кодирования libx264/libx265.
        audio_codec: Аудиокодек в режиме 'hard' (по умолчанию 'copy').
        fonts_dir: Необязательная папка с пользовательскими шрифтами (.ttf/.otf).
        overwrite: Перезаписывать ли существующий выходной файл.
        ffmpeg_path: Явный путь к исполняемому файлу ffmpeg.

    Returns:
        SubtitleResult с путём к результату и выполненной командой.
    """
    if mode not in ("hard", "soft"):
        raise ValueError(f"Неизвестный режим {mode!r}. Используйте 'hard' или 'soft'.")

    src_video = Path(video_path)
    if not src_video.is_file():
        raise FileNotFoundError(f"Видеофайл не найден: {src_video}")

    dst_video = Path(output_path) if output_path is not None else _default_output_path(src_video)
    if dst_video.resolve() == src_video.resolve():
        raise ValueError("Путь выходного файла не должен совпадать с исходным видеофайлом.")

    if dst_video.exists() and not overwrite:
        raise FileExistsError(f"Выходной файл уже существует: {dst_video}")

    dst_video.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg(ffmpeg_path)
    info = probe_video(src_video, ffmpeg_path=ffmpeg)
    if not info.has_video:
        raise SubtitleError(f"В файле нет видеопотока: {src_video}")

    with tempfile.TemporaryDirectory(prefix="video_subtitles_") as tmp:
        sub_file, sub_ext = _prepare_subtitle_file(subtitle_source, shift_seconds, Path(tmp))

        cmd: list[str] = [ffmpeg, "-hide_banner", "-y" if overwrite else "-n"]

        if mode == "hard":
            cmd.extend(["-i", str(src_video)])

            eff_style = style or SubtitleStyle()
            generated_ass = False
            if sub_ext == ".srt":
                parsed_cues = parse_srt(sub_file.read_text(encoding="utf-8-sig"))
                if parsed_cues:
                    sub_file = _write_styled_ass(
                        parsed_cues,
                        eff_style,
                        Path(tmp) / "styled.ass",
                        info.width,
                        info.height,
                    )
                    sub_ext = ".ass"
                    generated_ass = True

            filter_opts = [f"filename={escape_ffmpeg_filter_value(str(sub_file))}"]
            if fonts_dir is not None:
                fdir = Path(fonts_dir)
                if not fdir.is_dir():
                    raise FileNotFoundError(f"Папка со шрифтами не найдена: {fdir}")
                filter_opts.append(f"fontsdir={escape_ffmpeg_filter_value(str(fdir.resolve()))}")

            is_ass = sub_ext in {".ass", ".ssa"}
            if not generated_ass and (not is_ass or apply_style_to_ass):
                force_style = eff_style.to_ass_force_style()
                # Внутри одинарных кавычек force_style='...' запятые допустимы
                safe_force = force_style.replace("'", "\\'")
                filter_opts.append(f"force_style='{safe_force}'")

            vf_arg = "subtitles=" + ":".join(filter_opts)
            cmd.extend(["-vf", vf_arg])
            cmd.extend(["-map", "0:v:0", "-map", "0:a?"])
            cmd.extend(["-c:v", video_codec])
            if video_codec in {"libx264", "libx265", "libvpx-vp9"}:
                cmd.extend(["-crf", str(crf), "-preset", preset])
            if video_codec in {"libx264", "libx265"}:
                cmd.extend(["-pix_fmt", "yuv420p"])
            if info.has_audio:
                cmd.extend(["-c:a", audio_codec])
            if dst_video.suffix.lower() in {".mp4", ".m4v", ".mov"}:
                cmd.extend(["-movflags", "+faststart"])
            cmd.append(str(dst_video))

        else:
            # softsub: копируем видео и аудио без перекодирования
            cmd.extend(["-i", str(src_video)])
            if shift_seconds != 0.0 and sub_ext in {".ass", ".ssa", ".vtt"}:
                cmd.extend(["-itsoffset", f"{shift_seconds:.3f}"])
            cmd.extend(["-i", str(sub_file)])

            sub_codec = _pick_softsub_codec(dst_video, sub_ext)
            cmd.extend(
                [
                    "-map",
                    "0:v",
                    "-map",
                    "0:a?",
                    "-map",
                    "1:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    "-c:s",
                    sub_codec,
                ]
            )
            if language:
                cmd.extend(["-metadata:s:s:0", f"language={language}"])
            if title:
                cmd.extend(["-metadata:s:s:0", f"title={title}"])
            if default_track:
                cmd.extend(["-disposition:s:0", "default"])
            if dst_video.suffix.lower() in {".mp4", ".m4v", ".mov"}:
                cmd.extend(["-movflags", "+faststart"])
            cmd.append(str(dst_video))

        env = os.environ.copy()
        extra_kw = _subprocess_no_window_kwargs()

        if progress_callback is not None:
            run_cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
            proc_p = subprocess.Popen(
                run_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                **extra_kw,  # type: ignore[arg-type]
            )
            progress_callback(0.0)
            total_us = (info.duration * 1_000_000.0) if info.duration and info.duration > 0 else None
            assert proc_p.stdout is not None
            for line in proc_p.stdout:
                line = line.strip()
                if line.startswith("out_time_us=") and total_us:
                    try:
                        cur_us = int(line.split("=", 1)[1])
                        ratio = max(0.0, min(0.99, cur_us / total_us))
                        progress_callback(ratio)
                    except ValueError:
                        pass
                elif line == "progress=end":
                    progress_callback(1.0)
            stderr_text = proc_p.stderr.read() if proc_p.stderr else ""
            rc = proc_p.wait()
            if rc != 0:
                err_tail = "\n".join(stderr_text.splitlines()[-15:])
                raise SubtitleError(
                    f"FFmpeg завершился с ошибкой (код {rc}):\n{err_tail}"
                )
            progress_callback(1.0)
        else:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                check=False,
                **extra_kw,  # type: ignore[arg-type]
            )
            if proc.returncode != 0:
                err_tail = "\n".join(proc.stderr.splitlines()[-15:])
                raise SubtitleError(
                    f"FFmpeg завершился с ошибкой (код {proc.returncode}):\n{err_tail}"
                )

    return SubtitleResult(
        output_path=dst_video,
        mode=mode,
        command=cmd,
        ffmpeg_path=ffmpeg,
    )
