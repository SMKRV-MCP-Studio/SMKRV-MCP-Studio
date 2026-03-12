"""Sandboxed Jinja2 post-processing for tool results."""

import json
import logging

from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 10_000_000  # 10 MB safety limit


def _build_environment() -> SandboxedEnvironment:
    """Create a sandboxed Jinja2 environment with safe built-ins and filters."""
    env = SandboxedEnvironment(
        autoescape=False,
        keep_trailing_newline=False,
    )
    # Safe built-in functions
    env.globals.update(
        {
            "sum": sum,
            "len": len,
            "min": min,
            "max": max,
            "round": round,
            "sorted": sorted,
            "enumerate": enumerate,
            "zip": zip,
            "range": range,
            "abs": abs,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
        }
    )
    # Custom filters
    env.filters["tojson"] = lambda v: json.dumps(v, default=str, ensure_ascii=False)
    env.filters["sum_attr"] = lambda items, attr: sum(
        (item.get(attr, 0) if isinstance(item, dict) else getattr(item, attr, 0))
        for item in items
    )
    env.filters["map_attr"] = lambda items, attr: [
        (item.get(attr) if isinstance(item, dict) else getattr(item, attr, None))
        for item in items
    ]
    env.filters["unique"] = lambda items: list(dict.fromkeys(items))
    return env


def apply_transform(
    rows: list[dict],
    template_str: str,
    global_vars: dict | None = None,
    params: dict | None = None,
) -> list[dict] | dict | str:
    """Apply a Jinja2 template to SQL query results.

    Template context:
      - rows: list[dict] -- raw SQL results
      - vars: dict -- global variables from ServerConfig
      - params: dict -- tool parameter values
      - Built-ins: sum, len, min, max, round, sorted, enumerate, zip, range, etc.

    Filters:
      - tojson -- serialize value to JSON string
      - sum_attr -- sum a specific attribute across items
      - map_attr -- extract a specific attribute from items
      - unique -- remove duplicate values
      - groupby -- Jinja2 built-in groupby

    If template output is valid JSON, it is parsed back to Python.
    Otherwise it is returned as a raw string.
    """
    env = _build_environment()
    tmpl = env.from_string(template_str)

    output = tmpl.render(
        rows=rows,
        vars=global_vars or {},
        params=params or {},
    )

    if len(output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise ValueError(
            f"Transform output exceeds {_MAX_OUTPUT_BYTES // 1_000_000}MB limit"
        )

    stripped = output.strip()
    if not stripped:
        return []

    # Try to parse as JSON
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    return stripped
