"""Configuration from environment variables (prefix MEM_)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"MEM_{name}", default)


@dataclass(frozen=True)
class Settings:
    database_url: str
    embed_model: str          # 'fake' is a deterministic embedder for tests
    embed_dim: int
    stand: bool               # stand mode: the clock may be shifted (MEM_CLOCK_OFFSET, /v1/_stand/clock)
    clock_offset_s: float
    status_secret: str | None
    host: str
    port: int
    admin_token: str | None   # initial admin token (created at startup if set)
    mcp_lang: str = "en"      # language of MCP tool descriptions: en | ru

    @staticmethod
    def load() -> "Settings":
        return Settings(
            database_url=_env("DATABASE_URL", "postgresql://mem:mem@localhost:5432/mem"),
            embed_model=_env("EMBED_MODEL", "intfloat/multilingual-e5-small"),
            embed_dim=int(_env("EMBED_DIM", "384")),
            stand=_env("STAND", "0") == "1",
            clock_offset_s=float(_env("CLOCK_OFFSET", "0")),
            status_secret=_env("STATUS_SECRET"),
            host=_env("HOST", "0.0.0.0"),
            port=int(_env("PORT", "3120")),
            admin_token=_env("ADMIN_TOKEN"),
            mcp_lang=_env("MCP_LANG", "en"),
        )
