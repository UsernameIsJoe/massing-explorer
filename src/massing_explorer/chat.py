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


def run_chat_turn(session: StudySession, client: OllamaClient, user_text: str) -> str:
    """Process one user message; may involve multiple tool-call rounds."""
    session.messages.append(ChatMessage(role="user", content=user_text))

    messages = _build_ollama_messages(session)
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
