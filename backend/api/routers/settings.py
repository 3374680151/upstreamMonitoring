"""Application settings routes."""
from __future__ import annotations

from fastapi import APIRouter
from backend.services.settings_service import SettingsService


router = APIRouter()
service = SettingsService()


@router.get("/settings")
def get_settings():
    return service.get()


@router.put("/settings")
def update_settings(payload: dict = None):
    return service.update(payload or {})
