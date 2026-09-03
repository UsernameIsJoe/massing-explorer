"""
Manual check: does the local LLM reach for the search tool on its own?

Not part of the unit suite - it needs a running Ollama server. Run directly:
    python tests/llm_search_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from massing_explorer.load import load_program_file
from massing_explorer.ollama_client import OllamaClient
from massing_explorer.session import StudySession
from massing_explorer.tools import TOOL_DEFINITIONS, execute_tool, set_grouping

PROGRAM = r"c:\Users\tu\Downloads\Underwood_Elementary_Space_Summary.xlsx"
CONFIG = "config/project.example.yaml"
SYSTEM = Path("prompts/chat_system.txt").read_text(encoding="utf-8")

CORE = "CORE ACADEMIC"
SPED = "SPECIAL EDUCATION"
ART = "ART & MUSIC"
HPE = "HEALTH & PHYSICAL EDUCATION"
DINING = "DINING & FOOD SERVICE"
MEDIA = "MEDIA CENTER"
ADMIN = "ADMINISTRATION & GUIDANCE"
CUSTODIAL = "CUSTODIAL & MAINTENANCE"
MEDICAL = "MEDICAL"

# Prompt -> tool we expect the model to select
CASES = [
    (
        "My site gives me 300 ft of frontage, nothing can be wider than 100 ft "
        "or longer than 200 ft. What massing would actually fit? Keep it as low "
        "as you can.",
        "search_site_schemes",
    ),
    (
        "Make the academic wing 70 ft wide and the community base 100 ft wide, "
        "then tell me if that breaks my 300 ft frontage.",
        "solve_dimensions",
    ),
]


def build_session(study_id: str) -> StudySession:
    program = load_program_file(PROGRAM, config_path=CONFIG)
    session = StudySession(study_id=study_id, program=program, config_path=CONFIG)
    set_grouping(
        session,
        [
            {
                "id": "academic",
                "name": "Academic Bar",
                "departments": [CORE, SPED, ART],
                "story_count": 4,
            },
            {
                "id": "community",
                "name": "Community Base",
                "departments": [HPE, DINING, MEDIA, ADMIN, MEDICAL, CUSTODIAL],
                "story_count": 2,
            },
        ],
    )
    session.double_height_rooms.append("Gymnasium")
    session.save()
    return session


def run_case(prompt: str, expected: str, index: int) -> bool:
    session = build_session(f"llm_check_{index}")
    client = OllamaClient()
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ]

    called: list[str] = []
    for _turn in range(6):
        # OllamaClient.chat returns the whole response; the reply is in "message"
        message = client.chat(messages, tools=TOOL_DEFINITIONS).get("message", {})
        calls = message.get("tool_calls") or []
        if not calls:
            print(f"    final: {(message.get('content') or '').strip()[:400]}")
            break
        messages.append(message)
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw = fn.get("arguments")
            args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            called.append(name)
            print(f"    -> {name}({json.dumps(args)[:160]})")
            out = execute_tool(session, name, args)
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(out)[:4000],
                    "name": name,
                }
            )

    ok = expected in called
    print(f"    tools: {called}")
    print(f"    expected {expected}: {'PASS' if ok else 'FAIL'}\n")
    return ok


def main() -> int:
    passed = 0
    for i, (prompt, expected) in enumerate(CASES):
        print(f"[{i + 1}] {prompt}")
        if run_case(prompt, expected, i):
            passed += 1
    print(f"{passed}/{len(CASES)} cases selected the right tool")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
