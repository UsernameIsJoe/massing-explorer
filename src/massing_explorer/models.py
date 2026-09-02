from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Room:
    room_name: str
    qty: int
    area_sf: float
    department: str
    total_area_sf: float = 0.0
    comments: str = ""

    def __post_init__(self) -> None:
        if self.total_area_sf == 0.0 and self.qty > 0:
            self.total_area_sf = self.area_sf * self.qty


@dataclass
class Department:
    name: str
    nfa_sf: float
    target_gsf: float = 0.0
    room_count: int = 0
    declared_total_sf: float | None = None
    rooms: list[Room] = field(default_factory=list)


@dataclass
class GrossingConfig:
    area_adjustment: float = 1.15
    grossing_factor: float = 1.50

    @property
    def combined_multiplier(self) -> float:
        return self.area_adjustment * self.grossing_factor


@dataclass
class VerificationMessage:
    passed: bool
    message: str
    severity: str = "info"  # info | warning | error


@dataclass
class ProgramStudy:
    version: str = "1.0"
    units: str = "feet"
    area_unit: str = "sf"
    source_file: str = ""
    grossing: GrossingConfig = field(default_factory=GrossingConfig)
    rooms: list[Room] = field(default_factory=list)
    departments: list[Department] = field(default_factory=list)
    totals: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    verification: list[VerificationMessage] = field(default_factory=list)

    @property
    def verification_passed(self) -> bool:
        return all(m.passed or m.severity != "error" for m in self.verification)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "units": self.units,
            "area_unit": self.area_unit,
            "source_file": self.source_file,
            "grossing": {
                "area_adjustment": self.grossing.area_adjustment,
                "grossing_factor": self.grossing.grossing_factor,
            },
            "rooms": [
                {
                    "room_name": r.room_name,
                    "qty": r.qty,
                    "area_sf": r.area_sf,
                    "department": r.department,
                    "total_area_sf": r.total_area_sf,
                    "comments": r.comments,
                }
                for r in self.rooms
            ],
            "departments": [
                {
                    "name": d.name,
                    "nfa_sf": d.nfa_sf,
                    "target_gsf": d.target_gsf,
                    "room_count": d.room_count,
                    "declared_total_sf": d.declared_total_sf,
                }
                for d in self.departments
            ],
            "totals": self.totals,
            "metadata": self.metadata,
            "verification": [
                {"passed": v.passed, "message": v.message, "severity": v.severity}
                for v in self.verification
            ],
        }
