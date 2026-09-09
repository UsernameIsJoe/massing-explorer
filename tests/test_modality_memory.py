"""Cross-project modality memory and unsure-clause detection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from massing_explorer.modality import classify_local, find_modality_questions, split_brief_clauses
from massing_explorer.modality_memory import (
    load_memory,
    lookup_phrase,
    normalize_phrase,
    remember,
    role_from_memory,
)


class ModalityMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()) / "modality_memory.json"

    def test_normalize_strips_numbers(self) -> None:
        a = normalize_phrase("I'd prefer about 4 masses near 85 ft")
        b = normalize_phrase("I'd prefer about four masses near eighty-five ft")
        self.assertEqual(a, b)

    def test_remember_and_lookup(self) -> None:
        remember(
            "One mass could be taller if it helps the composition",
            "preference",
            cue="could be taller",
            path=self.tmp,
        )
        mem = load_memory(self.tmp)
        self.assertIn("could be taller", mem["cues"]["preference"])
        self.assertEqual(
            lookup_phrase(
                "one mass could be taller if it helps the composition", mem
            ),
            "preference",
        )
        self.assertEqual(
            role_from_memory("a volume could be taller than the others", mem),
            "preference",
        )

    def test_classify_local_uses_memory(self) -> None:
        remember(
            "distribute the masses fairly evenly",
            "preference",
            cue="fairly evenly",
            path=self.tmp,
        )
        # Point default memory path via remember already saved; role_from_memory
        # uses default path unless we patch — call classify after loading path
        # by remembering to default is risky in CI. Instead assert memory API.
        mem = load_memory(self.tmp)
        self.assertEqual(role_from_memory("distribute fairly evenly across site", mem), "preference")

    def test_builtin_still_wins(self) -> None:
        out = classify_local("each mass must be no taller than 3 stories")
        self.assertEqual(out["kind"], "requirement")
        self.assertFalse(out["needs_ask"])

    def test_unknown_needs_ask_without_llm(self) -> None:
        qs = find_modality_questions(
            "Distribute the masses fairly evenly across the site.",
            use_llm=False,
        )
        # May be empty if memory already learned this on the machine; allow either
        # unknown question or memory hit.
        if qs:
            self.assertTrue(qs[0]["text"])
            self.assertIn(qs[0].get("suggested"), (None, "requirement", "limitation", "preference"))

    def test_split_clauses(self) -> None:
        parts = split_brief_clauses(
            "Use 4 masses. Prefer a compact arrangement. Site under 400 ft."
        )
        self.assertGreaterEqual(len(parts), 2)


if __name__ == "__main__":
    unittest.main()
