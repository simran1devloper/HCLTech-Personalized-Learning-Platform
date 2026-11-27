"""
config.py — Environment variables and application configuration
"""

from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # Server
    PORT: int = 8000
    ENVIRONMENT: str = "development"

    # Security
    SECRET_KEY: str = "change-this-secret-key-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Database backend for legacy routes: "sqlite" (local, default) or "supabase"
    DB_BACKEND: str = "sqlite"

    # Supabase (only used when DB_BACKEND=supabase)
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""

    # Gemini AI
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"

    # Legacy quiz grading: accept client-supplied answers when no quiz_token is sent (forgeable). Disable once
    # all clients send quiz_token.
    ALLOW_UNSIGNED_QUIZ: bool = True

    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",   # Vite dev server (web/)
        "http://127.0.0.1:5173",
        "http://localhost:4173",   # vite preview
        "http://127.0.0.1:4173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "null",  # For file:// protocol during dev
    ]

    class Config:
        env_file = (".env", "../.env")
        env_file_encoding = "utf-8"
        extra = "ignore"
        case_sensitive = True


settings = Settings()
