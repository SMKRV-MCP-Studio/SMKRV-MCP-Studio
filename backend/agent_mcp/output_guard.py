"""OWASP LLM01 output scanning for agent-mcp responses.

Lightweight L0 protection for the agent-mcp container:
- Scans for obvious injection patterns (critical severity only)
- No ML model dependency — pure regex

This module is intentionally self-contained to avoid importing from
the backend ``app`` package (different container, different package tree).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Critical-only patterns (subset of full YAML for agent-mcp container)
# ---------------------------------------------------------------------------

_CRITICAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("llm_tag_injection", re.compile(r'<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>')),
    ("llm_tag_injection", re.compile(r'(?i)\[INST\]|\[/INST\]|<<SYS>>|</SYS>>')),
    ("llm_tag_injection", re.compile(r'(?i)<\|?(system|user|assistant|function|tool)\|?>')),
    ("llm_tag_injection", re.compile(
        r'<\|begin_of_text\|>|<\|end_of_text\|>|<\|start_header_id\|>'
    )),
    ("instruction_override", re.compile(
        r'(?i)\b(ignore|disregard|forget|override|bypass)\b.{0,30}'
        r'\b(all\s+)?(previous|prior|above|earlier|existing)\b.{0,20}'
        r'\b(instructions?|rules?|guidelines?|directives?|prompts?)\b'
    )),
    ("instruction_override", re.compile(
        r'(?i)(игнорируй|забудь|проигнорируй|отбрось).{0,30}'
        r'(предыдущ|прошл|прежн|все|данн).{0,20}'
        r'(инструкци|правил|указани|команд)'
    )),
    ("role_manipulation", re.compile(
        r'(?i)\b(enter|switch\s+to|activate|enable)\b.{0,20}'
        r'\b(developer|debug|admin|root|sudo|god|jailbreak|DAN)\s*(mode|access)\b'
    )),
    ("system_prompt_extraction", re.compile(
        r'(?i)\b(show|reveal|display|print|dump)\b.{0,30}'
        r'\b(system\s+prompt|system\s+message|hidden\s+instructions?|internal\s+instructions?)\b'
    )),
]

# Fields that contain user-controlled content and should be scanned
_SCANNABLE_FIELDS = {
    "description", "sql_query", "template", "transform_template",
    "static_content", "uri_template",
}

# Tools that return user-controlled data from the database
DATA_TOOLS = frozenset({
    "list_tools", "get_tool",
    "list_resources", "get_resource",
    "list_prompts", "get_prompt",
    "export_config",
    "get_flow_layout",
    "get_server_config", "get_global_variables",
    "get_generated_code",
})


def _scan_critical(text: str) -> list[dict]:
    """Quick scan for critical injection patterns only."""
    warnings = []
    for category, regex in _CRITICAL_PATTERNS:
        m = regex.search(text)
        if m:
            warnings.append({
                "category": category,
                "severity": "CRITICAL",
                "matched_text": m.group(0)[:100],
            })
    return warnings


def scan_output(data: dict) -> dict:
    """Scan agent-mcp output for critical injection patterns.

    Returns the original data with ``_injection_warnings`` added
    if any critical patterns are detected. Does NOT wrap or modify
    the actual content fields.
    """
    if not isinstance(data, dict):
        return data

    all_warnings: list[dict] = []
    try:
        for key, value in data.items():
            if isinstance(value, str) and key in _SCANNABLE_FIELDS:
                all_warnings.extend(_scan_critical(value))

        items = data.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    for key, value in item.items():
                        if isinstance(value, str) and key in _SCANNABLE_FIELDS:
                            all_warnings.extend(_scan_critical(value))

        if all_warnings:
            data = {**data, "_injection_warnings": all_warnings}
            logger.warning(
                "Output guard: %d critical injection(s) detected in response",
                len(all_warnings),
            )
    except Exception:
        logger.debug("Output scan failed", exc_info=True)

    return data
