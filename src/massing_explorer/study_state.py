from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MassGrouping:
    id: str
    name: str
    departments: list[str] = field(default_factory=list)
    story_count: int = 1
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "departments": self.departments,
            "story_count": self.story_count,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MassGrouping:
        return cls(
            id=data["id"],
            name=data["name"],
            departments=list(data.get("departments", [])),
            story_count=int(data.get("story_count", 1)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class MassPairing:
    """Two or more masses that sit together along a frontage.

    total_length_ft is the length they must share when length_is_cap is false.
    When length_is_cap is true it is only a maximum — the bar is sized for
    function and must not stretch to fill the cap.
    """

    id: str
    mass_ids: list[str] = field(default_factory=list)
    total_length_ft: float = 0.0
    length_is_cap: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mass_ids": self.mass_ids,
            "total_length_ft": self.total_length_ft,
            "length_is_cap": self.length_is_cap,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MassPairing:
        return cls(
            id=data["id"],
            mass_ids=list(data.get("mass_ids", [])),
            total_length_ft=float(data.get("total_length_ft", 0.0)),
            length_is_cap=bool(data.get("length_is_cap", False)),
            notes=str(data.get("notes", "")),
        )


@dataclass
class ChatMessage:
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatMessage:
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls"),
        )
