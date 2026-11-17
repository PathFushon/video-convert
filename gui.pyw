"""Запуск графического интерфейса без консольного окна (Windows .pyw)."""

import sys
import traceback
from pathlib import Path


def _show_fatal_error(text: str) -> None:
    log_path = Path(__file__).resolve().with_name("gui_error.log")
    try:
        log_path.write_text(text, encoding="utf-8")
    except OSError:
        pass

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"Не удалось запустить программу:\n\n{text}\n\nПодробности сохранены в gui_error.log",
                "Ошибка запуска",
                0x10,
            )
            return
        except Exception:
            pass

    print(text, file=sys.stderr)


if __name__ == "__main__":
    try:
        from video_subtitles.gui import main

        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        _show_fatal_error(traceback.format_exc())
        raise SystemExit(1)
