"""Графический интерфейс (Tkinter) для автоматического создания и наложения субтитров на видео."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Optional

from .core import SubtitleResult, SubtitleStyle, add_subtitles, probe_video
from .fonts import find_default_font_name, list_system_fonts
from .srt import SubtitleCue, format_timestamp_srt, parse_srt, parse_timestamp, write_srt
from .transcribe import WHISPER_LANGUAGES, WHISPER_MODELS, transcribe_video

ALIGN_LABELS = {
    "Внизу по центру": "bottom",
    "По центру экрана": "center",
    "Сверху по центру": "top",
}

PRESETS = ("ultrafast", "veryfast", "fast", "medium", "slow")


def _enable_windows_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes  # type: ignore

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def _open_path_in_os(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class SubtitleEditorDialog(tk.Toplevel):
    """Встроенный редактор реплик субтитров (создание и правка без сторонних программ)."""

    def __init__(
        self,
        parent: tk.Misc,
        initial_cues: list[SubtitleCue],
        on_apply,
        apply_label: str = "Использовать эти субтитры",
    ) -> None:
        super().__init__(parent)
        self.title("Редактор субтитров")
        self.geometry("740x520")
        self.minsize(620, 420)
        self.transient(parent)
        self.grab_set()

        self._cues: list[SubtitleCue] = list(initial_cues)
        self._on_apply = on_apply
        self._apply_label = apply_label

        self._build_ui()
        self._refresh_tree()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        tree_frame = ttk.Frame(root)
        tree_frame.pack(fill="both", expand=True)

        columns = ("num", "start", "end", "text")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("num", text="№")
        self.tree.heading("start", text="Начало")
        self.tree.heading("end", text="Конец")
        self.tree.heading("text", text="Текст субтитра")

        self.tree.column("num", width=40, anchor="center", stretch=False)
        self.tree.column("start", width=110, anchor="center", stretch=False)
        self.tree.column("end", width=110, anchor="center", stretch=False)
        self.tree.column("text", width=420, anchor="w", stretch=True)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        form = ttk.LabelFrame(root, text="Редактирование / добавление реплики", padding=10)
        form.pack(fill="x", pady=(10, 8))

        row1 = ttk.Frame(form)
        row1.pack(fill="x")

        ttk.Label(row1, text="Начало (сек или ЧЧ:ММ:СС,ммм):").pack(side="left")
        self.start_var = tk.StringVar(value="00:00:00,000")
        ttk.Entry(row1, textvariable=self.start_var, width=16).pack(side="left", padx=(6, 16))

        ttk.Label(row1, text="Конец:").pack(side="left")
        self.end_var = tk.StringVar(value="00:00:03,000")
        ttk.Entry(row1, textvariable=self.end_var, width=16).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(form)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="Текст:").pack(side="left")
        self.text_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.text_var).pack(side="left", fill="x", expand=True, padx=(8, 0))

        btn_row = ttk.Frame(form)
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="+ Добавить реплику", command=self._add_cue).pack(side="left")
        ttk.Button(btn_row, text="Обновить выбранную", command=self._update_selected).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(btn_row, text="Удалить выбранную", command=self._delete_selected).pack(
            side="left", padx=(6, 0)
        )

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(4, 0))
        ttk.Button(bottom, text="Загрузить из .srt...", command=self._load_srt).pack(side="left")
        ttk.Button(bottom, text="Сохранить в .srt...", command=self._save_srt).pack(
            side="left", padx=(6, 0)
        )

        ttk.Button(bottom, text="Отмена", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text=self._apply_label, command=self._apply).pack(
            side="right", padx=(0, 8)
        )

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._cues.sort(key=lambda c: (c.start, c.end))
        for idx, cue in enumerate(self._cues, start=1):
            self.tree.insert(
                "",
                "end",
                iid=str(idx - 1),
                values=(
                    idx,
                    format_timestamp_srt(cue.start),
                    format_timestamp_srt(cue.end),
                    cue.text.replace("\n", " / "),
                ),
            )

    def _on_select_row(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        cue = self._cues[int(sel[0])]
        self.start_var.set(format_timestamp_srt(cue.start))
        self.end_var.set(format_timestamp_srt(cue.end))
        self.text_var.set(cue.text)

    def _read_form_cue(self) -> Optional[SubtitleCue]:
        try:
            start = parse_timestamp(self.start_var.get())
            end = parse_timestamp(self.end_var.get())
            text = self.text_var.get().strip()
            return SubtitleCue(start=start, end=end, text=text)
        except ValueError as exc:
            messagebox.showerror("Ошибка в реплике", str(exc), parent=self)
            return None

    def _add_cue(self) -> None:
        cue = self._read_form_cue()
        if cue is None:
            return
        self._cues.append(cue)
        self._refresh_tree()
        self.start_var.set(format_timestamp_srt(cue.end + 0.2))
        self.end_var.set(format_timestamp_srt(cue.end + 3.2))
        self.text_var.set("")

    def _update_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Выбор", "Сначала выберите реплику в таблице.", parent=self)
            return
        cue = self._read_form_cue()
        if cue is None:
            return
        self._cues[int(sel[0])] = cue
        self._refresh_tree()

    def _delete_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        del self._cues[int(sel[0])]
        self._refresh_tree()

    def _load_srt(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Открыть файл субтитров SRT",
            filetypes=[("SubRip (.srt)", "*.srt"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            cues = parse_srt(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            messagebox.showerror("Ошибка чтения", str(exc), parent=self)
            return
        self._cues = cues
        self._refresh_tree()

    def _save_srt(self) -> None:
        if not self._cues:
            messagebox.showwarning("Пусто", "Добавьте хотя бы одну реплику.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить субтитры в SRT",
            defaultextension=".srt",
            filetypes=[("SubRip (.srt)", "*.srt")],
        )
        if not path:
            return
        write_srt(self._cues, path)
        messagebox.showinfo("Сохранено", f"Файл сохранён:\n{path}", parent=self)

    def _apply(self) -> None:
        if not self._cues:
            messagebox.showwarning("Пусто", "Добавьте хотя бы одну реплику.", parent=self)
            return
        self._on_apply(list(self._cues))
        self.destroy()


class SubtitleApp:
    """Главное окно приложения для автоматического создания и добавления субтитров."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Автосубтитры на видео")
        self.root.geometry("860x780")
        self.root.minsize(760, 700)

        self._style_obj = ttk.Style()
        for theme in ("vista", "clam", "default"):
            if theme in self._style_obj.theme_names():
                self._style_obj.theme_use(theme)
                break

        # Основные пути
        self.video_path_var = tk.StringVar()
        self.video_info_var = tk.StringVar(value="Выберите видеофайл — субтитры создадутся автоматически")
        self.out_path_var = tk.StringVar()

        # Источник субтитров: auto / file / editor
        self.source_mode_var = tk.StringVar(value="auto")
        self.sub_path_var = tk.StringVar()
        self._editor_cues: Optional[list[SubtitleCue]] = None

        # Настройки Whisper (auto)
        self._model_labels = {label: key for key, label in WHISPER_MODELS.items()}
        self.model_label_var = tk.StringVar(value=WHISPER_MODELS["small"])
        self._lang_labels = {label: key for key, label in WHISPER_LANGUAGES.items()}
        self.speech_lang_label_var = tk.StringVar(value=WHISPER_LANGUAGES["auto"])
        self.translate_var = tk.BooleanVar(value=False)
        self.save_srt_var = tk.BooleanVar(value=True)
        self.review_before_burn_var = tk.BooleanVar(value=False)

        # Режим наложения
        self.mode_var = tk.StringVar(value="hard")

        # Стиль Hardsub
        default_font = find_default_font_name() or "Arial"
        self.font_var = tk.StringVar(value=default_font)
        self.font_size_var = tk.IntVar(value=28)
        self.primary_color_var = tk.StringVar(value="#FFFFFF")
        self.outline_color_var = tk.StringVar(value="#000000")
        self.outline_var = tk.DoubleVar(value=2.0)
        self.shadow_var = tk.DoubleVar(value=1.0)
        self.bold_var = tk.BooleanVar(value=False)
        self.italic_var = tk.BooleanVar(value=False)
        self.box_var = tk.BooleanVar(value=False)
        self.align_label_var = tk.StringVar(value="Внизу по центру")
        self.margin_v_var = tk.IntVar(value=24)

        # Настройки Softsub
        self.lang_var = tk.StringVar(value="rus")
        self.title_var = tk.StringVar(value="Русские субтитры")
        self.default_track_var = tk.BooleanVar(value=True)

        # Кодирование и тайминги
        self.shift_var = tk.DoubleVar(value=0.0)
        self.crf_var = tk.IntVar(value=18)
        self.preset_var = tk.StringVar(value="medium")

        self.status_var = tk.StringVar(value="Выберите видео и нажмите «Создать и добавить субтитры».")
        self.progress_var = tk.DoubleVar(value=0.0)
        self._is_running = False
        self._last_output: Optional[Path] = None
        self._last_srt: Optional[Path] = None

        self._system_fonts = self._collect_fonts(default_font)
        self._build_ui()
        self._bind_preview_updates()
        self._on_source_mode_change()
        self._on_mode_change()
        self._update_preview()

    def _collect_fonts(self, default_font: str) -> list[str]:
        fonts = list_system_fonts()
        if not fonts:
            fonts = ["Arial", "DejaVu Sans", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"]
        if default_font and default_font not in fonts:
            fonts = [default_font, *fonts]
        return fonts

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.pack(fill="both", expand=True)

        # --- 1. Видео ---
        video_frame = ttk.LabelFrame(container, text="1. Видео", padding=10)
        video_frame.pack(fill="x")
        video_frame.columnconfigure(1, weight=1)

        ttk.Label(video_frame, text="Видеофайл:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(video_frame, textvariable=self.video_path_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=3
        )
        ttk.Button(video_frame, text="Выбрать видео...", command=self._browse_video).grid(
            row=0, column=2, sticky="ew", pady=3
        )

        ttk.Label(
            video_frame, textvariable=self.video_info_var, foreground="#555555"
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=8)

        ttk.Label(video_frame, text="Сохранить результат:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(video_frame, textvariable=self.out_path_var).grid(
            row=2, column=1, sticky="ew", padx=8, pady=3
        )
        ttk.Button(video_frame, text="Обзор...", command=self._browse_output).grid(
            row=2, column=2, sticky="ew", pady=3
        )

        # --- 2. Откуда взять субтитры ---
        src_frame = ttk.LabelFrame(container, text="2. Создание / источник субтитров", padding=10)
        src_frame.pack(fill="x", pady=(10, 0))
        src_frame.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            src_frame,
            text="Автоматически распознать речь из видео (ИИ Whisper — ничего не нужно готовить)",
            variable=self.source_mode_var,
            value="auto",
            command=self._on_source_mode_change,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        # Панель параметров Whisper
        self.auto_panel = ttk.Frame(src_frame, padding=(22, 4, 0, 4))
        self.auto_panel.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.auto_panel.columnconfigure(1, weight=1)

        ttk.Label(self.auto_panel, text="Модель ИИ:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(
            self.auto_panel,
            textvariable=self.model_label_var,
            values=list(WHISPER_MODELS.values()),
            state="readonly",
            width=48,
        ).grid(row=0, column=1, sticky="w", padx=(6, 16), pady=3)

        ttk.Label(self.auto_panel, text="Язык речи:").grid(row=0, column=2, sticky="w", pady=3)
        ttk.Combobox(
            self.auto_panel,
            textvariable=self.speech_lang_label_var,
            values=list(WHISPER_LANGUAGES.values()),
            state="readonly",
            width=20,
        ).grid(row=0, column=3, sticky="w", padx=(6, 0), pady=3)

        auto_checks = ttk.Frame(self.auto_panel)
        auto_checks.grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 0))
        ttk.Checkbutton(
            auto_checks, text="Сохранить файл .srt рядом с видео", variable=self.save_srt_var
        ).pack(side="left")
        ttk.Checkbutton(
            auto_checks,
            text="Проверить текст в редакторе перед наложением",
            variable=self.review_before_burn_var,
        ).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(
            auto_checks, text="Перевести на английский", variable=self.translate_var
        ).pack(side="left", padx=(14, 0))

        ttk.Radiobutton(
            src_frame,
            text="Использовать готовый файл субтитров (.srt, .ass, .vtt, .json)",
            variable=self.source_mode_var,
            value="file",
            command=self._on_source_mode_change,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.file_panel = ttk.Frame(src_frame, padding=(22, 4, 0, 2))
        self.file_panel.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.file_panel.columnconfigure(0, weight=1)

        self.sub_entry = ttk.Entry(self.file_panel, textvariable=self.sub_path_var)
        self.sub_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.sub_browse_btn = ttk.Button(
            self.file_panel, text="Выбрать файл...", command=self._browse_subtitles
        )
        self.sub_browse_btn.grid(row=0, column=1)

        ed_row = ttk.Frame(src_frame)
        ed_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Radiobutton(
            ed_row,
            text="Создать или отредактировать вручную в редакторе",
            variable=self.source_mode_var,
            value="editor",
            command=self._on_source_mode_change,
        ).pack(side="left")
        self.editor_btn = ttk.Button(ed_row, text="Открыть редактор...", command=self._open_editor)
        self.editor_btn.pack(side="left", padx=(12, 0))

        # --- 3. Как добавить на видео ---
        mode_frame = ttk.LabelFrame(container, text="3. Как добавить субтитры на видео", padding=8)
        mode_frame.pack(fill="x", pady=(10, 0))

        ttk.Radiobutton(
            mode_frame,
            text="Вжечь в кадр (Hardsub) — видны всегда и везде, настраиваемый стиль",
            variable=self.mode_var,
            value="hard",
            command=self._on_mode_change,
        ).pack(side="left")
        ttk.Radiobutton(
            mode_frame,
            text="Отдельная дорожка (Softsub) — можно включать/выключать",
            variable=self.mode_var,
            value="soft",
            command=self._on_mode_change,
        ).pack(side="left", padx=(18, 0))

        # --- 4. Настройки ---
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True, pady=(10, 0))

        self.hard_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.hard_tab, text="Оформление (Hardsub)")
        self._build_hard_tab(self.hard_tab)

        self.soft_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.soft_tab, text="Дорожка (Softsub)")
        self._build_soft_tab(self.soft_tab)

        self.adv_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.adv_tab, text="Тайминг и качество")
        self._build_advanced_tab(self.adv_tab)

        # --- 5. Запуск и прогресс ---
        bottom = ttk.Frame(container, padding=(0, 10, 0, 0))
        bottom.pack(fill="x")

        self.progress_bar = ttk.Progressbar(
            bottom, variable=self.progress_var, maximum=100.0, mode="determinate"
        )
        self.progress_bar.pack(fill="x")

        status_row = ttk.Frame(bottom)
        status_row.pack(fill="x", pady=(8, 0))

        ttk.Label(status_row, textvariable=self.status_var).pack(side="left")

        self.open_video_btn = ttk.Button(
            status_row, text="Открыть видео", command=self._open_output_video, state="disabled"
        )
        self.open_video_btn.pack(side="right", padx=(6, 0))

        self.open_folder_btn = ttk.Button(
            status_row, text="Открыть папку", command=self._open_output_folder, state="disabled"
        )
        self.open_folder_btn.pack(side="right", padx=(6, 0))

        self.run_btn = ttk.Button(
            status_row, text="Создать и добавить субтитры", command=self._start_processing
        )
        self.run_btn.pack(side="right")

    def _build_hard_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)

        ttk.Label(parent, text="Шрифт:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(
            parent, textvariable=self.font_var, values=self._system_fonts, state="normal"
        ).grid(row=0, column=1, sticky="ew", padx=(6, 16), pady=3)

        ttk.Label(parent, text="Размер:").grid(row=0, column=2, sticky="w", pady=3)
        ttk.Spinbox(parent, from_=10, to=96, textvariable=self.font_size_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(6, 0), pady=3
        )

        ttk.Label(parent, text="Цвет текста:").grid(row=1, column=0, sticky="w", pady=3)
        c1_frame = ttk.Frame(parent)
        c1_frame.grid(row=1, column=1, sticky="w", padx=(6, 16), pady=3)
        self.primary_swatch = tk.Label(
            c1_frame, width=3, bg=self.primary_color_var.get(), relief="solid", bd=1
        )
        self.primary_swatch.pack(side="left")
        ttk.Entry(c1_frame, textvariable=self.primary_color_var, width=10).pack(side="left", padx=6)
        ttk.Button(
            c1_frame,
            text="Цвет...",
            command=lambda: self._pick_color(self.primary_color_var, self.primary_swatch),
        ).pack(side="left")

        ttk.Label(parent, text="Цвет обводки:").grid(row=1, column=2, sticky="w", pady=3)
        c2_frame = ttk.Frame(parent)
        c2_frame.grid(row=1, column=3, sticky="w", padx=(6, 0), pady=3)
        self.outline_swatch = tk.Label(
            c2_frame, width=3, bg=self.outline_color_var.get(), relief="solid", bd=1
        )
        self.outline_swatch.pack(side="left")
        ttk.Entry(c2_frame, textvariable=self.outline_color_var, width=10).pack(side="left", padx=6)
        ttk.Button(
            c2_frame,
            text="Цвет...",
            command=lambda: self._pick_color(self.outline_color_var, self.outline_swatch),
        ).pack(side="left")

        ttk.Label(parent, text="Толщина обводки:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Spinbox(
            parent, from_=0.0, to=8.0, increment=0.5, textvariable=self.outline_var, width=8
        ).grid(row=2, column=1, sticky="w", padx=(6, 16), pady=3)

        ttk.Label(parent, text="Положение:").grid(row=2, column=2, sticky="w", pady=3)
        ttk.Combobox(
            parent,
            textvariable=self.align_label_var,
            values=list(ALIGN_LABELS.keys()),
            state="readonly",
            width=20,
        ).grid(row=2, column=3, sticky="w", padx=(6, 0), pady=3)

        ttk.Label(parent, text="Отступ по вертикали:").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(parent, from_=0, to=200, textvariable=self.margin_v_var, width=8).grid(
            row=3, column=1, sticky="w", padx=(6, 16), pady=3
        )

        ttk.Label(parent, text="Тень:").grid(row=3, column=2, sticky="w", pady=3)
        ttk.Spinbox(
            parent, from_=0.0, to=8.0, increment=0.5, textvariable=self.shadow_var, width=8
        ).grid(row=3, column=3, sticky="w", padx=(6, 0), pady=3)

        flags_frame = ttk.Frame(parent)
        flags_frame.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 4))
        ttk.Checkbutton(flags_frame, text="Жирный", variable=self.bold_var).pack(side="left")
        ttk.Checkbutton(flags_frame, text="Курсив", variable=self.italic_var).pack(
            side="left", padx=(14, 0)
        )
        ttk.Checkbutton(
            flags_frame, text="Полупрозрачная плашка под текстом", variable=self.box_var
        ).pack(side="left", padx=(14, 0))

        preview_box = ttk.LabelFrame(parent, text="Предпросмотр стиля", padding=6)
        preview_box.grid(row=5, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        parent.rowconfigure(5, weight=1)

        self.preview_canvas = tk.Canvas(preview_box, height=96, bg="#1B263B", highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda _e: self._update_preview())

    def _build_soft_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        ttk.Label(parent, text="Код языка (ISO 639-2):").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Combobox(
            parent,
            textvariable=self.lang_var,
            values=("rus", "eng", "ukr", "bel", "kaz", "deu", "fra", "spa"),
            width=12,
        ).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(parent, text="Название дорожки:").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=self.title_var, width=32).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )

        ttk.Checkbutton(
            parent,
            text="Сделать дорожку основной (включается по умолчанию в плеере)",
            variable=self.default_track_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=8)

        ttk.Label(
            parent,
            text="Примечание: в режиме Softsub видео и аудио копируются без перекодирования.",
            foreground="#555555",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_advanced_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Сдвиг субтитров по времени (сек):").grid(
            row=0, column=0, sticky="w", pady=6
        )
        ttk.Spinbox(
            parent, from_=-300.0, to=300.0, increment=0.5, textvariable=self.shift_var, width=10
        ).grid(row=0, column=1, sticky="w", padx=8, pady=6)
        ttk.Label(
            parent,
            text="(положительное число — позже, отрицательное — раньше)",
            foreground="#555555",
        ).grid(row=0, column=2, sticky="w")

        ttk.Label(parent, text="Качество видео (CRF, 15–28):").grid(
            row=1, column=0, sticky="w", pady=6
        )
        ttk.Spinbox(parent, from_=14, to=32, textvariable=self.crf_var, width=10).grid(
            row=1, column=1, sticky="w", padx=8, pady=6
        )
        ttk.Label(
            parent,
            text="(меньше = выше качество и больше размер файла; 18–20 оптимально)",
            foreground="#555555",
        ).grid(row=1, column=2, sticky="w")

        ttk.Label(parent, text="Скорость кодирования:").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(
            parent, textvariable=self.preset_var, values=PRESETS, state="readonly", width=12
        ).grid(row=2, column=1, sticky="w", padx=8, pady=6)

    def _bind_preview_updates(self) -> None:
        vars_to_watch = (
            self.font_var,
            self.font_size_var,
            self.primary_color_var,
            self.outline_color_var,
            self.outline_var,
            self.bold_var,
            self.italic_var,
            self.box_var,
            self.align_label_var,
            self.margin_v_var,
        )
        for var in vars_to_watch:
            var.trace_add("write", lambda *_args: self._update_preview())

    def _set_frame_children_state(self, frame: ttk.Frame, state: str) -> None:
        for child in frame.winfo_children():
            if isinstance(child, ttk.Frame):
                self._set_frame_children_state(child, state)
            else:
                try:
                    if isinstance(child, ttk.Combobox) and state == "normal":
                        child.configure(state="readonly")
                    else:
                        child.configure(state=state)
                except tk.TclError:
                    pass

    def _on_source_mode_change(self) -> None:
        src_mode = self.source_mode_var.get()
        self._set_frame_children_state(self.auto_panel, "normal" if src_mode == "auto" else "disabled")
        self._set_frame_children_state(self.file_panel, "normal" if src_mode == "file" else "disabled")
        self.editor_btn.configure(state="normal" if src_mode == "editor" else "disabled")
        if src_mode == "auto":
            self.run_btn.configure(text="Создать и добавить субтитры")
        else:
            self.run_btn.configure(text="Добавить субтитры")

    def _on_mode_change(self) -> None:
        if self.mode_var.get() == "hard":
            self.notebook.select(self.hard_tab)
        else:
            self.notebook.select(self.soft_tab)

    def _pick_color(self, target_var: tk.StringVar, swatch: tk.Label) -> None:
        initial = target_var.get() if target_var.get().startswith("#") else "#FFFFFF"
        _rgb, hex_color = colorchooser.askcolor(
            color=initial, parent=self.root, title="Выбор цвета"
        )
        if hex_color:
            target_var.set(hex_color.upper())
            try:
                swatch.configure(bg=hex_color)
            except tk.TclError:
                pass

    def _update_preview(self) -> None:
        if not hasattr(self, "preview_canvas"):
            return
        canvas = self.preview_canvas
        canvas.delete("all")
        w = max(canvas.winfo_width(), 320)
        h = max(canvas.winfo_height(), 90)

        font_name = self.font_var.get().strip() or "Arial"
        try:
            raw_size = int(self.font_size_var.get())
        except Exception:
            raw_size = 24
        preview_size = max(10, min(30, int(raw_size * 0.72)))

        weight = "bold" if self.bold_var.get() else "normal"
        slant = "italic" if self.italic_var.get() else "roman"
        font_spec = (font_name, preview_size, weight, slant)

        primary = self.primary_color_var.get().strip() or "#FFFFFF"
        outline = self.outline_color_var.get().strip() or "#000000"
        for swatch, color in ((self.primary_swatch, primary), (self.outline_swatch, outline)):
            try:
                swatch.configure(bg=color)
            except tk.TclError:
                pass

        align = ALIGN_LABELS.get(self.align_label_var.get(), "bottom")
        if align == "top":
            y = 22
        elif align == "center":
            y = h // 2
        else:
            y = h - 22
        x = w // 2
        sample_text = "Пример субтитров • Sample Subtitle"

        if self.box_var.get():
            temp_id = canvas.create_text(x, y, text=sample_text, font=font_spec)
            bbox = canvas.bbox(temp_id)
            canvas.delete(temp_id)
            if bbox:
                canvas.create_rectangle(
                    bbox[0] - 8,
                    bbox[1] - 4,
                    bbox[2] + 8,
                    bbox[3] + 4,
                    fill="#000000",
                    outline="",
                )

        try:
            outline_px = int(round(float(self.outline_var.get())))
        except Exception:
            outline_px = 2
        if outline_px > 0 and not self.box_var.get():
            d = min(outline_px, 3)
            for dx in (-d, 0, d):
                for dy in (-d, 0, d):
                    if dx == 0 and dy == 0:
                        continue
                    try:
                        canvas.create_text(
                            x + dx, y + dy, text=sample_text, font=font_spec, fill=outline
                        )
                    except tk.TclError:
                        pass

        try:
            canvas.create_text(x, y, text=sample_text, font=font_spec, fill=primary)
        except tk.TclError:
            canvas.create_text(x, y, text=sample_text, font=font_spec, fill="#FFFFFF")

    def _browse_video(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Выберите видеофайл",
            filetypes=[
                ("Видеофайлы", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
                ("Все файлы", "*.*"),
            ],
        )
        if not path:
            return
        src = Path(path)
        self.video_path_var.set(str(src))
        self.out_path_var.set(str(src.with_name(f"{src.stem}_subtitled{src.suffix}")))

        try:
            info = probe_video(src)
            parts = []
            if info.width and info.height:
                parts.append(f"{info.width}×{info.height}")
            if info.duration is not None:
                parts.append(f"длительность {info.duration:.1f} сек")
            parts.append("со звуком" if info.has_audio else "без звука (распознавание речи недоступно)")
            self.video_info_var.set(" • ".join(parts))
        except Exception as exc:
            self.video_info_var.set(f"Не удалось прочитать метаданные: {exc}")

    def _browse_subtitles(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Выберите файл субтитров",
            filetypes=[
                ("Субтитры", "*.srt *.ass *.ssa *.vtt *.json"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.sub_path_var.set(path)

    def _browse_output(self) -> None:
        initial_name = "output_subtitled.mp4"
        if self.video_path_var.get().strip():
            src = Path(self.video_path_var.get().strip())
            initial_name = f"{src.stem}_subtitled{src.suffix}"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Сохранить итоговое видео",
            initialfile=initial_name,
            defaultextension=".mp4",
            filetypes=[
                ("MP4", "*.mp4"),
                ("MKV", "*.mkv"),
                ("MOV", "*.mov"),
                ("WebM", "*.webm"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.out_path_var.set(path)

    def _open_editor(self) -> None:
        initial = list(self._editor_cues) if self._editor_cues else []

        def on_apply(cues: list[SubtitleCue]) -> None:
            self._editor_cues = cues
            self.status_var.set(f"В редакторе подготовлено реплик: {len(cues)}")

        SubtitleEditorDialog(self.root, initial, on_apply)

    def _read_style_and_encoding(self) -> tuple[SubtitleStyle, float, int, str]:
        style = SubtitleStyle(
            font_name=self.font_var.get().strip() or None,
            font_size=int(self.font_size_var.get()),
            primary_color=self.primary_color_var.get().strip() or "#FFFFFF",
            outline_color=self.outline_color_var.get().strip() or "#000000",
            back_color="#00000080",
            outline=float(self.outline_var.get()),
            shadow=float(self.shadow_var.get()),
            bold=bool(self.bold_var.get()),
            italic=bool(self.italic_var.get()),
            alignment=ALIGN_LABELS.get(self.align_label_var.get(), "bottom"),
            margin_v=int(self.margin_v_var.get()),
            border_style=3 if self.box_var.get() else 1,
        )
        shift_sec = float(self.shift_var.get())
        crf = int(self.crf_var.get())
        preset = self.preset_var.get()
        return style, shift_sec, crf, preset

    def _start_processing(self) -> None:
        if self._is_running:
            return

        video_str = self.video_path_var.get().strip()
        if not video_str or not Path(video_str).is_file():
            messagebox.showerror("Ошибка", "Сначала выберите видеофайл.", parent=self.root)
            return

        src_mode = self.source_mode_var.get()
        if src_mode == "file":
            sub_str = self.sub_path_var.get().strip()
            if not sub_str or not Path(sub_str).is_file():
                messagebox.showerror("Ошибка", "Выберите существующий файл субтитров.", parent=self.root)
                return
        elif src_mode == "editor" and not self._editor_cues:
            messagebox.showerror(
                "Ошибка", "Откройте редактор и добавьте хотя бы одну реплику.", parent=self.root
            )
            return

        out_str = self.out_path_var.get().strip()
        out_path = Path(out_str) if out_str else None

        try:
            style, shift_sec, crf, preset = self._read_style_and_encoding()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=self.root)
            return

        mode = self.mode_var.get()
        lang = self.lang_var.get().strip() or "rus"
        title = self.title_var.get().strip() or None
        default_track = bool(self.default_track_var.get())

        model_key = self._model_labels.get(self.model_label_var.get(), "small")
        speech_lang = self._lang_labels.get(self.speech_lang_label_var.get(), "auto")
        translate = bool(self.translate_var.get())
        save_srt = bool(self.save_srt_var.get())
        review_before_burn = bool(self.review_before_burn_var.get())

        self._set_busy(True)
        self.progress_var.set(0.0)
        self.status_var.set("Подготовка...")

        def set_progress(pct: float) -> None:
            self.root.after(0, lambda p=pct: self.progress_var.set(max(0.0, min(100.0, p))))

        def set_status(msg: str) -> None:
            self.root.after(0, lambda m=msg: self.status_var.set(m))

        def finish_render(sub_source, srt_saved: Optional[Path]) -> None:
            try:
                set_status("Наложение субтитров на видео...")
                res = add_subtitles(
                    video_path=video_str,
                    subtitle_source=sub_source,
                    output_path=out_path,
                    mode=mode,  # type: ignore[arg-type]
                    style=style,
                    shift_seconds=shift_sec,
                    language=lang,
                    title=title,
                    default_track=default_track,
                    crf=crf,
                    preset=preset,
                    progress_callback=lambda r: set_progress(
                        (55.0 + r * 45.0) if src_mode == "auto" else (r * 100.0)
                    ),
                )
                self.root.after(0, lambda: self._on_success(res, srt_saved))
            except Exception as exc:
                self.root.after(0, lambda err=exc: self._on_error(err))

        def worker() -> None:
            try:
                if src_mode == "file":
                    finish_render(self.sub_path_var.get().strip(), None)
                    return

                if src_mode == "editor":
                    assert self._editor_cues is not None
                    finish_render(list(self._editor_cues), None)
                    return

                # src_mode == "auto": сначала распознаём речь
                cues = transcribe_video(
                    video_str,
                    model_name=model_key,
                    language=speech_lang,
                    translate_to_english=translate,
                    progress_callback=lambda r: set_progress(r * 55.0),
                    status_callback=set_status,
                )

                saved_srt: Optional[Path] = None
                if save_srt:
                    target_srt = (
                        out_path.with_suffix(".srt")
                        if out_path is not None
                        else Path(video_str).with_suffix(".srt")
                    )
                    saved_srt = write_srt(cues, target_srt)

                if review_before_burn:
                    def open_review() -> None:
                        self._set_busy(False)
                        self.status_var.set("Проверьте распознанные субтитры и нажмите «Наложить».")

                        def on_confirmed(edited_cues: list[SubtitleCue]) -> None:
                            if saved_srt is not None:
                                write_srt(edited_cues, saved_srt)
                            self._set_busy(True)
                            threading.Thread(
                                target=lambda: finish_render(edited_cues, saved_srt),
                                daemon=True,
                            ).start()

                        SubtitleEditorDialog(
                            self.root,
                            cues,
                            on_confirmed,
                            apply_label="Наложить на видео",
                        )

                    self.root.after(0, open_review)
                else:
                    finish_render(cues, saved_srt)
            except Exception as exc:
                self.root.after(0, lambda err=exc: self._on_error(err))

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._is_running = busy
        self.run_btn.configure(state="disabled" if busy else "normal")
        if busy:
            self._last_output = None
            self._last_srt = None
            self.open_folder_btn.configure(state="disabled")
            self.open_video_btn.configure(state="disabled")

    def _on_success(self, result: SubtitleResult, srt_path: Optional[Path]) -> None:
        self._set_busy(False)
        self._last_output = result.output_path
        self._last_srt = srt_path
        self.progress_var.set(100.0)
        self.open_folder_btn.configure(state="normal")
        self.open_video_btn.configure(state="normal")
        self.status_var.set(f"Готово! Сохранено: {result.output_path.name}")

        msg = f"Видео с субтитрами готово:\n{result.output_path}"
        if srt_path:
            msg += f"\n\nФайл субтитров (.srt):\n{srt_path}"
        messagebox.showinfo("Готово", msg, parent=self.root)

    def _on_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self.status_var.set("Произошла ошибка при обработке.")
        messagebox.showerror("Ошибка", str(exc), parent=self.root)

    def _open_output_folder(self) -> None:
        if self._last_output and self._last_output.exists():
            _open_path_in_os(self._last_output.parent)

    def _open_output_video(self) -> None:
        if self._last_output and self._last_output.exists():
            _open_path_in_os(self._last_output)


def main() -> int:
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    SubtitleApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
