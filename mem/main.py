"""Entry point: migrations -> schema check -> model -> API + MCP on one port."""
from __future__ import annotations

import contextlib
import logging

import uvicorn
from fastapi import FastAPI

from mem import db
from mem.api import build_router, install_error_handlers
from mem.clock import Clock
from mem.config.settings import Settings
from mem.embedder import SafeEmbedder, build
from mem.mcp_server import McpAuthGate, build_mcp
from mem.service import MemService

log = logging.getLogger("mem")


def create_app(settings: Settings | None = None, embedder=None) -> FastAPI:
    settings = settings or Settings.load()
    inner = embedder or build(settings.embed_model, settings.embed_dim)
    emb = SafeEmbedder(inner)
    db.migrate(settings.database_url, settings.embed_dim, emb.model_id)
    db.check_schema(settings.database_url, settings.embed_dim, emb.model_id)
    pool = db.make_pool(settings.database_url)
    svc = MemService(pool, emb, Clock(settings.stand, settings.clock_offset_s))
    if settings.admin_token:
        svc.ensure_service_admin()
        svc.create_client("admin", ["*"], ["read", "write", "review", "forget", "admin"], token=settings.admin_token)

    with pool.connection() as conn:
        ns_rows = conn.execute("SELECT id, profile FROM namespaces ORDER BY id").fetchall()
    namespaces = [r["id"] for r in ns_rows]
    mcps = {r["id"]: build_mcp(r["id"], svc, r["profile"], settings.mcp_lang) for r in ns_rows}
    mcp_apps = {ns: m.streamable_http_app() for ns, m in mcps.items()}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with contextlib.AsyncExitStack() as stack:
            for m in mcps.values():
                await stack.enter_async_context(m.session_manager.run())
            yield
        pool.close()

    app = FastAPI(title="mem", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    install_error_handlers(app)
    app.include_router(build_router(svc, settings))
    for ns, a in mcp_apps.items():
        app.mount(f"/mcp/{ns}", McpAuthGate(a, svc, ns))
    app.state.svc = svc
    app.state.settings = settings
    log.info("mem started: model=%s dim=%s namespaces=%s stand=%s", emb.model_id, emb.dim, namespaces, settings.stand)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.load()
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="info", proxy_headers=False)


if __name__ == "__main__":
    main()
