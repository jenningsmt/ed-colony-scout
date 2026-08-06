"""Scoring-rubric config<->dataclass round-trip, stock-default help text,
and the "Customize Scoring..." Tkinter edit dialog for the Run tab."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from scripts.colonization_reachability_export import RING_DENSITY_FLOOR, ScoringRubric, STOCK_RUBRIC

# (field_name, label, parse_type) -- parse_type drives both the dialog's
# validation and rubric_from_dict's coercion. Explicit, not derived from
# dataclasses.fields()[i].type: the engine script has
# `from __future__ import annotations`, which makes that a *string*
# ("int"/"float") at runtime, not a usable callable.
RUBRIC_FIELDS: list[tuple[str, str, type]] = [
    ("elw_first_n", "Earth-like World -- \"first N\" tier size", int),
    ("elw_first_pts", "Earth-like World -- first-tier points (each)", float),
    ("elw_rest_pts", "Earth-like World -- each additional (pts)", float),
    ("ww_first_n", "Water World -- \"first N\" tier size", int),
    ("ww_first_pts", "Water World -- first-tier points (each)", float),
    ("ww_rest_pts", "Water World -- each additional (pts)", float),
    ("hmct_first_n", "Terraformable HMC -- \"first N\" tier size", int),
    ("hmct_first_pts", "Terraformable HMC -- first-tier points (each)", float),
    ("hmct_rest_pts", "Terraformable HMC -- each additional (pts)", float),
    ("ring_metallic_base_pts", "Metallic ring -- base points", float),
    ("ring_icy_base_pts", "Icy ring -- base points", float),
]


def rubric_to_dict(rubric: ScoringRubric) -> dict:
    return dataclasses.asdict(rubric)


def rubric_from_dict(data: dict) -> ScoringRubric:
    """Defaults any missing/invalid field to the stock value -- forward-
    compatible with older saved configs, defensive against corrupt JSON.
    Safe to call with {} (returns the stock rubric's values)."""
    stock = dataclasses.asdict(STOCK_RUBRIC)
    kwargs = {}
    for field_name, _label, kind in RUBRIC_FIELDS:
        raw = data.get(field_name) if isinstance(data, dict) else None
        try:
            kwargs[field_name] = kind(raw) if raw is not None else stock[field_name]
        except (TypeError, ValueError):
            kwargs[field_name] = stock[field_name]
    return ScoringRubric(**kwargs)


def _fmt(kind: type, value) -> str:
    return str(value) if kind is int else f"{value:g}"


RUBRIC_HELP_TEMPLATE = (
    "How colonization scoring works\n\n"
    "Every reachable, colonization-eligible system earns points in five "
    "categories; colonization_score is the sum.\n\n"
    "Body categories (tiered -- the first few of a type outweigh later ones):\n"
    "  - Earth-like World: first [elw_first_n] worth [elw_first_pts] pts each, "
    "each additional worth [elw_rest_pts] pts\n"
    "  - Water World: first [ww_first_n] worth [ww_first_pts] pts each, "
    "each additional worth [ww_rest_pts] pts\n"
    "  - Terraformable High Metal Content World: first [hmct_first_n] worth "
    "[hmct_first_pts] pts each, each additional worth [hmct_rest_pts] pts\n\n"
    "Ring categories (density-based -- only a system's single densest ring "
    "of each class counts):\n"
    f"  - A ring only counts if its surface density is at or above "
    f"{RING_DENSITY_FLOOR:.1f}x the galactic median for its class (fixed, not editable).\n"
    "  - Metallic ring: [ring_metallic_base_pts] base pts, scaled up for density "
    "above the floor (base x (1 + (ratio-1) x 0.5) -- the 0.5 slope and the "
    "galactic median constants are fixed, not editable).\n"
    "  - Icy ring: [ring_icy_base_pts] base pts, same scaling.\n\n"
    "Occupied-system disqualifier (fixed, always on, not editable):\n"
    "  A system with an established government is already claimed by a "
    "faction and is never a valid colonization target -- it's excluded from "
    "both the Candidates and Densest Systems reports, though it still counts "
    "as a hop-through waypoint when building the reachability graph.\n\n"
    "The values in brackets above reflect your current settings (or stock "
    "defaults, if unchanged) -- edit them via \"Customize Scoring...\"."
)


def format_rubric_help(rubric: ScoringRubric) -> str:
    text = RUBRIC_HELP_TEMPLATE
    for field_name, _label, kind in RUBRIC_FIELDS:
        text = text.replace(f"[{field_name}]", _fmt(kind, getattr(rubric, field_name)))
    return text


class RubricDialog(tk.Toplevel):
    """Modal edit form: one Entry per RUBRIC_FIELDS row + Reset to Stock
    Defaults/Cancel/OK. self.result is the accepted ScoringRubric, or None
    if cancelled."""

    def __init__(self, parent: tk.Widget, current: ScoringRubric) -> None:
        super().__init__(parent)
        self.title("Customize Scoring")
        self.resizable(False, False)
        self.transient(parent)
        self.result: Optional[ScoringRubric] = None
        self._vars: dict[str, tk.StringVar] = {}

        form = ttk.Frame(self)
        form.pack(padx=12, pady=12, fill="both", expand=True)
        for row, (field_name, label, _kind) in enumerate(RUBRIC_FIELDS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=str(getattr(current, field_name)))
            self._vars[field_name] = var
            ttk.Entry(form, textvariable=var, width=10).grid(row=row, column=1, sticky="w", padx=(6, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(buttons, text="Reset to Stock Defaults", command=self._reset).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="right", padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()

    def _reset(self) -> None:
        stock = dataclasses.asdict(STOCK_RUBRIC)
        for field_name, _label, _kind in RUBRIC_FIELDS:
            self._vars[field_name].set(str(stock[field_name]))

    def _ok(self) -> None:
        kwargs = {}
        for field_name, label, kind in RUBRIC_FIELDS:
            raw = self._vars[field_name].get().strip()
            try:
                kwargs[field_name] = kind(raw)
            except ValueError:
                messagebox.showerror(
                    "Invalid value", f"{label!r} must be a {kind.__name__} (got {raw!r}).", parent=self
                )
                return
        self.result = ScoringRubric(**kwargs)
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()
