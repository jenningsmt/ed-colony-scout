"""Generic sortable-Treeview + CSV-loading helpers for the results tables.

Column specs (headings, widths, numeric-ness) are supplied by the caller
(app.py) rather than inferred from the CSV header -- that only duplicates
labels, not scoring logic, which stays entirely in the reachability script.
"""

from __future__ import annotations

import csv
from pathlib import Path
from tkinter import ttk
from typing import NamedTuple


class ColumnSpec(NamedTuple):
    key: str
    heading: str
    width: int
    numeric: bool
    anchor: str = "w"


def build_tree(parent: ttk.Frame, columns: list[ColumnSpec]) -> ttk.Treeview:
    container = ttk.Frame(parent)
    container.pack(fill="both", expand=True)

    tree = ttk.Treeview(container, columns=[c.key for c in columns], show="headings")
    for col in columns:
        tree.heading(col.key, text=col.heading, command=lambda c=col.key: _sort_by_column(tree, c))
        tree.column(col.key, width=col.width, anchor=col.anchor, stretch=False)

    vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    container.rowconfigure(0, weight=1)
    container.columnconfigure(0, weight=1)

    # Stashed for _sort_by_column -- ttk.Treeview is a plain Python object,
    # so attaching state directly to the instance is fine here.
    tree._column_specs = {c.key: c for c in columns}  # type: ignore[attr-defined]
    tree._sort_ascending = {}  # type: ignore[attr-defined]
    return tree


def _sort_by_column(tree: ttk.Treeview, col: str) -> None:
    ascending = tree._sort_ascending.get(col, True)  # type: ignore[attr-defined]
    spec = tree._column_specs.get(col)  # type: ignore[attr-defined]
    numeric = spec.numeric if spec else False

    def sort_key(item_id: str):
        value = tree.set(item_id, col)
        if numeric:
            try:
                return float(value)
            except ValueError:
                return float("-inf")
        return value.lower()

    items = list(tree.get_children(""))
    items.sort(key=sort_key, reverse=not ascending)
    for index, item_id in enumerate(items):
        tree.move(item_id, "", index)
    tree._sort_ascending[col] = not ascending  # type: ignore[attr-defined]


def load_csv_into_tree(tree: ttk.Treeview, csv_path: Path) -> None:
    tree.delete(*tree.get_children())
    columns = tree["columns"]
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            values = [row.get(col, "") for col in columns]
            tree.insert("", "end", values=values)


def load_dicts_into_tree(tree: ttk.Treeview, rows: list[dict]) -> None:
    """Same row-insertion logic as load_csv_into_tree, but from in-memory
    dicts rather than a CSV file -- for results that arrive via a
    JSON-sentinel mechanism instead of a written CSV. Callers are
    responsible for pre-formatting display values (Yes/No, "unreachable",
    etc.) before calling this, same division of labor as load_csv_into_
    tree's raw-string CSV values."""
    tree.delete(*tree.get_children())
    columns = tree["columns"]
    for row in rows:
        values = [row.get(col, "") for col in columns]
        tree.insert("", "end", values=values)
