"""
Scheme search: find story counts and widths that fit a site envelope.

Everything before this module *evaluated* a scheme the user specified — you gave
it widths and story counts and it told you whether they broke a limit. It could
not answer "what would fit?". This module does.

Design rule: the search only *proposes*. Every candidate it returns has been run
through `solve_massing_study` — the same code path that produces the reports —
and is dropped if any check fails. There is deliberately no second dimension or
validation path here that could disagree with the solver.

Free variables per mass are story count and width. Everything else follows:

    plate  = plate_area(target_gsf, stories, void_area)
    length = plate / width

so a mass is feasible when some width satisfies both limits at once:

    width >= plate / max_length      (short enough)
    width <= max_width               (narrow enough)

For a pairing the width is not free at all — it is fixed by the shared
frontage, `W = sum(plates) / total_length` — so only the story counts vary.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Iterator

from .config import load_project_config
from .solver import (
    _mass_target_gsf,
    _match_anchor_rooms,
    _void_area_for_mass,
    rectangle_fits_room,
    resolve_step_weights,
    solve_massing_study,
    solve_paired_masses,
    solve_stepped_plates,
)
from .session import StudySession

WIDTH_STEP_FT = 5.0
WIDTHS_PER_STORY_COUNT = 5
DEFAULT_MIN_WIDTH_FT = 45.0
DEFAULT_MAX_STORIES = 4

# The search must be *at least as strict* as the solver on every shared limit,
# otherwise it proposes schemes the solver then rejects. Snapping a width down
# to a round number lengthens the plate, so lower bounds are always rounded up.
LIMIT_EPS = 0.0
ASPECT_CAP = 6.0  # aspect ratios past this are treated as equally bad
MAX_COMBINATIONS = 200_000  # hard bound so a wide search cannot hang

# A classroom bar much wider than this cannot daylight its rooms from the
# facade, so the search should not treat "as wide as legally allowed" as best.
DEFAULT_DAYLIGHT_MAX_WIDTH_FT = 90.0
DEFAULT_DAYLIGHT_KEYWORDS = (
    "academic",
    "special education",
    "classroom",
    "art",
    "music",
    "science",
)

PREFERENCE_WEIGHTS: dict[str, dict[str, float]] = {
    # height   = how tall (gsf-weighted mean storeys)
    # spread   = how much of the available frontage is consumed
    # aspect   = how slender the plates are
    # daylight = how far a daylight-sensitive mass exceeds a workable width
    # Height/spread express taste and vary by mode; daylight and aspect are
    # buildability, so they carry the same weight in every mode.
    "balanced": {"height": 1.0, "spread": 1.0, "aspect": 0.6, "daylight": 1.5},
    "low_rise": {"height": 3.0, "spread": 0.4, "aspect": 0.6, "daylight": 1.5},
    "compact": {"height": 0.4, "spread": 3.0, "aspect": 0.6, "daylight": 1.5},
}


@dataclass
class SiteEnvelope:
    max_building_length_ft: float | None = None
    max_building_width_ft: float | None = None
    max_total_length_ft: float | None = None
    max_stories: int = DEFAULT_MAX_STORIES
    min_width_ft: float = DEFAULT_MIN_WIDTH_FT

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_building_length_ft": self.max_building_length_ft,
            "max_building_width_ft": self.max_building_width_ft,
            "max_total_length_ft": self.max_total_length_ft,
            "max_stories": self.max_stories,
            "min_width_ft": self.min_width_ft,
        }

    def constraints(self) -> dict[str, float]:
        """Envelope as solver constraint keys, so verification sees the limits."""
        out: dict[str, float] = {}
        if self.max_building_length_ft is not None:
            out["max_building_length_ft"] = self.max_building_length_ft
        if self.max_building_width_ft is not None:
            out["max_building_width_ft"] = self.max_building_width_ft
        if self.max_total_length_ft is not None:
            out["max_total_length_ft"] = self.max_total_length_ft
        return out


@dataclass
class MassOption:
    mass_id: str
    mass_name: str
    stories: int
    width_ft: float
    length_ft: float  # ground floor: this is the frontage the mass occupies
    plate_sf: float  # ground floor plate
    target_gsf: float
    daylight_sensitive: bool = False
    daylight_max_width_ft: float = DEFAULT_DAYLIGHT_MAX_WIDTH_FT
    stepped: bool = False

    @property
    def aspect(self) -> float:
        if self.width_ft <= 0 or self.length_ft <= 0:
            return ASPECT_CAP
        return max(self.length_ft / self.width_ft, self.width_ft / self.length_ft)

    @property
    def daylight_penalty(self) -> float:
        """0 when the mass can daylight its rooms, rising as it gets too deep."""
        if not self.daylight_sensitive or self.daylight_max_width_ft <= 0:
            return 0.0
        excess = self.width_ft - self.daylight_max_width_ft
        if excess <= 0:
            return 0.0
        return min(1.0, excess / self.daylight_max_width_ft)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mass_id": self.mass_id,
            "mass_name": self.mass_name,
            "stories": self.stories,
            "width_ft": round(self.width_ft, 1),
            "length_ft": round(self.length_ft, 1),
            "plate_sf": round(self.plate_sf),
            "target_gsf": round(self.target_gsf),
            "stepped": self.stepped,
        }


@dataclass
class SchemeCandidate:
    options: list[MassOption]
    total_length_ft: float
    score: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    verified: bool = False
    failed_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_length_ft": round(self.total_length_ft, 1),
            "score": round(self.score, 4),
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "verified": self.verified,
            "failed_checks": self.failed_checks,
            "masses": [o.to_dict() for o in self.options],
        }

    def summary(self) -> str:
        parts = [
            f"{o.mass_name} {o.width_ft:.0f}x{o.length_ft:.0f} ft / {o.stories} st"
            for o in self.options
        ]
        return f"{self.total_length_ft:.0f} ft total - " + "; ".join(parts)


def _width_grid(lo: float, hi: float, count: int = WIDTHS_PER_STORY_COUNT) -> list[float]:
    """Candidate widths spanning a feasible range, snapped to a round step."""
    if hi < lo:
        return []

    # Round the narrow end up and the wide end down: a width below `lo` makes the
    # plate longer than the length cap, and a width above `hi` is too deep.
    lo = math.ceil(lo * 100) / 100
    hi = math.floor(hi * 100) / 100
    if hi < lo:
        return []
    if hi - lo < WIDTH_STEP_FT:
        return [lo]

    widths: set[float] = {lo, hi}
    step_lo = math.ceil(lo / WIDTH_STEP_FT) * WIDTH_STEP_FT
    step_hi = math.floor(hi / WIDTH_STEP_FT) * WIDTH_STEP_FT
    if step_hi > step_lo:
        span = step_hi - step_lo
        inner = max(1, count - 2)
        for i in range(inner + 1):
            w = step_lo + span * i / inner
            widths.add(round(w / WIDTH_STEP_FT) * WIDTH_STEP_FT)
    return sorted(w for w in widths if lo - 0.01 <= w <= hi + 0.01)


def _anchors_fit(width: float, length: float, anchors: list[dict[str, Any]]) -> bool:
    for a in anchors:
        rw, rl = a["min_width_ft"], a["min_length_ft"]
        if rw <= 0 or rl <= 0:
            continue
        if not rectangle_fits_room(width, length, rw, rl):
            return False
    return True


def _daylight_settings(config: dict[str, Any]) -> tuple[float, tuple[str, ...]]:
    daylight = (config.get("daylight") or {}) if config else {}
    max_width = float(
        daylight.get("preferred_max_width_ft", DEFAULT_DAYLIGHT_MAX_WIDTH_FT)
    )
    keywords = daylight.get("departments")
    if keywords:
        return max_width, tuple(str(k).strip().lower() for k in keywords)
    return max_width, DEFAULT_DAYLIGHT_KEYWORDS


def _is_daylight_sensitive(departments: list[str], keywords: tuple[str, ...]) -> bool:
    return any(k in d.lower() for d in departments for k in keywords)


def _mass_context(
    session: StudySession, mass_def: Any, config: dict[str, Any]
) -> tuple[float, float, list[dict[str, Any]]]:
    target = _mass_target_gsf(session, mass_def.departments)
    voids = _void_area_for_mass(session, mass_def.departments, config)
    anchors = _match_anchor_rooms(session, mass_def.departments, config)
    return target, sum(v.area_sf for v in voids), anchors


def _mass_options(
    session: StudySession,
    mass_def: Any,
    config: dict[str, Any],
    envelope: SiteEnvelope,
) -> list[MassOption]:
    """Feasible (stories, width) pairs for one free-standing mass."""
    target, void_area, anchors = _mass_context(session, mass_def, config)
    dl_width, dl_keywords = _daylight_settings(config)
    sensitive = _is_daylight_sensitive(mass_def.departments, dl_keywords)
    options: list[MassOption] = []

    # Explicit step weights describe named levels, so they pin the height. A
    # taper is a shape rather than a set of floors, so the height stays free.
    explicit_steps = session.floor_steps.get(mass_def.id)
    story_range = (
        [len(explicit_steps)]
        if explicit_steps
        else list(range(1, envelope.max_stories + 1))
    )

    for stories in story_range:
        weights = resolve_step_weights(session, mass_def.id, stories)
        plates = solve_stepped_plates(target, weights, void_area)
        if not plates or plates[0] <= 0:
            continue
        ground = plates[0]
        longest = max(plates)  # a cantilever can put the worst floor upstairs

        lo = envelope.min_width_ft
        # A length cap rejects bars that run past it. It is not the length to
        # generate, so the width grid does not start at plate / cap.
        # Without a stated width cap, stop at a square plate; wider than that is
        # the same rectangle rotated.
        hi = envelope.max_building_width_ft or max(lo, math.sqrt(ground))

        for width in _width_grid(lo, hi):
            if (
                envelope.max_building_length_ft
                and longest / width > envelope.max_building_length_ft + LIMIT_EPS
            ):
                continue
            # Anchor rooms sit on the ground floor
            if not _anchors_fit(width, ground / width, anchors):
                continue
            # Prefer widths that can leave a usable L around a double-height void
            if void_area > 0 and stories >= 2:
                from .layout import (
                    clear_dims_for_departments,
                    min_arm_depth_ft,
                    plate_can_host_void_layout,
                )

                voids = _void_area_for_mass(session, mass_def.departments, config)
                void_plate = plates[1] if len(plates) > 1 else ground
                clears = []
                reqs = clear_dims_for_departments(list(mass_def.departments), config)
                void_rooms = {v.room.lower() for v in voids}
                for req in reqs.values():
                    room = (req.room or "").lower()
                    if room and any(room in v or v in room for v in void_rooms):
                        continue
                    anchors_cfg = (config.get("anchor_rooms") or {}).get(req.room) or {}
                    if anchors_cfg.get("double_height"):
                        continue
                    clears.append(req)
                if not plate_can_host_void_layout(
                    width,
                    void_plate / width,
                    voids,
                    clears,
                    min_arm_depth_ft(config),
                ):
                    continue
            options.append(
                MassOption(
                    mass_id=mass_def.id,
                    mass_name=mass_def.name,
                    stories=stories,
                    width_ft=width,
                    length_ft=ground / width,
                    plate_sf=ground,
                    target_gsf=target,
                    daylight_sensitive=sensitive,
                    daylight_max_width_ft=dl_width,
                    stepped=max(weights) - min(weights) > 1e-9,
                )
            )
    return options


def _pairing_options(
    session: StudySession,
    members: list[Any],
    total_length_ft: float,
    config: dict[str, Any],
    envelope: SiteEnvelope,
) -> list[list[MassOption]]:
    """
    Feasible story-count combinations for a shared-width pairing.

    Width is not free here: the shared frontage fixes it, and the member lengths
    always sum to `total_length_ft`. Only the story counts vary.
    """
    contexts = [_mass_context(session, m, config) for m in members]
    dl_width, dl_keywords = _daylight_settings(config)
    results: list[list[MassOption]] = []

    def walk(index: int, chosen: list[int]) -> None:
        if index == len(members):
            # The shared width divides the ground-floor plates, since the
            # frontage the pair occupies is set by its footprint on the site.
            ground: list[float] = []
            longest: list[float] = []
            weights_by_member: list[list[float]] = []
            for i, member in enumerate(members):
                weights = resolve_step_weights(session, member.id, chosen[i])
                plates = solve_stepped_plates(
                    contexts[i][0], weights, contexts[i][1]
                )
                if not plates:
                    return
                ground.append(plates[0])
                longest.append(max(plates))
                weights_by_member.append(weights)

            width, lengths = solve_paired_masses(ground, total_length_ft)
            if width < envelope.min_width_ft - LIMIT_EPS:
                return
            if (
                envelope.max_building_width_ft
                and width > envelope.max_building_width_ft + LIMIT_EPS
            ):
                return
            group: list[MassOption] = []
            for i, member in enumerate(members):
                if (
                    envelope.max_building_length_ft
                    and longest[i] / width
                    > envelope.max_building_length_ft + LIMIT_EPS
                ):
                    return
                if not _anchors_fit(width, lengths[i], contexts[i][2]):
                    return
                member_weights = weights_by_member[i]
                group.append(
                    MassOption(
                        mass_id=member.id,
                        mass_name=member.name,
                        stories=chosen[i],
                        width_ft=width,
                        length_ft=lengths[i],
                        plate_sf=ground[i],
                        target_gsf=contexts[i][0],
                        daylight_sensitive=_is_daylight_sensitive(
                            member.departments, dl_keywords
                        ),
                        daylight_max_width_ft=dl_width,
                        stepped=max(member_weights) - min(member_weights) > 1e-9,
                    )
                )
            results.append(group)
            return

        member = members[index]
        explicit = session.floor_steps.get(member.id)
        story_range = (
            [len(explicit)] if explicit else range(1, envelope.max_stories + 1)
        )
        for stories in story_range:
            walk(index + 1, [*chosen, stories])

    walk(0, [])
    return results


def _groups(
    session: StudySession, config: dict[str, Any], envelope: SiteEnvelope
) -> list[list[list[MassOption]]]:
    """
    Split masses into independently-choosable groups.

    A pairing is one group (its members are coupled through the shared width);
    every other mass is a group of its own.
    """
    by_id = {m.id: m for m in session.masses}
    grouped: set[str] = set()
    groups: list[list[list[MassOption]]] = []

    for pairing in session.pairings:
        members = [by_id[mid] for mid in pairing.mass_ids if mid in by_id]
        if len(members) < 2 or pairing.total_length_ft <= 0:
            continue
        assignments = _pairing_options(
            session, members, pairing.total_length_ft, config, envelope
        )
        if not assignments:
            return []  # pairing cannot be satisfied at all
        groups.append(assignments)
        grouped.update(m.id for m in members)

    for mass_def in session.masses:
        if mass_def.id in grouped:
            continue
        options = _mass_options(session, mass_def, config, envelope)
        if not options:
            return []  # this mass cannot fit the envelope in any configuration
        groups.append([[o] for o in options])

    return groups


def _combine(
    groups: list[list[list[MassOption]]], max_total_length_ft: float | None
) -> Iterator[list[MassOption]]:
    """Enumerate group choices, pruning branches that already overrun frontage."""
    # Cheapest remaining frontage per group, for the pruning bound
    min_tail = [0.0] * (len(groups) + 1)
    for i in range(len(groups) - 1, -1, -1):
        cheapest = min(sum(o.length_ft for o in a) for a in groups[i])
        min_tail[i] = min_tail[i + 1] + cheapest

    emitted = 0

    def walk(index: int, chosen: list[MassOption], length: float) -> Iterator[list[MassOption]]:
        nonlocal emitted
        if emitted >= MAX_COMBINATIONS:
            return
        if max_total_length_ft is not None:
            if length + min_tail[index] > max_total_length_ft + 1.0:
                return
        if index == len(groups):
            emitted += 1
            yield list(chosen)
            return
        for assignment in groups[index]:
            span = sum(o.length_ft for o in assignment)
            yield from walk(index + 1, chosen + assignment, length + span)

    yield from walk(0, [], 0.0)


def _score(
    candidates: list[SchemeCandidate],
    envelope: SiteEnvelope,
    preference: str,
) -> None:
    """
    Attach comparable metrics and a weighted score (lower is better).

    Metrics are absolute, not normalised across the candidate set: a scheme's
    score must not change because of what else happens to be in the list.
    """
    weights = PREFERENCE_WEIGHTS.get(preference, PREFERENCE_WEIGHTS["balanced"])
    reference_length = envelope.max_total_length_ft or max(
        (c.total_length_ft for c in candidates), default=1.0
    )
    reference_length = max(reference_length, 1e-6)

    for cand in candidates:
        total_gsf = sum(o.target_gsf for o in cand.options) or 1.0
        mean_stories = (
            sum(o.stories * o.target_gsf for o in cand.options) / total_gsf
        )
        height = (mean_stories - 1.0) / max(envelope.max_stories - 1, 1)
        spread = cand.total_length_ft / reference_length
        aspect = sum(min(o.aspect, ASPECT_CAP) for o in cand.options) / (
            len(cand.options) * ASPECT_CAP
        )
        daylight = max((o.daylight_penalty for o in cand.options), default=0.0)

        cand.metrics = {
            "height": height,
            "spread": spread,
            "aspect": aspect,
            "daylight": daylight,
            "mean_stories": mean_stories,
        }
        cand.score = (
            weights["height"] * height
            + weights["spread"] * spread
            + weights["aspect"] * aspect
            + weights["daylight"] * daylight
        )


def _verify(
    session: StudySession,
    candidate: SchemeCandidate,
    envelope: SiteEnvelope,
    config_path: str | None,
) -> None:
    """
    Run a candidate through the real solver and record any failed check.

    This is the whole trust model of the search: proposals are only returned if
    the same code path that writes the reports agrees they pass.
    """
    trial = copy.deepcopy(session)
    trial.constraints.update(envelope.constraints())
    apply_scheme(trial, candidate, save=False)
    result = solve_massing_study(trial, config_path=config_path)
    candidate.failed_checks = [v.message for v in result.validation if not v.passed]
    candidate.verified = not candidate.failed_checks


def apply_scheme_from_dict(
    session: StudySession,
    data: dict[str, Any],
    save: bool = True,
) -> dict[str, Any]:
    """Apply a scheme stored in its serialised form (as saved on the session)."""
    by_id = {m.id: m for m in session.masses}
    applied: list[str] = []
    for entry in data.get("masses", []):
        mass_id = entry.get("mass_id")
        mass = by_id.get(mass_id)
        if mass is None:
            continue
        mass.story_count = int(entry["stories"])
        session.constraints[f"{mass_id}_width_ft"] = float(entry["width_ft"])
        applied.append(
            f"{entry.get('mass_name', mass_id)}: {entry['width_ft']:g} ft wide, "
            f"{entry['stories']} stories"
        )
    if save:
        session.save()
    return {"ok": bool(applied), "applied": applied}


def apply_scheme(
    session: StudySession,
    candidate: SchemeCandidate,
    save: bool = True,
) -> dict[str, Any]:
    """Write a candidate's story counts and widths onto a session."""
    by_id = {m.id: m for m in session.masses}
    for option in candidate.options:
        mass = by_id.get(option.mass_id)
        if mass is not None:
            mass.story_count = option.stories
        session.constraints[f"{option.mass_id}_width_ft"] = round(option.width_ft, 2)
    if save:
        session.save()
    return {
        "ok": True,
        "applied": candidate.to_dict(),
    }


def search_schemes(
    session: StudySession,
    envelope: SiteEnvelope,
    preference: str = "balanced",
    top_n: int = 3,
    config_path: str | None = None,
    verify: bool = True,
) -> tuple[list[SchemeCandidate], list[str]]:
    """
    Find story-count / width combinations that fit the envelope.

    Returns (ranked verified candidates, notes explaining any dead end).
    """
    notes: list[str] = []
    if not session.masses:
        return [], ["No masses defined. Set groupings before searching."]

    config = load_project_config(config_path or session.config_path or None)
    groups = _groups(session, config, envelope)
    if not groups:
        return [], [
            "No mass can satisfy the envelope on its own. Relax the width or "
            "length cap, allow more stories, or regroup the program."
        ]

    raw = [
        SchemeCandidate(options=combo, total_length_ft=sum(o.length_ft for o in combo))
        for combo in _combine(groups, envelope.max_total_length_ft)
    ]
    if not raw:
        cheapest = sum(
            min(sum(o.length_ft for o in a) for a in group) for group in groups
        )
        notes.append(
            f"Every combination overruns the site: the shortest possible layout is "
            f"{cheapest:.0f} ft against a {envelope.max_total_length_ft:.0f} ft cap."
        )
        return [], notes

    _score(raw, envelope, preference)
    raw.sort(key=lambda c: (c.score, c.total_length_ft))

    if not verify:
        return raw[:top_n], notes

    # Walk the ranking and keep the best schemes the solver actually accepts.
    verified: list[SchemeCandidate] = []
    rejected = 0
    for cand in raw:
        if len(verified) >= top_n:
            break
        _verify(session, cand, envelope, config_path)
        if cand.verified:
            verified.append(cand)
        else:
            rejected += 1
        if rejected > 40:  # ranking is clearly unproductive; stop churning
            break

    if rejected:
        notes.append(
            f"{rejected} higher-ranked scheme(s) were discarded because the solver "
            f"rejected them."
        )
    if not verified:
        notes.append(
            "Nothing passed verification. The envelope is likely infeasible for "
            "this grouping."
        )
    return verified, notes
