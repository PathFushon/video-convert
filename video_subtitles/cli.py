"""CLI-интерфейс для добавления субтитров на видео."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .core import SubtitleError, SubtitleStyle, add_subtitles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="add_subtitles.py",
        description=(
            "Добавляет субтитры (.srt, .ass, .ssa, .vtt, .json) на видео: "
            "вжённые в кадр (hard) или как отдельная переключаемая дорожка (soft)."
        ),
    )
    parser.add_argument("video", type=Path, help="Путь к исходному видеофайлу")
    parser.add_argument(
        "subtitles",
        type=Path,
        help="Путь к файлу субтитров (.srt, .ass, .ssa, .vtt, .json)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Путь к итоговому видео (по умолчанию <имя>_subtitled.<ext>)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=("hard", "soft"),
        default="hard",
        help="Режим: hard (вжечь в видео, по умолчанию) или soft (дорожка без перекодирования)",
    )

    style_group = parser.add_argument_group("Оформление субтитров (для режима hard)")
    style_group.add_argument("--font", default=None, help="Имя шрифта (например, 'DejaVu Sans')")
    style_group.add_argument("--font-size", type=int, default=24, help="Размер шрифта (по умолчанию 24)")
    style_group.add_argument(
        "--color",
        default="#FFFFFF",
        help="Цвет текста (#RRGGBB, white, yellow и т.д., по умолчанию #FFFFFF)",
    )
    style_group.add_argument(
        "--outline-color",
        default="#000000",
        help="Цвет обводки (#RRGGBB, по умолчанию #000000)",
    )
    style_group.add_argument(
        "--back-color",
        default="#00000080",
        help="Цвет тени/подложки (#RRGGBB или #RRGGBBAA, по умолчанию #00000080)",
    )
    style_group.add_argument(
        "--outline",
        type=float,
        default=2.0,
        help="Толщина обводки в пикселях (по умолчанию 2.0)",
    )
    style_group.add_argument(
        "--shadow",
        type=float,
        default=1.0,
        help="Размер тени (по умолчанию 1.0)",
    )
    style_group.add_argument("--bold", action="store_true", help="Жирный шрифт")
    style_group.add_argument("--italic", action="store_true", help="Курсив")
    style_group.add_argument(
        "--box",
        action="store_true",
        help="Рисовать полупрозрачную плашку за текстом (BorderStyle=3)",
    )
    style_group.add_argument(
        "--align",
        default="bottom",
        help="Положение: bottom, center, top или код ASS 1–9 (по умолчанию bottom)",
    )
    style_group.add_argument(
        "--margin-v",
        type=int,
        default=24,
        help="Отступ по вертикали в пикселях (по умолчанию 24)",
    )
    style_group.add_argument(
        "--fonts-dir",
        type=Path,
        default=None,
        help="Папка с пользовательскими шрифтами (.ttf/.otf)",
    )

    soft_group = parser.add_argument_group("Настройки дорожки (для режима soft)")
    soft_group.add_argument(
        "--lang",
        default="rus",
        help="Код языка субтитров ISO 639-2 (например, rus, eng; по умолчанию rus)",
    )
    soft_group.add_argument(
        "--title",
        default=None,
        help="Название дорожки субтитров в плеере",
    )
    soft_group.add_argument(
        "--no-default",
        action="store_true",
        help="Не помечать дорожку как основную (default)",
    )

    enc_group = parser.add_argument_group("Кодирование и прочее")
    enc_group.add_argument(
        "--shift",
        type=float,
        default=0.0,
        help="Сдвиг субтитров по времени в секундах (например, 1.5 или -0.8)",
    )
    enc_group.add_argument("--codec", default="libx264", help="Видеокодек для hard (по умолчанию libx264)")
    enc_group.add_argument("--crf", type=int, default=18, help="Качество CRF для hard (по умолчанию 18)")
    enc_group.add_argument("--preset", default="medium", help="Пресет кодирования (по умолчанию medium)")
    enc_group.add_argument("--ffmpeg", type=Path, default=None, help="Явный путь к ffmpeg")
    enc_group.add_argument("--no-overwrite", action="store_true", help="Не перезаписывать выходной файл")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    style = SubtitleStyle(
        font_name=args.font,
        font_size=args.font_size,
        primary_color=args.color,
        outline_color=args.outline_color,
        back_color=args.back_color,
        outline=args.outline,
        shadow=args.shadow,
        bold=args.bold,
        italic=args.italic,
        alignment=args.align,
        margin_v=args.margin_v,
        border_style=3 if args.box else 1,
    )

    try:
        result = add_subtitles(
            video_path=args.video,
            subtitle_source=args.subtitles,
            output_path=args.output,
            mode=args.mode,
            style=style,
            shift_seconds=args.shift,
            language=args.lang,
            title=args.title,
            default_track=not args.no_default,
            video_codec=args.codec,
            crf=args.crf,
            preset=args.preset,
            fonts_dir=args.fonts_dir,
            overwrite=not args.no_overwrite,
            ffmpeg_path=args.ffmpeg,
        )
    except (SubtitleError, FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(f"Готово ({result.mode}): {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
