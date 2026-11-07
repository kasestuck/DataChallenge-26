"""Compatibility entry point for: uvicorn main:app --reload --port 8001."""

from backend.main import app

__all__ = ["app"]
