"""Authentication request schemas."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    password: str = ""
