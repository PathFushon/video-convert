# video-convert — программа на Python для автоматического создания и наложения субтитров

Готовое приложение с графическим интерфейсом (без терминала), CLI и Python-библиотекой: просто выберите видео, а программа **сама распознает речь (ИИ Whisper / whisper.cpp)**, создаст субтитры и наложит их на видео. Также поддерживается загрузка готовых `.srt`, `.ass`, `.ssa`, `.vtt`, `.json` и встроенный редактор реплик.

## Возможности

- **Автоматическое создание субтитров из видео (Whisper):**
  - Выбираете только видео — программа сама извлекает звук, распознаёт речь (поддерживается русский, английский и десятки других языков, а также автоопределение) и генерирует субтитры.
  - Работает локально на CPU без тяжёлого PyTorch (через `pywhispercpp` / `whisper.cpp`).
  - Автоматически сохраняет готовый `.srt` рядом с видео и при желании открывает встроенный редактор перед вжиганием.
- **Два режима работы:**
  - `hard` (по умолчанию) — вжигание (burn-in) субтитров прямо в кадр с настраиваемым шрифтом, размером, цветом, обводкой, тенью, подложкой и положением.
  - `soft` — добавление субтитров отдельной переключаемой дорожкой (`mov_text` для MP4/MOV, `srt`/`ass` для MKV, `webvtt` для WebM) **без перекодирования видео и аудио**.
- **Поддержка кириллицы** и автоматический выбор системного шрифта (`DejaVu Sans`, `Liberation Sans`, `Noto Sans`, `Arial` и др.).
- **Гибкие входные форматы:** `.srt`, `.ass`, `.ssa`, `.vtt`, `.json` (список реплик) или объекты `SubtitleCue` прямо из Python-кода.
- **Коррекция таймингов** (`--shift`) для сдвига субтитров вперёд или назад в секундах.
- **Работает «из коробки»:** автоматически использует системный `ffmpeg` из `PATH` или встроенный бинарник из пакета `imageio-ffmpeg`.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Графический интерфейс (без терминала)

На Windows достаточно дважды кликнуть по файлу **`start_gui.bat`** (или **`gui.pyw`**) в папке проекта — откроется оконное приложение, где можно:
1. Выбрать видеофайл;
2. Нажать **«Создать и добавить субтитры»** — программа сама распознает речь, создаст субтитры и наложит их на видео;
3. При желании настроить шрифт, цвет, обводку, плашку и положение с предпросмотром или поправить текст во встроенном редакторе;
4. Отслеживать прогресс в процентах и открыть готовое видео кнопкой.

Также GUI можно запустить командой `python -m video_subtitles.gui`.

## Быстрый старт (CLI)

### 1. Автоматически создать субтитры из речи и вжечь в видео

```bash
python add_subtitles.py video.mp4
```

С указанием модели и языка (например, `small` — оптимально для русского):

```bash
python add_subtitles.py video.mp4 -o video_with_subs.mp4 --model small --speech-lang ru
```

### 2. Вжечь готовые субтитры в видео (hardsub)

```bash
python add_subtitles.py video.mp4 subtitles.srt -o video_with_subs.mp4
```

С настройкой стиля (крупный жёлтый шрифт, полупрозрачная плашка, сверху):

```bash
python add_subtitles.py video.mp4 subtitles.srt \
  -o video_styled.mp4 \
  --font "DejaVu Sans" \
  --font-size 28 \
  --color "#FFFF00" \
  --bold \
  --box \
  --align bottom \
  --margin-v 30
```

### 2. Добавить как переключаемую дорожку (softsub, без перекодирования)

```bash
python add_subtitles.py video.mp4 subtitles.srt \
  -o video_soft.mp4 \
  --mode soft \
  --lang rus \
  --title "Русские субтитры"
```

### 3. Сдвинуть субтитры по времени на +1.5 секунды

```bash
python add_subtitles.py video.mp4 subtitles.srt -o synced.mp4 --shift 1.5
```

### 4. Использование JSON-файла вместо SRT

Файл `cues.json`:
```json
[
  {"start": 0.5, "end": 3.0, "text": "Привет! Это тестовый ролик."},
  {"start": "00:00:03,500", "end": "00:00:06,200", "text": "Субтитры добавлены на Python."}
]
```

Запуск:
```bash
python add_subtitles.py video.mp4 cues.json -o output.mp4
```

## Использование как Python-библиотеки

```python
from video_subtitles import SubtitleCue, SubtitleStyle, add_subtitles

cues = [
    SubtitleCue(start=0.5, end=3.0, text="Первая фраза"),
    SubtitleCue(start=3.5, end=6.0, text="Вторая фраза"),
]

style = SubtitleStyle(
    font_size=28,
    primary_color="#FFFFFF",
    outline_color="#000000",
    outline=2.0,
    bold=True,
    alignment="bottom",
)

result = add_subtitles(
    video_path="input.mp4",
    subtitle_source=cues,   # или путь к .srt / .ass / .vtt / .json
    output_path="output.mp4",
    mode="hard",
    style=style,
)
print("Готово:", result.output_path)
```

## Запуск тестов

```bash
pip install -e ".[dev]"
pytest -v
```
