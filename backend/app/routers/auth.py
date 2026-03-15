"""Authentication router — setup, login, logout, profile management, 2FA."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.models.admin_user import AdminUser
from app.schemas.auth import (
    AuthStatusResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RecoveryCodesResponse,
    SetupRequest,
    TotpDisableRequest,
    TotpSetupInitResponse,
    TotpStatusResponse,
    TotpVerifyLoginRequest,
    TotpVerifySetupRequest,
    UpdateProfileRequest,
)
from app.services.auth import (
    check_2fa_rate_limit,
    check_rate_limit,
    clear_attempts,
    clear_auth_cookie,
    create_2fa_pending_token,
    create_access_token,
    decode_2fa_pending_token,
    hash_password,
    record_failed_2fa_attempt,
    record_failed_attempt,
    set_auth_cookie,
    verify_password,
)
from app.services.client_ip import get_client_ip
from app.services.totp import (
    count_remaining_recovery_codes,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_qr_data_uri,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_codes,
    verify_and_consume_recovery_code,
    verify_totp_code,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")


# --- Endpoints ---


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(db: AsyncSession = Depends(get_db)) -> AuthStatusResponse:
    """Check whether initial setup is required (no admin exists)."""
    result = await db.execute(select(func.count(AdminUser.id)))
    count = result.scalar_one()
    return AuthStatusResponse(setup_required=count == 0)


@router.post("/setup", response_model=LoginResponse)
async def setup_admin(
    body: SetupRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Create the first admin account. Only works when no admin exists."""
    result = await db.execute(select(func.count(AdminUser.id)))
    count = result.scalar_one()
    if count > 0:
        raise HTTPException(status_code=409, detail="Admin account already exists")

    admin = AdminUser(
        username=body.username,
        password_hash=await hash_password(body.password),
    )
    db.add(admin)
    await db.commit()

    token = create_access_token(admin.username)
    set_auth_cookie(response, token)
    logger.info("Admin account created: %s", admin.username)
    return LoginResponse(username=admin.username)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Authenticate with username/password. If 2FA is enabled, returns a pending token."""
    client_ip = get_client_ip(request)

    if not await check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    result = await db.execute(select(AdminUser).where(AdminUser.username == body.username))
    admin = result.scalar_one_or_none()

    if admin is None or not await verify_password(body.password, admin.password_hash):
        await record_failed_attempt(client_ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    await clear_attempts(client_ip)

    if admin.totp_enabled:
        pending = create_2fa_pending_token(admin.username)
        return LoginResponse(
            username=admin.username,
            requires_2fa=True,
            pending_token=pending,
        )

    token = create_access_token(admin.username)
    set_auth_cookie(response, token)
    return LoginResponse(username=admin.username)


@router.post("/login/verify-2fa", response_model=LoginResponse)
async def verify_2fa_login(
    body: TotpVerifyLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Step 2 of login: verify TOTP code or recovery code.

    Uses a separate rate limiter from login (5 attempts / 5 min) to prevent
    shared-counter abuse where login attempts could exhaust 2FA budget.
    """
    client_ip = get_client_ip(request)

    if not await check_2fa_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many 2FA attempts. Try again later.")

    username = decode_2fa_pending_token(body.pending_token)
    if username is None:
        raise HTTPException(status_code=401, detail="Invalid or expired verification token")

    admin = await _get_admin_by_username(db, username)
    if admin is None or not admin.totp_enabled:
        raise HTTPException(status_code=401, detail="Invalid verification state")

    if body.is_recovery:
        if not admin.recovery_codes_hash:
            await record_failed_2fa_attempt(client_ip)
            raise HTTPException(status_code=401, detail="No recovery codes available")
        matched, updated_hash = verify_and_consume_recovery_code(
            body.code, admin.recovery_codes_hash
        )
        if not matched:
            await record_failed_2fa_attempt(client_ip)
            raise HTTPException(status_code=401, detail="Invalid recovery code")
        admin.recovery_codes_hash = updated_hash
        await db.commit()
    else:
        secret = decrypt_totp_secret(admin.totp_secret_encrypted)  # type: ignore[arg-type]
        if not verify_totp_code(secret, body.code):
            await record_failed_2fa_attempt(client_ip)
            raise HTTPException(status_code=401, detail="Invalid verification code")

    await clear_attempts(client_ip)
    token = create_access_token(admin.username)
    set_auth_cookie(response, token)
    return LoginResponse(username=admin.username)


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Clear the session cookie."""
    clear_auth_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def get_me(request: Request, db: AsyncSession = Depends(get_db)) -> MeResponse:
    """Get current authenticated admin info."""
    admin = await get_current_admin(request, db)
    return MeResponse(username=admin.username, totp_enabled=admin.totp_enabled)


@router.patch("/me", response_model=MeResponse)
async def update_profile(
    body: UpdateProfileRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    """Change username and/or password. Requires current password."""
    admin = await get_current_admin(request, db)

    if not await verify_password(body.current_password, admin.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    if body.new_username is not None and body.new_username != admin.username:
        existing = await db.execute(
            select(AdminUser).where(AdminUser.username == body.new_username)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Username already taken")
        admin.username = body.new_username

    if body.new_password is not None:
        admin.password_hash = await hash_password(body.new_password)

    await db.commit()
    await db.refresh(admin)
    logger.info("Admin profile updated: %s", admin.username)
    return MeResponse(username=admin.username, totp_enabled=admin.totp_enabled)


# --- 2FA management endpoints ---


@router.post("/2fa/setup", response_model=TotpSetupInitResponse)
async def setup_2fa(request: Request, db: AsyncSession = Depends(get_db)) -> TotpSetupInitResponse:
    """Begin 2FA setup: generate secret, QR code, and recovery codes.

    Does NOT activate 2FA yet — call ``/2fa/verify-setup`` to confirm.
    """
    admin = await get_current_admin(request, db)

    if admin.totp_enabled:
        raise HTTPException(status_code=409, detail="2FA is already enabled")

    secret = generate_totp_secret()
    recovery_codes = generate_recovery_codes()

    admin.totp_secret_encrypted = encrypt_totp_secret(secret)
    admin.recovery_codes_hash = hash_recovery_codes(recovery_codes)
    await db.commit()

    qr_uri = generate_qr_data_uri(secret, admin.username)

    return TotpSetupInitResponse(
        qr_data_uri=qr_uri,
        secret=secret,
        recovery_codes=recovery_codes,
    )


@router.post("/2fa/verify-setup")
async def verify_2fa_setup(
    body: TotpVerifySetupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify a TOTP code to activate 2FA. Called after ``/2fa/setup``."""
    admin = await get_current_admin(request, db)

    if admin.totp_enabled:
        raise HTTPException(status_code=409, detail="2FA is already enabled")
    if not admin.totp_secret_encrypted:
        raise HTTPException(
            status_code=400, detail="2FA setup not initiated. Call POST /2fa/setup first."
        )

    secret = decrypt_totp_secret(admin.totp_secret_encrypted)
    if not verify_totp_code(secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid code. Please try again.")

    admin.totp_enabled = True
    await db.commit()
    logger.info("2FA enabled for admin: %s", admin.username)

    return {"ok": True, "message": "2FA enabled successfully"}


@router.post("/2fa/disable")
async def disable_2fa(
    body: TotpDisableRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disable 2FA. Requires current password + valid TOTP code."""
    admin = await get_current_admin(request, db)

    if not admin.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is not enabled")

    if not await verify_password(body.current_password, admin.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    secret = decrypt_totp_secret(admin.totp_secret_encrypted)  # type: ignore[arg-type]
    if not verify_totp_code(secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    admin.totp_enabled = False
    admin.totp_secret_encrypted = None
    admin.recovery_codes_hash = None
    await db.commit()
    logger.info("2FA disabled for admin: %s", admin.username)

    return {"ok": True, "message": "2FA disabled successfully"}


@router.post("/2fa/regenerate-recovery", response_model=RecoveryCodesResponse)
async def regenerate_recovery_codes(
    body: TotpDisableRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RecoveryCodesResponse:
    """Regenerate recovery codes. Invalidates all previous codes."""
    admin = await get_current_admin(request, db)

    if not admin.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA is not enabled")

    if not await verify_password(body.current_password, admin.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    secret = decrypt_totp_secret(admin.totp_secret_encrypted)  # type: ignore[arg-type]
    if not verify_totp_code(secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    codes = generate_recovery_codes()
    admin.recovery_codes_hash = hash_recovery_codes(codes)
    await db.commit()
    logger.info("Recovery codes regenerated for admin: %s", admin.username)

    return RecoveryCodesResponse(recovery_codes=codes)


@router.get("/2fa/status", response_model=TotpStatusResponse)
async def get_2fa_status(
    request: Request, db: AsyncSession = Depends(get_db)
) -> TotpStatusResponse:
    """Get 2FA status and remaining recovery codes count."""
    admin = await get_current_admin(request, db)
    return TotpStatusResponse(
        enabled=admin.totp_enabled,
        remaining_recovery_codes=count_remaining_recovery_codes(admin.recovery_codes_hash),
    )


# --- Internal helpers ---


async def _get_admin_by_username(db: AsyncSession, username: str) -> AdminUser | None:
    """Fetch an admin by username."""
    result = await db.execute(select(AdminUser).where(AdminUser.username == username))
    return result.scalar_one_or_none()
