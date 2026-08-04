"""Tkinter control UI for the ED Colony Scout reachability/scoring pipeline."""

from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

from gui import config as config_module
from gui import db_lookup, pipeline, treeview_utils
from gui.treeview_utils import ColumnSpec
from gui.worker import DONE_SENTINEL, Worker

MAX_LOG_LINES = 5000
POLL_INTERVAL_MS = 100
SHUTDOWN_POLL_MS = 200
SHUTDOWN_TIMEOUT_MS = 20000
SEARCH_DEBOUNCE_MS = 200

CANDIDATES_COLUMNS = [
    ColumnSpec("rank", "Rank", 50, True, "e"),
    ColumnSpec("system_name", "System", 220, False, "w"),
    ColumnSpec("x", "X", 70, True, "e"),
    ColumnSpec("y", "Y", 70, True, "e"),
    ColumnSpec("z", "Z", 70, True, "e"),
    ColumnSpec("distance_ly", "Dist (ly)", 80, True, "e"),
    ColumnSpec("hop_count", "Hops", 55, True, "e"),
    ColumnSpec("total_body_count", "Bodies", 60, True, "e"),
    ColumnSpec("elw_count", "ELW", 50, True, "e"),
    ColumnSpec("ww_count", "WW", 50, True, "e"),
    ColumnSpec("hmct_terraformable_count", "HMC-T", 60, True, "e"),
    ColumnSpec("elw_score", "ELW Score", 80, True, "e"),
    ColumnSpec("ww_score", "WW Score", 80, True, "e"),
    ColumnSpec("hmct_score", "HMC-T Score", 90, True, "e"),
    ColumnSpec("metallic_ring_ratio", "Metallic Ratio", 100, True, "e"),
    ColumnSpec("metallic_ring_score", "Metallic Score", 100, True, "e"),
    ColumnSpec("icy_ring_ratio", "Icy Ratio", 80, True, "e"),
    ColumnSpec("icy_ring_score", "Icy Score", 80, True, "e"),
    ColumnSpec("ring_score", "Ring Score", 85, True, "e"),
    ColumnSpec("colonization_score", "Score", 80, True, "e"),
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


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"ED Colony Scout {config_module.APP_VERSION}")
        self.root.geometry("1100x750")

        self.worker = Worker()
        self.config: dict[str, Any] = config_module.load_config()
        self._pending_result_paths: Optional[tuple[Path, Path]] = None
        self._search_after_id: Optional[str] = None
        self._last_auto_sector_label = ""

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.run_tab = ttk.Frame(self.notebook)
        self.candidates_tab = ttk.Frame(self.notebook)
        self.dense_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.run_tab, text="Run")
        self.notebook.add(self.candidates_tab, text="Candidates")
        self.notebook.add(self.dense_tab, text="Densest Systems")
        self.notebook.add(self.settings_tab, text="Settings")

        self._build_run_tab(self.run_tab)
        self.candidates_tree = treeview_utils.build_tree(self.candidates_tab, CANDIDATES_COLUMNS)
        self.dense_tree = treeview_utils.build_tree(self.dense_tab, DENSEST_COLUMNS)
        self._build_settings_tab(self.settings_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_INTERVAL_MS, self._poll_log_queue)

    # ------------------------------------------------------------------
    # Run tab
    # ------------------------------------------------------------------
    def _build_run_tab(self, parent: ttk.Frame) -> None:
        # -- Sector Database --
        db_frame = ttk.LabelFrame(parent, text="Sector Database")
        db_frame.pack(fill="x", padx=6, pady=6)

        self.db_path_var = tk.StringVar(value=self.config.get("db_path", ""))
        self.sector_label_var = tk.StringVar(value=self.config.get("sector_label", ""))

        db_row = ttk.Frame(db_frame)
        db_row.pack(fill="x", padx=6, pady=4)
        ttk.Label(db_row, text="DB file:").pack(side="left")
        ttk.Entry(db_row, textvariable=self.db_path_var, width=60).pack(
            side="left", fill="x", expand=True, padx=(6, 6)
        )
        ttk.Button(db_row, text="Browse...", command=self._browse_db_path).pack(side="left")

        label_row = ttk.Frame(db_frame)
        label_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(label_row, text="Sector label:").pack(side="left")
        ttk.Entry(label_row, textvariable=self.sector_label_var, width=40).pack(side="left", padx=(6, 0))

        self.db_path_var.trace_add("write", lambda *_args: self._maybe_autofill_sector_label())
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
        self.origin_combo.bind("<KeyRelease>", self._schedule_origin_search)
        ttk.Label(
            origin_frame,
            text="Type to search systems in the selected DB (results appear in the dropdown).",
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

        # -- Run / Cancel + log --
        controls = ttk.Frame(parent)
        controls.pack(fill="x", padx=6, pady=6)
        self.run_button = ttk.Button(controls, text="Run", command=self._on_run)
        self.run_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self._on_cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(6, 0))
        self.status_label = ttk.Label(controls, text="Idle")
        self.status_label.pack(side="left", padx=12)

        log_frame = ttk.LabelFrame(parent, text="Log")
        log_frame.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_text = tk.Text(log_frame, height=14, state="disabled", wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def _browse_db_path(self) -> None:
        current = self.db_path_var.get().strip()
        initialdir = str(Path(current).parent) if current else str(db_lookup.DEFAULT_SECTOR_LIBRARY_DIR)
        chosen = filedialog.askopenfilename(
            initialdir=initialdir,
            filetypes=[("Sector DB", "*.sqlite"), ("All files", "*.*")],
        )
        if chosen:
            self.db_path_var.set(chosen)

    def _maybe_autofill_sector_label(self) -> None:
        db_path = self.db_path_var.get().strip()
        if not db_path:
            return
        current_label = self.sector_label_var.get().strip()
        if current_label and current_label != self._last_auto_sector_label:
            return  # user has manually edited it -- don't clobber
        derived = db_lookup.derive_sector_label(db_path)
        self.sector_label_var.set(derived)
        self._last_auto_sector_label = derived

    def _schedule_origin_search(self, _event: Any = None) -> None:
        if self._search_after_id is not None:
            self.root.after_cancel(self._search_after_id)
        self._search_after_id = self.root.after(SEARCH_DEBOUNCE_MS, self._run_origin_search)

    def _run_origin_search(self) -> None:
        self._search_after_id = None
        db_path = self.db_path_var.get().strip()
        text = self.origin_system_var.get()
        matches = db_lookup.search_system_names(db_path, text, limit=50)
        self.origin_combo["values"] = matches

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
    # Run / Cancel
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

        self.config["db_path"] = self.db_path_var.get().strip()
        self.config["sector_label"] = self.sector_label_var.get().strip()
        self.config["origin_system"] = self.origin_system_var.get().strip()
        self.config["max_jump_ly"] = _float_or(self.max_jump_ly_var.get(), 15.0)
        self.config["max_hops"] = _int_or(self.max_hops_var.get(), 8)
        self.config["dense_top_n"] = _int_or(self.dense_top_n_var.get(), 20)
        self.config["out_dir"] = self.out_dir_var.get().strip()
        return self.config

    def _on_run(self) -> None:
        if self.worker.is_running():
            return
        cfg = self._collect_config()

        db_path = cfg["db_path"]
        if not db_path or not Path(db_path).exists():
            messagebox.showerror(
                "Database not found", f"No sector DB found at:\n\n{db_path or '(not set)'}"
            )
            return
        if not cfg["sector_label"]:
            messagebox.showwarning("Sector label required", "Enter a sector label before running.")
            return
        origin = cfg["origin_system"]
        if not origin:
            messagebox.showwarning("Origin system required", "Enter an origin system before running.")
            return
        if not db_lookup.system_exists(db_path, origin):
            suggestions = db_lookup.search_system_names(db_path, origin, limit=8)
            detail = ""
            if suggestions:
                detail = "\n\nDid you mean:\n  " + "\n  ".join(suggestions)
            messagebox.showerror("System not found", f"{origin!r} was not found in this DB.{detail}")
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
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.status_label.configure(text="Running...")

    def _on_cancel(self) -> None:
        if self.worker.is_running():
            self.worker.cancel()
            self.status_label.configure(text="Cancelling...")

    def _on_job_done(self, returncode: str) -> None:
        self.run_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_label.configure(text=f"Idle (last run exit={returncode})")

        if returncode == "0" and self._pending_result_paths is not None:
            candidates_path, dense_path = self._pending_result_paths
            try:
                treeview_utils.load_csv_into_tree(self.candidates_tree, candidates_path)
                treeview_utils.load_csv_into_tree(self.dense_tree, dense_path)
                self.notebook.select(self.candidates_tab)
            except OSError as exc:
                messagebox.showerror("Load failed", f"Could not load results:\n\n{exc}")
        self._pending_result_paths = None

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


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
