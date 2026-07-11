"""MCP tools for managing Studio MCP prompts."""

from __future__ import annotations

from agent_mcp import backend_client


async def list_prompts(
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> dict:
    """List MCP prompts configured in Studio.

    Args:
        skip: Pagination offset.
        limit: Maximum items to return (1-100).
        search: Search string to filter by name or description.

    Returns:
        Object with 'items' array and 'total' count.
    """
    params: dict = {"skip": skip, "limit": limit}
    if search:
        params["search"] = search
    return await backend_client.get("/prompts", params=params)


async def get_prompt(prompt_id: str) -> dict:
    """Get a prompt by its ID.

    Args:
        prompt_id: UUID of the prompt.

    Returns:
        Full prompt object with template and arguments.
    """
    return await backend_client.get(f"/prompts/{prompt_id}")


async def create_prompt(
    name: str,
    template: str,
    description: str = "",
    arguments: list[dict] | None = None,
) -> dict:
    """Create a new MCP prompt.

    Args:
        name: Prompt name (used as the MCP prompt identifier).
        template: Prompt template text. Use {arg_name} for argument placeholders.
        description: Human-readable description.
        arguments: List of argument definitions. Each has: name, description, required.

    Returns:
        Created prompt object.
    """
    payload: dict = {
        "name": name,
        "template": template,
        "description": description,
    }
    if arguments is not None:
        payload["arguments"] = arguments
    return await backend_client.post("/prompts", data=payload)


async def update_prompt(
    prompt_id: str,
    name: str | None = None,
    template: str | None = None,
    description: str | None = None,
    arguments: list[dict] | None = None,
) -> dict:
    """Update an existing prompt. Only provided fields are changed. Version auto-increments.

    Args:
        prompt_id: UUID of the prompt to update.
        name: New prompt name.
        template: New template text.
        description: New description.
        arguments: New argument definitions (replaces all existing).

    Returns:
        Updated prompt object.
    """
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if template is not None:
        payload["template"] = template
    if description is not None:
        payload["description"] = description
    if arguments is not None:
        payload["arguments"] = arguments
    return await backend_client.patch(f"/prompts/{prompt_id}", data=payload)


async def delete_prompt(prompt_id: str) -> dict:
    """Delete a prompt by ID.

    Args:
        prompt_id: UUID of the prompt to delete.

    Returns:
        Confirmation message.
    """
    await backend_client.delete(f"/prompts/{prompt_id}")
    return {"deleted": True, "id": prompt_id}
