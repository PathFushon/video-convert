"""Графический интерфейс (Tkinter) для добавления субтитров на видео."""

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

ALIGN_LABELS = {
    "Внизу по центру": "bottom",
    "По центру экрана": "center",
    "Сверху по центру": "top",
}
ALIGN_REVERSE = {v: k for k, v in ALIGN_LABELS.items()}

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
    ) -> None:
        super().__init__(parent)
        self.title("Редактор субтитров")
        self.geometry("720x500")
        self.minsize(600, 420)
        self.transient(parent)
        self.grab_set()

        self._cues: list[SubtitleCue] = list(initial_cues)
        self._on_apply = on_apply

        self._build_ui()
        self._refresh_tree()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # Таблица реплик
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
        self.tree.column("text", width=400, anchor="w", stretch=True)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        # Поля ввода реплики
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

        # Нижняя панель
        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(4, 0))
        ttk.Button(bottom, text="Загрузить из .srt...", command=self._load_srt).pack(side="left")
        ttk.Button(bottom, text="Сохранить в .srt...", command=self._save_srt).pack(
            side="left", padx=(6, 0)
        )

        ttk.Button(bottom, text="Отмена", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="Использовать эти субтитры", command=self._apply).pack(
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
        # Предлагаем следующий интервал для удобства
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
    """Главное окно приложения для добавления субтитров на видео."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Добавление субтитров на видео")
        self.root.geometry("820x720")
        self.root.minsize(740, 650)

        self._style_obj = ttk.Style()
        for theme in ("vista", "clam", "default"):
            if theme in self._style_obj.theme_names():
                self._style_obj.theme_use(theme)
                break

        self.video_path_var = tk.StringVar()
        self.video_info_var = tk.StringVar(value="Файл не выбран")
        self.sub_path_var = tk.StringVar()
        self.out_path_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="hard")

        # Встроенные реплики из редактора (если пользователь создал их вручную)
        self._editor_cues: Optional[list[SubtitleCue]] = None

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

        self.status_var = tk.StringVar(value="Выберите видео и субтитры для начала работы.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self._is_running = False
        self._last_output: Optional[Path] = None

        self._system_fonts = self._collect_fonts(default_font)
        self._build_ui()
        self._bind_preview_updates()
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

        # --- 1. Файлы ---
        files_frame = ttk.LabelFrame(container, text="1. Файлы", padding=10)
        files_frame.pack(fill="x")
        files_frame.columnconfigure(1, weight=1)

        ttk.Label(files_frame, text="Видеофайл:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(files_frame, textvariable=self.video_path_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=4
        )
        ttk.Button(files_frame, text="Выбрать видео...", command=self._browse_video).grid(
            row=0, column=2, sticky="ew", pady=4
        )

        ttk.Label(
            files_frame, textvariable=self.video_info_var, foreground="#555555"
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=8)

        ttk.Label(files_frame, text="Субтитры:").grid(row=2, column=0, sticky="w", pady=4)
        self.sub_entry = ttk.Entry(files_frame, textvariable=self.sub_path_var)
        self.sub_entry.grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        sub_btns = ttk.Frame(files_frame)
        sub_btns.grid(row=2, column=2, sticky="ew", pady=4)
        ttk.Button(sub_btns, text="Выбрать файл...", command=self._browse_subtitles).pack(
            side="left"
        )
        ttk.Button(sub_btns, text="Редактор...", command=self._open_editor).pack(
            side="left", padx=(6, 0)
        )

        ttk.Label(files_frame, text="Сохранить как:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(files_frame, textvariable=self.out_path_var).grid(
            row=3, column=1, sticky="ew", padx=8, pady=4
        )
        ttk.Button(files_frame, text="Обзор...", command=self._browse_output).grid(
            row=3, column=2, sticky="ew", pady=4
        )

        # --- 2. Режим ---
        mode_frame = ttk.LabelFrame(container, text="2. Режим добавления", padding=10)
        mode_frame.pack(fill="x", pady=(10, 0))

        ttk.Radiobutton(
            mode_frame,
            text="Вжечь в кадр (Hardsub) — видны в любом плеере, можно настроить стиль",
            variable=self.mode_var,
            value="hard",
            command=self._on_mode_change,
        ).pack(anchor="w")
        ttk.Radiobutton(
            mode_frame,
            text="Отдельная дорожка (Softsub) — можно включать/выключать, без перекодирования",
            variable=self.mode_var,
            value="soft",
            command=self._on_mode_change,
        ).pack(anchor="w", pady=(4, 0))

        # --- 3. Настройки ---
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True, pady=(10, 0))

        # Вкладка Hardsub
        self.hard_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.hard_tab, text="Оформление (Hardsub)")
        self._build_hard_tab(self.hard_tab)

        # Вкладка Softsub
        self.soft_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.soft_tab, text="Дорожка (Softsub)")
        self._build_soft_tab(self.soft_tab)

        # Вкладка Дополнительно
        self.adv_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.adv_tab, text="Тайминг и качество")
        self._build_advanced_tab(self.adv_tab)

        # --- 4. Запуск и прогресс ---
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
            status_row, text="Добавить субтитры", command=self._start_processing
        )
        self.run_btn.pack(side="right")

    def _build_hard_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)

        ttk.Label(parent, text="Шрифт:").grid(row=0, column=0, sticky="w", pady=4)
        font_combo = ttk.Combobox(
            parent, textvariable=self.font_var, values=self._system_fonts, state="normal"
        )
        font_combo.grid(row=0, column=1, sticky="ew", padx=(6, 16), pady=4)

        ttk.Label(parent, text="Размер:").grid(row=0, column=2, sticky="w", pady=4)
        ttk.Spinbox(parent, from_=10, to=96, textvariable=self.font_size_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(6, 0), pady=4
        )

        # Цвета
        ttk.Label(parent, text="Цвет текста:").grid(row=1, column=0, sticky="w", pady=4)
        c1_frame = ttk.Frame(parent)
        c1_frame.grid(row=1, column=1, sticky="w", padx=(6, 16), pady=4)
        self.primary_swatch = tk.Label(c1_frame, width=3, bg=self.primary_color_var.get(), relief="solid", bd=1)
        self.primary_swatch.pack(side="left")
        ttk.Entry(c1_frame, textvariable=self.primary_color_var, width=10).pack(side="left", padx=6)
        ttk.Button(
            c1_frame,
            text="Цвет...",
            command=lambda: self._pick_color(self.primary_color_var, self.primary_swatch),
        ).pack(side="left")

        ttk.Label(parent, text="Цвет обводки:").grid(row=1, column=2, sticky="w", pady=4)
        c2_frame = ttk.Frame(parent)
        c2_frame.grid(row=1, column=3, sticky="w", padx=(6, 0), pady=4)
        self.outline_swatch = tk.Label(c2_frame, width=3, bg=self.outline_color_var.get(), relief="solid", bd=1)
        self.outline_swatch.pack(side="left")
        ttk.Entry(c2_frame, textvariable=self.outline_color_var, width=10).pack(side="left", padx=6)
        ttk.Button(
            c2_frame,
            text="Цвет...",
            command=lambda: self._pick_color(self.outline_color_var, self.outline_swatch),
        ).pack(side="left")

        # Обводка, тень, положение, отступ
        ttk.Label(parent, text="Толщина обводки:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(
            parent, from_=0.0, to=8.0, increment=0.5, textvariable=self.outline_var, width=8
        ).grid(row=2, column=1, sticky="w", padx=(6, 16), pady=4)

        ttk.Label(parent, text="Положение:").grid(row=2, column=2, sticky="w", pady=4)
        ttk.Combobox(
            parent,
            textvariable=self.align_label_var,
            values=list(ALIGN_LABELS.keys()),
            state="readonly",
            width=20,
        ).grid(row=2, column=3, sticky="w", padx=(6, 0), pady=4)

        ttk.Label(parent, text="Отступ по вертикали:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Spinbox(parent, from_=0, to=200, textvariable=self.margin_v_var, width=8).grid(
            row=3, column=1, sticky="w", padx=(6, 16), pady=4
        )

        ttk.Label(parent, text="Тень:").grid(row=3, column=2, sticky="w", pady=4)
        ttk.Spinbox(
            parent, from_=0.0, to=8.0, increment=0.5, textvariable=self.shadow_var, width=8
        ).grid(row=3, column=3, sticky="w", padx=(6, 0), pady=4)

        # Флажки
        flags_frame = ttk.Frame(parent)
        flags_frame.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 6))
        ttk.Checkbutton(flags_frame, text="Жирный", variable=self.bold_var).pack(side="left")
        ttk.Checkbutton(flags_frame, text="Курсив", variable=self.italic_var).pack(
            side="left", padx=(14, 0)
        )
        ttk.Checkbutton(
            flags_frame, text="Полупрозрачная плашка под текстом", variable=self.box_var
        ).pack(side="left", padx=(14, 0))

        # Мини-превью
        preview_box = ttk.LabelFrame(parent, text="Предпросмотр стиля", padding=6)
        preview_box.grid(row=5, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        parent.rowconfigure(5, weight=1)

        self.preview_canvas = tk.Canvas(preview_box, height=110, bg="#1B263B", highlightthickness=0)
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
            text="Примечание: в режиме Softsub видео и аудио копируются без перекодирования,\n"
            "поэтому обработка занимает пару секунд.",
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

    def _on_mode_change(self) -> None:
        if self.mode_var.get() == "hard":
            self.notebook.select(self.hard_tab)
        else:
            self.notebook.select(self.soft_tab)

    def _pick_color(self, target_var: tk.StringVar, swatch: tk.Label) -> None:
        initial = target_var.get() if target_var.get().startswith("#") else "#FFFFFF"
        _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self.root, title="Выбор цвета")
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
        h = max(canvas.winfo_height(), 100)

        font_name = self.font_var.get().strip() or "Arial"
        try:
            raw_size = int(self.font_size_var.get())
        except Exception:
            raw_size = 24
        preview_size = max(10, min(32, int(raw_size * 0.72)))

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
            y = 24
        elif align == "center":
            y = h // 2
        else:
            y = h - 24
        x = w // 2
        sample_text = "Пример субтитров • Sample Subtitle"

        # Плашка под текстом
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

        # Имитация обводки
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
                        canvas.create_text(x + dx, y + dy, text=sample_text, font=font_spec, fill=outline)
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
        if not self.out_path_var.get().strip():
            self.out_path_var.set(str(src.with_name(f"{src.stem}_subtitled{src.suffix}")))

        try:
            info = probe_video(src)
            parts = []
            if info.width and info.height:
                parts.append(f"{info.width}×{info.height}")
            if info.duration is not None:
                parts.append(f"длительность {info.duration:.1f} сек")
            parts.append("со звуком" if info.has_audio else "без звука")
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
        if not path:
            return
        self._editor_cues = None
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
        initial: list[SubtitleCue] = []
        if self._editor_cues is not None:
            initial = list(self._editor_cues)
        else:
            cur_sub = self.sub_path_var.get().strip()
            if cur_sub and Path(cur_sub).is_file() and cur_sub.lower().endswith(".srt"):
                try:
                    initial = parse_srt(Path(cur_sub).read_text(encoding="utf-8-sig"))
                except Exception:
                    initial = []

        def on_apply(cues: list[SubtitleCue]) -> None:
            self._editor_cues = cues
            self.sub_path_var.set(f"[Из редактора: {len(cues)} реплик]")

        SubtitleEditorDialog(self.root, initial, on_apply)

    def _start_processing(self) -> None:
        if self._is_running:
            return

        video_str = self.video_path_var.get().strip()
        if not video_str or not Path(video_str).is_file():
            messagebox.showerror("Ошибка", "Выберите существующий видеофайл.", parent=self.root)
            return

        if self._editor_cues is not None:
            sub_source = list(self._editor_cues)
        else:
            sub_str = self.sub_path_var.get().strip()
            if not sub_str or not Path(sub_str).is_file():
                messagebox.showerror(
                    "Ошибка",
                    "Выберите файл субтитров или создайте их через кнопку «Редактор...».",
                    parent=self.root,
                )
                return
            sub_source = sub_str

        out_str = self.out_path_var.get().strip()
        out_path = Path(out_str) if out_str else None

        try:
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
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=self.root)
            return

        mode = self.mode_var.get()
        lang = self.lang_var.get().strip() or "rus"
        title = self.title_var.get().strip() or None
        default_track = bool(self.default_track_var.get())
        preset = self.preset_var.get()

        self._is_running = True
        self._last_output = None
        self.run_btn.configure(state="disabled")
        self.open_folder_btn.configure(state="disabled")
        self.open_video_btn.configure(state="disabled")
        self.progress_var.set(0.0)
        self.status_var.set("Обработка видео... Пожалуйста, подождите.")

        def on_progress(ratio: float) -> None:
            pct = max(0.0, min(100.0, ratio * 100.0))
            self.root.after(
                0,
                lambda: (
                    self.progress_var.set(pct),
                    self.status_var.set(f"Обработка видео: {pct:.0f}%"),
                ),
            )

        def worker() -> None:
            try:
                result = add_subtitles(
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
                    progress_callback=on_progress,
                )
                self.root.after(0, lambda: self._on_success(result))
            except Exception as exc:
                self.root.after(0, lambda err=exc: self._on_error(err))

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self, result: SubtitleResult) -> None:
        self._is_running = False
        self._last_output = result.output_path
        self.progress_var.set(100.0)
        self.run_btn.configure(state="normal")
        self.open_folder_btn.configure(state="normal")
        self.open_video_btn.configure(state="normal")
        self.status_var.set(f"Готово! Сохранено: {result.output_path.name}")
        messagebox.showinfo("Готово", f"Видео с субтитрами сохранено:\n{result.output_path}", parent=self.root)

    def _on_error(self, exc: Exception) -> None:
        self._is_running = False
        self.run_btn.configure(state="normal")
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
