"""Tkinter control UI for the ED Colony Scout reachability/scoring pipeline."""

from __future__ import annotations

import json
import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

from gui import config as config_module
from gui import db_lookup, pipeline, system_detail, treeview_utils
from gui import rubric as rubric_module
from gui.treeview_utils import ColumnSpec
from gui.worker import DONE_SENTINEL, Worker, describe_exit

MAX_LOG_LINES = 5000
POLL_INTERVAL_MS = 100
SHUTDOWN_POLL_MS = 200
SHUTDOWN_TIMEOUT_MS = 20000
SEARCH_DEBOUNCE_MS = 200

CANDIDATES_COLUMNS = [
    ColumnSpec("rank", "Rank", 50, True, "e"),
    ColumnSpec("colonization_score", "Score", 80, True, "e"),
    ColumnSpec("system_name", "System", 220, False, "w"),
    ColumnSpec("key_scoring_factors", "Key Scoring Factors", 170, False, "w"),
    ColumnSpec("hop_count", "Hops", 55, True, "e"),
    ColumnSpec("distance_ly", "Dist (ly)", 80, True, "e"),
    ColumnSpec("total_body_count", "Bodies", 60, True, "e"),
    ColumnSpec("elw_count", "ELW", 50, True, "e"),
    ColumnSpec("ww_count", "WW", 50, True, "e"),
    ColumnSpec("hmct_terraformable_count", "HMC-T", 60, True, "e"),
    ColumnSpec("metallic_ring_ratio", "Metallic Ratio", 100, True, "e"),
    ColumnSpec("icy_ring_ratio", "Icy Ratio", 80, True, "e"),
    ColumnSpec("x", "X", 70, True, "e"),
    ColumnSpec("y", "Y", 70, True, "e"),
    ColumnSpec("z", "Z", 70, True, "e"),
]

DENSEST_COLUMNS = [
    ColumnSpec("rank", "Rank", 50, True, "e"),
    ColumnSpec("system_name", "System", 220, False, "w"),
    ColumnSpec("distance_ly", "Dist (ly)", 80, True, "e"),
    ColumnSpec("hop_count", "Hops", 55, True, "e"),
    ColumnSpec("total_body_count", "Bodies", 60, True, "e"),
    ColumnSpec("primary_stellar_type", "Primary Star", 160, False, "w"),
    ColumnSpec("additional_stellar_count", "Extra Stars", 90, True, "e"),
    ColumnSpec("planetary_body_count", "Planets", 70, True, "e"),
    ColumnSpec("planetary_body_breakdown", "Planet Breakdown", 300, False, "w"),
    ColumnSpec("metallic_icy_rings", "Metallic/Icy Rings", 350, False, "w"),
]

PF_PATH_COLUMNS = [
    ColumnSpec("hop", "Hop", 50, True, "e"),
    ColumnSpec("system_name", "System", 220, False, "w"),
    ColumnSpec("leg_distance_ly", "Leg Dist (ly)", 100, True, "e"),
    ColumnSpec("occupied", "Occupied?", 90, False, "center"),
    ColumnSpec("x", "X", 70, True, "e"),
    ColumnSpec("y", "Y", 70, True, "e"),
    ColumnSpec("z", "Z", 70, True, "e"),
]

PF_CANDIDATES_COLUMNS = [
    ColumnSpec("system_name", "System", 220, False, "w"),
    ColumnSpec("straight_line_ly", "Straight-line (ly)", 130, True, "e"),
    ColumnSpec("hop_count", "Hops", 90, True, "e"),
    ColumnSpec("within_cap", "Within Cap?", 90, False, "center"),
]

ORIGIN_HELP_TEXT = (
    "Origin System defines the starting point of the reachability search.\n\n"
    "This app finds every system reachable via a CHAIN of jumps, where each "
    "hop must land within Max Jump (ly) of the PREVIOUS system in the chain "
    "-- not a straight-line radius around the origin. This mirrors how "
    "colonization bridging actually works in-game: a chain of short hops "
    "can reach much farther than a single long jump.\n\n"
    "The origin is normally your own existing colony (or the system you "
    "plan to colonize from). Results are every system reachable within Max "
    "Hops jumps of it, ranked by colonization_score."
)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"ED Colony Scout {config_module.APP_VERSION}")
        self.root.geometry("1100x820")

        self.worker = Worker()
        self.config: dict[str, Any] = config_module.load_config()
        self._pending_result_paths: Optional[tuple[Path, Path]] = None
        self._pending_extracted_db_paths: Optional[list[str]] = None
        self._pending_path_result: Optional[dict] = None
        self._pending_path_mode: Optional[str] = None
        self._search_after_id: Optional[str] = None
        self._pf_origin_search_after_id: Optional[str] = None
        self._pf_target_search_after_id: Optional[str] = None  # shared: sp/rc target combos are never both mapped at once
        # self._last_auto_sector_label is set in _build_run_tab, once the
        # persisted db_paths are known (see the comment there for why).

        # Shared status/log panel first -- Tk `pack` allocates from the
        # outside in, so the bottom panel must claim its edge before the
        # Notebook's expand=True consumes all remaining space.
        self.shared_panel = ttk.Frame(root)
        self._build_shared_status_panel(self.shared_panel)
        self.shared_panel.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(side="top", fill="both", expand=True, padx=8, pady=8)

        self.prep_tab = ttk.Frame(self.notebook)
        self.run_tab = ttk.Frame(self.notebook)
        self.path_finder_tab = ttk.Frame(self.notebook)
        self.candidates_tab = ttk.Frame(self.notebook)
        self.dense_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.prep_tab, text="Prep Sectors")
        self.notebook.add(self.run_tab, text="Run")
        self.notebook.add(self.path_finder_tab, text="Path Finder")
        self.notebook.add(self.candidates_tab, text="Candidates")
        self.notebook.add(self.dense_tab, text="Densest Systems")
        self.notebook.add(self.settings_tab, text="Settings")

        self._build_prep_tab(self.prep_tab)
        self._build_run_tab(self.run_tab)
        self._build_path_finder_tab(self.path_finder_tab)
        self.candidates_tree = treeview_utils.build_tree(self.candidates_tab, CANDIDATES_COLUMNS)
        self.candidates_tree.bind(
            "<Double-1>", lambda e: self._on_system_row_double_click(e, self.candidates_tree)
        )
        self.dense_tree = treeview_utils.build_tree(self.dense_tab, DENSEST_COLUMNS)
        self.dense_tree.bind("<Double-1>", lambda e: self._on_system_row_double_click(e, self.dense_tree))
        self._build_settings_tab(self.settings_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_INTERVAL_MS, self._poll_log_queue)

    # ------------------------------------------------------------------
    # Shared status/log panel
    # ------------------------------------------------------------------
    def _build_shared_status_panel(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=6, pady=(6, 0))
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self._on_cancel, state="disabled")
        self.cancel_button.pack(side="left")
        self.status_label = ttk.Label(controls, text="Idle")
        self.status_label.pack(side="left", padx=12)

        log_frame = ttk.LabelFrame(parent, text="Log")
        log_frame.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    # ------------------------------------------------------------------
    # Prep Sectors tab
    # ------------------------------------------------------------------
    def _build_prep_tab(self, parent: ttk.Frame) -> None:
        loc_frame = ttk.LabelFrame(parent, text="Data Locations")
        loc_frame.pack(fill="x", padx=6, pady=6)

        self.galaxy_dump_path_var = tk.StringVar(value=self.config.get("galaxy_dump_path", ""))
        self.sector_library_dir_var = tk.StringVar(value=self.config.get("sector_library_dir", ""))

        grid = ttk.Frame(loc_frame)
        grid.pack(fill="x", padx=6, pady=6)
        ttk.Label(grid, text="Galaxy dump path:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.galaxy_dump_path_var, width=55).grid(row=0, column=1, sticky="we", pady=4)
        ttk.Button(grid, text="Browse...", command=self._browse_galaxy_dump).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(grid, text="Sector library directory:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.sector_library_dir_var, width=55).grid(row=1, column=1, sticky="we", pady=4)
        ttk.Button(grid, text="Browse...", command=self._browse_sector_library_dir).grid(row=1, column=2, padx=(6, 0))
        grid.columnconfigure(1, weight=1)

        sector_frame = ttk.LabelFrame(parent, text="Sector Name Prefixes")
        sector_frame.pack(fill="x", padx=6, pady=6)
        list_row = ttk.Frame(sector_frame)
        list_row.pack(fill="x", padx=6, pady=6)
        self.sector_prefix_listbox = tk.Listbox(list_row, height=6, selectmode="extended")
        self.sector_prefix_listbox.pack(side="left", fill="both", expand=True)
        for prefix in self.config.get("sector_prefixes", []):
            self.sector_prefix_listbox.insert("end", prefix)

        list_buttons = ttk.Frame(list_row)
        list_buttons.pack(side="left", padx=6)
        ttk.Button(list_buttons, text="Remove selected", command=self._remove_selected_sector_prefixes).pack(
            fill="x", pady=2
        )
        ttk.Button(list_buttons, text="Clear all", command=self._clear_sector_prefixes).pack(fill="x", pady=2)

        entry_row = ttk.Frame(sector_frame)
        entry_row.pack(fill="x", padx=6, pady=(0, 6))
        self.sector_prefix_entry = ttk.Entry(entry_row)
        self.sector_prefix_entry.pack(side="left", fill="x", expand=True)
        self.sector_prefix_entry.bind("<Return>", lambda _e: self._add_sector_prefix())
        ttk.Button(entry_row, text="Add", command=self._add_sector_prefix).pack(side="left", padx=(6, 0))

        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=6, pady=6)
        self.extract_button = ttk.Button(controls, text="Extract", command=self._on_extract)
        self.extract_button.pack(side="left")

    def _browse_galaxy_dump(self) -> None:
        chosen = filedialog.askopenfilename(
            initialdir=str(Path(self.galaxy_dump_path_var.get() or ".").parent),
            filetypes=[("Galaxy dump", "*.json.gz *.json"), ("All files", "*.*")],
        )
        if chosen:
            self.galaxy_dump_path_var.set(chosen)

    def _browse_sector_library_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.sector_library_dir_var.get() or ".")
        if chosen:
            self.sector_library_dir_var.set(chosen)

    def _add_sector_prefix(self) -> None:
        value = self.sector_prefix_entry.get().strip()
        if not value:
            return
        existing = self.sector_prefix_listbox.get(0, "end")
        if value not in existing:
            self.sector_prefix_listbox.insert("end", value)
        self.sector_prefix_entry.delete(0, "end")

    def _remove_selected_sector_prefixes(self) -> None:
        for index in reversed(self.sector_prefix_listbox.curselection()):
            self.sector_prefix_listbox.delete(index)

    def _clear_sector_prefixes(self) -> None:
        self.sector_prefix_listbox.delete(0, "end")

    def _on_extract(self) -> None:
        if self.worker.is_running():
            messagebox.showinfo(
                "Job running", "Another job is already running. Wait for it to finish or cancel it first."
            )
            return
        prefixes = list(self.sector_prefix_listbox.get(0, "end"))
        galaxy_dump_path = self.galaxy_dump_path_var.get().strip()
        sector_library_dir = self.sector_library_dir_var.get().strip()

        if not galaxy_dump_path or not Path(galaxy_dump_path).exists():
            messagebox.showerror(
                "Galaxy dump not found",
                f"No galaxy dump file found at:\n\n{galaxy_dump_path or '(not set)'}",
            )
            return
        if not prefixes:
            messagebox.showwarning("No sector prefixes", "Add at least one sector prefix before extracting.")
            return
        if not sector_library_dir:
            messagebox.showwarning(
                "Sector library directory required", "Set a sector library directory before extracting."
            )
            return
        Path(sector_library_dir).mkdir(parents=True, exist_ok=True)

        self.config["galaxy_dump_path"] = galaxy_dump_path
        self.config["sector_prefixes"] = prefixes
        self.config["sector_library_dir"] = sector_library_dir
        config_module.save_config(self.config)

        self._append_log(f"--- Starting extraction: {', '.join(prefixes)} ---")
        self._pending_extracted_db_paths = None
        started = self.worker.start(pipeline.run_sector_extraction_pipeline, self.config)
        if not started:
            return
        self._set_controls_running(True)

    # ------------------------------------------------------------------
    # Run tab
    # ------------------------------------------------------------------
    def _build_run_tab(self, parent: ttk.Frame) -> None:
        # -- Sector Database(s) --
        db_frame = ttk.LabelFrame(parent, text="Sector Database(s)")
        db_frame.pack(fill="x", padx=6, pady=6)

        self.sector_label_var = tk.StringVar(value=self.config.get("sector_label", ""))

        list_row = ttk.Frame(db_frame)
        list_row.pack(fill="x", padx=6, pady=6)
        self.db_listbox = tk.Listbox(list_row, height=5, selectmode="extended")
        self.db_listbox.pack(side="left", fill="both", expand=True)
        initial_db_paths = list(self.config.get("db_paths", []))
        for p in initial_db_paths:
            self.db_listbox.insert("end", p)
        # If the persisted sector_label exactly matches what auto-derivation
        # would produce from the persisted db_paths, treat it as still-auto
        # (not a manual edit) so a returning user who adds/removes DBs keeps
        # getting label updates -- otherwise _last_auto_sector_label's
        # per-session "" default would make ANY persisted non-empty label
        # look manually edited forever, even one that was never touched.
        self._last_auto_sector_label = db_lookup.derive_sector_label(initial_db_paths)

        list_buttons = ttk.Frame(list_row)
        list_buttons.pack(side="left", padx=6)
        ttk.Button(list_buttons, text="Add...", command=self._add_db_paths).pack(fill="x", pady=2)
        ttk.Button(list_buttons, text="Remove selected", command=self._remove_selected_db_paths).pack(
            fill="x", pady=2
        )
        ttk.Button(list_buttons, text="Clear all", command=self._clear_db_paths).pack(fill="x", pady=2)

        ttk.Label(
            db_frame,
            text="Routing through an adjacent sector's systems can shorten hop distance "
                 "or make an otherwise-unreachable candidate reachable -- select every "
                 "sector DB you want included in the reachability graph.",
            wraplength=560, justify="left", foreground="#666666",
        ).pack(anchor="w", padx=6, pady=(0, 4))

        label_row = ttk.Frame(db_frame)
        label_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(label_row, text="Sector label:").pack(side="left")
        ttk.Entry(label_row, textvariable=self.sector_label_var, width=40).pack(side="left", padx=(6, 0))

        self._maybe_autofill_sector_label()

        # -- Origin System --
        origin_frame = ttk.LabelFrame(parent, text="Origin System")
        origin_frame.pack(fill="x", padx=6, pady=6)
        self.origin_system_var = tk.StringVar(value=self.config.get("origin_system", ""))

        origin_row = ttk.Frame(origin_frame)
        origin_row.pack(fill="x", padx=6, pady=6)
        ttk.Label(origin_row, text="System name:").pack(side="left")
        self.origin_combo = ttk.Combobox(origin_row, textvariable=self.origin_system_var, width=45)
        self.origin_combo.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.origin_combo.bind(
            "<KeyRelease>",
            lambda _e: self._schedule_system_search(self.origin_combo, self.origin_system_var, "_search_after_id"),
        )
        ttk.Button(origin_row, text="?", width=2, command=self._show_origin_help).pack(side="left", padx=(6, 0))
        ttk.Label(
            origin_frame,
            text="Type to search systems across all selected DBs (results appear in the dropdown).",
            foreground="#666666",
        ).pack(anchor="w", padx=6, pady=(0, 6))

        # -- Parameters --
        params_frame = ttk.LabelFrame(parent, text="Parameters")
        params_frame.pack(fill="x", padx=6, pady=6)

        self.max_jump_ly_var = tk.StringVar(value=str(self.config.get("max_jump_ly", 15.0)))
        self.max_hops_var = tk.StringVar(value=str(self.config.get("max_hops", 8)))
        self.dense_top_n_var = tk.StringVar(value=str(self.config.get("dense_top_n", 20)))

        grid = ttk.Frame(params_frame)
        grid.pack(fill="x", padx=6, pady=6)
        ttk.Label(grid, text="Max jump (ly):").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(grid, textvariable=self.max_jump_ly_var, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(grid, text="Max hops:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(grid, textvariable=self.max_hops_var, width=8).grid(row=1, column=1, sticky="w")
        ttk.Label(grid, text="Densest-systems top N:").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(grid, textvariable=self.dense_top_n_var, width=8).grid(row=2, column=1, sticky="w")

        # -- Scoring --
        scoring_frame = ttk.LabelFrame(parent, text="Scoring")
        scoring_frame.pack(fill="x", padx=6, pady=6)
        self.scoring_rubric = rubric_module.rubric_from_dict(self.config.get("scoring_rubric") or {})
        scoring_row = ttk.Frame(scoring_frame)
        scoring_row.pack(fill="x", padx=6, pady=6)
        ttk.Button(scoring_row, text="Customize Scoring...", command=self._open_rubric_dialog).pack(side="left")
        ttk.Button(scoring_row, text="?", width=2, command=self._show_rubric_help).pack(side="left", padx=(6, 0))
        self.rubric_status_label = ttk.Label(scoring_row, text=self._rubric_status_text())
        self.rubric_status_label.pack(side="left", padx=12)

        # -- Run (Cancel/Status/Log live in the shared panel below the Notebook) --
        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=6, pady=6)
        self.run_button = ttk.Button(controls, text="Run", command=self._on_run)
        self.run_button.pack(side="left")

    def _add_db_paths(self) -> None:
        existing = list(self.db_listbox.get(0, "end"))
        initialdir = str(Path(existing[-1]).parent) if existing else str(db_lookup.DEFAULT_SECTOR_LIBRARY_DIR)
        chosen = filedialog.askopenfilenames(
            initialdir=initialdir,
            filetypes=[("Sector DB", "*.sqlite"), ("All files", "*.*")],
        )
        for path in chosen:
            if path not in existing:
                self.db_listbox.insert("end", path)
                existing.append(path)
        self._maybe_autofill_sector_label()

    def _remove_selected_db_paths(self) -> None:
        for index in reversed(self.db_listbox.curselection()):
            self.db_listbox.delete(index)
        self._maybe_autofill_sector_label()

    def _clear_db_paths(self) -> None:
        self.db_listbox.delete(0, "end")
        self._maybe_autofill_sector_label()

    def _maybe_autofill_sector_label(self) -> None:
        db_paths = list(self.db_listbox.get(0, "end"))
        if not db_paths:
            return
        current_label = self.sector_label_var.get().strip()
        if current_label and current_label != self._last_auto_sector_label:
            return  # user has manually edited it -- don't clobber
        derived = db_lookup.derive_sector_label(db_paths)
        self.sector_label_var.set(derived)
        self._last_auto_sector_label = derived

    def _schedule_system_search(self, combo: ttk.Combobox, var: tk.StringVar, after_attr: str) -> None:
        after_id = getattr(self, after_attr)
        if after_id is not None:
            self.root.after_cancel(after_id)
        setattr(self, after_attr, self.root.after(
            SEARCH_DEBOUNCE_MS, lambda: self._run_system_search(combo, var, after_attr)
        ))

    def _run_system_search(self, combo: ttk.Combobox, var: tk.StringVar, after_attr: str) -> None:
        setattr(self, after_attr, None)
        db_paths = list(self.db_listbox.get(0, "end"))
        matches = db_lookup.search_system_names(db_paths, var.get(), limit=50)
        combo["values"] = matches

    def _show_origin_help(self) -> None:
        messagebox.showinfo("Origin System", ORIGIN_HELP_TEXT)

    def _rubric_status_text(self) -> str:
        return "Using stock defaults" if self.scoring_rubric == rubric_module.STOCK_RUBRIC else "Customized"

    def _open_rubric_dialog(self) -> None:
        dialog = rubric_module.RubricDialog(self.root, self.scoring_rubric)
        self.root.wait_window(dialog)
        if dialog.result is not None:
            self.scoring_rubric = dialog.result
            self.rubric_status_label.configure(text=self._rubric_status_text())

    def _show_rubric_help(self) -> None:
        messagebox.showinfo("How Scoring Works", rubric_module.format_rubric_help(self.scoring_rubric))

    def _on_system_row_double_click(self, event: tk.Event, tree: ttk.Treeview) -> None:
        """Shared by every results tree (Candidates, Densest Systems, both
        Path Finder trees) since they all key a "system_name" column the
        same way -- double-clicking a row opens the read-only System Detail
        popup for that system."""
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        name = tree.set(item_id, "system_name")
        if not name:
            return
        db_paths = list(self.db_listbox.get(0, "end"))
        detail = db_lookup.get_system_detail(db_paths, name)
        if detail is None:
            messagebox.showinfo(
                "System not found",
                f"{name!r} was not found in the currently selected DB(s).\n\n"
                "This can happen if the DB selection changed after this result "
                "was generated.",
                parent=self.root,
            )
            return
        system_detail.SystemDetailDialog(self.root, detail)

    # ------------------------------------------------------------------
    # Path Finder tab
    # ------------------------------------------------------------------
    def _build_path_finder_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent, text="Uses the same sector DB selection as the Run tab.", foreground="#666666",
        ).pack(anchor="w", padx=6, pady=(6, 0))

        mode_row = ttk.Frame(parent)
        mode_row.pack(fill="x", padx=6, pady=6)
        self.pf_mode_var = tk.StringVar(value="shortest_path")
        ttk.Radiobutton(
            mode_row, text="Shortest Path", variable=self.pf_mode_var,
            value="shortest_path", command=self._refresh_pf_mode,
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            mode_row, text="Reachable?", variable=self.pf_mode_var,
            value="reachable", command=self._refresh_pf_mode,
        ).pack(side="left")

        # -- Shared Max Jump (ly): one field, used by both modes' graph-building --
        jump_row = ttk.Frame(parent)
        jump_row.pack(fill="x", padx=6, pady=(0, 6))
        self.pf_max_jump_ly_var = tk.StringVar(value=str(self.config.get("pf_max_jump_ly", 15.0)))
        ttk.Label(jump_row, text="Max jump (ly):").pack(side="left")
        ttk.Entry(jump_row, textvariable=self.pf_max_jump_ly_var, width=8).pack(side="left", padx=(6, 0))
        self._pf_anchor_row = jump_row  # pack(after=...) anchor for the toggled mode frames

        self.pf_origin_system_var = tk.StringVar(
            value=self.config.get("pf_origin_system") or self.config.get("origin_system", "")
        )
        self.pf_target_system_var = tk.StringVar(value=self.config.get("pf_target_system", ""))

        # -- Shortest Path frame --
        self.pf_shortest_frame = ttk.LabelFrame(parent, text="Shortest Path")

        sp_row1 = ttk.Frame(self.pf_shortest_frame)
        sp_row1.pack(fill="x", padx=6, pady=6)
        ttk.Label(sp_row1, text="Origin system:").pack(side="left")
        self.pf_origin_combo = ttk.Combobox(sp_row1, textvariable=self.pf_origin_system_var, width=40)
        self.pf_origin_combo.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.pf_origin_combo.bind(
            "<KeyRelease>",
            lambda _e: self._schedule_system_search(self.pf_origin_combo, self.pf_origin_system_var, "_pf_origin_search_after_id"),
        )

        sp_row2 = ttk.Frame(self.pf_shortest_frame)
        sp_row2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(sp_row2, text="Target system:").pack(side="left")
        self.pf_target_combo_sp = ttk.Combobox(sp_row2, textvariable=self.pf_target_system_var, width=40)
        self.pf_target_combo_sp.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.pf_target_combo_sp.bind(
            "<KeyRelease>",
            lambda _e: self._schedule_system_search(self.pf_target_combo_sp, self.pf_target_system_var, "_pf_target_search_after_id"),
        )

        self.find_path_button = ttk.Button(self.pf_shortest_frame, text="Find Path", command=self._on_find_path)
        self.find_path_button.pack(anchor="w", padx=6, pady=6)

        # -- Reachable? frame --
        self.pf_reachable_frame = ttk.LabelFrame(parent, text="Reachable?")

        rc_row1 = ttk.Frame(self.pf_reachable_frame)
        rc_row1.pack(fill="x", padx=6, pady=6)
        ttk.Label(rc_row1, text="Target system:").pack(side="left")
        self.pf_target_combo_rc = ttk.Combobox(rc_row1, textvariable=self.pf_target_system_var, width=40)
        self.pf_target_combo_rc.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.pf_target_combo_rc.bind(
            "<KeyRelease>",
            lambda _e: self._schedule_system_search(self.pf_target_combo_rc, self.pf_target_system_var, "_pf_target_search_after_id"),
        )

        rc_row2 = ttk.Frame(self.pf_reachable_frame)
        rc_row2.pack(fill="x", padx=6, pady=(0, 6))
        self.pf_any_hops_var = tk.BooleanVar(value=bool(self.config.get("pf_any_hops", True)))
        self.pf_max_hops_var = tk.StringVar(value=str(self.config.get("pf_max_hops", 20)))
        ttk.Checkbutton(
            rc_row2, text="Any number of hops", variable=self.pf_any_hops_var, command=self._refresh_pf_any_hops,
        ).pack(side="left")
        ttk.Label(rc_row2, text="Max hops:").pack(side="left", padx=(12, 0))
        self.pf_max_hops_entry = ttk.Entry(rc_row2, textvariable=self.pf_max_hops_var, width=8)
        self.pf_max_hops_entry.pack(side="left", padx=(6, 0))

        rc_row3 = ttk.Frame(self.pf_reachable_frame)
        rc_row3.pack(fill="x", padx=6, pady=(0, 6))
        self.pf_candidate_count_var = tk.StringVar(value=str(self.config.get("pf_candidate_count", 10)))
        ttk.Label(rc_row3, text="# of candidates to check:").pack(side="left")
        ttk.Entry(rc_row3, textvariable=self.pf_candidate_count_var, width=8).pack(side="left", padx=(6, 0))

        self.check_reachable_button = ttk.Button(
            self.pf_reachable_frame, text="Check Reachability", command=self._on_check_reachable
        )
        self.check_reachable_button.pack(anchor="w", padx=6, pady=6)

        self._refresh_pf_any_hops()

        # -- Shared results area --
        results_frame = ttk.LabelFrame(parent, text="Result")
        results_frame.pack(fill="both", expand=True, padx=6, pady=6)
        self.pf_summary_label = ttk.Label(results_frame, text="", wraplength=1000, justify="left")
        self.pf_summary_label.pack(anchor="w", padx=6, pady=6)

        ttk.Label(results_frame, text="Candidates Considered:").pack(anchor="w", padx=6)
        self.pf_candidates_tree = treeview_utils.build_tree(results_frame, PF_CANDIDATES_COLUMNS)
        self.pf_candidates_tree.bind(
            "<Double-1>", lambda e: self._on_system_row_double_click(e, self.pf_candidates_tree)
        )

        ttk.Label(results_frame, text="Hop Path:").pack(anchor="w", padx=6, pady=(6, 0))
        self.pf_path_tree = treeview_utils.build_tree(results_frame, PF_PATH_COLUMNS)
        self.pf_path_tree.bind("<Double-1>", lambda e: self._on_system_row_double_click(e, self.pf_path_tree))

        self._refresh_pf_mode()

    def _refresh_pf_mode(self) -> None:
        if self.pf_mode_var.get() == "reachable":
            self.pf_shortest_frame.pack_forget()
            if not self.pf_reachable_frame.winfo_ismapped():
                self.pf_reachable_frame.pack(fill="x", padx=6, pady=6, after=self._pf_anchor_row)
        else:
            self.pf_reachable_frame.pack_forget()
            if not self.pf_shortest_frame.winfo_ismapped():
                self.pf_shortest_frame.pack(fill="x", padx=6, pady=6, after=self._pf_anchor_row)

    def _refresh_pf_any_hops(self) -> None:
        self.pf_max_hops_entry.configure(state=("disabled" if self.pf_any_hops_var.get() else "normal"))

    def _collect_pf_config(self) -> dict[str, Any]:
        def _float_or(value: str, default: float) -> float:
            try:
                return float(value)
            except ValueError:
                return default

        def _int_or(value: str, default: int) -> int:
            try:
                return int(value)
            except ValueError:
                return default

        self.config["db_paths"] = list(self.db_listbox.get(0, "end"))
        self.config["pf_origin_system"] = self.pf_origin_system_var.get().strip()
        self.config["pf_target_system"] = self.pf_target_system_var.get().strip()
        self.config["pf_max_jump_ly"] = _float_or(self.pf_max_jump_ly_var.get(), 15.0)
        self.config["pf_any_hops"] = bool(self.pf_any_hops_var.get())
        self.config["pf_max_hops"] = _int_or(self.pf_max_hops_var.get(), 20)
        self.config["pf_candidate_count"] = _int_or(self.pf_candidate_count_var.get(), 10)
        self.config["out_dir"] = self.out_dir_var.get().strip()
        return self.config

    def _on_find_path(self) -> None:
        if self.worker.is_running():
            messagebox.showinfo(
                "Job running", "Another job is already running. Wait for it to finish or cancel it first."
            )
            return
        cfg = self._collect_pf_config()
        db_paths = cfg["db_paths"]
        if not db_paths:
            messagebox.showwarning(
                "No sector DBs selected", "Select at least one sector DB on the Run tab before searching."
            )
            return
        missing = [p for p in db_paths if not Path(p).exists()]
        if missing:
            messagebox.showerror(
                "Database(s) not found",
                "The following sector DB(s) no longer exist:\n\n" + "\n".join(missing),
            )
            return
        origin, target = cfg["pf_origin_system"], cfg["pf_target_system"]
        if not origin or not target:
            messagebox.showwarning("Origin and target required", "Enter both an origin system and a target system.")
            return
        if not db_lookup.system_exists(db_paths, origin):
            messagebox.showerror("System not found", f"{origin!r} was not found in any selected DB.")
            return
        if not db_lookup.system_exists(db_paths, target):
            messagebox.showerror("System not found", f"{target!r} was not found in any selected DB.")
            return
        if cfg["pf_max_jump_ly"] <= 0:
            messagebox.showwarning("Invalid parameter", "Max jump (ly) must be greater than 0.")
            return

        out_dir = cfg["out_dir"] or "out"
        cfg["out_dir"] = out_dir
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        config_module.save_config(cfg)

        self._append_log(f"--- Finding shortest path: {origin!r} -> {target!r} ---")
        self._pending_path_result = None
        self._pending_path_mode = "shortest_path"
        started = self.worker.start(pipeline.run_shortest_path_pipeline, cfg)
        if not started:
            return
        self._set_controls_running(True)

    def _on_check_reachable(self) -> None:
        if self.worker.is_running():
            messagebox.showinfo(
                "Job running", "Another job is already running. Wait for it to finish or cancel it first."
            )
            return
        cfg = self._collect_pf_config()
        db_paths = cfg["db_paths"]
        if not db_paths:
            messagebox.showwarning(
                "No sector DBs selected", "Select at least one sector DB on the Run tab before searching."
            )
            return
        missing = [p for p in db_paths if not Path(p).exists()]
        if missing:
            messagebox.showerror(
                "Database(s) not found",
                "The following sector DB(s) no longer exist:\n\n" + "\n".join(missing),
            )
            return
        target = cfg["pf_target_system"]
        if not target:
            messagebox.showwarning("Target system required", "Enter a target system.")
            return
        if not db_lookup.system_exists(db_paths, target):
            messagebox.showerror("System not found", f"{target!r} was not found in any selected DB.")
            return
        if cfg["pf_max_jump_ly"] <= 0:
            messagebox.showwarning("Invalid parameter", "Max jump (ly) must be greater than 0.")
            return
        if not cfg["pf_any_hops"] and cfg["pf_max_hops"] <= 0:
            messagebox.showwarning(
                "Invalid parameter", "Max hops must be greater than 0 (or check 'Any number of hops')."
            )
            return
        if cfg["pf_candidate_count"] <= 0:
            messagebox.showwarning("Invalid parameter", "# of candidates to check must be greater than 0.")
            return

        out_dir = cfg["out_dir"] or "out"
        cfg["out_dir"] = out_dir
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        config_module.save_config(cfg)

        self._append_log(f"--- Checking reachability of {target!r} ---")
        self._pending_path_result = None
        self._pending_path_mode = "reachable"
        started = self.worker.start(pipeline.run_check_reachable_pipeline, cfg)
        if not started:
            return
        self._set_controls_running(True)

    def _format_max_hops(self, max_hops: Optional[int]) -> str:
        return "unlimited" if max_hops is None else str(max_hops)

    def _format_path_rows(self, path_rows: list[dict]) -> list[dict]:
        return [{**row, "occupied": "Yes" if row["occupied"] else "No"} for row in path_rows]

    def _format_candidate_rows(self, candidates: list[dict]) -> list[dict]:
        return [
            {
                **c,
                "hop_count": c["hop_count"] if c["hop_count"] is not None else "unreachable",
                "within_cap": "Yes" if c["within_cap"] else "No",
            }
            for c in candidates
        ]

    def _load_path_result(self, result: dict, mode: Optional[str]) -> None:
        self.pf_path_tree.delete(*self.pf_path_tree.get_children())
        self.pf_candidates_tree.delete(*self.pf_candidates_tree.get_children())

        if mode == "shortest_path":
            if result.get("reachable"):
                summary = f"{result['origin']} -> {result['target']}: reachable in {result['hop_count']} hop(s)."
                treeview_utils.load_dicts_into_tree(self.pf_path_tree, self._format_path_rows(result["path"]))
            else:
                summary = f"{result['origin']} -> {result['target']}: NOT reachable in the loaded data."
            self.pf_summary_label.configure(text=summary)

        elif mode == "reachable":
            candidates = result.get("candidates") or []
            treeview_utils.load_dicts_into_tree(self.pf_candidates_tree, self._format_candidate_rows(candidates))

            if not candidates:
                summary = f"No occupied systems exist anywhere in the loaded data to check against {result['target']}."
            elif result.get("reachable") and result.get("best_candidate"):
                best = result["best_candidate"]
                summary = (
                    f"{best['system_name']} can reach {result['target']} in {best['hop_count']} hop(s) "
                    f"(within the {self._format_max_hops(result['max_hops'])}-hop cap)."
                )
                treeview_utils.load_dicts_into_tree(self.pf_path_tree, self._format_path_rows(result["path"]))
            elif result.get("closest_miss"):
                miss = result["closest_miss"]
                excess = miss.get("excess_hops")
                excess_text = f" ({excess} over the cap)" if excess is not None else ""
                summary = (
                    f"No checked candidate reaches {result['target']} within the "
                    f"{self._format_max_hops(result['max_hops'])}-hop cap. Closest miss: "
                    f"{miss['system_name']} at {miss['hop_count']} hop(s){excess_text}."
                )
            else:
                summary = f"None of the checked candidates are even connected to {result['target']} in the loaded data."
            self.pf_summary_label.configure(text=summary)

        self.notebook.select(self.path_finder_tab)

    # ------------------------------------------------------------------
    # Settings tab
    # ------------------------------------------------------------------
    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        self.out_dir_var = tk.StringVar(value=self.config.get("out_dir", ""))

        frame = ttk.Frame(parent)
        frame.pack(fill="x", padx=12, pady=12)

        ttk.Label(frame, text="Output directory:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.out_dir_var, width=60).grid(row=0, column=1, sticky="we", pady=4)
        ttk.Button(frame, text="Browse...", command=self._browse_out_dir).grid(row=0, column=2, padx=(6, 0))

        frame.columnconfigure(1, weight=1)

        ttk.Button(parent, text="Save settings", command=self._save_settings).pack(anchor="w", padx=12, pady=6)

    def _browse_out_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.out_dir_var.get() or ".")
        if chosen:
            self.out_dir_var.set(chosen)

    def _save_settings(self) -> None:
        self.config["out_dir"] = self.out_dir_var.get().strip()
        config_module.save_config(self.config)
        messagebox.showinfo("Settings", "Settings saved.")

    # ------------------------------------------------------------------
    # Run / Extract / Cancel
    # ------------------------------------------------------------------
    def _collect_config(self) -> dict[str, Any]:
        def _float_or(value: str, default: float) -> float:
            try:
                return float(value)
            except ValueError:
                return default

        def _int_or(value: str, default: int) -> int:
            try:
                return int(value)
            except ValueError:
                return default

        self.config["db_paths"] = list(self.db_listbox.get(0, "end"))
        self.config["sector_label"] = self.sector_label_var.get().strip()
        self.config["origin_system"] = self.origin_system_var.get().strip()
        self.config["max_jump_ly"] = _float_or(self.max_jump_ly_var.get(), 15.0)
        self.config["max_hops"] = _int_or(self.max_hops_var.get(), 8)
        self.config["dense_top_n"] = _int_or(self.dense_top_n_var.get(), 20)
        self.config["out_dir"] = self.out_dir_var.get().strip()
        self.config["scoring_rubric"] = rubric_module.rubric_to_dict(self.scoring_rubric)
        return self.config

    def _on_run(self) -> None:
        if self.worker.is_running():
            messagebox.showinfo(
                "Job running", "Another job is already running. Wait for it to finish or cancel it first."
            )
            return
        cfg = self._collect_config()

        db_paths = cfg["db_paths"]
        if not db_paths:
            messagebox.showwarning("No sector DBs selected", "Add at least one sector DB before running.")
            return
        missing = [p for p in db_paths if not Path(p).exists()]
        if missing:
            messagebox.showerror(
                "Database(s) not found",
                "The following sector DB(s) no longer exist:\n\n" + "\n".join(missing),
            )
            return
        if not cfg["sector_label"]:
            messagebox.showwarning("Sector label required", "Enter a sector label before running.")
            return
        origin = cfg["origin_system"]
        if not origin:
            messagebox.showwarning("Origin system required", "Enter an origin system before running.")
            return
        if not db_lookup.system_exists(db_paths, origin):
            suggestions = db_lookup.search_system_names(db_paths, origin, limit=8)
            detail = ""
            if suggestions:
                detail = "\n\nDid you mean:\n  " + "\n  ".join(suggestions)
            messagebox.showerror("System not found", f"{origin!r} was not found in any selected DB.{detail}")
            return
        if cfg["max_jump_ly"] <= 0:
            messagebox.showwarning("Invalid parameter", "Max jump (ly) must be greater than 0.")
            return
        if cfg["max_hops"] <= 0:
            messagebox.showwarning("Invalid parameter", "Max hops must be greater than 0.")
            return
        if cfg["dense_top_n"] <= 0:
            messagebox.showwarning("Invalid parameter", "Densest-systems top N must be greater than 0.")
            return

        out_dir = cfg["out_dir"] or "out"
        cfg["out_dir"] = out_dir
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        config_module.save_config(cfg)

        self._append_log(f"--- Starting run: {origin!r} in {cfg['sector_label']!r} ---")
        self._pending_result_paths = None
        started = self.worker.start(pipeline.run_reachability_pipeline, cfg)
        if not started:
            return
        self._set_controls_running(True)

    def _set_controls_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.run_button.configure(state=state)
        self.extract_button.configure(state=state)
        self.find_path_button.configure(state=state)
        self.check_reachable_button.configure(state=state)
        self.cancel_button.configure(state=("normal" if running else "disabled"))
        if running:
            self.status_label.configure(text="Running...")

    def _on_cancel(self) -> None:
        if self.worker.is_running():
            self.worker.cancel()
            self.status_label.configure(text="Cancelling...")

    def _on_job_done(self, returncode: str) -> None:
        self._set_controls_running(False)
        self.status_label.configure(text=f"Idle (last run {describe_exit(returncode)})")

        if returncode == "0" and self._pending_result_paths is not None:
            candidates_path, dense_path = self._pending_result_paths
            try:
                treeview_utils.load_csv_into_tree(self.candidates_tree, candidates_path)
                treeview_utils.load_csv_into_tree(self.dense_tree, dense_path)
                self.notebook.select(self.candidates_tab)
            except OSError as exc:
                messagebox.showerror("Load failed", f"Could not load results:\n\n{exc}")
        self._pending_result_paths = None

        # Extraction also publishes its databases on 3 (some unreadable dump records, reported in the log).
        if returncode in ("0", "3") and self._pending_extracted_db_paths is not None:
            self._offer_extracted_dbs(self._pending_extracted_db_paths)
        self._pending_extracted_db_paths = None

        if returncode == "0" and self._pending_path_result is not None:
            self._load_path_result(self._pending_path_result, self._pending_path_mode)
        self._pending_path_result = None
        self._pending_path_mode = None

    def _offer_extracted_dbs(self, db_paths: list[str]) -> None:
        """Auto-adds the newly extracted DBs to the Run tab's DB listbox
        (deduped) and logs a confirmation -- closes the loop between
        prepping data and using it, matching this app's existing
        low-friction feel."""
        existing = list(self.db_listbox.get(0, "end"))
        added = []
        for p in db_paths:
            if p not in existing:
                self.db_listbox.insert("end", p)
                existing.append(p)
                added.append(p)
        self._maybe_autofill_sector_label()
        if added:
            self._append_log(f"Added {len(added)} new sector DB(s) to the Run tab's DB selection.")

    # ------------------------------------------------------------------
    # Log polling
    # ------------------------------------------------------------------
    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                line = self.worker.log_queue.get_nowait()
                if line.startswith(DONE_SENTINEL + ":"):
                    self._on_job_done(line.split(":", 1)[1])
                elif line.startswith(pipeline.RESULT_SENTINEL + ":"):
                    payload = line.split(":", 1)[1]
                    candidates_str, dense_str = payload.split(pipeline.RESULT_SEP, 1)
                    self._pending_result_paths = (Path(candidates_str), Path(dense_str))
                elif line.startswith(pipeline.EXTRACT_RESULT_SENTINEL + ":"):
                    payload = line.split(":", 1)[1]
                    self._pending_extracted_db_paths = payload.split(pipeline.EXTRACT_RESULT_SEP)
                elif line.startswith(pipeline.PATH_RESULT_SENTINEL + ":"):
                    payload = line.split(":", 1)[1]
                    self._pending_path_result = json.loads(payload)
                else:
                    self._append_log(line)
        except queue.Empty:
            pass

        self.root.after(POLL_INTERVAL_MS, self._poll_log_queue)

    def _on_close(self) -> None:
        if not self.worker.is_running():
            self.root.destroy()
            return
        if not messagebox.askyesno(
            "Job running",
            "A job is still running. Cancel it and wait for it to stop before exiting?",
        ):
            return
        self.worker.cancel()
        self.run_button.configure(state="disabled")
        self.extract_button.configure(state="disabled")
        self.find_path_button.configure(state="disabled")
        self.check_reachable_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.status_label.configure(text="Cancelling -- please wait before closing...")
        self._wait_for_shutdown(0)

    def _wait_for_shutdown(self, elapsed_ms: int) -> None:
        # Closing the window must not kill the worker thread mid-flight (it's
        # a daemon thread; destroying root ends the process outright) -- wait
        # for it to actually finish flushing/closing its db connections.
        if not self.worker.is_running():
            self.root.destroy()
            return
        if elapsed_ms >= SHUTDOWN_TIMEOUT_MS:
            if messagebox.askyesno(
                "Still running",
                "The job hasn't stopped yet. Force quit anyway? This may leave "
                "partially-written data in an inconsistent state.",
            ):
                self.root.destroy()
            else:
                self._wait_for_shutdown(0)
            return
        self.root.after(
            SHUTDOWN_POLL_MS, lambda: self._wait_for_shutdown(elapsed_ms + SHUTDOWN_POLL_MS)
        )


def _icon_path() -> Path:
    """Resolves icon.png for the running window's iconphoto: the frozen
    exe's bundled copy (sys._MEIPASS, set by PyInstaller -- see
    packaging/ed_colony_scout.spec's `datas`) when packaged, or the
    repo-root icon.png when run from source."""
    base = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent)
    return base / "icon.png"


def main() -> None:
    root = tk.Tk()
    icon_path = _icon_path()
    if icon_path.exists():
        root.iconphoto(True, tk.PhotoImage(file=str(icon_path)))
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
