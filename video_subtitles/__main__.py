"""Точка входа при запуске `python -m video_subtitles`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
