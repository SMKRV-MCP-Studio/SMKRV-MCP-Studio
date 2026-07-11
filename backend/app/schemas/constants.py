"""Shared schema constants and utilities."""

import re

ENTITY_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_\-]{0,254}$"
ENTITY_NAME_RE = re.compile(ENTITY_NAME_PATTERN)

# Return-type annotations that may be embedded verbatim into generated Python.
# Anything outside this set is rejected at the schema layer and mapped to the
# default by the codegen filter, so user text can never reach the `def ... -> X:`
# position as raw source.
VALID_RETURN_TYPES = {"list[dict]", "dict", "str", "int", "float"}
DEFAULT_RETURN_TYPE = "list[dict]"
