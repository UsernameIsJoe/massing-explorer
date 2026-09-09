"""Modality role cues: wording decides requirement / limitation / preference.

Numbers (digits or spelled) are values only — never the role.
Canonical cues:
  requirement — must, needs to, has to, requires, exactly
  limitation  — should be, cannot exceed, no more than, at least, should stay under
  preference  — prefer, ideally, would rather, better if, if possible, around, closer to
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from massing_explorer.brief import _role_from_words

TRAINING = Path(r"c:\Users\tu\Downloads\llm_modality_training_50_sets.txt")
IMPERATIVE_USE = re.compile(r"^\s*use\b", re.I)


class ModalityCueTests(unittest.TestCase):
    def test_requirement_cues(self) -> None:
        for text in (
            "must be together",
            "needs to be wide",
            "has to share a mass",
            "requires four masses",
            "exactly four masses",
            "there should be exactly three masses",
            "needs a width of eighty ft",
        ):
            self.assertEqual(_role_from_words(text), "requirement", text)

    def test_limitation_cues(self) -> None:
        for text in (
            "should be no taller than three stories",
            "cannot exceed one hundred eighty ft",
            "no more than four floors",
            "at least forty ft wide",
            "should stay under four hundred ft",
            "must not exceed three stories",
            "cannot be separated",
            "no mass should exceed that height",
            "gym should be on the ground floor",
        ):
            self.assertEqual(_role_from_words(text), "limitation", text)

    def test_preference_cues(self) -> None:
        for text in (
            "prefer a three to five proportion",
            "ideally wider",
            "would rather keep most masses lower",
            "better if larger",
            "if possible closer to eighty five",
            "around thirty percent open",
            "closer to thirty ft would be ideal",
            "I'd like the academic mass longer",
            "I’d like the academic mass longer",
            "I would prefer all but one to stay under three stories",
        ):
            self.assertEqual(_role_from_words(text), "preference", text)

    def test_numbers_do_not_decide_role(self) -> None:
        self.assertEqual(_role_from_words("must be 85 ft wide"), "requirement")
        self.assertEqual(_role_from_words("must be eighty-five ft wide"), "requirement")
        self.assertEqual(_role_from_words("prefer about four masses"), "preference")
        self.assertEqual(_role_from_words("no more than 4 floors"), "limitation")
        self.assertEqual(_role_from_words("no more than four floors"), "limitation")

    def test_training_file_labeled_lines(self) -> None:
        if not TRAINING.exists():
            self.skipTest("training file not present")
        sets: list[dict[str, str]] = []
        cur: dict[str, str] | None = None
        for line in TRAINING.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("SET "):
                cur = {"requirement": "", "limitation": "", "preference": ""}
                sets.append(cur)
            elif cur is None:
                continue
            elif line.startswith("REQUIREMENT:"):
                cur["requirement"] = line.split(":", 1)[1].strip()
            elif line.startswith("LIMITATION:"):
                cur["limitation"] = line.split(":", 1)[1].strip()
            elif line.startswith("PREFERENCE:"):
                cur["preference"] = line.split(":", 1)[1].strip()

        mismatches: list[str] = []
        for i, s in enumerate(sets, start=1):
            for kind in ("requirement", "limitation", "preference"):
                text = s[kind]
                if not text:
                    continue
                got = _role_from_words(text, default="unknown")
                if got == kind:
                    continue
                # Imperative "Use N masses" has no modality cue; mass_count
                # extractors still default that lever to requirement.
                if (
                    got == "unknown"
                    and kind == "requirement"
                    and IMPERATIVE_USE.search(text)
                ):
                    continue
                # "should be" is a limitation cue even when a training label
                # marked a soft ground-floor ask as requirement.
                if (
                    kind == "requirement"
                    and got == "limitation"
                    and re.search(r"\bshould\s+be\b", text, flags=re.I)
                    and not re.search(r"\bexactly\b", text, flags=re.I)
                ):
                    continue
                # "exactly" is a requirement cue; a training line that pairs
                # "should be exactly" under Limitation still reads as requirement.
                if (
                    kind == "limitation"
                    and got == "requirement"
                    and re.search(r"\bexactly\b", text, flags=re.I)
                ):
                    continue
                mismatches.append(f"SET {i} {kind} -> {got}: {text}")
        self.assertEqual(mismatches, [], "\n".join(mismatches))


if __name__ == "__main__":
    unittest.main()
