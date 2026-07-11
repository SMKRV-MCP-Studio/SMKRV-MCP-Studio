"""Pydantic schemas for authentication endpoints."""

from pydantic import BaseModel, Field


class AuthStatusResponse(BaseModel):
    setup_required: bool


class SetupRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class LoginResponse(BaseModel):
    username: str
    requires_2fa: bool = False
    pending_token: str | None = None


class MeResponse(BaseModel):
    username: str
    totp_enabled: bool = False


class UpdateProfileRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_username: str | None = Field(default=None, min_length=1, max_length=255)
    new_password: str | None = Field(default=None, min_length=8, max_length=255)


class TotpSetupInitResponse(BaseModel):
    qr_data_uri: str
    secret: str
    recovery_codes: list[str]


class TotpVerifySetupRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TotpVerifyLoginRequest(BaseModel):
    pending_token: str
    code: str = Field(min_length=6, max_length=20)
    is_recovery: bool = False


class TotpDisableRequest(BaseModel):
    current_password: str = Field(min_length=1)
    code: str = Field(min_length=6, max_length=6)


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str]


class TotpStatusResponse(BaseModel):
    enabled: bool
    remaining_recovery_codes: int
