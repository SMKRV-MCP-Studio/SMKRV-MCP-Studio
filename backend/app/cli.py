"""CLI management commands for SMKRV MCP Studio.

Usage (inside Docker container):
    python -m app.cli reset-password
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.admin_user import AdminUser
from app.services.auth import hash_password


async def _reset_password() -> None:
    """Reset admin password interactively."""
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        result = await db.execute(select(AdminUser))
        admin = result.scalar_one_or_none()

        if admin is None:
            print("No admin account found. Start the app and complete setup first.")
            await engine.dispose()
            sys.exit(1)

        print(f"Resetting password for admin: {admin.username}")

        password = getpass.getpass("New password (min 8 chars): ")
        if len(password) < 8:
            print("Error: password must be at least 8 characters.")
            await engine.dispose()
            sys.exit(1)

        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.")
            await engine.dispose()
            sys.exit(1)

        admin.password_hash = hash_password(password)
        await db.commit()
        print(f"Password reset successfully for '{admin.username}'.")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="SMKRV MCP Studio CLI")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("reset-password", help="Reset admin password")

    args = parser.parse_args()

    if args.command == "reset-password":
        asyncio.run(_reset_password())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
