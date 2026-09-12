from __future__ import annotations

import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from . import __version__
from .dialogue_preview import DialoguePreviewRenderer
from .engine import EditSession, HMDSStudioError, RomModel
from .entities import EntityIndex, EntityRef
from .i18n import LANG_NAMES, Translator
from .project import TranslationProject
from .settings import AppSettings
from .team import (
    DIALOGUE_EXTENSION, PACK_EXTENSION, SUPPORTED_IMPORT_EXTENSIONS,
    import_entries, make_entry, safe_make_entry, read_exchange_file, text_fingerprint,
    write_csv, write_dialogue_file, write_pack_file,
)
from .validation import editor_to_raw, ensure_terminal_control, raw_to_editor, validate_translation

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover
    Image = None
    ImageTk = None

APP_TITLE = f"HMDS Character Translation Studio {__version__} — Atm"
SUPPORTED_GAME_CODES = {"ABCP"}

STATUS_KEYS = ["untranslated", "translated", "review", "reviewed", "ignored"]


def _key(sid: int, idx: int) -> Tuple[int, int]:
    return int(sid), int(idx)


class PlaceholderEntry(ttk.Entry):
    def __init__(self, master, placeholder: str, on_change=None, **kwargs):
        self.var = tk.StringVar()
        super().__init__(master, textvariable=self.var, **kwargs)
        self.placeholder = placeholder
        self.on_change = on_change
        self._showing_placeholder = False
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<KeyRelease>", self._key_release)
        self._focus_out()

    def _focus_in(self, _evt=None):
        if self._showing_placeholder:
            self.delete(0, "end")
            self._showing_placeholder = False

    def _focus_out(self, _evt=None):
        if not self.get().strip():
            self.delete(0, "end")
            self.insert(0, self.placeholder)
            self._showing_placeholder = True

    def _key_release(self, _evt=None):
        if self.on_change:
            self.on_change()

    def query(self) -> str:
        return "" if self._showing_placeholder else self.get().strip()


class DialogueRichList(tk.Text):
    """Fast, read-only dialogue list with command-only coloring.

    Clicking a row loads it into the editor, but the list deliberately does not paint
    the whole row blue or allow drag-selection. This keeps the list visually stable
    while translating for long periods.
    """

    TOKEN_RE = re.compile(
        r"\{05\s+0C\}|\{(?:FF|81)\s+[0-9A-Fa-f]{2}\}|"
        r"\[(?:NOME|NAME|NOMBRE|FF 2A|ÍCONE 99|ICON 99|ICONO 99)\]|"
        r"♥|♪|…|⟦05\s+0C[^⟧]*⟧",
        re.I,
    )

    def __init__(self, master, on_select=None, **kwargs):
        super().__init__(master, wrap="none", cursor="arrow", font=("Consolas", 9),
                         padx=4, pady=2, undo=False, takefocus=False, exportselection=False, **kwargs)
        self.on_select = on_select
        self.line_to_iid: Dict[int, str] = {}
        self.iid_to_line: Dict[str, int] = {}
        self.selected_iid: Optional[str] = None
        self._batch = False
        self.configure(state="disabled")
        self.tag_configure("status", foreground="#4a5260")
        self.tag_configure("meta", foreground="#7d8490")
        # Zebra stripes keep long rows aligned visually. The active dialogue gets a
        # single blue row so the translator always knows which line is open below.
        self.tag_configure("zebra", background="#f3f5f8")
        self.tag_configure("current", background="#1479d4", foreground="#ffffff")
        self.bind("<Button-1>", self._click)
        self.bind("<B1-Motion>", self._block_drag)
        self.bind("<ButtonRelease-1>", self._block_drag)
        self.bind("<Control-a>", lambda e: "break")

    def set_command_colors(self, command_fg: str, command_bg: str,
                           page_fg: str, page_bg: str,
                           symbol_fg: str, symbol_bg: str) -> None:
        self.tag_configure("command", foreground=command_fg, background=command_bg)
        self.tag_configure("pagebreak", foreground=page_fg, background=page_bg, font=("Consolas", 9, "bold"))
        self.tag_configure("symbol", foreground=symbol_fg, background=symbol_bg)

    def begin_update(self) -> None:
        self._batch = True
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.line_to_iid.clear()
        self.iid_to_line.clear()
        # Keep the logical active row across search/refresh when possible.

    def end_update(self) -> None:
        try:
            self.tag_remove("sel", "1.0", "end")
            self.tag_raise("pagebreak")
            self.tag_raise("command")
            self.tag_raise("symbol")
            self.tag_raise("current")
        except Exception:
            pass
        self._paint_current_row()
        self.configure(state="disabled")
        self._batch = False

    def clear(self) -> None:
        self.begin_update()
        self.end_update()

    def _insert_fragmented(self, text: str) -> None:
        pos = 0
        for match in self.TOKEN_RE.finditer(text):
            if match.start() > pos:
                self.insert("end", text[pos:match.start()])
            token = match.group(0)
            if re.search(r"05\s+0C", token, re.I):
                tag = "pagebreak"
            elif token in {"♥", "♪", "…"}:
                tag = "symbol"
            else:
                tag = "command"
            self.insert("end", token, tag)
            pos = match.end()
        if pos < len(text):
            self.insert("end", text[pos:])

    def _render_row(self, status: str, friendly: str, *, meta: str = "", new_box_label: str = "NEW BOX") -> None:
        self.insert("end", f"{status:<15}", "status")
        if meta:
            self.insert("end", f" {meta:<28}", "meta")
        self.insert("end", "  ")
        single = re.sub(r"\{05\s+0C\}", f"⟦05 0C · {new_box_label}⟧", friendly, flags=re.I)
        single = single.replace("\n", " / ")
        if len(single) > 260:
            single = single[:257] + "..."
        self._insert_fragmented(single)

    def add_row(self, iid: str, status: str, friendly: str, *, meta: str = "", reviewed: bool = False,
                new_box_label: str = "NEW BOX") -> None:
        own_state = not self._batch
        if own_state:
            self.configure(state="normal")
        line = int(self.index("end-1c").split(".")[0])
        if self.get("1.0", "end-1c"):
            self.insert("end", "\n")
            line += 1
        self.line_to_iid[line] = iid
        self.iid_to_line[iid] = line
        self._render_row(status, friendly, meta=meta, new_box_label=new_box_label)
        if line % 2 == 0:
            self.tag_add("zebra", f"{line}.0", f"{line}.end")
        if own_state:
            self._paint_current_row()
            self.configure(state="disabled")

    def update_row(self, iid: str, status: str, friendly: str, *, meta: str = "", new_box_label: str = "NEW BOX") -> None:
        line = self.iid_to_line.get(iid)
        if not line:
            return
        self.configure(state="normal")
        self.delete(f"{line}.0", f"{line}.end")
        self.mark_set("insert", f"{line}.0")
        # Render into the insertion point by temporarily using a mark at end of line.
        # Text.insert('end', ...) would append to the widget, so create row fragments
        # directly at the known line.
        pos = f"{line}.0"
        self.insert(pos, f"{status:<15}", "status")
        pos = f"{line}.end"
        if meta:
            self.insert(pos, f" {meta:<28}", "meta")
        self.insert(f"{line}.end", "  ")
        single = re.sub(r"\{05\s+0C\}", f"⟦05 0C · {new_box_label}⟧", friendly, flags=re.I).replace("\n", " / ")
        if len(single) > 260:
            single = single[:257] + "..."
        cursor = 0
        for match in self.TOKEN_RE.finditer(single):
            if match.start() > cursor:
                self.insert(f"{line}.end", single[cursor:match.start()])
            token = match.group(0)
            tag = "pagebreak" if re.search(r"05\s+0C", token, re.I) else "symbol" if token in {"♥", "♪", "…"} else "command"
            self.insert(f"{line}.end", token, tag)
            cursor = match.end()
        if cursor < len(single):
            self.insert(f"{line}.end", single[cursor:])
        self.tag_remove("sel", "1.0", "end")
        if line % 2 == 0:
            self.tag_add("zebra", f"{line}.0", f"{line}.end")
        else:
            self.tag_remove("zebra", f"{line}.0", f"{line}.end")
        self._paint_current_row()
        self.configure(state="disabled")

    def _paint_current_row(self) -> None:
        try:
            self.tag_remove("current", "1.0", "end")
            if self.selected_iid and self.selected_iid in self.iid_to_line:
                line = self.iid_to_line[self.selected_iid]
                self.tag_add("current", f"{line}.0", f"{line}.end")
                self.tag_raise("current")
        except Exception:
            pass

    def _click(self, event):
        try:
            self.tag_remove("sel", "1.0", "end")
        except Exception:
            pass
        line = int(self.index(f"@{event.x},{event.y}").split(".")[0])
        iid = self.line_to_iid.get(line)
        if iid:
            self.selected_iid = iid
            self._paint_current_row()
            if self.on_select:
                self.on_select()
        return "break"

    def _block_drag(self, _event=None):
        try:
            self.tag_remove("sel", "1.0", "end")
        except Exception:
            pass
        return "break"

    def selection(self):
        return (self.selected_iid,) if self.selected_iid else ()

    def selection_set(self, iid: str) -> None:
        if iid not in self.iid_to_line:
            return
        self.selected_iid = iid
        self.see(f"{self.iid_to_line[iid]}.0")
        try:
            self.tag_remove("sel", "1.0", "end")
        except Exception:
            pass
        self._paint_current_row()

    def exists(self, iid: str) -> bool:
        return iid in self.iid_to_line


class ImportReviewDialog(tk.Toplevel):
    """Modal import comparator. Nothing is applied until rows are confirmed."""

    def __init__(self, master, tr: Translator, prepared_rows: List[dict], source_name: str):
        super().__init__(master)
        self.tr = tr
        self.prepared_rows = prepared_rows
        self.selected_entries: Optional[List[dict]] = None
        self.title(f"{tr('import.review_title')} — {source_name}")
        self.geometry("1280x760")
        self.minsize(980, 580)
        self.transient(master)
        self.grab_set()

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=tr("import.review_hint"), style="Muted.TLabel", wraplength=1220).pack(anchor="w", pady=(0, 8))

        pane = ttk.Panedwindow(outer, orient="vertical")
        pane.pack(fill="both", expand=True)

        table_frame = ttk.Frame(pane)
        pane.add(table_frame, weight=3)
        table_frame.rowconfigure(0, weight=1); table_frame.columnconfigure(0, weight=1)
        cols = ("use", "character", "id", "compare", "current", "incoming")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        widths = {"use":55, "character":135, "id":90, "compare":150, "current":330, "incoming":330}
        labels = {
            "use": tr("import.col_use"), "character": tr("import.col_character"), "id": tr("import.col_id"),
            "compare": tr("import.col_compare"), "current": tr("import.col_current"), "incoming": tr("import.col_incoming"),
        }
        for c in cols:
            self.tree.heading(c, text=labels[c])
            self.tree.column(c, width=widths[c], stretch=c in {"current", "incoming"}, anchor="w" if c != "use" else "center")
        vs = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); vs.grid(row=0, column=1, sticky="ns"); hs.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("same", foreground="#6b7280", background="#f5f6f8")
        self.tree.tag_configure("different", background="#fff3cd")
        self.tree.tag_configure("new", background="#e8f7ee")
        self.tree.tag_configure("bad", foreground="#9f1c1c", background="#fdebec")
        self.tree.bind("<Button-1>", self._toggle_clicked)
        self.tree.bind("<<TreeviewSelect>>", self._show_details)

        detail = ttk.Frame(pane, padding=(0, 8, 0, 0))
        pane.add(detail, weight=2)
        detail.columnconfigure(0, weight=1); detail.columnconfigure(1, weight=1); detail.rowconfigure(1, weight=1)
        ttk.Label(detail, text=tr("import.details_project"), style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(detail, text=tr("import.details_file"), style="Title.TLabel").grid(row=0, column=1, sticky="w", padx=(8,0))
        self.current_text = tk.Text(detail, wrap="word", height=8, font=("Consolas", 10), state="disabled", bg="#f7f8fa")
        self.incoming_text = tk.Text(detail, wrap="word", height=8, font=("Consolas", 10), state="disabled", bg="#fff8dc")
        self.current_text.grid(row=1, column=0, sticky="nsew")
        self.incoming_text.grid(row=1, column=1, sticky="nsew", padx=(8,0))
        self.detail_status = ttk.Label(detail, text="", style="Muted.TLabel")
        self.detail_status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(5,0))

        self._selected: Dict[int, bool] = {}
        for i, row in enumerate(prepared_rows):
            selectable = bool(row.get("selectable"))
            selected = bool(row.get("selected_default")) and selectable
            self._selected[i] = selected
            compare_key = row.get("compare_key", "same")
            compare_text = tr(f"import.{compare_key}")
            tag = "bad" if compare_key in {"incompatible", "protected"} else "different" if compare_key in {"different", "text_status", "status_only"} else "new" if compare_key == "new" else "same"
            self.tree.insert("", "end", iid=str(i), values=("☑" if selected else "☐" if selectable else "—",
                row.get("entity_name", ""), f"{row.get('script_id')} / {row.get('string_index')}", compare_text,
                row.get("current_display", ""), row.get("incoming_display", "")), tags=(tag,))

        if prepared_rows:
            self.tree.selection_set("0"); self.tree.focus("0"); self._show_details()

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Button(footer, text=tr("import.select_all"), command=self._select_all).pack(side="left")
        ttk.Button(footer, text=tr("import.select_none"), command=self._select_none).pack(side="left", padx=(6, 0))
        self.count_label = ttk.Label(footer, text="", style="Muted.TLabel")
        self.count_label.pack(side="left", padx=12)
        ttk.Button(footer, text=tr("import.cancel"), command=self._cancel).pack(side="right")
        ttk.Button(footer, text=tr("import.apply"), command=self._accept).pack(side="right", padx=(0, 6))
        self._update_count()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _show_details(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        i = int(sel[0])
        row = self.prepared_rows[i]
        self._set_text(self.current_text, row.get("current_full", ""))
        self._set_text(self.incoming_text, row.get("incoming_full", ""))
        current_status = self.tr(f"status.{row.get('current_status', 'untranslated')}")
        incoming_key = row.get("incoming_status", "")
        incoming_status = self.tr(f"status.{incoming_key}") if incoming_key else "—"
        status_line = f"{self.tr('import.status_current')}: {current_status}    •    {self.tr('import.status_incoming')}: {incoming_status}"
        reason = str(row.get("reason") or "").strip()
        if reason:
            status_line += f"\n{self.tr('import.reason')}: {reason}"
        self.detail_status.configure(text=status_line)

    def _row_selectable(self, i: int) -> bool:
        return 0 <= i < len(self.prepared_rows) and bool(self.prepared_rows[i].get("selectable"))

    def _toggle_clicked(self, event):
        try:
            iid = self.tree.identify_row(event.y)
            col = self.tree.identify_column(event.x)
            if not iid:
                return
            # First column is the checkbox. Clicking elsewhere only selects the row
            # so the translator can inspect both full texts without toggling it.
            if col != "#1":
                return
            i = int(iid)
            if not self._row_selectable(i):
                return "break"
            self._selected[i] = not self._selected.get(i, False)
            vals = list(self.tree.item(iid, "values")); vals[0] = "☑" if self._selected[i] else "☐"
            self.tree.item(iid, values=vals)
            self._update_count()
            return "break"
        except Exception:
            return None

    def _select_all(self):
        for i in range(len(self.prepared_rows)):
            if self._row_selectable(i):
                self._selected[i] = True
                vals = list(self.tree.item(str(i), "values")); vals[0] = "☑"; self.tree.item(str(i), values=vals)
        self._update_count()

    def _select_none(self):
        for i in range(len(self.prepared_rows)):
            if self._row_selectable(i):
                self._selected[i] = False
                vals = list(self.tree.item(str(i), "values")); vals[0] = "☐"; self.tree.item(str(i), values=vals)
        self._update_count()

    def _update_count(self):
        chosen = sum(1 for i, v in self._selected.items() if v and self._row_selectable(i))
        available = sum(1 for i in range(len(self.prepared_rows)) if self._row_selectable(i))
        self.count_label.configure(text=f"{chosen} / {available}")

    def _accept(self):
        self.selected_entries = [row["entry"] for i, row in enumerate(self.prepared_rows) if self._selected.get(i) and self._row_selectable(i)]
        self.destroy()

    def _cancel(self):
        self.selected_entries = None
        self.destroy()


class CTSApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = AppSettings.load()
        self.tr = Translator(self.settings.language)
        self.title(APP_TITLE)
        self.geometry("1520x930")
        self.minsize(1180, 740)

        self.model: Optional[RomModel] = None
        self.session = EditSession()
        self.project = TranslationProject()
        self.entities = EntityIndex()
        self.preview_renderer = DialoguePreviewRenderer(Path(__file__).with_name("data"))
        self.current_entity: Optional[EntityRef] = None
        self.current_dialogue: Optional[dict] = None
        self.current_dialogue_rows: List[dict] = []
        self.current_preview_images: List[object] = []
        self.dirty = False
        self._thumb_refs: Dict[str, object] = {}
        self._about_icon_ref = None
        self._tooltip = None
        self._tooltip_token = None
        self._help_image_refs: List[object] = []

        # UI/performance state. Expensive list/preview work is debounced so typing,
        # resizing panes and searching remain responsive on large characters.
        self._live_after_id = None
        self._preview_after_id = None
        self._dialogue_search_after_id = None
        self._character_search_after_id = None
        self._thumb_after_id = None
        self._thumb_generation = 0
        self._friendly_cache: Dict[Tuple[int, int, str], str] = {}
        self._preview_render_cache: Dict[tuple, object] = {}
        self._technical_visible = False

        self.tech_var = tk.BooleanVar(value=False)
        self.protect_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value=self._status_label("untranslated"))

        self._set_app_icon()
        self._build_style()
        self._build_menu()
        self._build_main()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self.reset_layout)

    # --------------------------------------------------------------- localization / settings

    def _status_labels(self) -> Dict[str, str]:
        return {k: self.tr(f"status.{k}") for k in STATUS_KEYS}

    def _status_label(self, key: str) -> str:
        return self._status_labels().get(key, key)

    def _status_key_from_label(self, label: str) -> str:
        rev = {v: k for k, v in self._status_labels().items()}
        return rev.get(label, "untranslated")

    def _set_app_icon(self) -> None:
        data = Path(__file__).with_name("data")
        ico = data / "app_icon.ico"
        png = data / "app_icon.png"
        try:
            if sys.platform.startswith("win") and ico.exists():
                self.iconbitmap(default=str(ico))
            elif png.exists():
                self._app_icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._app_icon_photo)
        except Exception:
            pass

    def _apply_setting_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("Character.Treeview", rowheight=max(22, int(self.settings.thumbnail_size + 6)))
        palette = self.settings.highlight_palette
        if palette == "blue":
            self.command_colors = ("#114c82", "#e4f1ff")
            self.page_colors = ("#0e5a78", "#d9f3ff")
            self.symbol_colors = ("#7b2b5b", "#fdebf5")
        elif palette == "warm":
            self.command_colors = ("#7b3e00", "#fff0d5")
            self.page_colors = ("#7d4f00", "#ffe7ba")
            self.symbol_colors = ("#8b244c", "#fde9ef")
        else:
            self.command_colors = ("#642b7e", "#f2e6f8")
            self.page_colors = ("#165d83", "#dcefff")
            self.symbol_colors = ("#96385b", "#fdebf2")
        for widget in getattr(self, "_tagged_text_widgets", []):
            self._configure_text_tags(widget)
            self._apply_text_highlights(widget)
        if hasattr(self, "dialogue_tree") and isinstance(self.dialogue_tree, DialogueRichList):
            self.dialogue_tree.set_command_colors(self.command_colors[0], self.command_colors[1], self.page_colors[0], self.page_colors[1], self.symbol_colors[0], self.symbol_colors[1])

    # --------------------------------------------------------------- UI construction

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Muted.TLabel", foreground="#616b7a")
        style.configure("Character.Treeview", rowheight=max(22, int(self.settings.thumbnail_size + 6)))
        style.configure("Dialogue.Treeview", rowheight=23)
        self._tagged_text_widgets: List[tk.Text] = []
        self._apply_setting_styles()

    def _build_menu(self) -> None:
        menu = tk.Menu(self)

        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label=self.tr("open_rom"), command=self.open_rom, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label=self.tr("build_rom"), command=self.build_rom, accelerator="Ctrl+G")
        file_menu.add_separator()
        file_menu.add_command(label=self.tr("exit"), command=self._on_close)
        menu.add_cascade(label=self.tr("menu.file"), menu=file_menu)

        project_menu = tk.Menu(menu, tearoff=0)
        project_menu.add_command(label=self.tr("open_project"), command=self.open_project)
        project_menu.add_command(label=self.tr("save_project"), command=self.save_project, accelerator="Ctrl+S")
        project_menu.add_command(label=self.tr("save_as"), command=lambda: self.save_project(save_as=True))
        project_menu.add_separator()
        project_menu.add_command(label=self.tr("validate_project"), command=self.validate_project)
        menu.add_cascade(label=self.tr("menu.project"), menu=project_menu)

        export_menu = tk.Menu(menu, tearoff=0)
        export_menu.add_command(label=self.tr("export_dialogue_package"), command=self.export_selected_dialogue_package)
        export_menu.add_command(label=self.tr("export_character_package"), command=self.export_character_package)
        export_menu.add_command(label=self.tr("export_character_csv"), command=self.export_character_csv)
        export_menu.add_separator()
        export_menu.add_command(label=self.tr("import_package"), command=self.import_package)
        menu.add_cascade(label=self.tr("menu.export"), menu=export_menu)

        view_menu = tk.Menu(menu, tearoff=0)
        view_menu.add_checkbutton(label=self.tr("technical_mode"), variable=self.tech_var, command=self._toggle_technical_mode)
        view_menu.add_command(label=self.tr("reset_layout"), command=self.reset_layout)
        view_menu.add_separator()
        view_menu.add_command(label=self.tr("options"), command=self.show_options)
        menu.add_cascade(label=self.tr("menu.view"), menu=view_menu)

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label=self.tr("help_contents"), command=self.show_help_contents, accelerator="F1")
        help_menu.add_separator()
        help_menu.add_command(label=self.tr("about"), command=self.show_about)
        menu.add_cascade(label=self.tr("menu.help"), menu=help_menu)

        self.config(menu=menu)
        self.bind_all("<Control-o>", lambda e: self.open_rom())
        self.bind_all("<Control-s>", lambda e: self.save_project())
        self.bind_all("<Control-g>", lambda e: self.build_rom())
        self.bind_all("<F1>", lambda e: self.show_help_contents())

    def _build_main(self) -> None:
        info = ttk.Frame(self, padding=(9, 5))
        info.pack(fill="x")
        ttk.Label(info, text=self.tr("app_name"), style="Title.TLabel").pack(side="left")
        self.rom_info = ttk.Label(info, text=self.tr("open_normal_rom"), style="Muted.TLabel")
        self.rom_info.pack(side="right")

        self.top = ttk.Panedwindow(self, orient="horizontal")
        self.top.pack(fill="both", expand=True, padx=8, pady=(0, 7))

        # ---------------- Characters: vertical split so its height can be adjusted independently.
        left = ttk.Frame(self.top, padding=5)
        self.top.add(left, weight=1)
        self.character_split = ttk.Panedwindow(left, orient="vertical")
        self.character_split.pack(fill="both", expand=True)

        char_panel = ttk.Frame(self.character_split)
        self.character_split.add(char_panel, weight=5)
        ttk.Label(char_panel, text=self.tr("characters"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(char_panel, text=self.tr("characters_desc"), style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        self.char_search = PlaceholderEntry(char_panel, self.tr("search_placeholder"), on_change=self._schedule_character_refresh)
        self.char_search.pack(fill="x", pady=(0, 5))
        char_box = ttk.Frame(char_panel)
        char_box.pack(fill="both", expand=True)
        char_box.rowconfigure(0, weight=1); char_box.columnconfigure(0, weight=1)
        self.char_tree = ttk.Treeview(char_box, columns=("count", "progress"), show="tree headings", selectmode="browse", style="Character.Treeview")
        self.char_tree.heading("#0", text=self.tr("characters"))
        self.char_tree.heading("count", text=self.tr("dialogues"))
        self.char_tree.heading("progress", text="%")
        self.char_tree.column("#0", width=166, stretch=True)
        self.char_tree.column("count", width=48, anchor="center", stretch=False)
        self.char_tree.column("progress", width=48, anchor="center", stretch=False)
        self.char_tree.tag_configure("unmapped", foreground="#8b929d")
        cv = ttk.Scrollbar(char_box, orient="vertical", command=self.char_tree.yview)
        ch = ttk.Scrollbar(char_box, orient="horizontal", command=self.char_tree.xview)
        self.char_tree.configure(yscrollcommand=cv.set, xscrollcommand=ch.set)
        self.char_tree.grid(row=0, column=0, sticky="nsew"); cv.grid(row=0, column=1, sticky="ns"); ch.grid(row=1, column=0, sticky="ew")
        self.char_tree.bind("<<TreeviewSelect>>", self._on_character_selected)

        char_summary = ttk.Frame(self.character_split, padding=5)
        self.character_split.add(char_summary, weight=1)
        self.character_summary = ttk.Label(char_summary, text=self.tr("select_dialogue"), justify="left", style="Muted.TLabel", wraplength=230)
        self.character_summary.pack(anchor="nw", fill="both", expand=True)

        # ---------------- Middle: dialogue list over compact editor, adjustable with sash.
        middle = ttk.Frame(self.top, padding=5)
        self.top.add(middle, weight=3)
        self.middle_split = ttk.Panedwindow(middle, orient="vertical")
        self.middle_split.pack(fill="both", expand=True)

        dialogue_panel = ttk.Frame(self.middle_split)
        self.middle_split.add(dialogue_panel, weight=5)
        mh = ttk.Frame(dialogue_panel); mh.pack(fill="x")
        self.dialogue_title = ttk.Label(mh, text=self.tr("dialogues"), style="Title.TLabel"); self.dialogue_title.pack(side="left")
        self.dialogue_progress = ttk.Label(mh, text="", style="Muted.TLabel"); self.dialogue_progress.pack(side="right")
        self.dialogue_search = PlaceholderEntry(dialogue_panel, self.tr("search_placeholder"), on_change=self._schedule_dialogue_refresh)
        self.dialogue_search.pack(fill="x", pady=(4, 4))

        self.dialogue_header = ttk.Frame(dialogue_panel)
        self.dialogue_header.pack(fill="x", padx=(4, 18), pady=(0, 2))
        self.dialogue_header_status = ttk.Label(self.dialogue_header, text=self.tr("column.status"), width=15, style="Muted.TLabel")
        self.dialogue_header_status.grid(row=0, column=0, sticky="w")
        self.dialogue_header_meta = ttk.Label(self.dialogue_header, text=self.tr("column.structure"), width=28, style="Muted.TLabel")
        self.dialogue_header_text = ttk.Label(self.dialogue_header, text=self.tr("column.dialogue"), style="Muted.TLabel")
        self.dialogue_header_text.grid(row=0, column=2, sticky="w")
        self.dialogue_header.columnconfigure(2, weight=1)

        db = ttk.Frame(dialogue_panel); db.pack(fill="both", expand=True)
        db.rowconfigure(0, weight=1); db.columnconfigure(0, weight=1)
        self.dialogue_tree = DialogueRichList(db, on_select=self._on_dialogue_selected)
        dv = ttk.Scrollbar(db, orient="vertical", command=self.dialogue_tree.yview)
        dh = ttk.Scrollbar(db, orient="horizontal", command=self.dialogue_tree.xview)
        self.dialogue_tree.configure(yscrollcommand=dv.set, xscrollcommand=dh.set)
        self.dialogue_tree.grid(row=0, column=0, sticky="nsew"); dv.grid(row=0, column=1, sticky="ns"); dh.grid(row=1, column=0, sticky="ew")
        self._bind_command_help(self.dialogue_tree)


        editor = ttk.Frame(self.middle_split, padding=(0, 5, 0, 0))
        self.middle_split.add(editor, weight=2)
        eh = ttk.Frame(editor); eh.pack(fill="x")
        ttk.Label(eh, text=self.tr("editor"), style="Title.TLabel").pack(side="left")
        ttk.Label(eh, text=self.tr("editor_hint"), style="Muted.TLabel").pack(side="left", padx=(8, 0))
        ttk.Checkbutton(eh, text=self.tr("protect_codes"), variable=self.protect_var, command=self._validate_live).pack(side="right")
        ttk.Label(editor, text=self.tr("command_hint"), style="Muted.TLabel").pack(anchor="w", pady=(2,0))

        commands = ttk.Frame(editor); commands.pack(fill="x", pady=(4, 4))
        buttons = [
            (self.tr("button_name"), "[NOME]"),
            (self.tr("button_ff2a"), "[FF 2A]"),
            (self.tr("button_heart"), "♥"),
            (self.tr("button_music"), "♪"),
            (self.tr("button_ellipsis"), "…"),
            (self.tr("button_icon99"), "[ÍCONE 99]"),
            (self.tr("button_newbox"), "\n{05 0C}\n"),
        ]
        for label, token in buttons:
            ttk.Button(commands, text=label, command=lambda t=token: self._insert_token(t)).pack(side="left", padx=(0, 4))

        edit_grid = ttk.Frame(editor); edit_grid.pack(fill="both", expand=True)
        edit_grid.columnconfigure(0, weight=1); edit_grid.columnconfigure(1, weight=1); edit_grid.rowconfigure(0, weight=1)
        orig = ttk.Labelframe(edit_grid, text=self.tr("original_rom"), padding=4); orig.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        trans = ttk.Labelframe(edit_grid, text=self.tr("translation"), padding=4); trans.grid(row=0, column=1, sticky="nsew")
        self.original_text = tk.Text(orig, wrap="none", width=34, height=5, font=("Consolas", 10), state="disabled", bg="#f4f5f7")
        self.original_text.pack(fill="both", expand=True)
        self.translation_text = tk.Text(trans, wrap="none", width=34, height=5, font=("Consolas", 10), undo=True, bg="#fff8dc")
        self.translation_text.pack(fill="both", expand=True)
        self.translation_text.bind("<Return>", self._translation_return)
        self.translation_text.bind("<KeyRelease>", self._translation_key_release)
        self._tagged_text_widgets.extend([self.original_text, self.translation_text])
        self._configure_text_tags(self.original_text); self._configure_text_tags(self.translation_text)
        self._bind_command_help(self.original_text); self._bind_command_help(self.translation_text)

        edit_footer = ttk.Frame(editor); edit_footer.pack(fill="x", pady=(4, 0))
        ttk.Label(edit_footer, text=self.tr("state") + ":").pack(side="left")
        self.status_combo = ttk.Combobox(edit_footer, textvariable=self.status_var, state="readonly", width=15, values=list(self._status_labels().values()))
        self.status_combo.pack(side="left", padx=(4, 8))
        self.btn_revert = ttk.Button(edit_footer, text=self.tr("revert"), command=self.revert_current); self.btn_revert.pack(side="right")
        self.btn_apply = ttk.Button(edit_footer, text=self.tr("save_dialogue"), command=self.save_current_dialogue); self.btn_apply.pack(side="right", padx=(0, 5))

        # ---------------- Right side: preview stays clean; technical mode gets a full tab.
        right = ttk.Frame(self.top, padding=5)
        self.top.add(right, weight=2)
        self.right_notebook = ttk.Notebook(right)
        self.right_notebook.pack(fill="both", expand=True)

        self.preview_tab = ttk.Frame(self.right_notebook)
        self.right_notebook.add(self.preview_tab, text=self.tr("preview"))
        self.preview_split = ttk.Panedwindow(self.preview_tab, orient="vertical")
        self.preview_split.pack(fill="both", expand=True, padx=2, pady=2)

        preview_panel = ttk.Frame(self.preview_split); self.preview_split.add(preview_panel, weight=6)
        self.context_label = ttk.Label(preview_panel, text=self.tr("select_dialogue"), justify="left", style="Muted.TLabel", wraplength=470)
        self.context_label.pack(anchor="w", fill="x", pady=(2, 4))
        pb = ttk.Frame(preview_panel); pb.pack(fill="both", expand=True)
        pb.rowconfigure(0, weight=1); pb.columnconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(pb, bg="#252a31", highlightthickness=0)
        pv = ttk.Scrollbar(pb, orient="vertical", command=self.preview_canvas.yview); ph = ttk.Scrollbar(pb, orient="horizontal", command=self.preview_canvas.xview)
        self.preview_canvas.configure(yscrollcommand=pv.set, xscrollcommand=ph.set)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew"); pv.grid(row=0, column=1, sticky="ns"); ph.grid(row=1, column=0, sticky="ew")
        self.preview_canvas.bind("<Configure>", lambda e: self._schedule_preview_redraw())
        self.preview_canvas.bind("<MouseWheel>", self._preview_mousewheel)
        self.preview_canvas.bind("<Shift-MouseWheel>", self._preview_shift_mousewheel)
        self.preview_canvas.bind("<Button-4>", self._preview_linux_up)
        self.preview_canvas.bind("<Button-5>", self._preview_linux_down)

        validation_panel = ttk.Frame(self.preview_split); self.preview_split.add(validation_panel, weight=1)
        ttk.Label(validation_panel, text=self.tr("validation"), style="Title.TLabel").pack(anchor="w")
        self.metrics_text = tk.Text(validation_panel, height=6, wrap="word", state="disabled", font=("Consolas", 9))
        self.metrics_text.pack(fill="both", expand=True, pady=(3, 0))

        # Technical inspector is a dedicated full-size tab. It is created once and
        # only exposed when Technical mode is enabled, so normal translators do not
        # lose preview space while advanced users get a practical inspection surface.
        self.technical_panel = ttk.Frame(self.right_notebook, padding=5)
        tih = ttk.Frame(self.technical_panel); tih.pack(fill="x", pady=(0, 4))
        ttk.Label(tih, text=self.tr("technical.inspector"), style="Title.TLabel").pack(side="left")
        ttk.Label(tih, text=self.tr("technical.tab_hint"), style="Muted.TLabel").pack(side="left", padx=(10, 0))
        ttk.Button(tih, text=self.tr("technical.copy"), command=self._copy_technical_report).pack(side="right")
        tib = ttk.Frame(self.technical_panel); tib.pack(fill="both", expand=True)
        tib.rowconfigure(0, weight=1); tib.columnconfigure(0, weight=1)
        self.technical_text = tk.Text(tib, wrap="none", state="disabled", font=("Consolas", 10), bg="#f7f8fa", padx=8, pady=8)
        tsv = ttk.Scrollbar(tib, orient="vertical", command=self.technical_text.yview)
        tsh = ttk.Scrollbar(tib, orient="horizontal", command=self.technical_text.xview)
        self.technical_text.configure(yscrollcommand=tsv.set, xscrollcommand=tsh.set)
        self.technical_text.grid(row=0, column=0, sticky="nsew")
        tsv.grid(row=0, column=1, sticky="ns")
        tsh.grid(row=1, column=0, sticky="ew")

        self._update_dialogue_headers()

        self.statusbar = ttk.Label(self, text=self.tr("open_normal_rom"), anchor="w", padding=(8, 3), relief="sunken")
        self.statusbar.pack(fill="x", side="bottom")

    # --------------------------------------------------------------- scheduling / technical mode

    def _schedule_character_refresh(self) -> None:
        if self._character_search_after_id is not None:
            try: self.after_cancel(self._character_search_after_id)
            except Exception: pass
        self._character_search_after_id = self.after(120, self._run_character_refresh)

    def _run_character_refresh(self) -> None:
        self._character_search_after_id = None
        self.refresh_characters()

    def _schedule_dialogue_refresh(self) -> None:
        if self._dialogue_search_after_id is not None:
            try: self.after_cancel(self._dialogue_search_after_id)
            except Exception: pass
        self._dialogue_search_after_id = self.after(120, self._run_dialogue_refresh)

    def _run_dialogue_refresh(self) -> None:
        self._dialogue_search_after_id = None
        self.refresh_dialogues()

    def _schedule_live_validation(self, delay: int = 150) -> None:
        if self._live_after_id is not None:
            try: self.after_cancel(self._live_after_id)
            except Exception: pass
        self._live_after_id = self.after(delay, self._run_live_validation)

    def _run_live_validation(self) -> None:
        self._live_after_id = None
        self._validate_live()

    def _schedule_preview_redraw(self, delay: int = 100) -> None:
        if self._preview_after_id is not None:
            try: self.after_cancel(self._preview_after_id)
            except Exception: pass
        self._preview_after_id = self.after(delay, self._run_preview_redraw)

    def _run_preview_redraw(self) -> None:
        self._preview_after_id = None
        self._redraw_preview()

    def _update_dialogue_headers(self) -> None:
        if not hasattr(self, "dialogue_header_meta"):
            return
        try:
            self.dialogue_header_meta.grid_forget()
            self.dialogue_header_text.grid_forget()
            if self.tech_var.get():
                self.dialogue_header_meta.grid(row=0, column=1, sticky="w")
                self.dialogue_header_text.grid(row=0, column=2, sticky="w")
            else:
                self.dialogue_header_text.grid(row=0, column=1, columnspan=2, sticky="w")
        except Exception:
            pass

    def _toggle_technical_mode(self) -> None:
        self._update_dialogue_headers()
        if hasattr(self, "technical_panel") and hasattr(self, "right_notebook"):
            tabs = {str(x) for x in self.right_notebook.tabs()}
            name = str(self.technical_panel)
            if self.tech_var.get() and name not in tabs:
                self.right_notebook.add(self.technical_panel, text=self.tr("technical.inspector"))
                self._technical_visible = True
                # Technical mode is intentionally obvious: open the full-size inspector
                # immediately. The preview remains one click away in the first tab.
                self.after_idle(self._expand_technical_inspector)
            elif not self.tech_var.get() and name in tabs:
                try:
                    self.right_notebook.select(self.preview_tab)
                except Exception:
                    pass
                self.right_notebook.forget(self.technical_panel)
                self._technical_visible = False
        self.refresh_dialogues()
        self._update_technical_inspector()

    def _expand_technical_inspector(self) -> None:
        try:
            self.right_notebook.select(self.technical_panel)
            self.technical_text.focus_set()
        except Exception:
            pass

    def _copy_technical_report(self) -> None:
        report = self._technical_report()
        if not report:
            return
        try:
            self.clipboard_clear(); self.clipboard_append(report); self.update_idletasks()
            self.set_status(self.tr("technical.copied"))
        except Exception:
            pass

    def _technical_report(self) -> str:
        ids = self._selected_ids()
        if not ids or not self.model:
            return self.tr("technical.select")
        sid, idx = ids
        sc = self.model.parse_script(sid)
        current = self.model.current_text(self.session, sid, idx)
        original = sc.strings[idx] if 0 <= idx < len(sc.strings) else ""
        ptr_entry_rel = 4 + sid * 4
        ptr_entry_rom = self.model.script_start + ptr_entry_rel
        rec_rom_start = self.model.script_start + sc.st
        rec_rom_end = self.model.script_start + sc.en
        if self.settings.language == "en":
            legend = [
                "Structure legend:",
                "  S / Script ID : physical script record containing the dialogue",
                "  STR index     : string slot inside that script",
                "  F / Face ID   : portrait graphics package used in this context",
                "  E / Expr      : portrait expression/variant state",
                "  left / right  : side of the screen where the portrait is drawn",
                "  Pointer entry : pointer-table slot that locates the script record",
                "  Record        : physical RIFF script range inside ScriptS",
                "",
            ]
        elif self.settings.language == "es":
            legend = [
                "Leyenda de estructura:",
                "  S / Script ID : registro físico del script que contiene el diálogo",
                "  STR index     : posición de la cadena dentro de ese script",
                "  F / Face ID   : paquete gráfico del retrato usado en este contexto",
                "  E / Expr      : estado/variante de expresión del retrato",
                "  left / right  : lado de la pantalla donde se dibuja el retrato",
                "  Pointer entry : entrada de la tabla de punteros que localiza el script",
                "  Record        : rango físico RIFF del script dentro de ScriptS",
                "",
            ]
        else:
            legend = [
                "Legenda da estrutura:",
                "  S / Script ID : registro físico de script que contém a fala",
                "  STR index     : posição da string dentro desse script",
                "  F / Face ID   : pacote gráfico do retrato usado nesse contexto",
                "  E / Expr      : estado/variante de expressão do retrato",
                "  left / right  : lado da tela onde o retrato é desenhado",
                "  Pointer entry : entrada da tabela de ponteiros que localiza o script",
                "  Record        : intervalo físico RIFF do script dentro do ScriptS",
                "",
            ]
        lines = legend + [
            f"Game Code       : {self.model.game_code}",
            f"ROM SHA-256     : {self.model.sha256}",
            f"ScriptS FAT ID  : 66",
            f"ScriptS ROM     : 0x{self.model.script_start:08X} .. 0x{self.model.script_end:08X}",
            f"Pointer count   : {self.model.pointer_count}",
            "",
            f"Script ID       : {sid}",
            f"STR index       : {idx}",
            f"Pointer entry   : ScriptS+0x{ptr_entry_rel:08X} | ROM 0x{ptr_entry_rom:08X}",
            f"Pointer value   : 0x{self.model.ptr[sid]:08X}",
            f"Record          : ScriptS+0x{sc.st:08X} .. 0x{sc.en:08X}",
            f"Record ROM      : 0x{rec_rom_start:08X} .. 0x{rec_rom_end:08X}",
            f"Record size     : {len(sc.rec)} bytes",
            f"RIFF declared   : {sc.riff_length} bytes",
            f"RIFF effective  : {getattr(sc, 'effective_riff_length', sc.riff_length)} bytes",
            f"RIFF tolerated  : {'yes' if getattr(sc, 'tolerated_riff_overflow', False) else 'no'}",
            "",
            f"CODE bytes      : {len(sc.code or b'')}",
            f"JUMP tables     : {sc.jump_count}",
            f"STR count       : {len(sc.strings)}",
        ]
        for tag in ("CODE", "JUMP", "STR "):
            if tag in sc.chunk_offsets:
                off = sc.chunk_offsets[tag]
                chunk = next((c for c in sc.chunks if c.tag == tag), None)
                paylen = len(chunk.payload) if chunk else 0
                lines.append(f"{tag.strip():<16}: record+0x{off:06X} | payload {paylen} bytes")
        if "STR " in sc.chunk_offsets and 0 <= idx < len(sc.raw_slots):
            chunk = next((c for c in sc.chunks if c.tag == "STR "), None)
            if chunk and len(chunk.payload) >= 4:
                count = int.from_bytes(chunk.payload[0:4], "little")
                if idx < count and 4 + count * 4 <= len(chunk.payload):
                    slot_off = int.from_bytes(chunk.payload[4 + idx*4:8 + idx*4], "little")
                    blob_base = 4 + count * 4
                    rel = sc.chunk_offsets["STR "] + 8 + blob_base + slot_off
                    lines.append(f"STR slot offset : record+0x{rel:06X} | ROM 0x{rec_rom_start + rel:08X}")
        lines.extend([
            "",
            f"Original chars  : {len(original)}",
            f"Current chars   : {len(current)}",
            f"Original bytes  : {len(sc.raw_slots[idx]) if 0 <= idx < len(sc.raw_slots) else 0}",
        ])
        ctx = self.entities.dialogue_context_for_block(sid, idx)
        if ctx.get("rows"):
            lines.append("")
            lines.append("Visual contexts:")
            for row in ctx["rows"]:
                lines.append(
                    f"  {row.get('display_name','?')} | CID {row.get('character_id')} | "
                    f"Face {','.join(map(str,row.get('face_ids') or [])) or '-'} | "
                    f"Expr {','.join(map(str,row.get('expressions') or [])) or '-'} | "
                    f"Side {','.join(row.get('sides') or []) or '-'}"
                )
        return "\n".join(lines)

    def _update_technical_inspector(self) -> None:
        if not hasattr(self, "technical_text") or not self.tech_var.get():
            return
        report = self._technical_report()
        self.technical_text.configure(state="normal")
        self.technical_text.delete("1.0", "end")
        self.technical_text.insert("1.0", report)
        self.technical_text.configure(state="disabled")

    # --------------------------------------------------------------- layout / wheel / highlighting

    def reset_layout(self) -> None:
        try:
            w = max(self.top.winfo_width(), 1100)
            preset = self.settings.layout_preset
            if preset == "compact":
                left, right = 225, 400
            elif preset == "preview":
                left, right = 230, 600
            else:
                left, right = 250, 500
            self.top.sashpos(0, left)
            self.top.sashpos(1, max(left + 500, w - right))
        except Exception:
            pass
        for pane, ratio in ((getattr(self, "character_split", None), 0.84), (getattr(self, "middle_split", None), 0.69), (getattr(self, "preview_split", None), 0.82)):
            try:
                if pane:
                    pane.sashpos(0, int(max(300, pane.winfo_height()) * ratio))
            except Exception:
                pass

    def _preview_mousewheel(self, event):
        delta = -1 if event.delta > 0 else 1
        self.preview_canvas.yview_scroll(delta * int(self.settings.scroll_speed), "units")
        return "break"

    def _preview_shift_mousewheel(self, event):
        delta = -1 if event.delta > 0 else 1
        self.preview_canvas.xview_scroll(delta * int(self.settings.scroll_speed), "units")
        return "break"

    def _preview_linux_up(self, event=None):
        self.preview_canvas.yview_scroll(-int(self.settings.scroll_speed), "units"); return "break"

    def _preview_linux_down(self, event=None):
        self.preview_canvas.yview_scroll(int(self.settings.scroll_speed), "units"); return "break"

    def _configure_text_tags(self, widget: tk.Text) -> None:
        widget.tag_configure("command", foreground=self.command_colors[0], background=self.command_colors[1])
        widget.tag_configure("pagebreak", foreground=self.page_colors[0], background=self.page_colors[1], font=("Consolas", 10, "bold"))
        widget.tag_configure("symbol", foreground=self.symbol_colors[0], background=self.symbol_colors[1])

    def _apply_text_highlights(self, widget: tk.Text) -> None:
        try:
            disabled = str(widget.cget("state")) == "disabled"
            if disabled: widget.configure(state="normal")
            for tag in ("command", "pagebreak", "symbol"): widget.tag_remove(tag, "1.0", "end")
            text = widget.get("1.0", "end-1c")
            for m in re.finditer(r"\{05\s+0C\}", text, re.I): widget.tag_add("pagebreak", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
            for m in re.finditer(r"\[(?:NOME|FF 2A|ÍCONE 99)\]|\{(?!05\s+0C)[0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})?\}", text, re.I):
                widget.tag_add("command", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
            for m in re.finditer(r"[♥♪…☆]", text): widget.tag_add("symbol", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
            if disabled: widget.configure(state="disabled")
        except Exception:
            pass

    # --------------------------------------------------------------- command help / tooltips

    def _bind_command_help(self, widget: tk.Text) -> None:
        widget.bind("<Motion>", lambda e, w=widget: self._command_motion(w, e), add="+")
        widget.bind("<Leave>", lambda e: self._hide_tooltip(), add="+")

    def _display_token_at(self, widget: tk.Text, event) -> Optional[str]:
        try:
            idx = widget.index(f"@{event.x},{event.y}")
            line_s, col_s = idx.split(".")
            line = int(line_s); col = int(col_s)
            text = widget.get(f"{line}.0", f"{line}.end")
            for m in DialogueRichList.TOKEN_RE.finditer(text):
                if m.start() <= col <= m.end():
                    return m.group(0)
        except Exception:
            return None
        return None

    def _normalize_command_token(self, token: str) -> str:
        t = (token or "").strip()
        u = t.upper()
        if "05 0C" in u:
            return "{05 0C}"
        if u in {"[NOME]", "[NAME]", "[NOMBRE]"}:
            return "{FF 24}"
        if u == "[FF 2A]":
            return "{FF 2A}"
        if u in {"[ÍCONE 99]", "[ICON 99]", "[ICONO 99]"}:
            return "{81 99}"
        if t == "♥":
            return "{81 CD}"
        if t == "♪":
            return "{81 F4}"
        if t == "…":
            return "{81 63}"
        m = re.fullmatch(r"\{((?:FF|81)\s+[0-9A-Fa-f]{2})\}", t, re.I)
        return "{" + m.group(1).upper() + "}" if m else t

    def _command_info(self, token: str) -> Tuple[str, str, str]:
        raw = self._normalize_command_token(token)
        lang = self.settings.language
        data = {
            "pt-BR": {
                "{05 0C}": ("Nova caixa de diálogo", "Confirmado", "Controle estrutural 05 0C. Encerra a caixa atual e continua a mesma fala em uma nova caixa. O tradutor pode movê-lo, mas isso muda onde o jogador precisa avançar o texto."),
                "{FF 24}": ("Nome do jogador", "Confirmado", "Placeholder dinâmico. O jogo substitui este controle pelo nome do jogador em tempo de execução. Na edição ele aparece como [NOME]."),
                "{FF 2A}": ("Controle FF 2A", "Significado não confirmado", "O efeito exato ainda não foi confirmado. A ferramenta mostra [FF 2A] apenas para o controle não ficar invisível. Os bytes FF 2A são preservados sem interpretação."),
                "{81 99}": ("Ícone 81 99", "Mapeamento visual", "Controle gráfico identificado como 81 99. A ferramenta o mostra como [ÍCONE 99]. O significado visual exato ainda deve ser tratado com cautela; o código original é preservado."),
                "{81 F4}": ("Nota musical", "Mapeamento visual", "Controle gráfico 81 F4, representado pela ferramenta como ♪. Ele não conta como texto comum e deve ser preservado quando fizer parte da fala original."),
                "{81 CD}": ("Coração", "Mapeamento visual", "Controle gráfico 81 CD, representado pela ferramenta como ♥. O byte original é preservado no arquivo do jogo."),
                "{81 63}": ("Glifo especial", "Mapeamento visual", "Controle 81 63 representado visualmente como … no editor. A ferramenta mantém o código original ao salvar."),
            },
            "en": {
                "{05 0C}": ("New dialogue box", "Confirmed", "Structural 05 0C control. It ends the current box and continues the same dialogue in a new box. Moving it changes where the player advances the text."),
                "{FF 24}": ("Player name", "Confirmed", "Dynamic placeholder. The game replaces this control with the player's name at runtime. The editor shows it as [NOME]."),
                "{FF 2A}": ("FF 2A control", "Meaning unconfirmed", "Its exact runtime meaning is not confirmed. The tool shows [FF 2A] only so the control is never invisible. The original FF 2A bytes are preserved."),
                "{81 99}": ("81 99 icon", "Visual mapping", "Graphical control 81 99. The tool displays it as [ICON 99]. Its exact visual meaning is still treated cautiously; the original code is preserved."),
                "{81 F4}": ("Music note", "Visual mapping", "Graphical control 81 F4, shown as ♪ by the tool. It is not ordinary text and should be preserved when present in the original dialogue."),
                "{81 CD}": ("Heart", "Visual mapping", "Graphical control 81 CD, shown as ♥. The original byte sequence is preserved when saving."),
                "{81 63}": ("Special glyph", "Visual mapping", "Control 81 63 is represented as … in the editor. The tool preserves the original code."),
            },
            "es": {
                "{05 0C}": ("Nueva caja de diálogo", "Confirmado", "Control estructural 05 0C. Termina la caja actual y continúa el mismo diálogo en una nueva caja. Moverlo cambia dónde el jugador avanza el texto."),
                "{FF 24}": ("Nombre del jugador", "Confirmado", "Marcador dinámico. El juego reemplaza este control por el nombre del jugador durante la ejecución. En el editor aparece como [NOME]."),
                "{FF 2A}": ("Control FF 2A", "Significado no confirmado", "Su función exacta todavía no está confirmada. La herramienta muestra [FF 2A] únicamente para que el control no quede invisible. Los bytes originales se conservan."),
                "{81 99}": ("Icono 81 99", "Mapeo visual", "Control gráfico 81 99. La herramienta lo muestra como [ICONO 99]. Su significado visual exacto se trata con cautela; el código original se conserva."),
                "{81 F4}": ("Nota musical", "Mapeo visual", "Control gráfico 81 F4, mostrado como ♪. No es texto normal y debe conservarse cuando forma parte del diálogo original."),
                "{81 CD}": ("Corazón", "Mapeo visual", "Control gráfico 81 CD, mostrado como ♥. La secuencia original se conserva al guardar."),
                "{81 63}": ("Glifo especial", "Mapeo visual", "El control 81 63 se representa como … en el editor. La herramienta conserva el código original."),
            },
        }
        title, confidence, desc = data.get(lang, data["pt-BR"]).get(raw, (
            raw,
            "Desconhecido" if lang == "pt-BR" else "Unknown" if lang == "en" else "Desconocido",
            ("Controle do jogo ainda não documentado nesta versão da ferramenta. O valor bruto é preservado."
             if lang == "pt-BR" else
             "Game control not documented in this version of the tool. The raw value is preserved."
             if lang == "en" else
             "Control del juego aún no documentado en esta versión. El valor original se conserva."),
        ))
        return title, confidence, desc

    def _command_motion(self, widget: tk.Text, event) -> None:
        token = self._display_token_at(widget, event)
        if not token:
            self._hide_tooltip()
            return
        normalized = self._normalize_command_token(token)
        x = self.winfo_pointerx() + 16
        y = self.winfo_pointery() + 18
        if normalized == self._tooltip_token and self._tooltip is not None:
            # Follow the pointer while it remains over the same command.
            try: self._tooltip.geometry(f"+{x}+{y}")
            except Exception: pass
            return
        self._hide_tooltip()
        title, confidence, desc = self._command_info(token)
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        frame = ttk.Frame(tip, padding=7, relief="solid", borderwidth=1)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{title}  {normalized}", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(frame, text=confidence, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text=desc, wraplength=360, justify="left").pack(anchor="w", pady=(3, 0))
        tip.geometry(f"+{x}+{y}")
        self._tooltip = tip
        self._tooltip_token = normalized

    def _hide_tooltip(self) -> None:
        if self._tooltip is not None:
            try:
                self._tooltip.destroy()
            except Exception:
                pass
        self._tooltip = None; self._tooltip_token = None

    def _translation_return(self, _evt=None):
        """Second consecutive Enter becomes an explicit visible 05 0C page break."""
        try:
            idx = self.translation_text.index("insert")
            line = idx.split(".")[0]
            current_line = self.translation_text.get(f"{line}.0", f"{line}.end")
            if current_line.strip() == "":
                self.translation_text.insert("insert", "{05 0C}\n")
                self._apply_text_highlights(self.translation_text)
                self._schedule_live_validation(20)
                return "break"
        except Exception:
            pass
        return None

    def _translation_key_release(self, _evt=None) -> None:
        self._apply_text_highlights(self.translation_text)
        self._schedule_live_validation(150)

    def _insert_token(self, token: str) -> None:
        self.translation_text.insert("insert", token)
        self.translation_text.focus_set()
        self._apply_text_highlights(self.translation_text)
        self._schedule_live_validation(20)

    def set_status(self, text: str) -> None:
        self.statusbar.configure(text=text); self.update_idletasks()

    def _confirm_discard(self) -> bool:
        return True if not self.dirty else messagebox.askyesno("Alterações não salvas", "Há alterações não salvas no projeto. Continuar mesmo assim?")

    @staticmethod
    def _unique_dialogues(rows: List[dict]) -> List[dict]:
        seen: Dict[Tuple[int, int], dict] = {}
        for row in rows:
            k = _key(row.get("script_id", -1), row.get("string_index", -1))
            if k not in seen: seen[k] = dict(row)
            else:
                dst = seen[k]
                for field in ("face_ids", "expressions", "sides"):
                    vals = list(dst.get(field) or [])
                    for v in (row.get(field) or []):
                        if v not in vals: vals.append(v)
                    dst[field] = vals
        return sorted(seen.values(), key=lambda x: (int(x.get("script_id", 0)), int(x.get("string_index", 0))))

    def _thumb_for(self, entity: EntityRef):
        if entity.entity_id in self._thumb_refs: return self._thumb_refs[entity.entity_id]
        thumb = None; p = entity.preferred_thumbnail
        if self.settings.show_thumbnails and p and p.exists():
            try:
                if Image is not None and ImageTk is not None:
                    im = Image.open(p).convert("RGBA")
                    size = int(self.settings.thumbnail_size)
                    im.thumbnail((size, size), Image.Resampling.NEAREST)
                    thumb = ImageTk.PhotoImage(im)
                else:
                    thumb = tk.PhotoImage(file=str(p))
            except Exception: thumb = None
        self._thumb_refs[entity.entity_id] = thumb
        return thumb

    # --------------------------------------------------------------- ROM / lists

    def open_rom(self) -> None:
        if not self._confirm_discard(): return
        path = filedialog.askopenfilename(title=self.tr("open_rom"), filetypes=[("Nintendo DS ROM", "*.nds"), ("All", "*.*")])
        if not path: return
        try:
            self.set_status("Abrindo ROM...")
            model = RomModel.from_file(path, localization_only=True, progress=lambda p, m: self.set_status(f"{p}% - {m}"))
            if model.game_code not in SUPPORTED_GAME_CODES:
                raise HMDSStudioError(f"Game Code não suportado nesta versão: {model.game_code}. HMDS Cute permanece fora por enquanto.")
            if not self.entities.compatible_with_model(model):
                raise HMDSStudioError("O índice de personagens não é compatível com esta estrutura ScriptS.")
            self.model = model; self.session = EditSession(); self.project = TranslationProject()
            self.current_entity = None; self.current_dialogue = None; self.current_dialogue_rows = []; self.current_preview_images = []
            self.dirty = False; self._thumb_refs.clear(); self._friendly_cache.clear(); self._preview_render_cache.clear()
            self.refresh_characters(); self._clear_dialogue_view()
            anomalies = len(getattr(model, "tolerated_script_riff_overflows", []))
            extra = f" | {anomalies} RIFF antigo(s) tolerado(s)" if anomalies else ""
            unmapped = sum(1 for e in self.entities.entities(with_dialogues_only=False) if e.dialogue_count <= 0)
            self.rom_info.configure(text=f"{Path(path).name} | {model.game_code} | {model.script_count} scripts | {self.entities.entity_count} personagens | {unmapped} sem vínculo de fala{extra}")
            self.set_status(f"ROM carregada | {model.compatibility_label} | {model.script_count} scripts")
        except Exception as exc:
            self.model = None; messagebox.showerror("Não foi possível abrir a ROM", str(exc)); self.set_status("Falha ao abrir a ROM.")

    def _character_progress(self, entity_id: str) -> str:
        if not self.model: return "-"
        rows = self.entities.dialogues(entity_id, unique=True)
        if not rows: return "-"
        done = sum(1 for r in rows if self.project.status(int(r["script_id"]), int(r["string_index"]), self.session) in ("translated", "review", "reviewed", "ignored"))
        return f"{round(done * 100 / len(rows))}%"

    def _friendly_for(self, sid: int, idx: int, raw: str) -> str:
        key = (int(sid), int(idx), raw)
        cached = self._friendly_cache.get(key)
        if cached is not None:
            return cached
        value = raw_to_editor(raw)
        # Keep the cache bounded. A translation session rarely needs more than a few
        # thousand physical strings on screen, so this avoids unbounded growth.
        if len(self._friendly_cache) > 8000:
            self._friendly_cache.clear()
        self._friendly_cache[key] = value
        return value

    def refresh_characters(self) -> None:
        if not hasattr(self, "char_tree"):
            return
        for item in self.char_tree.get_children():
            self.char_tree.delete(item)
        if not self.model:
            return
        query = self.char_search.query() if hasattr(self, "char_search") else ""
        entities = self.entities.entities(query, with_dialogues_only=False)
        need_thumbs = []
        for entity in entities:
            progress = self._character_progress(entity.entity_id) if self.settings.show_progress else ""
            values = (entity.dialogue_count if entity.dialogue_count else "-", progress)
            kwargs = {"iid": entity.entity_id, "text": entity.display_name, "values": values}
            if entity.dialogue_count <= 0:
                kwargs["tags"] = ("unmapped",)
            cached = self._thumb_refs.get(entity.entity_id)
            if cached is not None:
                kwargs["image"] = cached
            elif self.settings.show_thumbnails and entity.preferred_thumbnail:
                need_thumbs.append(entity)
            self.char_tree.insert("", "end", **kwargs)

        # Decode thumbnails in short idle batches. Loading 175 PNGs synchronously was
        # one of the most visible freezes when opening/searching the character list.
        self._thumb_generation += 1
        generation = self._thumb_generation
        if self._thumb_after_id is not None:
            try: self.after_cancel(self._thumb_after_id)
            except Exception: pass
        if need_thumbs:
            self._thumb_after_id = self.after(1, lambda: self._load_thumbnail_batch(need_thumbs, 0, generation))

    def _load_thumbnail_batch(self, entities: List[EntityRef], start: int, generation: int) -> None:
        if generation != self._thumb_generation:
            return
        end = min(len(entities), start + 12)
        for entity in entities[start:end]:
            image = self._thumb_for(entity)
            if image is not None and self.char_tree.exists(entity.entity_id):
                try: self.char_tree.item(entity.entity_id, image=image)
                except Exception: pass
        if end < len(entities):
            self._thumb_after_id = self.after(1, lambda: self._load_thumbnail_batch(entities, end, generation))
        else:
            self._thumb_after_id = None

    def _update_current_character_progress(self) -> None:
        entity = self.current_entity
        if not entity or not hasattr(self, "char_tree") or not self.char_tree.exists(entity.entity_id):
            return
        progress = self._character_progress(entity.entity_id) if self.settings.show_progress else ""
        self.char_tree.item(entity.entity_id, values=(entity.dialogue_count if entity.dialogue_count else "-", progress))
        if entity.dialogue_count <= 0:
            if self.settings.language == "en":
                note = "No Script/STR dialogue is mapped to this Character ID in the current research index. This does not prove the ID never speaks in-game."
            elif self.settings.language == "es":
                note = "No hay diálogos Script/STR vinculados a este Character ID en el índice de investigación actual. Esto no demuestra que el ID nunca hable en el juego."
            else:
                note = "Nenhuma fala Script/STR está vinculada a este Character ID no índice de pesquisa atual. Isso não prova que o ID nunca fale no jogo."
        else:
            note = progress or "-"
        self.character_summary.configure(
            text=f"{entity.display_name}\nCharacter ID: {entity.character_id if entity.character_id is not None else '-'}\n"
                 f"{self.tr('dialogues')}: {entity.dialogue_count}\n{note}"
        )

    def _on_character_selected(self, _evt=None) -> None:
        sel = self.char_tree.selection()
        if not sel:
            return
        entity = self.entities.entity_ref(sel[0])
        if not entity:
            return
        self.current_entity = entity
        self.current_dialogue = None
        self.dialogue_tree.selected_iid = None
        self.dialogue_title.configure(text=self.tr("dialogues_of", name=entity.display_name))
        self._update_current_character_progress()
        self.refresh_dialogues()
        self._set_text(self.original_text, "", readonly=True)
        self._set_text(self.translation_text, "", readonly=False)
        self.preview_canvas.delete("all")
        self.current_preview_images = []
        self._update_technical_inspector()

    def refresh_dialogues(self) -> None:
        if not hasattr(self, "dialogue_tree"):
            return
        self._update_dialogue_headers()
        self.dialogue_tree.set_command_colors(
            self.command_colors[0], self.command_colors[1],
            self.page_colors[0], self.page_colors[1],
            self.symbol_colors[0], self.symbol_colors[1]
        )
        self.dialogue_tree.begin_update()
        try:
            if not self.model or not self.current_entity:
                return
            query = self.dialogue_search.query().casefold() if hasattr(self, "dialogue_search") else ""
            rows = self.entities.dialogues(self.current_entity.entity_id, unique=True)
            self.current_dialogue_rows = rows
            shown = 0
            labels = self._status_labels()
            for row in rows:
                sid, idx = int(row["script_id"]), int(row["string_index"])
                script = self.model.parse_script(sid)
                if not script.valid or not (0 <= idx < len(script.strings)):
                    continue
                raw = self.model.current_text(self.session, sid, idx)
                friendly = self._friendly_for(sid, idx, raw)
                if query and query not in friendly.casefold() and query not in str(sid) and query not in str(idx):
                    continue
                state = self.project.status(sid, idx, self.session)
                meta = ""
                if self.tech_var.get():
                    faces = ",".join(map(str, row.get("face_ids") or [])) or "-"
                    exprs = ",".join(map(str, row.get("expressions") or [])) or "-"
                    sides = ",".join(row.get("sides") or []) or "-"
                    meta = f"S{sid}:{idx} F{faces} E{exprs} {sides}"
                self.dialogue_tree.add_row(
                    f"{sid}:{idx}", labels.get(state, state), friendly,
                    meta=meta, new_box_label=self.tr("list_newbox")
                )
                shown += 1
        finally:
            self.dialogue_tree.end_update()

        total = len(self.current_dialogue_rows)
        done = sum(
            1 for r in self.current_dialogue_rows
            if self.project.status(int(r["script_id"]), int(r["string_index"]), self.session)
            in ("translated", "review", "reviewed", "ignored")
        )
        pct = round(done * 100 / total) if total else 0
        if shown != total:
            suffix = f" | {shown} exibidas" if self.settings.language == "pt-BR" else f" | {shown} shown" if self.settings.language == "en" else f" | {shown} mostrados"
        else:
            suffix = ""
        self.dialogue_progress.configure(text=(f"{done}/{total} | {pct}%" if self.settings.show_progress else f"{shown}/{total}") + suffix)
        if not self.current_dialogue_rows:
            self.context_label.configure(text=f"{self.current_entity.display_name}\n{self.tr('no_dialogues')}")

    def _on_dialogue_selected(self, _evt=None) -> None:
        if not self.model:
            return
        sel = self.dialogue_tree.selection()
        if not sel:
            return
        sid_s, idx_s = sel[0].split(":", 1)
        sid, idx = int(sid_s), int(idx_s)
        row = next((r for r in self.current_dialogue_rows if int(r["script_id"]) == sid and int(r["string_index"]) == idx), None)
        if not row:
            return
        self.current_dialogue = row
        script = self.model.parse_script(sid)
        original = script.strings[idx]
        current = self.model.current_text(self.session, sid, idx)
        self._set_text(self.original_text, self._friendly_for(sid, idx, original), readonly=True)
        self._set_text(self.translation_text, self._friendly_for(sid, idx, current), readonly=False)
        self.status_var.set(self._status_label(self.project.status(sid, idx, self.session)))
        self._validate_live()
        self._update_technical_inspector()

    # --------------------------------------------------------------- editor / preview

    def _set_text(self, widget: tk.Text, text: str, readonly: bool) -> None:
        widget.configure(state="normal"); widget.delete("1.0", "end"); widget.insert("1.0", text); self._apply_text_highlights(widget)
        if readonly: widget.configure(state="disabled")

    def _clear_dialogue_view(self) -> None:
        self.dialogue_tree.clear()
        self.current_dialogue = None; self._set_text(self.original_text, "", True); self._set_text(self.translation_text, "", False)
        self.context_label.configure(text=self.tr("select_dialogue")); self.preview_canvas.delete("all"); self.current_preview_images = []
        self.metrics_text.configure(state="normal"); self.metrics_text.delete("1.0", "end"); self.metrics_text.configure(state="disabled")

    def _selected_ids(self) -> Optional[Tuple[int, int]]:
        if not self.current_dialogue: return None
        return int(self.current_dialogue["script_id"]), int(self.current_dialogue["string_index"])

    def _editor_value(self) -> str:
        return self.translation_text.get("1.0", "end-1c")

    def _validate_live(self) -> None:
        ids = self._selected_ids()
        if not ids or not self.model:
            return
        sid, idx = ids
        original = self.model.parse_script(sid).strings[idx]
        raw = editor_to_raw(self._editor_value())
        result = validate_translation(original, raw, protect_controls=self.protect_var.get())
        self._draw_preview(raw)
        context = self.entities.dialogue_context_for_block(sid, idx)
        rows = context["rows"]
        if rows:
            names = " | ".join(r.get("display_name") or r.get("entity_id") or "?" for r in rows)
            details = " ; ".join(
                f"{r.get('display_name')}: Face {','.join(map(str,r.get('face_ids') or [])) or '-'} / "
                f"Expr {','.join(map(str,r.get('expressions') or [])) or '-'} / {','.join(r.get('sides') or []) or '-'}"
                for r in rows[:3]
            )
            self.context_label.configure(text=f"{names}\n{details}")
        else:
            self.context_label.configure(text=self.current_entity.display_name if self.current_entity else "")
        self.metrics_text.configure(state="normal")
        self.metrics_text.delete("1.0", "end")
        for line in result.lines:
            label = "!" if line.overflow else "OK"
            box = "Caixa" if self.settings.language == "pt-BR" else "Box" if self.settings.language == "en" else "Caja"
            line_word = "linha" if self.settings.language == "pt-BR" else "line" if self.settings.language == "en" else "línea"
            self.metrics_text.insert("end", f"{label}  {box} {line.page}, {line_word} {line.line}: {line.byte_count}/30 bytes\n")
        if result.messages:
            self.metrics_text.insert("end", "\n")
            for msg in result.messages:
                if self.settings.language == "en":
                    label = "ERROR" if msg.level == "error" else "WARNING" if msg.level == "warning" else "INFO"
                elif self.settings.language == "es":
                    label = "ERROR" if msg.level == "error" else "AVISO" if msg.level == "warning" else "INFO"
                else:
                    label = "ERRO" if msg.level == "error" else "AVISO" if msg.level == "warning" else "INFO"
                self.metrics_text.insert("end", f"{label}: {msg.message}\n")
        self.metrics_text.configure(state="disabled")
        self._update_technical_inspector()

    def _draw_preview(self, raw_text: str) -> None:
        canvas = self.preview_canvas
        old_y = canvas.yview()
        canvas.delete("all")
        self.current_preview_images = []
        if not self.current_dialogue:
            return
        if not self.preview_renderer.available or ImageTk is None or Image is None:
            canvas.create_text(14, 14, anchor="nw", text="Preview gráfico indisponível. Pillow não foi incorporado.", fill="#e4e7eb")
            return
        sid, idx = int(self.current_dialogue["script_id"]), int(self.current_dialogue["string_index"])
        block = self.entities.dialogue_context_for_block(sid, idx)
        contexts = self.preview_renderer.contexts_from_entity_rows(block["rows"])
        name = self.current_entity.display_name if self.current_entity else ""
        speaker_id = self.current_entity.entity_id if self.current_entity else None
        ff2a_placeholder = self.tr("preview.dynamic")
        compose_kwargs = dict(
            name=name, bg_id=0, contexts=contexts, speaker_entity_id=speaker_id,
            text_shadow=self.settings.preview_shadow,
            shadow_color=self.settings.preview_shadow_color,
            shadow_x=self.settings.preview_shadow_x,
            shadow_y=self.settings.preview_shadow_y,
            ff2a_placeholder=ff2a_placeholder,
        )
        try:
            pages = self.preview_renderer.pages_from_raw(raw_text, ff2a_placeholder)
        except Exception as exc:
            canvas.create_text(14, 14, anchor="nw", text=f"Falha no preview: {exc}", fill="#ffb0b0")
            return
        y = 10
        canvas_w = max(canvas.winfo_width(), 420)
        scale = float(self.settings.preview_scale)
        box_label = "Caixa" if self.settings.language == "pt-BR" else "Box" if self.settings.language == "en" else "Caja"
        for page_index in range(len(pages)):
            cache_key = (sid, idx, raw_text, page_index, scale, self.settings.preview_shadow,
                         self.settings.preview_shadow_color, self.settings.preview_shadow_x, self.settings.preview_shadow_y,
                         ff2a_placeholder, speaker_id)
            image = self._preview_render_cache.get(cache_key)
            if image is None:
                image, _, _ = self.preview_renderer.compose(raw_text, page=page_index, **compose_kwargs)
                image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.NEAREST)
                if len(self._preview_render_cache) > 80:
                    self._preview_render_cache.clear()
                self._preview_render_cache[cache_key] = image
            photo = ImageTk.PhotoImage(image)
            self.current_preview_images.append(photo)
            x = max(10, (canvas_w - image.width) // 2)
            canvas.create_text(x, y, anchor="nw", text=f"{box_label} {page_index+1}", fill="#d4d8de", font=("Segoe UI", 9, "bold"))
            y += 18
            canvas.create_image(x, y, anchor="nw", image=photo)
            y += image.height + 18
        canvas.configure(scrollregion=(0, 0, max(canvas_w, 420), y + 6))
        if old_y and old_y != (0.0, 1.0):
            try: canvas.yview_moveto(old_y[0])
            except Exception: pass

    def _redraw_preview(self) -> None:
        if self._selected_ids(): self._draw_preview(editor_to_raw(self._editor_value()))

    def _current_row_meta(self, row: dict, sid: int, idx: int) -> str:
        if not self.tech_var.get():
            return ""
        faces = ",".join(map(str, row.get("face_ids") or [])) or "-"
        exprs = ",".join(map(str, row.get("expressions") or [])) or "-"
        sides = ",".join(row.get("sides") or []) or "-"
        return f"S{sid}:{idx} F{faces} E{exprs} {sides}"

    def _update_current_dialogue_row(self) -> None:
        ids = self._selected_ids()
        if not ids or not self.model or not self.current_dialogue:
            return
        sid, idx = ids
        iid = f"{sid}:{idx}"
        if not self.dialogue_tree.exists(iid):
            return
        raw = self.model.current_text(self.session, sid, idx)
        friendly = self._friendly_for(sid, idx, raw)
        state = self.project.status(sid, idx, self.session)
        self.dialogue_tree.update_row(
            iid,
            self._status_labels().get(state, state),
            friendly,
            meta=self._current_row_meta(self.current_dialogue, sid, idx),
            new_box_label=self.tr("list_newbox"),
        )
        self.dialogue_tree.selection_set(iid)
        rows = self.current_dialogue_rows
        total = len(rows)
        done = sum(
            1 for r in rows
            if self.project.status(int(r["script_id"]), int(r["string_index"]), self.session)
            in ("translated", "review", "reviewed", "ignored")
        )
        pct = round(done * 100 / total) if total else 0
        self.dialogue_progress.configure(text=f"{done}/{total} | {pct}%" if self.settings.show_progress else f"{total}/{total}")

    def save_current_dialogue(self) -> None:
        ids = self._selected_ids()
        if not ids or not self.model:
            return
        sid, idx = ids
        original = self.model.parse_script(sid).strings[idx]
        raw = ensure_terminal_control(original, editor_to_raw(self._editor_value()))
        result = validate_translation(original, raw, protect_controls=self.protect_var.get())
        errors = [m.message for m in result.messages if m.level == "error"]
        if errors:
            messagebox.showerror("A fala não pode ser salva", "\n\n".join(errors))
            return
        warnings = [m.message for m in result.messages if m.level == "warning"]
        if warnings and not messagebox.askyesno("Avisos", "\n".join("- " + x for x in warnings[:8]) + "\n\nSalvar mesmo assim?"):
            return
        try:
            if raw == original:
                edits = self.session.string_edits.get(sid)
                if edits and idx in edits:
                    edits.pop(idx, None)
                    if not edits:
                        self.session.string_edits.pop(sid, None)
            else:
                self.model.set_string(self.session, sid, idx, raw)
            state = self._status_key_from_label(self.status_var.get())
            if state == "untranslated" and raw != original:
                state = "translated"
            self.project.set_status(sid, idx, state)
            self.dirty = True
            self._friendly_cache.clear()
            self._set_text(self.translation_text, raw_to_editor(raw), False)
            # Update only the edited physical row and current character progress.
            # Rebuilding 500+ Tk text rows after every save caused visible freezes.
            self._update_current_dialogue_row()
            self._update_current_character_progress()
            self._validate_live()
            self.set_status(f"Script {sid} / STR {idx} salvo na sessão")
        except Exception as exc:
            messagebox.showerror("Falha ao salvar fala", str(exc))

    def revert_current(self) -> None:
        ids = self._selected_ids()
        if not ids or not self.model:
            return
        sid, idx = ids
        original = self.model.parse_script(sid).strings[idx]
        edits = self.session.string_edits.get(sid)
        if edits:
            edits.pop(idx, None)
            if not edits:
                self.session.string_edits.pop(sid, None)
        self.project.statuses.pop(self.project.key(sid, idx), None)
        self._friendly_cache.clear()
        self._set_text(self.translation_text, raw_to_editor(original), False)
        self.status_var.set(self._status_label("untranslated"))
        self.dirty = True
        self._update_current_dialogue_row()
        self._update_current_character_progress()
        self._validate_live()

    # --------------------------------------------------------------- projects / build

    def save_project(self, save_as: bool = False) -> None:
        if not self.model: return
        path = self.project.path
        if save_as or not path:
            initial = (self.model.source_path.stem if self.model.source_path else "HMDS") + ".ctsproj"
            selected = filedialog.asksaveasfilename(title=self.tr("save_project"), defaultextension=".ctsproj", initialfile=initial, filetypes=[("CTS Project", "*.ctsproj")])
            if not selected: return
            path = Path(selected)
        try: self.project.save(self.model, self.session, path); self.dirty = False; self.set_status(f"Projeto salvo | {path}")
        except Exception as exc: messagebox.showerror("Falha ao salvar projeto", str(exc))

    def open_project(self) -> None:
        if not self.model: messagebox.showinfo("ROM", "Abra primeiro a ROM base usada pelo projeto."); return
        if not self._confirm_discard(): return
        selected = filedialog.askopenfilename(title=self.tr("open_project"), filetypes=[("CTS Project", "*.ctsproj"), ("All", "*.*")])
        if not selected: return
        try: self.project.load(self.model, self.session, selected); self.dirty = False; self.refresh_characters(); self.refresh_dialogues(); self.set_status(f"Projeto carregado | {selected}")
        except Exception as exc: messagebox.showerror("Projeto incompatível", str(exc))

    def validate_project(self, quiet: bool = False) -> bool:
        if not self.model: return False
        errors, warnings, checked = [], [], 0
        for sid, edits in sorted(self.session.string_edits.items()):
            sc = self.model.parse_script(sid)
            for idx, text in edits.items():
                if idx >= len(sc.strings): errors.append(f"Script {sid} STR {idx}: índice inexistente"); continue
                r = validate_translation(sc.strings[idx], text, protect_controls=True); checked += 1
                for m in r.messages:
                    row = f"Script {sid} / STR {idx}: {m.message}"
                    if m.level == "error": errors.append(row)
                    elif m.level == "warning": warnings.append(row)
        if errors:
            if not quiet: messagebox.showerror("Validação", f"{len(errors)} erro(s), {len(warnings)} aviso(s).\n\n" + "\n".join(errors[:15]))
            return False
        if not quiet: messagebox.showinfo("Validação", f"{checked} fala(s) editada(s).\nErros: 0\nAvisos: {len(warnings)}" + ("\n\n" + "\n".join(warnings[:12]) if warnings else ""))
        return True

    def build_rom(self) -> None:
        if not self.model: return
        if not self.validate_project(quiet=True): messagebox.showerror("Build bloqueada", "Há erros de validação."); return
        src = self.model.source_path; initial = (src.stem if src else "HMDS") + "_traduzida.nds"
        selected = filedialog.asksaveasfilename(title=self.tr("build_rom"), defaultextension=".nds", initialfile=initial, filetypes=[("Nintendo DS ROM", "*.nds")])
        if not selected: return
        out = Path(selected)
        try:
            if src and out.resolve() == src.resolve(): raise HMDSStudioError("A ROM original nunca é sobrescrita. Escolha outro nome.")
            self.set_status("Reconstruindo ScriptS e ponteiros..."); data, guard = self.model.build_rom(self.session); out.write_bytes(data)
            verify = RomModel(data, out, localization_only=True)
            if verify.script_count != self.model.script_count: raise HMDSStudioError("Contagem de scripts mudou após a build.")
            messagebox.showinfo("Build concluída", f"ROM gerada e reaberta com sucesso.\n\n{out}\n\nScripts: {verify.script_count}\nPonteiros: {guard.get('pointerCount')}")
            self.set_status(f"ROM gerada e validada | {out.name}")
        except Exception as exc: messagebox.showerror("Falha ao gerar ROM", str(exc)); self.set_status("Build falhou; a ROM original não foi alterada.")

    # --------------------------------------------------------------- export / import

    def _current_entity_entries_with_omitted(self) -> Tuple[List[dict], List[dict]]:
        """Return exportable entries and a report of rows intentionally omitted."""
        if not self.model or not self.current_entity:
            return [], []
        out: List[dict] = []
        omitted: List[dict] = []
        for row in self._unique_dialogues(self.entities.dialogues(self.current_entity.entity_id)):
            sid, idx = int(row["script_id"]), int(row["string_index"])
            entry, reason = safe_make_entry(
                self.model, self.session, self.project, sid, idx,
                entity_id=self.current_entity.entity_id,
                entity_name=self.current_entity.display_name,
            )
            if entry is not None:
                out.append(entry)
            else:
                omitted.append({"script_id": sid, "string_index": idx, "reason": reason or self.tr("export.omitted_unknown")})
        return out, omitted

    def _current_entity_entries(self) -> List[dict]:
        entries, _omitted = self._current_entity_entries_with_omitted()
        return entries

    def _show_export_omitted(self, omitted: List[dict]) -> None:
        if not omitted:
            return
        lines = []
        for row in omitted[:8]:
            lines.append(f"S{row.get('script_id')} / STR {row.get('string_index')}: {row.get('reason', '')}")
        extra = len(omitted) - len(lines)
        if extra > 0:
            lines.append(self.tr("export.omitted_more", count=extra))
        messagebox.showwarning(
            self.tr("menu.export"),
            self.tr("export.omitted_summary", count=len(omitted)) + "\n\n" + "\n".join(lines),
        )

    def export_selected_dialogue_package(self) -> None:
        """Export exactly the dialogue currently open in the editor."""
        if not self.model or not self.current_dialogue:
            messagebox.showinfo(self.tr("menu.export"), self.tr("select_dialogue"))
            return
        sid, idx = self._selected_ids() or (-1, -1)
        if sid < 0:
            return
        entity_id = self.current_entity.entity_id if self.current_entity else ""
        entity_name = self.current_entity.display_name if self.current_entity else ""
        entry, reason = safe_make_entry(
            self.model, self.session, self.project, sid, idx,
            entity_id=entity_id, entity_name=entity_name,
        )
        if entry is None:
            messagebox.showwarning(
                self.tr("menu.export"),
                self.tr("export.current_blocked", reason=reason or self.tr("export.omitted_unknown")),
            )
            return
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", entity_name or "dialogue").strip("_") or "dialogue"
        selected = filedialog.asksaveasfilename(
            title=self.tr("export_dialogue_package"),
            defaultextension=DIALOGUE_EXTENSION,
            initialfile=f"{safe_name}_S{sid}_STR{idx}{DIALOGUE_EXTENSION}",
            filetypes=[("CTS Dialogue", f"*{DIALOGUE_EXTENSION}")],
        )
        if not selected:
            return
        write_dialogue_file(selected, self.model, entry, scope={"type": "dialogue", "script_id": sid, "string_index": idx})
        self.set_status(self.tr("export.status_one", name=Path(selected).name, sid=sid, idx=idx))

    def export_character_package(self) -> None:
        if not self.model or not self.current_entity:
            messagebox.showinfo(self.tr("menu.export"), self.tr("export.select_character"))
            return
        entries, omitted = self._current_entity_entries_with_omitted()
        if not entries:
            messagebox.showinfo(self.tr("menu.export"), self.tr("no_dialogues"))
            self._show_export_omitted(omitted)
            return
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", self.current_entity.display_name).strip("_") or "character"
        selected = filedialog.asksaveasfilename(
            title=self.tr("export_character_package"),
            defaultextension=PACK_EXTENSION,
            initialfile=f"{safe_name}_completo{PACK_EXTENSION}",
            filetypes=[("CTS Pack", f"*{PACK_EXTENSION}")],
        )
        if not selected:
            return
        write_pack_file(selected, self.model, entries, scope={
            "type": "character",
            "entity_id": self.current_entity.entity_id,
            "display_name": self.current_entity.display_name,
        }, omitted=omitted)
        self.set_status(self.tr("export.status_many", name=Path(selected).name, count=len(entries)))
        self._show_export_omitted(omitted)

    def export_character_csv(self) -> None:
        if not self.model or not self.current_entity:
            messagebox.showinfo(self.tr("menu.export"), self.tr("export.select_character"))
            return
        entries, omitted = self._current_entity_entries_with_omitted()
        if not entries:
            messagebox.showinfo(self.tr("menu.export"), self.tr("no_dialogues"))
            self._show_export_omitted(omitted)
            return
        selected = filedialog.asksaveasfilename(
            title=self.tr("export_character_csv"), defaultextension=".csv",
            initialfile=f"{self.current_entity.display_name.replace(' ', '_')}.csv",
            filetypes=[("CSV UTF-8", "*.csv")],
        )
        if selected:
            write_csv(selected, entries)
            self.set_status(self.tr("export.status_csv", name=Path(selected).name, count=len(entries)))
            self._show_export_omitted(omitted)

    def _prepare_import_rows(self, rows: List[dict]) -> List[dict]:
        prepared: List[dict] = []
        if not self.model:
            return prepared
        valid_statuses = {"untranslated", "translated", "review", "reviewed", "ignored"}

        for entry in rows:
            item = {
                "entry": entry,
                "selectable": False,
                "selected_default": False,
                "compare_key": "incompatible",
                "entity_name": str(entry.get("entity_name") or ""),
                "current_display": "",
                "incoming_display": "",
                "current_full": "",
                "incoming_full": "",
                "current_status": "untranslated",
                "incoming_status": str(entry.get("status") or ""),
                "reason": "",
            }
            try:
                sid = int(entry.get("script_id")); idx = int(entry.get("string_index"))
                item["script_id"] = sid; item["string_index"] = idx
                sc = self.model.parse_script(sid)
                if not sc.valid or not (0 <= idx < len(sc.strings)):
                    raise ValueError(self.tr("import.reason_missing"))
                original = sc.strings[idx]
                expected = str(entry.get("original_sha256") or "")
                if expected and expected != text_fingerprint(original):
                    raise ValueError(self.tr("import.reason_base_mismatch"))

                current_raw = self.model.current_text(self.session, sid, idx)
                current_friendly = raw_to_editor(current_raw)
                local_status = self.project.status(sid, idx, self.session)
                incoming_status = str(entry.get("status") or "")
                has_translation = bool(entry.get("has_translation", "translation" in entry))
                incoming_friendly = str(entry.get("translation") or "") if has_translation else ""
                incoming_raw = ensure_terminal_control(original, editor_to_raw(incoming_friendly)) if has_translation else None

                item["current_full"] = current_friendly
                item["incoming_full"] = incoming_friendly if has_translation else self.tr("import.legacy_no_text")
                item["current_display"] = current_friendly.replace("\n", " / ")[:360]
                item["incoming_display"] = (incoming_friendly if has_translation else self.tr("import.no_text_short")).replace("\n", " / ")[:360]
                item["current_status"] = local_status
                item["incoming_status"] = incoming_status

                text_diff = incoming_raw is not None and incoming_raw != current_raw
                status_diff = incoming_status in valid_statuses and incoming_status != local_status
                protected_text = bool(
                    text_diff and hasattr(self.model, "is_script_write_protected") and self.model.is_script_write_protected(sid)
                )

                if protected_text:
                    item["compare_key"] = "protected"
                    item["selectable"] = False
                    item["selected_default"] = False
                    item["reason"] = self.tr("import.reason_protected", sid=sid)
                else:
                    item["selectable"] = True
                    if text_diff and status_diff:
                        item["compare_key"] = "text_status"; item["selected_default"] = True
                    elif text_diff:
                        item["compare_key"] = "new" if current_raw == original and incoming_raw != original else "different"
                        item["selected_default"] = True
                    elif status_diff:
                        item["compare_key"] = "status_only"; item["selected_default"] = True
                    else:
                        item["compare_key"] = "same"; item["selected_default"] = False
            except Exception as exc:
                item["reason"] = str(exc)
                item["incoming_full"] = str(entry.get("translation") or "")
                item["incoming_display"] = item["incoming_full"].replace("\n", " / ")[:360]
            prepared.append(item)
        return prepared

    def _apply_import_rows(self, rows: List[dict], source_name: str) -> None:
        if not self.model:
            return
        prepared = self._prepare_import_rows(rows)
        dialog = ImportReviewDialog(self, self.tr, prepared, source_name)
        self.wait_window(dialog)
        selected_rows = dialog.selected_entries
        if selected_rows is None:
            return
        if not selected_rows:
            messagebox.showinfo(self.tr("import.review_title"), self.tr("import.none_selected"))
            return

        open_ids = self._selected_ids()
        result = import_entries(self.model, self.session, self.project, selected_rows, overwrite_conflicts=True)
        self.dirty = self.dirty or result.applied > 0
        self._friendly_cache.clear(); self._preview_render_cache.clear()
        self.refresh_characters(); self.refresh_dialogues()

        if open_ids:
            sid, idx = open_ids
            if any(int(r.get("script_id", -1)) == sid and int(r.get("string_index", -1)) == idx for r in selected_rows):
                self._set_text(self.translation_text, raw_to_editor(self.model.current_text(self.session, sid, idx)), False)
                self.status_var.set(self._status_label(self.project.status(sid, idx, self.session)))
                self._validate_live()

        summary = self.tr(
            "import.summary",
            source=source_name,
            applied=result.applied,
            texts=result.text_changes,
            statuses=result.status_changes,
            unchanged=result.unchanged,
            incompatible=result.incompatible,
        )
        if result.skipped:
            details = []
            for row in result.skipped[:8]:
                details.append(f"S{row.get('script_id')} / STR {row.get('string_index')}: {row.get('reason', '')}")
            if len(result.skipped) > len(details):
                details.append(self.tr("import.skipped_more", count=len(result.skipped)-len(details)))
            summary += "\n\n" + self.tr("import.skipped_title") + "\n" + "\n".join(details)
        messagebox.showinfo(self.tr("import.review_title"), summary)

    def import_package(self) -> None:
        if not self.model:
            messagebox.showinfo("ROM", self.tr("import.open_rom_first"))
            return
        all_patterns = " ".join(f"*{ext}" for ext in SUPPORTED_IMPORT_EXTENSIONS)
        selected = filedialog.askopenfilename(
            title=self.tr("import_package"),
            filetypes=[
                (self.tr("import.all_supported"), all_patterns),
                ("CTS Dialogue", f"*{DIALOGUE_EXTENSION}"),
                ("CTS Pack", f"*{PACK_EXTENSION}"),
                ("CSV UTF-8", "*.csv"),
                (self.tr("import.legacy_files"), "*.ctsexport *.ctsteam *.json"),
                (self.tr("import.all_files"), "*.*"),
            ],
        )
        if not selected:
            return
        try:
            payload = read_exchange_file(selected)
            if payload.get("game_code") and payload.get("game_code") != self.model.game_code:
                raise HMDSStudioError(self.tr("import.other_game_code"))
            rows = payload.get("entries", [])
            if not rows:
                messagebox.showinfo(self.tr("import.review_title"), self.tr("import.empty_file"))
                return
            self._apply_import_rows(rows, Path(selected).name)
        except Exception as exc:
            messagebox.showerror(self.tr("import.failed"), str(exc))

    # --------------------------------------------------------------- options / help / about

    def show_options(self) -> None:
        win = tk.Toplevel(self); win.title(self.tr("options.title")); win.transient(self); win.resizable(False, False); win.grab_set()
        body = ttk.Frame(win, padding=14); body.pack(fill="both", expand=True)
        lang = tk.StringVar(value=LANG_NAMES.get(self.settings.language, LANG_NAMES["pt-BR"]))
        scale = tk.StringVar(value=f"{int(round(self.settings.preview_scale*100))}%")
        thumb = tk.IntVar(value=self.settings.thumbnail_size)
        shadow = tk.BooleanVar(value=self.settings.preview_shadow)
        shadow_color = tk.StringVar(value=self.settings.preview_shadow_color)
        shadow_x = tk.IntVar(value=self.settings.preview_shadow_x)
        shadow_y = tk.IntVar(value=self.settings.preview_shadow_y)
        speed = tk.IntVar(value=self.settings.scroll_speed)
        layout = tk.StringVar(value=self.tr(f"layout.{self.settings.layout_preset}"))
        palette = tk.StringVar(value=self.tr(f"palette.{self.settings.highlight_palette}"))
        show_progress = tk.BooleanVar(value=self.settings.show_progress)
        show_thumbs = tk.BooleanVar(value=self.settings.show_thumbnails)

        rows = [
            (self.tr("options.language"), ttk.Combobox(body, state="readonly", width=24, textvariable=lang, values=list(LANG_NAMES.values()))),
            (self.tr("options.preview_scale"), ttk.Combobox(body, state="readonly", width=24, textvariable=scale, values=["100%","125%","145%","160%","175%"])),
            (self.tr("options.thumbnail_size"), ttk.Spinbox(body, from_=16, to=40, width=8, textvariable=thumb)),
            (self.tr("options.scroll_speed"), ttk.Spinbox(body, from_=1, to=12, width=8, textvariable=speed)),
            (self.tr("options.layout"), ttk.Combobox(body, state="readonly", width=24, textvariable=layout, values=[self.tr("layout.compact"),self.tr("layout.balanced"),self.tr("layout.preview")])),
            (self.tr("options.palette"), ttk.Combobox(body, state="readonly", width=24, textvariable=palette, values=[self.tr("palette.classic"),self.tr("palette.blue"),self.tr("palette.warm")])),
        ]
        for r, (label, widget) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0,12)); widget.grid(row=r, column=1, sticky="w", pady=4)

        base = len(rows)
        shadow_box = ttk.Labelframe(body, text=self.tr("options.shadow"), padding=9)
        shadow_box.grid(row=base, column=0, columnspan=2, sticky="ew", pady=(9, 3))
        ttk.Checkbutton(shadow_box, text=self.tr("options.shadow"), variable=shadow).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0,6))
        ttk.Label(shadow_box, text=self.tr("options.shadow_color")).grid(row=1, column=0, sticky="w", padx=(0,8), pady=3)
        color_swatch = tk.Label(shadow_box, width=4, relief="sunken", background=shadow_color.get())
        color_swatch.grid(row=1, column=1, sticky="w", padx=(0,6))

        def choose_shadow_color():
            chosen = colorchooser.askcolor(color=shadow_color.get(), parent=win, title=self.tr("options.shadow_color"))[1]
            if chosen:
                shadow_color.set(chosen)
                color_swatch.configure(background=chosen)

        ttk.Button(shadow_box, text=self.tr("options.choose_color"), command=choose_shadow_color).grid(row=1, column=2, sticky="w", pady=3)
        ttk.Label(shadow_box, text=self.tr("options.shadow_x")).grid(row=2, column=0, sticky="w", padx=(0,8), pady=3)
        ttk.Spinbox(shadow_box, from_=-4, to=4, width=6, textvariable=shadow_x).grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(shadow_box, text=self.tr("options.shadow_y")).grid(row=3, column=0, sticky="w", padx=(0,8), pady=3)
        ttk.Spinbox(shadow_box, from_=-4, to=4, width=6, textvariable=shadow_y).grid(row=3, column=1, sticky="w", pady=3)

        ttk.Checkbutton(body, text=self.tr("options.show_progress"), variable=show_progress).grid(row=base+1, column=0, columnspan=2, sticky="w", pady=(6,2))
        ttk.Checkbutton(body, text=self.tr("options.show_thumbnails"), variable=show_thumbs).grid(row=base+2, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(body, text=self.tr("options.language_note"), style="Muted.TLabel", wraplength=450).grid(row=base+3, column=0, columnspan=2, sticky="w", pady=(10,8))
        buttons = ttk.Frame(body); buttons.grid(row=base+4, column=0, columnspan=2, sticky="e")

        def save_opts():
            old_lang = self.settings.language
            self.settings.language = next((k for k, v in LANG_NAMES.items() if v == lang.get()), old_lang)
            self.settings.preview_scale = int(scale.get().replace("%", "")) / 100.0
            self.settings.thumbnail_size = int(thumb.get())
            self.settings.preview_shadow = bool(shadow.get())
            self.settings.preview_shadow_color = shadow_color.get()
            self.settings.preview_shadow_x = int(shadow_x.get())
            self.settings.preview_shadow_y = int(shadow_y.get())
            self.settings.scroll_speed = int(speed.get())
            layout_map = {self.tr("layout.compact"):"compact", self.tr("layout.balanced"):"balanced", self.tr("layout.preview"):"preview"}
            self.settings.layout_preset = layout_map.get(layout.get(), "balanced")
            pal_map = {self.tr("palette.classic"):"classic", self.tr("palette.blue"):"blue", self.tr("palette.warm"):"warm"}
            self.settings.highlight_palette = pal_map.get(palette.get(), "classic")
            self.settings.show_progress = bool(show_progress.get())
            self.settings.show_thumbnails = bool(show_thumbs.get())
            self.settings.save()
            self._thumb_refs.clear(); self._preview_render_cache.clear(); self._apply_setting_styles(); self.refresh_characters(); self.refresh_dialogues(); self.reset_layout(); self._redraw_preview(); win.destroy()
            if self.settings.language != old_lang:
                messagebox.showinfo(self.tr("options.title"), self.tr("options.restart"))

        ttk.Button(buttons, text=self.tr("options.cancel"), command=win.destroy).pack(side="right")
        ttk.Button(buttons, text=self.tr("options.save"), command=save_opts).pack(side="right", padx=(0,6))

    def _help_topics(self):
        lang = self.settings.language
        if lang == "en":
            return {
                "Getting started": [
                    ("p", "This tool is designed for translating dialogue in normal Harvest Moon DS without exposing the translator to offsets, pointer tables or script internals."),
                    ("num", "Open File > Open ROM and select a supported normal HMDS ROM."),
                    ("num", "Choose a character on the left. The face thumbnail helps confirm who you are working on."),
                    ("num", "Choose a dialogue in the center list. Original text and your editable translation appear below it."),
                    ("num", "Translate the dialogue, check the in-game preview and the 30-byte validation."),
                    ("num", "Save the dialogue, save the project, validate it, then build a new ROM."),
                    ("image", "overview.png"),
                    ("p", "The original ROM is never overwritten by the tool."),
                ],
                "Translating a dialogue": [
                    ("h2", "Writing rules"),
                    ("bullet", "Enter = line break inside the same dialogue box."),
                    ("bullet", "Press Enter twice: the editor automatically inserts a visible {05 0C} command on its own line, creating a new dialogue box. The New box button does the same."),
                    ("bullet", "The physical 05 00 terminator at the end of a string is hidden and preserved automatically."),
                    ("bullet", "Aim for no more than 30 bytes per line and 3 lines per box. The Validation panel reports the exact count."),
                    ("image", "editing.png"),
                    ("p", "Status values let you separate untranslated, translated and reviewed entries without changing the game itself."),
                ],
                "Commands and placeholders": [
                    ("p", "Colored items are game controls, not ordinary text. Hover a command for a quick explanation; the popup follows the pointer while you stay over the command."),
                    ("code", "{05 0C}   New dialogue box\n[NOME]    FF 24 — player name placeholder\n[FF 2A]   FF 2A — meaning still unconfirmed\n♥         81 CD visual mapping\n♪         81 F4 visual mapping\n…         81 63 visual mapping\n[ICON 99] 81 99 — exact visual meaning treated cautiously"),
                    ("image", "commands.png"),
                    ("p", "If a control is not fully understood, the tool describes that uncertainty instead of guessing. Its raw bytes are still preserved."),
                ],
                "Game preview": [
                    ("p", "The preview is built from HMDS dialogue assets, portraits and the game font. Every {05 0C} becomes another stacked box."),
                    ("bullet", "Use the mouse wheel or the visible scrollbar to move through multiple boxes."),
                    ("bullet", "The character name follows the selected speaker side when that context is available."),
                    ("bullet", "View > Options lets you change preview scale and the text shadow."),
                    ("image", "preview.png"),
                ],
                "Export / Import": [
                    ("p", "Exports are the intended way to split work between several translators without waiting for one person to finish the whole ROM."),
                    ("num", "Translator A exports one dialogue as .ctsdialogue, the full character as .ctspack, or uses CSV for spreadsheets."),
                    ("num", "Translator B can work on another character at the same time from the same ROM base."),
                    ("num", "You can also export only the currently open dialogue with Export > Export selected dialogue."),
                    ("num", "When a file comes back, use Export > Import package or Import CSV. A comparison window lists every entry side by side before anything is changed."),
                    ("num", "Use Select all or mark only the dialogues you want. Compatible selected rows replace the project value; equal rows are shown as equal and incompatible rows cannot be selected."),
                    ("num", "The tool checks Script ID, STR ID and a SHA-256 hash of the original text before applying the entry."),
                    ("image", "export.png"),
                ],
                "Projects and progress": [
                    ("p", "A .ctsproj file stores the current translation session, statuses and edits. Save it regularly with Project > Save project."),
                    ("p", "The percentage beside each character is a project aid only. It does not alter the ROM."),
                ],
                "Validate and build": [
                    ("num", "Use Project > Validate project. Fix errors first; review warnings such as lines above 30 bytes."),
                    ("num", "Use File > Build new ROM and choose a new filename."),
                    ("num", "The tool rebuilds the STR bank and pointer table, writes a new ROM, then reopens the generated ROM for a structural check."),
                    ("p", "Keep a clean copy of your base ROM outside the project folder."),
                ],
                "Options and language": [
                    ("p", "View > Options contains visual preferences. Language changes require restarting the tool."),
                    ("bullet", "Language: Portuguese (Brazil), English or Spanish."),
                    ("bullet", "Preview scale, thumbnail size, scroll speed and layout preset."),
                    ("bullet", "Text shadow: enable/disable it, choose its color, and set horizontal/vertical position in pixels."),
                    ("bullet", "Command highlight palette and progress/thumbnail visibility."),
                    ("image", "options.png"),
                ],
                "Technical mode": [
                    ("p", "View > Technical mode opens a full-size Technical inspector tab for ROM hackers and experienced translators. The Preview remains available in the neighboring tab."),
                    ("bullet", "The inspector starts with a legend: S = Script ID, STR = string slot, F = Face ID, E = expression, and left/right = the portrait side on screen."),
                    ("bullet", "Shows Script ID, STR index, ScriptS pointer entry/value, ROM offsets, record size and RIFF declared/effective length."),
                    ("bullet", "Shows CODE/JUMP/STR chunk offsets, string slot ROM offset, character/Face/expression context and whether a legacy RIFF anomaly was tolerated."),
                    ("bullet", "Use Copy to place the complete technical report on the clipboard. None of these fields must be edited for normal translation work."),
                    ("image", "technical.png"),
                ],
                "Compatibility and safety": [
                    ("p", "Current public profile: normal Harvest Moon DS PAL family (ABCP). HMDS Cute is intentionally disabled for now."),
                    ("p", "Older HMDS translation builds are accepted when their ScriptS remains structurally compatible. The parser tolerates the known short-RIFF case discovered during this project."),
                    ("p", "Unknown controls are not renamed as confirmed functions. FF 2A, for example, stays a neutral placeholder until its meaning is proven."),
                ],
                "Shortcuts": [("code", "Ctrl+O  Open ROM\nCtrl+S  Save project\nCtrl+G  Build ROM\nF1      Help contents\n")],
            }
        if lang == "es":
            return {
                "Primeros pasos": [
                    ("p", "La herramienta está pensada para traducir diálogos de Harvest Moon DS normal sin obligar al traductor a trabajar con offsets, punteros o hexadecimal."),
                    ("num", "Abre Archivo > Abrir ROM y selecciona una ROM compatible."),
                    ("num", "Elige un personaje a la izquierda y después un diálogo en la lista central."),
                    ("num", "Traduce abajo, revisa la vista previa y la validación de 30 bytes."),
                    ("num", "Guarda el diálogo, guarda el proyecto, valida y genera una nueva ROM."),
                    ("image", "overview.png"),
                ],
                "Traducir un diálogo": [
                    ("bullet", "Enter = salto de línea dentro de la misma caja."),
                    ("bullet", "Pulsa Enter dos veces: el editor inserta automáticamente un comando visible {05 0C} en su propia línea y crea una nueva caja. El botón Nueva caja hace lo mismo."),
                    ("bullet", "El terminador físico 05 00 final permanece oculto y se conserva automáticamente."),
                    ("bullet", "Intenta mantener 30 bytes por línea y 3 líneas por caja."),
                    ("image", "editing.png"),
                ],
                "Comandos y marcadores": [
                    ("p", "Los elementos coloreados son controles del juego. Pasa el cursor sobre uno para ver una explicación rápida; la ayuda sigue al puntero."),
                    ("code", "{05 0C}   Nueva caja\n[NOME]    FF 24 — nombre del jugador\n[FF 2A]   significado no confirmado\n♥         81 CD\n♪         81 F4\n…         81 63\n[ICONO 99] 81 99"),
                    ("image", "commands.png"),
                ],
                "Vista previa": [
                    ("p", "La vista previa usa recursos, retratos y la fuente de HMDS. Las cajas se apilan verticalmente."),
                    ("bullet", "Usa la rueda del ratón o la barra de desplazamiento."),
                    ("bullet", "En Opciones puedes cambiar la escala y la sombra del texto."),
                    ("image", "preview.png"),
                ],
                "Exportar / Importar": [
                    ("num", "Exporta un diálogo como .ctsdialogue, el personaje completo como .ctspack, o usa CSV para hojas de cálculo."),
                    ("num", "Otra persona puede trabajar en otro personaje con la misma ROM base."),
                    ("num", "También puedes exportar únicamente el diálogo abierto actualmente."),
                    ("num", "Al importar, aparece una ventana de comparación con el valor actual y el importado. Puedes seleccionar una, varias o todas las filas antes de modificar el proyecto."),
                    ("num", "La herramienta verifica Script, STR y hash SHA-256 del original. Las filas incompatibles no se pueden seleccionar."),
                    ("image", "export.png"),
                ],
                "Proyectos y progreso": [("p", "El archivo .ctsproj guarda ediciones y estados. El porcentaje por personaje sirve para organizar el trabajo.")],
                "Validar y generar": [
                    ("num", "Usa Proyecto > Validar proyecto."),
                    ("num", "Corrige errores y revisa advertencias."),
                    ("num", "Usa Archivo > Generar nueva ROM. La ROM original no se sobrescribe."),
                ],
                "Opciones e idioma": [
                    ("p", "El idioma se aplica al reiniciar."),
                    ("bullet", "Português (Brasil), English y Español."),
                    ("bullet", "Escala, miniaturas, velocidad y diseño."),
                    ("bullet", "Sombra: activar/desactivar, color y posición X/Y."),
                    ("image", "options.png"),
                ],
                "Modo técnico": [
                    ("p", "Ver > Modo técnico abre una pestaña de Inspector técnico a tamaño completo. La Vista previa permanece disponible en la pestaña vecina."),
                    ("bullet", "Incluye una leyenda: S = Script ID, STR = cadena, F = Face ID, E = expresión, left/right = lado del retrato en pantalla."),
                    ("bullet", "Muestra Script/STR, entrada y valor del puntero, offsets en ScriptS/ROM, RIFF, CODE/JUMP/STR y contexto de Face/expresión."),
                    ("bullet", "El botón Copiar envía el informe técnico completo al portapapeles. No es necesario usarlo para una traducción normal."),
                    ("image", "technical.png"),
                ],
                "Compatibilidad y seguridad": [("p", "Perfil actual: Harvest Moon DS normal PAL / ABCP. HMDS Cute todavía está deshabilitado. FF 2A sigue siendo un marcador neutral porque su función no está confirmada.")],
                "Atajos": [("code", "Ctrl+O  Abrir ROM\nCtrl+S  Guardar proyecto\nCtrl+G  Generar ROM\nF1      Ayuda\n")],
            }
        return {
            "Primeiros passos": [
                ("p", "Esta ferramenta foi feita para traduzir as falas do Harvest Moon DS normal sem obrigar o tradutor a entender offsets, tabelas de ponteiros ou hexadecimal."),
                ("num", "Abra Arquivo > Abrir ROM e escolha uma ROM compatível do HMDS normal."),
                ("num", "Escolha um personagem na coluna da esquerda. A miniatura do rosto ajuda a confirmar com quem você está trabalhando."),
                ("num", "Escolha uma fala na lista central. O original aparece à esquerda e a tradução editável à direita."),
                ("num", "Traduza, confira o preview do jogo e a validação de bytes."),
                ("num", "Clique em Salvar fala. Salve também o projeto .ctsproj para continuar depois."),
                ("num", "Quando quiser testar: Projeto > Validar projeto e depois Arquivo > Gerar nova ROM."),
                ("image", "overview.png"),
                ("p", "A ROM original nunca é sobrescrita pela ferramenta."),
            ],
            "Traduzindo uma fala": [
                ("h2", "Como escrever"),
                ("bullet", "Enter cria uma quebra de linha dentro da mesma caixa."),
                ("bullet", "Dois Enter seguidos criam automaticamente uma nova caixa: o editor transforma a linha vazia em um {05 0C} visível e colorido. O botão Nova caixa faz a mesma coisa."),
                ("bullet", "O 05 do terminador físico 05 00 no final da string fica escondido e é preservado automaticamente."),
                ("bullet", "Procure manter no máximo 30 bytes por linha e 3 linhas por caixa. O painel Validação mostra a contagem exata."),
                ("image", "editing.png"),
                ("p", "Use os estados Não traduzida, Traduzida, Para revisar e Revisada para organizar o projeto sem alterar o funcionamento do jogo."),
            ],
            "Comandos e placeholders": [
                ("p", "Tudo que aparece colorido é um controle/placeholder, não texto comum. Passe o mouse sobre o comando para ver uma explicação rápida; o popup acompanha o ponteiro enquanto você estiver sobre o comando."),
                ("code", "{05 0C}     Nova caixa de fala\n[NOME]      FF 24 — nome do jogador\n[FF 2A]     controle ainda sem significado confirmado\n♥           81 CD — mapeamento visual atual\n♪           81 F4 — mapeamento visual atual\n…           81 63 — mapeamento visual atual\n[ÍCONE 99]  81 99 — significado visual tratado com cautela"),
                ("image", "commands.png"),
                ("p", "Quando o significado de um controle não está confirmado, a ferramenta informa isso em vez de inventar uma função. O valor bruto continua sendo preservado."),
            ],
            "Preview do jogo": [
                ("p", "O preview usa a caixa de diálogo, os retratos e a fonte do HMDS. Cada {05 0C} vira outra caixa empilhada abaixo da anterior."),
                ("bullet", "Use a roda do mouse ou a barra de rolagem para ver caixas adicionais."),
                ("bullet", "O nome acompanha o lado do personagem selecionado quando esse contexto está disponível."),
                ("bullet", "Em Exibir > Opções você pode mudar a escala e configurar a sombra da fonte."),
                ("image", "preview.png"),
            ],
            "Exportar / Importar": [
                ("p", "A exportação foi feita para várias pessoas conseguirem trabalhar ao mesmo tempo em partes diferentes da tradução."),
                ("num", "Uma pessoa pode exportar uma fala como .ctsdialogue, várias falas ou o personagem inteiro como .ctspack, ou usar CSV para planilhas."),
                ("num", "Outra pessoa pode traduzir outro personagem ao mesmo tempo, usando a mesma ROM-base."),
                ("num", "Para uma única fala, use Exportar > Exportar fala atual. Para enviar tudo do personagem, use Exportar > Exportar personagem completo. Para planilhas, use Exportar personagem (CSV)."),
                ("num", "Quando receber um arquivo, use Exportar > Importar. O seletor mostra todos os formatos suportados e, antes de modificar o projeto, abre uma comparação com o valor atual e o importado lado a lado."),
                ("num", "Marque apenas as falas que deseja trazer, ou use Selecionar tudo. Linhas iguais aparecem como Iguais; diferenças ficam destacadas; entradas incompatíveis não podem ser marcadas."),
                ("num", "Cada entrada leva Script ID, STR ID e SHA-256 do texto original. Isso ajuda a impedir que uma tradução seja aplicada silenciosamente sobre a base errada."),
                ("image", "export.png"),
            ],
            "Projetos e progresso": [
                ("p", "O .ctsproj guarda as edições e os estados da tradução. Use Projeto > Salvar projeto com frequência."),
                ("p", "A porcentagem ao lado de cada personagem serve apenas para acompanhar o trabalho. Ela não é gravada como conteúdo do jogo."),
            ],
            "Validar e gerar ROM": [
                ("num", "Use Projeto > Validar projeto antes de gerar a ROM."),
                ("num", "Corrija erros. Avisos como linha acima de 30 bytes devem ser revisados, mesmo quando a ferramenta permite continuar."),
                ("num", "Use Arquivo > Gerar nova ROM e escolha outro nome de arquivo."),
                ("num", "A ferramenta reconstrói o banco STR e a tabela de ponteiros e depois reabre a ROM gerada para uma checagem estrutural."),
                ("p", "Mantenha sempre uma cópia limpa da ROM-base fora da pasta de trabalho."),
            ],
            "Opções e idiomas": [
                ("p", "Abra Exibir > Opções. A troca de idioma só entra em vigor depois que a ferramenta for reiniciada."),
                ("bullet", "Idiomas da interface: Português (Brasil), English e Español."),
                ("bullet", "Escala do preview, tamanho das miniaturas, velocidade de rolagem e layout padrão."),
                ("bullet", "Sombra da fonte: ativar/desativar, escolher a cor e definir posição horizontal/vertical em pixels."),
                ("bullet", "Paleta de destaque dos comandos e exibição de progresso/miniaturas."),
                ("image", "options.png"),
            ],
            "Modo técnico": [
                ("p", "Exibir > Modo técnico abre uma aba de Inspetor técnico em tamanho completo. O Preview continua disponível na aba ao lado."),
                ("bullet", "O relatório começa com uma legenda: S = Script ID; STR = posição da string; F = Face ID; E = expressão; left/right = lado da tela onde o retrato aparece."),
                ("bullet", "Mostra Script ID, STR, entrada/valor do ponteiro, offsets relativos ao ScriptS e offsets absolutos na ROM."),
                ("bullet", "Mostra tamanho do ScriptRecord, RIFF declarado/efetivo, chunks CODE/JUMP/STR e offset físico da string selecionada."),
                ("bullet", "Também mostra Character ID, Face, expressão e lado quando esse contexto está indexado."),
                ("bullet", "O botão Copiar envia o relatório técnico inteiro para a área de transferência. Nada disso precisa ser alterado para traduzir normalmente."),
                ("image", "technical.png"),
            ],
            "Compatibilidade e segurança": [
                ("p", "Perfil público atual: Harvest Moon DS normal da família PAL / ABCP. HMDS Cute continua desativado por enquanto."),
                ("p", "Versões antigas de tradução podem abrir quando o ScriptS continua estruturalmente compatível. O parser já tolera o caso conhecido de RIFF curto encontrado durante esta pesquisa."),
                ("p", "Controles desconhecidos não recebem significado inventado. O FF 2A, por exemplo, fica como placeholder neutro até a função ser confirmada."),
            ],
            "Atalhos": [("code", "Ctrl+O  Abrir ROM\nCtrl+S  Salvar projeto\nCtrl+G  Gerar ROM\nF1      Conteúdo da ajuda\n")],
        }

    def show_help_contents(self) -> None:
        win = tk.Toplevel(self); win.title(self.tr("help.title")); win.geometry("1000x690"); win.transient(self)
        pan = ttk.Panedwindow(win, orient="horizontal"); pan.pack(fill="both", expand=True, padx=8, pady=8)
        left = ttk.Frame(pan); right = ttk.Frame(pan); pan.add(left, weight=1); pan.add(right, weight=4)
        topics = ttk.Treeview(left, show="tree", selectmode="browse")
        tscroll = ttk.Scrollbar(left, orient="vertical", command=topics.yview); topics.configure(yscrollcommand=tscroll.set)
        topics.pack(side="left", fill="both", expand=True); tscroll.pack(side="right", fill="y")
        text = tk.Text(right, wrap="word", font=("Segoe UI", 10), padx=16, pady=12, cursor="arrow")
        rscroll = ttk.Scrollbar(right, orient="vertical", command=text.yview); text.configure(yscrollcommand=rscroll.set)
        text.pack(side="left", fill="both", expand=True); rscroll.pack(side="right", fill="y")
        text.tag_configure("h1", font=("Segoe UI", 16, "bold"), spacing3=9)
        text.tag_configure("h2", font=("Segoe UI", 11, "bold"), spacing1=8, spacing3=4)
        text.tag_configure("p", spacing3=8)
        text.tag_configure("bullet", lmargin1=20, lmargin2=36, spacing3=4)
        text.tag_configure("num", lmargin1=20, lmargin2=40, spacing3=5)
        text.tag_configure("code", font=("Consolas", 10), background="#f3f4f6", lmargin1=16, lmargin2=16, spacing1=5, spacing3=8)
        content = self._help_topics(); self._help_image_refs = []
        help_dir = Path(__file__).with_name("data") / "help"

        def render_image(name: str):
            path = help_dir / name
            if not path.exists() or Image is None or ImageTk is None:
                return
            try:
                im = Image.open(path).convert("RGBA")
                max_w = 700
                if im.width > max_w:
                    ratio = max_w / im.width
                    im = im.resize((max_w, max(1, round(im.height * ratio))), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(im); self._help_image_refs.append(photo)
                text.image_create("end", image=photo); text.insert("end", "\n\n")
            except Exception:
                pass

        def show_topic(_evt=None):
            sel = topics.selection()
            if not sel: return
            title = topics.item(sel[0], "text")
            text.configure(state="normal"); text.delete("1.0", "end")
            text.insert("end", title + "\n", "h1")
            num_i = 1
            for kind, value in content.get(title, []):
                if kind == "p": text.insert("end", value + "\n", "p")
                elif kind == "h2": text.insert("end", value + "\n", "h2")
                elif kind == "bullet": text.insert("end", "• " + value + "\n", "bullet")
                elif kind == "num":
                    text.insert("end", f"{num_i}. {value}\n", "num"); num_i += 1
                elif kind == "code": text.insert("end", value + "\n", "code")
                elif kind == "image": render_image(value)
            text.configure(state="disabled"); text.yview_moveto(0)

        for n, title in enumerate(content):
            topics.insert("", "end", iid=f"topic{n}", text=title)
        topics.bind("<<TreeviewSelect>>", show_topic)
        first = topics.get_children()[0]; topics.selection_set(first); show_topic()

    def show_about(self) -> None:
        win = tk.Toplevel(self); win.title(self.tr("about.title")); win.transient(self); win.resizable(False, False)
        body = ttk.Frame(win, padding=16); body.pack(fill="both", expand=True)
        icon_path = Path(__file__).with_name("data") / "app_icon.png"
        if icon_path.exists():
            try:
                img = tk.PhotoImage(file=str(icon_path)); self._about_icon_ref = img; ttk.Label(body, image=img).grid(row=0, column=0, rowspan=5, sticky="n", padx=(0,14))
            except Exception: pass
        ttk.Label(body, text="HMDS Character Translation Studio", font=("Segoe UI",13,"bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(body, text=f"Version {__version__}").grid(row=1, column=1, sticky="w", pady=(2,0))
        ttk.Label(body, text="© 2026 Atm").grid(row=2, column=1, sticky="w", pady=(7,0))
        ttk.Label(body, text="Translation utility for Harvest Moon DS (Nintendo DS).\nCurrent profile: normal HMDS PAL / ABCP.\nROM files are not included with this program.", justify="left", wraplength=360).grid(row=3, column=1, sticky="w", pady=(10,12))
        ttk.Button(body, text="OK", width=10, command=win.destroy).grid(row=4, column=1, sticky="e")

    def _on_close(self) -> None:
        if self._confirm_discard(): self.destroy()


def main() -> None:
    CTSApp().mainloop()
