from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Department, GrossingConfig, ProgramStudy, Room
from .study_state import ChatMessage, MassGrouping


STUDIES_DIR = Path("studies")


@dataclass
class StudySession:
    study_id: str
    program: ProgramStudy
    config_path: str = ""
    model: str = "qwen2.5:7b"
    masses: list[MassGrouping] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    adjacency_notes: list[str] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    double_height_rooms: list[str] = field(default_factory=list)
    last_massing: dict[str, Any] | None = None

    @property
    def study_dir(self) -> Path:
        return STUDIES_DIR / self.study_id

    @property
    def state_path(self) -> Path:
        return self.study_dir / "state.json"

    def department_names(self) -> list[str]:
        return [d.name for d in self.program.departments]

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "config_path": self.config_path,
            "model": self.model,
            "program": self.program.to_dict(),
            "masses": [m.to_dict() for m in self.masses],
            "constraints": self.constraints,
            "adjacency_notes": self.adjacency_notes,
            "double_height_rooms": self.double_height_rooms,
            "last_massing": self.last_massing,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StudySession:
        grossing_data = data["program"]["grossing"]
        grossing = GrossingConfig(
            area_adjustment=grossing_data["area_adjustment"],
            grossing_factor=grossing_data["grossing_factor"],
        )
        program = ProgramStudy(
            source_file=data["program"].get("source_file", ""),
            grossing=grossing,
            totals=data["program"].get("totals", {}),
            metadata=data["program"].get("metadata", {}),
        )
        for r in data["program"].get("rooms", []):
            program.rooms.append(
                Room(
                    room_name=r["room_name"],
                    qty=r["qty"],
                    area_sf=r["area_sf"],
                    department=r["department"],
                    total_area_sf=r.get("total_area_sf", 0),
                    comments=r.get("comments", ""),
                )
            )
        for d in data["program"].get("departments", []):
            program.departments.append(
                Department(
                    name=d["name"],
                    nfa_sf=d["nfa_sf"],
                    target_gsf=d["target_gsf"],
                    room_count=d.get("room_count", 0),
                    declared_total_sf=d.get("declared_total_sf"),
                )
            )

        return cls(
            study_id=data["study_id"],
            config_path=data.get("config_path", ""),
            model=data.get("model", "qwen2.5:7b"),
            program=program,
            masses=[MassGrouping.from_dict(m) for m in data.get("masses", [])],
            constraints=data.get("constraints", {}),
            adjacency_notes=data.get("adjacency_notes", []),
            double_height_rooms=data.get("double_height_rooms", []),
            last_massing=data.get("last_massing"),
            messages=[ChatMessage.from_dict(m) for m in data.get("messages", [])],
        )

    def save(self) -> None:
        self.study_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.to_dict(), indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, study_id: str) -> StudySession:
        path = STUDIES_DIR / study_id / "state.json"
        if not path.exists():
            raise FileNotFoundError(f"No study found: {study_id} ({path})")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


def slugify_study_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "study"
