"""Read-only "System Detail" popup: full body/ring/biosignal breakdown for
a single system, opened by double-clicking a system-name row in any of the
Candidates / Densest Systems / Path Finder result trees."""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from gui import treeview_utils
from gui.db_lookup import SystemDetail
from gui.treeview_utils import ColumnSpec

_SIGNAL_TYPE_RE = re.compile(r"^\$SAA_SignalType_(\w+);$")
_GENUS_NAME_RE = re.compile(r"^\$Codex_Ent_(\w+)_Genus_Name;$")

BODY_COLUMNS = [
    ColumnSpec("body_name", "Body", 260, False, "w"),
    ColumnSpec("body_type", "Type", 90, False, "w"),
    ColumnSpec("sub_type", "Sub-Type", 200, False, "w"),
    ColumnSpec("landable", "Landable", 80, False, "center"),
    ColumnSpec("terraform_state", "Terraform State", 140, False, "w"),
    ColumnSpec("biosignals", "Biosignals", 80, True, "e"),
    ColumnSpec("geosignals", "Geosignals", 80, True, "e"),
    ColumnSpec("genuses", "Genuses", 260, False, "w"),
]

RING_COLUMNS = [
    ColumnSpec("body_name", "Body", 220, False, "w"),
    ColumnSpec("ring_name", "Ring", 240, False, "w"),
    ColumnSpec("ring_class", "Class", 90, False, "w"),
    ColumnSpec("mass_mt", "Mass (Mt)", 110, True, "e"),
    ColumnSpec("inner_rad", "Inner Radius (m)", 130, True, "e"),
    ColumnSpec("outer_rad", "Outer Radius (m)", 130, True, "e"),
]


def _clean_signal_type(key: str) -> str:
    match = _SIGNAL_TYPE_RE.match(key)
    return match.group(1) if match else key


def _clean_genus_name(key: str) -> str:
    match = _GENUS_NAME_RE.match(key)
    return match.group(1) if match else key


class SystemDetailDialog(tk.Toplevel):
    """Read-only, non-blocking popup -- unlike RubricDialog there's nothing
    to collect back from the user, so no self.result/_ok/_cancel split."""

    def __init__(self, parent: tk.Widget, detail: SystemDetail) -> None:
        super().__init__(parent)
        self.title(f"{detail.system_name} — System Detail")
        self.geometry("900x600")
        self.resizable(True, True)
        self.transient(parent)

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(container, text=self._summary_text(detail), font=("", 10, "bold")).pack(
            anchor="w", pady=(0, 8)
        )

        ttk.Label(container, text="Bodies").pack(anchor="w")
        bodies_tree = treeview_utils.build_tree(container, BODY_COLUMNS)
        treeview_utils.load_dicts_into_tree(bodies_tree, [self._body_row(b) for b in detail.bodies])

        ttk.Label(container, text="Rings").pack(anchor="w", pady=(8, 0))
        rings_tree = treeview_utils.build_tree(container, RING_COLUMNS)
        treeview_utils.load_dicts_into_tree(rings_tree, [self._ring_row(r) for r in detail.rings])

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    @staticmethod
    def _summary_text(detail: SystemDetail) -> str:
        landable_count = sum(1 for b in detail.bodies if b.is_landable)
        bio_total = sum(
            count
            for b in detail.bodies
            for key, count in b.signal_counts.items()
            if _clean_signal_type(key) == "Biological"
        )
        other_total = sum(
            count
            for b in detail.bodies
            for key, count in b.signal_counts.items()
            if _clean_signal_type(key) != "Biological"
        )
        return (
            f"{len(detail.bodies)} bodies ({landable_count} landable) · "
            f"{bio_total} biosignals · {other_total} other signals · "
            f"{len(detail.rings)} ring(s)"
        )

    @staticmethod
    def _body_row(body) -> dict:
        biosignals = sum(
            count for key, count in body.signal_counts.items() if _clean_signal_type(key) == "Biological"
        )
        geosignals = sum(
            count for key, count in body.signal_counts.items() if _clean_signal_type(key) == "Geological"
        )
        other = {
            _clean_signal_type(key): count
            for key, count in body.signal_counts.items()
            if _clean_signal_type(key) not in ("Biological", "Geological")
        }
        biosignals_display = str(biosignals) if biosignals else ""
        geosignals_display = str(geosignals) if geosignals else ""
        if other:
            extra = ", ".join(f"{name} ×{count}" for name, count in other.items())
            geosignals_display = f"{geosignals_display} ({extra})".strip()
        landable = body.is_landable
        return {
            "body_name": body.body_name,
            "body_type": body.body_type or "",
            "sub_type": body.sub_type or "",
            "landable": "Yes" if landable else ("No" if landable is False else ""),
            "terraform_state": body.terraform_state or "",
            "biosignals": biosignals_display,
            "geosignals": geosignals_display,
            "genuses": ", ".join(_clean_genus_name(g) for g in body.genuses),
        }

    @staticmethod
    def _ring_row(ring) -> dict:
        return {
            "body_name": ring.body_name,
            "ring_name": ring.ring_name,
            "ring_class": ring.ring_class or "",
            "mass_mt": f"{ring.mass_mt:,.0f}" if ring.mass_mt is not None else "",
            "inner_rad": f"{ring.inner_rad:,.0f}" if ring.inner_rad is not None else "",
            "outer_rad": f"{ring.outer_rad:,.0f}" if ring.outer_rad is not None else "",
        }
