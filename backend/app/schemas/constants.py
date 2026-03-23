"""Shared schema constants and utilities."""

import re

ENTITY_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_\-]{0,254}$"
ENTITY_NAME_RE = re.compile(ENTITY_NAME_PATTERN)
