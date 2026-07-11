"""Shared SQL utility functions used by codegen and prompt_guard."""

import re


def is_passthrough_sql(sql: str, param_names: list[str]) -> tuple[bool, str]:
    """Detect passthrough SQL where the entire query is a single :param reference.

    Returns (is_passthrough, param_name).
    A passthrough tool executes user-supplied SQL directly without bind parameters.
    Example: sql_query=":query" with parameter "query" -> user provides raw SQL.

    SECURITY WARNING: Passthrough tools allow the MCP client (AI agent) to execute
    arbitrary SQL against the configured database connection. This bypasses all
    parameterized query protections and is equivalent to granting raw SQL access.
    """
    stripped = (sql or "").strip().rstrip(";").strip()
    match = re.fullmatch(r":(\w+)", stripped)
    if match:
        param_name = match.group(1)
        if param_name in param_names:
            return True, param_name
    return False, ""
