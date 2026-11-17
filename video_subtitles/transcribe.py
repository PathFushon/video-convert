"""Автоматическое распознавание речи из видео (Whisper / whisper.cpp) и генерация субтитров."""

from __future__ import annotations

import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    import numpy as np

from .core import (
    SubtitleError,
    SubtitleMode,
    SubtitleResult,
    SubtitleStyle,
    _subprocess_no_window_kwargs,
    add_subtitles,
    find_ffmpeg,
    probe_video,
)
from .srt import SubtitleCue, write_srt

WHISPER_MODELS: dict[str, str] = {
    "tiny": "tiny (~75 МБ — очень быстро, базовое качество)",
    "base": "base (~142 МБ — быстро, базовое качество)",
    "small": "small (~466 МБ — оптимально для русского языка)",
    "medium": "medium (~1.5 ГБ — высокое качество, медленнее)",
    "large-v3-turbo": "large-v3-turbo (~1.6 ГБ — максимальная точность)",
}

WHISPER_LANGUAGES: dict[str, str] = {
    "auto": "Автоопределение",
    "ru": "Русский (ru)",
    "en": "Английский (en)",
    "uk": "Украинский (uk)",
    "be": "Белорусский (be)",
    "kk": "Казахский (kk)",
    "de": "Немецкий (de)",
    "fr": "Французский (fr)",
    "es": "Испанский (es)",
    "it": "Итальянский (it)",
    "zh": "Китайский (zh)",
    "ja": "Японский (ja)",
}


@dataclass(frozen=True)
class AutoSubtitleResult:
    """Результат автоматического создания и наложения субтитров."""

    video_result: SubtitleResult
    srt_path: Optional[Path]
    cues: list[SubtitleCue]


def _default_models_dir() -> Path:
    try:
        from pywhispercpp.constants import MODELS_DIR  # type: ignore

        return Path(MODELS_DIR)
    except Exception:
        return Path.home() / ".local" / "share" / "pywhispercpp" / "models"


def _model_download_url(model_name: str) -> str:
    try:
        from pywhispercpp.constants import MODELS_BASE_URL, MODELS_PREFIX_URL  # type: ignore

        return f"{MODELS_BASE_URL}/{MODELS_PREFIX_URL}-{model_name}.bin"
    except Exception:
        return f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model_name}.bin"


def ensure_whisper_model(
    model_name: str = "small",
    models_dir: Optional[Path] = None,
    *,
    progress_callback: Optional[Callable[[float], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Path:
    """Находит локальную GGML-модель Whisper или скачивает её при первом запуске."""
    direct = Path(model_name)
    if direct.is_file():
        return direct.resolve()

    target_dir = Path(models_dir) if models_dir is not None else _default_models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    for candidate in (
        target_dir / f"ggml-{model_name}.bin",
        target_dir / f"{model_name}.bin",
        target_dir / model_name,
    ):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()

    url = _model_download_url(model_name)
    dest_file = target_dir / f"ggml-{model_name}.bin"
    part_file = target_dir / f"ggml-{model_name}.bin.part"

    if status_callback:
        status_callback(f"Скачивание модели Whisper ({model_name})...")

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "video-subtitles/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            total_hdr = resp.headers.get("Content-Length") or resp.headers.get("content-length")
            total = int(total_hdr) if total_hdr else 0
            downloaded = 0
            with open(part_file, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        if progress_callback:
                            progress_callback(min(1.0, downloaded / total))
                        if status_callback:
                            mb_done = downloaded / (1024 * 1024)
                            mb_total = total / (1024 * 1024)
                            status_callback(
                                f"Скачивание модели {model_name}: {mb_done:.1f} / {mb_total:.1f} МБ"
                            )
        part_file.replace(dest_file)
    except Exception as exc:
        if part_file.exists():
            part_file.unlink(missing_ok=True)
        raise SubtitleError(
            f"Не удалось скачать модель '{model_name}' ({url}): {exc}"
        ) from exc

    return dest_file.resolve()


def extract_audio_16k_mono(
    video_path: str | Path,
    ffmpeg_path: Optional[str | Path] = None,
) -> "np.ndarray":
    """Извлекает моно-аудио 16 кГц (float32) из видео через FFmpeg без временных файлов."""
    import numpy as np

    src = Path(video_path)
    if not src.is_file():
        raise FileNotFoundError(f"Видеофайл не найден: {src}")

    ffmpeg = find_ffmpeg(ffmpeg_path)
    info = probe_video(src, ffmpeg_path=ffmpeg)
    if not info.has_audio:
        raise SubtitleError(
            "В этом видео нет звуковой дорожки — невозможно автоматически распознать речь."
        )

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        **_subprocess_no_window_kwargs(),  # type: ignore[arg-type]
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SubtitleError(f"Не удалось извлечь звук из видео: {err}")

    if not proc.stdout:
        raise SubtitleError("Извлечённая аудиодорожка пуста.")

    # Копируем в writable float32 ndarray
    audio = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    return audio


def wrap_subtitle_text(text: str, max_line_chars: int = 42) -> str:
    """Аккуратно переносит длинную реплику на 2 строки для удобного чтения."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_line_chars:
        return cleaned

    words = cleaned.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_line_chars:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    if len(lines) <= 2:
        return "\n".join(lines)
    # Если строк больше двух, равномерно делим пополам по длине
    mid = len(cleaned) // 2
    best_idx = cleaned.find(" ", mid)
    alt_idx = cleaned.rfind(" ", 0, mid)
    split_at = best_idx if best_idx != -1 else alt_idx
    if split_at != -1:
        return cleaned[:split_at].strip() + "\n" + cleaned[split_at + 1 :].strip()
    return "\n".join(lines)


def transcribe_video(
    video_path: str | Path,
    *,
    model_name: str = "small",
    language: str = "auto",
    translate_to_english: bool = False,
    max_chars_per_cue: int = 84,
    max_line_chars: int = 42,
    models_dir: Optional[Path] = None,
    n_threads: Optional[int] = None,
    ffmpeg_path: Optional[str | Path] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> list[SubtitleCue]:
    """Распознаёт речь в видеофайле и возвращает список SubtitleCue."""
    try:
        from pywhispercpp.model import Model  # type: ignore
    except ImportError as exc:
        raise SubtitleError(
            "Для автоматического распознавания речи нужен пакет 'pywhispercpp'. "
            "Установите его командой: pip install pywhispercpp"
        ) from exc

    src = Path(video_path)
    info = probe_video(src, ffmpeg_path=ffmpeg_path)
    duration = info.duration or 0.0

    if status_callback:
        status_callback("Извлечение звуковой дорожки...")
    audio = extract_audio_16k_mono(src, ffmpeg_path=ffmpeg_path)

    model_file = ensure_whisper_model(
        model_name,
        models_dir=models_dir,
        progress_callback=progress_callback,
        status_callback=status_callback,
    )

    if status_callback:
        status_callback(f"Загрузка модели {model_name} и распознавание речи...")
    if progress_callback:
        progress_callback(0.0)

    threads = n_threads or min(8, max(2, os.cpu_count() or 4))
    lang_code = "" if (not language or language == "auto") else language

    model = Model(
        str(model_file),
        redirect_whispercpp_logs_to=None,
        n_threads=threads,
        print_progress=False,
        print_realtime=False,
        print_timestamps=False,
    )

    def on_segment(seg) -> None:
        sec_end = max(0.0, seg.t1 / 100.0)
        if duration > 0 and progress_callback:
            progress_callback(min(0.99, sec_end / duration))
        if status_callback:
            snippet = seg.text.strip().replace("\n", " ")
            if len(snippet) > 45:
                snippet = snippet[:42] + "..."
            if snippet:
                status_callback(f"Распознано: «{snippet}»")

    segments = model.transcribe(
        audio,
        language=lang_code,
        translate=translate_to_english,
        token_timestamps=True,
        max_len=max_chars_per_cue,
        split_on_word=True,
        new_segment_callback=on_segment,
    )

    cues: list[SubtitleCue] = []
    for seg in segments:
        raw_text = re.sub(r"\s+", " ", seg.text).strip()
        if not raw_text:
            continue
        start_s = max(0.0, seg.t0 / 100.0)
        end_s = max(start_s + 0.1, seg.t1 / 100.0)
        wrapped = wrap_subtitle_text(raw_text, max_line_chars=max_line_chars)
        cues.append(SubtitleCue(start=start_s, end=end_s, text=wrapped))

    if not cues:
        raise SubtitleError(
            "Речь в видео не обнаружена или не удалось распознать ни одной фразы."
        )

    if progress_callback:
        progress_callback(1.0)
    return cues


def auto_subtitle_video(
    video_path: str | Path,
    output_path: Optional[str | Path] = None,
    *,
    model_name: str = "small",
    language: str = "auto",
    translate_to_english: bool = False,
    save_srt: bool = True,
    srt_output_path: Optional[str | Path] = None,
    mode: SubtitleMode = "hard",
    style: Optional[SubtitleStyle] = None,
    shift_seconds: float = 0.0,
    track_language: str = "rus",
    track_title: Optional[str] = None,
    default_track: bool = True,
    crf: int = 18,
    preset: str = "medium",
    ffmpeg_path: Optional[str | Path] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> AutoSubtitleResult:
    """Полный цикл: распознаёт речь в видео -> сохраняет SRT -> накладывает субтитры на видео."""
    src = Path(video_path)

    def stt_progress(ratio: float) -> None:
        if progress_callback:
            progress_callback(ratio * 0.55)

    def render_progress(ratio: float) -> None:
        if progress_callback:
            progress_callback(0.55 + ratio * 0.45)

    cues = transcribe_video(
        src,
        model_name=model_name,
        language=language,
        translate_to_english=translate_to_english,
        ffmpeg_path=ffmpeg_path,
        progress_callback=stt_progress,
        status_callback=status_callback,
    )

    saved_srt: Optional[Path] = None
    if save_srt:
        if srt_output_path is not None:
            target_srt = Path(srt_output_path)
        elif output_path is not None:
            out_p = Path(output_path)
            # Не называем .srt так же, как выходное hardsub-видео,
            # иначе плеер (VLC/PotPlayer) покажет субтитры второй раз поверх кадра.
            target_srt = (
                out_p.with_name(f"{out_p.stem}_subtitles.srt")
                if mode == "hard"
                else out_p.with_suffix(".srt")
            )
        else:
            target_srt = src.with_suffix(".srt")
        saved_srt = write_srt(cues, target_srt)

    if status_callback:
        status_callback("Наложение субтитров на видео...")

    video_res = add_subtitles(
        video_path=src,
        subtitle_source=cues,
        output_path=output_path,
        mode=mode,
        style=style,
        shift_seconds=shift_seconds,
        language=track_language,
        title=track_title,
        default_track=default_track,
        crf=crf,
        preset=preset,
        ffmpeg_path=ffmpeg_path,
        progress_callback=render_progress,
    )

    return AutoSubtitleResult(video_result=video_res, srt_path=saved_srt, cues=cues)
