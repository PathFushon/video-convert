"""Автотесты для модуля video_subtitles."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_subtitles import (
    SubtitleCue,
    SubtitleError,
    SubtitleStyle,
    add_subtitles,
    auto_subtitle_video,
    color_to_ass,
    cues_from_json,
    extract_audio_16k_mono,
    find_ffmpeg,
    format_srt,
    format_timestamp_srt,
    parse_srt,
    parse_timestamp,
    probe_video,
    shift_cues,
    transcribe_video,
    wrap_subtitle_text,
    write_srt,
)
from video_subtitles.cli import main as cli_main


def _make_sample_video(path: Path, with_audio: bool = True, duration: float = 2.0) -> Path:
    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=#102040:s=640x360:d={duration}",
    ]
    if with_audio:
        cmd.extend(["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if with_audio:
        cmd.extend(["-c:a", "aac", "-shortest"])
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def test_timestamp_and_srt_roundtrip(tmp_path: Path) -> None:
    assert format_timestamp_srt(3661.25) == "01:01:01,250"
    assert parse_timestamp("01:01:01,250") == pytest.approx(3661.25)
    assert parse_timestamp("01:02.5") == pytest.approx(62.5)
    assert parse_timestamp(12.5) == pytest.approx(12.5)

    cues = [
        SubtitleCue(0.0, 1.5, "Привет, мир!"),
        SubtitleCue(2.0, 4.25, "Вторая строка\nи перенос"),
    ]
    srt_path = write_srt(cues, tmp_path / "subs.srt")
    parsed = parse_srt(srt_path.read_text(encoding="utf-8"))
    assert parsed == cues
    assert "00:00:00,000 --> 00:00:01,500" in format_srt(cues)


def test_shift_and_json_cues(tmp_path: Path) -> None:
    json_path = tmp_path / "cues.json"
    json_path.write_text(
        json.dumps(
            [
                {"start": 0.5, "end": 1.5, "text": "Первая"},
                {"start": "00:00:02,000", "end": "00:00:03,500", "text": "Вторая"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cues = cues_from_json(json_path)
    assert len(cues) == 2
    shifted = shift_cues(cues, -1.0)
    assert len(shifted) == 2
    assert shifted[0].start == pytest.approx(0.0)
    assert shifted[0].end == pytest.approx(0.5)
    assert shifted[1].start == pytest.approx(1.0)


def test_color_to_ass_and_style() -> None:
    assert color_to_ass("#FF0000") == "&H000000FF"
    assert color_to_ass("yellow") == "&H0000FFFF"
    assert color_to_ass("#FFFFFF80") == "&H7FFFFFFF"
    assert color_to_ass("&H00ABCDEF") == "&H00ABCDEF"

    style = SubtitleStyle(
        font_name="DejaVu Sans",
        font_size=28,
        primary_color="#FFFF00",
        bold=True,
        alignment="top",
    )
    force_style = style.to_ass_force_style()
    assert "FontName=DejaVu Sans" in force_style
    assert "FontSize=28" in force_style
    assert "PrimaryColour=&H0000FFFF" in force_style
    assert "Bold=-1" in force_style
    assert "Alignment=8" in force_style


def test_hardsub_renders_pixels_and_preserves_audio(tmp_path: Path) -> None:
    src = _make_sample_video(tmp_path / "input.mp4", with_audio=True, duration=2.0)
    out = tmp_path / "output_hard.mp4"
    cues = [SubtitleCue(0.0, 2.0, "ТЕСТ КИРИЛЛИЦЫ")]

    result = add_subtitles(
        src,
        cues,
        out,
        mode="hard",
        style=SubtitleStyle(
            font_name="DejaVu Sans",
            font_size=36,
            primary_color="#FFFFFF",
            outline_color="#000000",
            alignment="center",
        ),
        preset="ultrafast",
    )
    assert result.output_path.is_file()

    info = probe_video(out)
    assert info.has_video is True
    assert info.has_audio is True
    assert info.width == 640
    assert info.height == 360

    # Извлекаем кадр в формате raw RGB и проверяем, что появились светлые пиксели текста
    ffmpeg = find_ffmpeg()
    frame_proc = subprocess.run(
        [
            ffmpeg,
            "-ss",
            "1.0",
            "-i",
            str(out),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    raw = frame_proc.stdout
    assert len(raw) == 640 * 360 * 3
    bright_pixels = sum(
        1
        for i in range(0, len(raw), 3)
        if raw[i] > 200 and raw[i + 1] > 200 and raw[i + 2] > 200
    )
    assert bright_pixels > 100


def test_hardsub_special_filename_and_no_audio(tmp_path: Path) -> None:
    src = _make_sample_video(tmp_path / "silent.mp4", with_audio=False, duration=1.5)
    srt_path = tmp_path / "субтитры с пробелом '1' [тест].srt"
    write_srt([SubtitleCue(0.0, 1.5, "Привет!")], srt_path)

    out = tmp_path / "out_silent.mp4"
    progress_values: list[float] = []
    add_subtitles(
        src,
        srt_path,
        out,
        mode="hard",
        preset="ultrafast",
        progress_callback=progress_values.append,
    )
    info = probe_video(out)
    assert info.has_video is True
    assert info.has_audio is False
    assert progress_values
    assert progress_values[0] == 0.0
    assert progress_values[-1] == 1.0


def test_softsub_mp4_and_mkv(tmp_path: Path) -> None:
    src = _make_sample_video(tmp_path / "input.mp4", with_audio=True, duration=1.5)
    srt = write_srt([SubtitleCue(0.1, 1.4, "Переключаемые субтитры")], tmp_path / "sub.srt")

    out_mp4 = tmp_path / "soft.mp4"
    add_subtitles(src, srt, out_mp4, mode="soft", language="rus", title="Русские")
    info_mp4 = probe_video(out_mp4)
    assert info_mp4.has_video is True
    assert info_mp4.has_audio is True
    assert info_mp4.subtitle_streams == 1

    out_mkv = tmp_path / "soft.mkv"
    add_subtitles(src, srt, out_mkv, mode="soft", language="rus")
    info_mkv = probe_video(out_mkv)
    assert info_mkv.subtitle_streams == 1


def test_cli_execution(tmp_path: Path) -> None:
    src = _make_sample_video(tmp_path / "cli_in.mp4", with_audio=False, duration=1.0)
    srt = write_srt([SubtitleCue(0.0, 1.0, "CLI тест")], tmp_path / "cli.srt")
    out = tmp_path / "cli_out.mp4"

    exit_code = cli_main(
        [
            str(src),
            str(srt),
            "-o",
            str(out),
            "--mode",
            "hard",
            "--color",
            "yellow",
            "--font-size",
            "28",
            "--preset",
            "ultrafast",
        ]
    )
    assert exit_code == 0
    assert out.is_file()

    bad_exit = cli_main([str(tmp_path / "missing.mp4"), str(srt)])
    assert bad_exit == 1


def test_wrap_subtitle_text() -> None:
    short = "Короткая фраза"
    assert wrap_subtitle_text(short, max_line_chars=42) == short

    long_text = "Это достаточно длинная фраза для проверки переноса текста на две строки в субтитрах"
    wrapped = wrap_subtitle_text(long_text, max_line_chars=42)
    lines = wrapped.splitlines()
    assert len(lines) == 2
    assert all(line.strip() for line in lines)


def test_extract_audio_16k_mono(tmp_path: Path) -> None:
    with_audio = _make_sample_video(tmp_path / "audio.mp4", with_audio=True, duration=1.0)
    pcm = extract_audio_16k_mono(with_audio)
    assert pcm.ndim == 1
    assert 15000 <= len(pcm) <= 17000
    assert float(pcm.max()) <= 1.01
    assert float(pcm.min()) >= -1.01

    silent = _make_sample_video(tmp_path / "silent.mp4", with_audio=False, duration=1.0)
    with pytest.raises(SubtitleError):
        extract_audio_16k_mono(silent)


def test_auto_subtitle_pipeline_with_mocked_whisper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pywhispercpp.model as pw_model

    src = _make_sample_video(tmp_path / "speech.mp4", with_audio=True, duration=1.5)
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "ggml-tiny.bin").write_bytes(b"fake-ggml-model")

    class FakeSegment:
        def __init__(self, t0: int, t1: int, text: str) -> None:
            self.t0 = t0
            self.t1 = t1
            self.text = text

    class FakeModel:
        def __init__(self, model_path: str, **_kwargs) -> None:
            assert Path(model_path).name == "ggml-tiny.bin"

        def transcribe(self, audio, new_segment_callback=None, **_params):
            assert len(audio) > 10000
            segs = [
                FakeSegment(0, 70, "Привет, это автосубтитры"),
                FakeSegment(75, 145, "Работает без подготовки файла"),
            ]
            if new_segment_callback:
                for s in segs:
                    new_segment_callback(s)
            return segs

    monkeypatch.setattr(pw_model, "Model", FakeModel)

    # Проверяем transcribe_video напрямую
    cues = transcribe_video(
        src,
        model_name="tiny",
        language="ru",
        models_dir=models_dir,
    )
    assert len(cues) == 2
    assert cues[0].text == "Привет, это автосубтитры"

    # Проверяем полный цикл auto_subtitle_video (с патчем models_dir по умолчанию)
    monkeypatch.setattr("video_subtitles.transcribe._default_models_dir", lambda: models_dir)
    out = tmp_path / "auto_out.mp4"
    res = auto_subtitle_video(
        src,
        out,
        model_name="tiny",
        language="ru",
        save_srt=True,
        preset="ultrafast",
    )
    assert res.video_result.output_path.is_file()
    assert res.srt_path is not None and res.srt_path.is_file()
    assert len(parse_srt(res.srt_path.read_text(encoding="utf-8"))) == 2

