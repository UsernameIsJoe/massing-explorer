from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ollama_client import OllamaClient, OllamaError
from .session import StudySession
from .study_state import ChatMessage
from .tools import TOOL_DEFINITIONS, execute_tool, get_department_summary


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "chat_system.txt"
MAX_TOOL_ROUNDS = 8


def load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return "You are a massing study assistant. Use tools for all area calculations."


def _build_ollama_messages(session: StudySession) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": load_system_prompt()},
    ]
    # Inject program context once at start if no prior messages
    if not session.messages:
        summary = get_department_summary(session)
        messages.append(
            {
                "role": "system",
                "content": (
                    "Program loaded. Department summary from engine:\n"
                    + json.dumps(summary, indent=2)
                ),
            }
        )

    for msg in session.messages:
        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        messages.append(entry)
    return messages


def _print_assistant(content: str) -> None:
    print(f"\nAssistant:\n{content}\n")


def _commit_brief_with_reading(
    session: StudySession,
    client: OllamaClient,
    user_text: str,
    parsed: Any,
    reading: Any = None,
) -> dict[str, Any]:
    """
    Model chooses levers, engine commits and checks, model picks a scheme
    or one repair. No new geometry tools.
    """
    from .brief import apply_parsed_brief
    from .reading import request_reading, request_repair, request_scheme_index
    from .tools import apply_scheme, resize_mass

    if reading is None:
        reading = request_reading(
            client, user_text, session.department_names(), parsed.to_dict()
        )
    engine = apply_parsed_brief(session, parsed, reading=reading)
    engine["scheme_pick"] = None
    engine["repair"] = None

    schemes = (engine.get("search") or {}).get("schemes") or []
    index = request_scheme_index(client, user_text, schemes, reading)
    if index not in (None, 0):
        solved = apply_scheme(session, index)
        engine["solved"] = solved
        engine["scheme_pick"] = {"index": index}
    elif schemes:
        engine["scheme_pick"] = {"index": 0}

    solved = engine.get("solved") or {}
    failed = list(solved.get("failed_checks") or [])
    if failed:
        masses = [
            {"id": m.id, "name": m.name, "stories": m.story_count}
            for m in session.masses
        ]
        repair = request_repair(client, failed, masses)
        if repair and repair.get("action") == "add_story":
            mass = next(m for m in session.masses if m.id == repair["mass_id"])
            repaired = resize_mass(
                session, mass.id, story_count=mass.story_count + 1
            )
            engine["repair"] = {
                "action": "add_story",
                "mass_id": mass.id,
                "reason": repair.get("reason"),
                "result_ok": repaired.get("ok"),
                "all_checks_passed": repaired.get("all_checks_passed"),
                "failed_checks": repaired.get("failed_checks"),
            }
            if repaired.get("ok"):
                engine["solved"] = repaired
    return engine


def run_chat_turn(session: StudySession, client: OllamaClient, user_text: str) -> str:
    """Process one user message; may involve multiple tool-call rounds."""
    session.messages.append(ChatMessage(role="user", content=user_text))

    # Grouping and site limits come from the engine, not the LLM. Run the
    # brief through the pipeline first so a message like "custodial and dining
    # should stay together, site length is 300" is already applied.
    from .brief import apply_brief, parse_brief, should_apply_brief

    parsed = parse_brief(user_text, session.department_names())
    engine_note = ""
    from .reading import request_reading

    reading = request_reading(
        client, user_text, session.department_names(), parsed.to_dict()
    )
    if should_apply_brief(session, parsed) or not reading.empty:
        engine = _commit_brief_with_reading(
            session, client, user_text, parsed, reading=reading
        )
        compact = {
            "parsed": engine.get("parsed"),
            "reading": engine.get("reading"),
            "reading_notes": engine.get("reading_notes"),
            "scheme_pick": engine.get("scheme_pick"),
            "repair": engine.get("repair"),
            "grouping": engine.get("grouping"),
            "search_found": (engine.get("search") or {}).get("found"),
            "schemes": (engine.get("search") or {}).get("schemes"),
            "all_checks_passed": (engine.get("solved") or {}).get("all_checks_passed"),
            "failed_checks": (engine.get("solved") or {}).get("failed_checks"),
            "masses": (engine.get("solved") or {}).get("masses"),
            "instruction": engine.get("instruction"),
        }
        engine_note = (
            "The engine parsed the numbers. Your reading choices were applied "
            "through existing tools and checked. Report the reading, the scheme "
            "kept, and every failed check. Do not invent dimensions or regroup.\n"
            + json.dumps(compact, indent=2)[:6000]
        )

    messages = _build_ollama_messages(session)
    if engine_note:
        messages.append({"role": "system", "content": engine_note})
    final_content = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat(messages, tools=TOOL_DEFINITIONS)
        msg = response.get("message", {})
        role = msg.get("role", "assistant")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")

        if tool_calls:
            # Record assistant message with tool calls
            session.messages.append(
                ChatMessage(role="assistant", content=content, tool_calls=tool_calls)
            )
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    arguments = json.loads(raw_args) if raw_args else {}
                else:
                    arguments = raw_args

                print(f"  [tool] {name}({json.dumps(arguments)[:80]}...)")
                result = execute_tool(session, name, arguments)
                tool_msg = {"role": "tool", "content": result, "tool_name": name}
                messages.append(tool_msg)
                session.messages.append(ChatMessage(role="tool", content=result))

            session.save()
            continue

        # No tool calls — final response
        final_content = content
        session.messages.append(ChatMessage(role="assistant", content=content))
        messages.append({"role": "assistant", "content": content})
        session.save()
        break

    return final_content


def run_chat_loop(session: StudySession, client: OllamaClient) -> None:
    print("=" * 60)
    print(f"Massing Explorer Chat — study: {session.study_id}")
    print(f"Model: {client.model}")
    print(f"Program: {session.program.source_file}")
    print(f"Departments: {len(session.program.departments)}")
    if session.masses:
        print(f"Masses: {len(session.masses)} (resumed from saved state)")
    print("Commands: /status  /grouping  /solve  /quit")
    print("=" * 60)

    while True:
        try:
            user_text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_text:
            continue
        if user_text.lower() in ("/quit", "/exit", "quit", "exit"):
            print("Session saved.")
            break
        if user_text.lower() == "/status":
            from .tools import get_grouping_summary

            print(json.dumps(get_grouping_summary(session), indent=2))
            continue
        if user_text.lower() == "/grouping":
            from .tools import get_grouping_summary

            g = get_grouping_summary(session)
            if not g["masses"]:
                print("No groupings set yet.")
            for m in g["masses"]:
                print(
                    f"  [{m['id']}] {m['name']}: {m['story_count']} stories, "
                    f"{m['target_gsf']:,.0f} GSF — {', '.join(m['departments'])}"
                )
            if g["unassigned_departments"]:
                print(f"  Unassigned: {', '.join(g['unassigned_departments'])}")
            continue
        if user_text.lower() == "/solve":
            from .tools import solve_dimensions

            result = solve_dimensions(session)
            print(result.get("summary", result))
            continue

        try:
            reply = run_chat_turn(session, client, user_text)
            _print_assistant(reply)
        except OllamaError as e:
            print(f"\nOllama error: {e}")
        except json.JSONDecodeError as e:
            print(f"\nTool parse error: {e}")
