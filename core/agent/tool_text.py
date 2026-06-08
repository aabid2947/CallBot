"""Detect tool calls that the LLM leaked as PLAIN TEXT instead of returning as
structured `tool_calls` (CLAUDE.md bug #5 / voice_flow_problem.md V1).

Llama 3.3 on Groq intermittently writes a tool call inside the assistant
`content` using one of a few syntaxes, e.g.::

    <function=end_call></function>
    <function:end_call></function>
    <function=record_appointment_confirmed>{"scheduled_time": "..."}</function>
    <function(record_appointment_declined {"reason": "..."})
    end_call()

When that happens the real tool never runs (the outcome is lost / the call never
ends) and the raw string is spoken aloud by TTS. This module is PURE (no Pipecat,
no network) so it can be unit-tested on its own; the voice layer wraps it in a
FrameProcessor that strips the leak from the spoken text and fires the real tool.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .tools import (
    END_CALL,
    GET_APPOINTMENT_REQUEST,
    GET_CALLER_INFO,
    RECORD_APPOINTMENT_CONFIRMED,
    RECORD_APPOINTMENT_DECLINED,
    RECORD_APPOINTMENT_FOLLOWUP,
)

# Only these names are treated as leaked tool calls, so ordinary spoken text
# (e.g. "I'll call you back (maybe)") is never stripped.
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        GET_CALLER_INFO,
        GET_APPOINTMENT_REQUEST,
        RECORD_APPOINTMENT_CONFIRMED,
        RECORD_APPOINTMENT_DECLINED,
        RECORD_APPOINTMENT_FOLLOWUP,
        END_CALL,
    }
)

# `<function=NAME`, `<function(NAME`, or `<function:NAME`  (the tag-style leaks;
# Llama emits any of `= ( :` as the separator — the colon form slipped through
# before and got spoken aloud, e.g. "<function:end_call></function>").
_TAG_OPEN = re.compile(r"<\s*function\s*[=(:]\s*([A-Za-z_]\w*)", re.IGNORECASE)
# bare `NAME(`  (the model occasionally writes the call like code)
_BARE_OPEN = re.compile(r"([A-Za-z_]\w*)\s*\(", re.IGNORECASE)
# optional trailing `</function>` after the tag form
_CLOSE_TAG = re.compile(r"\s*</\s*function\s*>", re.IGNORECASE)


def _read_json_object(text: str, start: int) -> tuple[str | None, int]:
    """If text[start:] begins (after whitespace) with a balanced ``{...}`` object,
    return (json_substring, index_just_after_it). Otherwise (None, start).

    A brace-counting scan (string-aware) so nested braces / braces inside string
    values don't truncate the object the way a non-greedy regex would.
    """
    i = start
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "{":
        return None, start
    depth = 0
    in_str = False
    esc = False
    j = i
    while j < n:
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i : j + 1], j + 1
        j += 1
    return None, start  # unbalanced — leave it alone


def _skip_ws(text: str, pos: int) -> int:
    n = len(text)
    while pos < n and text[pos] in " \t":
        pos += 1
    return pos


def _parse_tag_form(text: str, name_end: int) -> tuple[str | None, int]:
    """Parse the rest of a ``<function...`` leak after the captured name. The JSON
    args may sit BEFORE the closing ``)`` (``<function(NAME {json})``) or AFTER the
    closing ``>`` (``<function=NAME>{json}</function>``). Returns (args_str, end)."""
    pos = name_end
    args_str, pos = _read_json_object(text, pos)          # <function(NAME {json}
    pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] in ")>":             # closing ) or >
        pos += 1
    if args_str is None:                                  # <function=NAME>{json}
        args_str, pos = _read_json_object(text, pos)
    m = _CLOSE_TAG.match(text, pos)                        # optional </function>
    if m:
        pos = m.end()
    return args_str, pos


def extract_leaked_tool_calls(text: str) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Split leaked tool calls out of ``text``.

    Returns ``(cleaned_text, calls)`` where ``calls`` is a list of
    ``(tool_name, args_dict)`` for every leaked call found (in order), and
    ``cleaned_text`` is ``text`` with those spans removed. If nothing leaked,
    ``calls`` is empty and ``cleaned_text == text``.
    """
    if not text or "function" not in text and "(" not in text:
        return text, []

    spans: list[tuple[int, int, str, str | None]] = []

    # Tag forms: <function=NAME ...> / <function(NAME ...
    for m in _TAG_OPEN.finditer(text):
        name = m.group(1)
        if name.lower() not in KNOWN_TOOLS:
            continue
        args_str, end = _parse_tag_form(text, m.end())
        spans.append((m.start(), end, name, args_str))

    # Bare form: NAME({...}) / NAME()  — only known names, not inside a tag span.
    for m in _BARE_OPEN.finditer(text):
        name = m.group(1)
        if name.lower() not in KNOWN_TOOLS:
            continue
        if any(s <= m.start() < e for s, e, _, _ in spans):
            continue
        args_str, after = _read_json_object(text, m.end())
        end = _skip_ws(text, after)
        if end < len(text) and text[end] == ")":
            end += 1
        spans.append((m.start(), end, name, args_str))

    if not spans:
        return text, []

    spans.sort(key=lambda s: s[0])
    cleaned_parts: list[str] = []
    calls: list[tuple[str, dict[str, Any]]] = []
    last = 0
    for start, end, name, args_str in spans:
        if start < last:  # overlapping match — skip
            continue
        cleaned_parts.append(text[last:start])
        last = end
        args: dict[str, Any] = {}
        if args_str:
            try:
                parsed = json.loads(args_str)
                if isinstance(parsed, dict):
                    args = parsed
            except json.JSONDecodeError:
                args = {}
        calls.append((name, args))
    cleaned_parts.append(text[last:])

    # Collapse the whitespace left where a mid-sentence leak was removed.
    cleaned = re.sub(r"[ \t]{2,}", " ", "".join(cleaned_parts)).strip()
    return cleaned, calls
