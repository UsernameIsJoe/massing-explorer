from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VoidRegion:
    room: str
    width_ft: float
    length_ft: float
    area_sf: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "room": self.room,
            "width_ft": round(self.width_ft, 2),
            "length_ft": round(self.length_ft, 2),
            "area_sf": round(self.area_sf, 1),
        }


@dataclass
class FloorPlate:
    level: int
    width_ft: float
    length_ft: float
    area_sf: float
    programs: list[str] = field(default_factory=list)
    voids: list[VoidRegion] = field(default_factory=list)
    usable_area_sf: float = 0.0

    def __post_init__(self) -> None:
        if self.usable_area_sf == 0.0:
            void_area = sum(v.area_sf for v in self.voids)
            self.usable_area_sf = max(0.0, self.area_sf - void_area)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "width_ft": round(self.width_ft, 2),
            "length_ft": round(self.length_ft, 2),
            "area_sf": round(self.area_sf, 1),
            "usable_area_sf": round(self.usable_area_sf, 1),
            "programs": self.programs,
            "voids": [v.to_dict() for v in self.voids],
        }


@dataclass
class CompromisedAnchor:
    room: str
    mass_id: str
    required_ft: str
    available_ft: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "room": self.room,
            "mass_id": self.mass_id,
            "required_ft": self.required_ft,
            "available_ft": self.available_ft,
            "reason": self.reason,
        }


@dataclass
class ValidationCheck:
    check: str
    passed: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "pass": self.passed,
            "message": self.message,
        }


@dataclass
class ResizeSuggestion:
    mass_id: str
    issue: str
    suggestion: str
    option_stories: int | None = None
    option_width_ft: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mass_id": self.mass_id,
            "issue": self.issue,
            "suggestion": self.suggestion,
            "option_stories": self.option_stories,
            "option_width_ft": (
                round(self.option_width_ft, 2)
                if self.option_width_ft is not None
                else None
            ),
        }


@dataclass
class SolvedMass:
    id: str
    name: str
    departments: list[str]
    floors: list[FloorPlate]
    target_gsf: float
    actual_gsf: float
    fit_delta_sf: float
    fit_pass: bool
    fixed_side: str = "width"
    fixed_dim_ft: float = 0.0
    pairing_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "departments": self.departments,
            "floors": [f.to_dict() for f in self.floors],
            "target_gsf": round(self.target_gsf, 1),
            "actual_gsf": round(self.actual_gsf, 1),
            "fit_delta_sf": round(self.fit_delta_sf, 1),
            "fit_pass": self.fit_pass,
            "fixed_side": self.fixed_side,
            "fixed_dim_ft": round(self.fixed_dim_ft, 2),
            "pairing_id": self.pairing_id,
        }


@dataclass
class MassingStudyResult:
    version: str = "1.0"
    units: str = "feet"
    study_id: str = ""
    gsf_tolerance: float = 0.03
    masses: list[SolvedMass] = field(default_factory=list)
    validation: list[ValidationCheck] = field(default_factory=list)
    compromised_anchor_rooms: list[CompromisedAnchor] = field(default_factory=list)
    resize_suggestions: list[ResizeSuggestion] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "units": self.units,
            "study_id": self.study_id,
            "gsf_tolerance": self.gsf_tolerance,
            "rationale": self.rationale,
            "masses": [m.to_dict() for m in self.masses],
            "validation": [v.to_dict() for v in self.validation],
            "compromised_anchor_rooms": [
                c.to_dict() for c in self.compromised_anchor_rooms
            ],
            "resize_suggestions": [s.to_dict() for s in self.resize_suggestions],
        }
