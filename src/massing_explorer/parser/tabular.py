from __future__ import annotations

from typing import Any

from ..models import ProgramStudy, Room
from .columns import (
    detect_columns,
    is_footer_row,
    is_instruction_row,
    normalize_header,
    to_int,
    to_number,
)


def _cell(row: list[Any], col_map: dict[str, int], field: str) -> Any:
    idx = col_map.get(field)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _find_header_row(rows: list[list[Any]]) -> tuple[int, dict[str, int]] | None:
    for i, row in enumerate(rows):
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        col_map = detect_columns(row)
        # Header must have room name + at least one numeric column indicator
        has_room = "room_name" in col_map
        has_numeric_col = "area_each" in col_map or "area_total" in col_map or "qty" in col_map
        normalized = [normalize_header(c) for c in row]
        looks_like_header = any("room" in h or "space" in h or "nfa" in h for h in normalized)
        if has_room and has_numeric_col and looks_like_header:
            return i, col_map
    return None


def _is_department_row(
    room_name: str,
    area_each: float | None,
    qty: int | None,
    area_total: float | None,
) -> bool:
    if not room_name or is_instruction_row(room_name) or is_footer_row(room_name):
        return False
    # Department header: has declared total, no per-room area
    if area_total is not None and area_total > 0:
        if area_each is None and qty is None:
            return True
        # All-caps short names without room-sized NFA often are departments
        if area_each is None and room_name == room_name.upper() and len(room_name) < 60:
            return True
    return False


def _is_room_row(
    room_name: str,
    area_each: float | None,
    qty: int | None,
    area_total: float | None,
) -> bool:
    if not room_name or is_instruction_row(room_name) or is_footer_row(room_name):
        return False
    if _is_department_row(room_name, area_each, qty, area_total):
        return False
    if area_each is not None and area_each >= 0:
        return True
    if area_total is not None and qty is not None and qty > 0:
        return True
    return False


def parse_tabular_rows(
    rows: list[list[Any]],
    source_file: str = "",
) -> tuple[list[Room], dict[str, float | None], dict[str, Any]]:
    """
    Parse generic tabular program data into rooms and extracted metadata.

    Returns:
        rooms, declared_totals (nfa_sf, gfa_sf, grossing_factor), metadata
    """
    header_info = _find_header_row(rows)
    if header_info is None:
        raise ValueError("Could not detect header row with room/area columns")

    header_idx, col_map = header_info
    rooms: list[Room] = []
    current_department = "uncategorized"
    has_dept_column = "department" in col_map
    declared_totals: dict[str, float | None] = {
        "nfa_sf": None,
        "gfa_sf": None,
        "grossing_factor": None,
    }
    metadata: dict[str, Any] = {"header_row": header_idx + 1, "column_map": col_map}

    for row in rows[header_idx + 1 :]:
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue

        room_name_raw = _cell(row, col_map, "room_name")
        room_name = str(room_name_raw).strip() if room_name_raw is not None else ""
        if not room_name:
            continue

        area_each = to_number(_cell(row, col_map, "area_each"))
        qty = to_int(_cell(row, col_map, "qty"))
        area_total = to_number(_cell(row, col_map, "area_total"))
        comments_raw = _cell(row, col_map, "comments")
        comments = str(comments_raw).strip() if comments_raw else ""
        dept_cell = _cell(row, col_map, "department")
        if has_dept_column and dept_cell is not None and str(dept_cell).strip():
            current_department = str(dept_cell).strip()

        lower_name = room_name.lower()

        # Extract document-level totals from footer rows
        if "net floor area" in lower_name and "gross" not in lower_name:
            if area_total is not None:
                declared_totals["nfa_sf"] = area_total
            continue
        if "gross floor area" in lower_name or ("gfa" in lower_name and "factor" not in lower_name):
            if area_total is not None:
                declared_totals["gfa_sf"] = area_total
            continue
        if "grossing factor" in lower_name:
            if area_total is not None:
                declared_totals["grossing_factor"] = area_total
            continue
        if is_footer_row(room_name):
            continue

        if _is_department_row(room_name, area_each, qty, area_total):
            if not has_dept_column:
                current_department = room_name
                metadata.setdefault("department_declared_totals", {})[room_name] = area_total
            continue

        if not _is_room_row(room_name, area_each, qty, area_total):
            continue

        qty_val = qty if qty is not None else 1
        if area_each is None and area_total is not None and qty_val > 0:
            area_each = area_total / qty_val
        elif area_each is None:
            area_each = 0.0

        total = area_total if area_total is not None else area_each * qty_val

        rooms.append(
            Room(
                room_name=room_name,
                qty=qty_val,
                area_sf=area_each,
                department=current_department,
                total_area_sf=total,
                comments=comments,
            )
        )

    return rooms, declared_totals, metadata
