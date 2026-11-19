"""Request and response models for admin authentication."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(AuthRequest):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    password: SecretStr = Field(min_length=8, max_length=128)


class EmptyRequest(AuthRequest):
    pass


class AdminSummary(BaseModel):
    username: str


class LoginResponse(BaseModel):
    admin: AdminSummary
    csrf_token: str


class SessionResponse(LoginResponse):
    expires_at: datetime
