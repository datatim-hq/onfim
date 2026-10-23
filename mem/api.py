"""REST adapter (03). Thin: parse the request -> call MemService -> JSON."""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from mem.errors import BadRequest, Forbidden, MemError, NotFound
from mem.service import Client, MemService


def is_local_request(request: Request) -> bool:
    """A direct request to 127.0.0.1:3120 (not through nginx: nginx always sets X-Real-IP)."""
    h = request.headers
    return "x-real-ip" not in h and "x-forwarded-for" not in h


def bearer(request: Request) -> str | None:
    a = request.headers.get("authorization", "")
    return a[7:].strip() if a.lower().startswith("bearer ") else None


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MemError)
    async def _mem_error(_req: Request, e: MemError):
        return JSONResponse(e.to_dict(), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, e: RequestValidationError):
        return JSONResponse({"error": "bad_request", "message": f"invalid request: {e.errors()[:3]}"}, status_code=400)


def build_router(svc: MemService, settings) -> APIRouter:
    r = APIRouter()

    def client(request: Request) -> Client:
        return svc.authenticate(bearer(request), is_local_request(request),
                                request.headers.get("x-mem-on-behalf-of"))   # 09: delegated client

    def q(request: Request) -> dict[str, Any]:
        """Query parameters; repeated ones (tags=a&tags=b) become a list."""
        out: dict[str, Any] = {}
        for k in request.query_params.keys():
            vals = request.query_params.getlist(k)
            out[k] = vals if len(vals) > 1 or k == "tags" else vals[0]
        if "tags" in out:
            flat = []
            for v in out["tags"]:
                flat += [t for t in v.split(",") if t]
            out["tags"] = flat
        for b in ("include_candidates", "include_archived", "explain"):
            if b in out:
                out[b] = str(out[b]).lower() in ("1", "true", "yes")
        return out

    # --- service routes without a token
    @r.get("/healthz")
    def healthz():
        return {"ok": True}

    @r.get("/status/{secret}")
    def status(secret: str):
        if not settings.status_secret or not hmac.compare_digest(secret, settings.status_secret):
            raise NotFound("not found")
        return svc.status()

    # --- stand: clock shift
    @r.post("/v1/_stand/clock")
    def stand_clock(body: dict = Body(default={}), c: Client = Depends(client)):
        if not c.can(None, "admin"):
            raise Forbidden("needs the admin right from localhost")
        if not svc.clock.stand:
            raise Forbidden("shifting the clock is allowed only on a stand (MEM_STAND=1)")
        if "offset_s" in body:
            svc.clock.set_offset(float(body["offset_s"]))
        elif "advance_s" in body:
            svc.clock.advance(float(body["advance_s"]))
        elif "advance_days" in body:
            svc.clock.advance(float(body["advance_days"]) * 86400)
        return {"offset_s": svc.clock.offset_seconds(), "now": svc.clock.now().isoformat()}

    # --- sleep
    @r.post("/v1/_admin/sleep/run")
    def sleep_run(body: dict = Body(default={}), c: Client = Depends(client)):
        return svc.sleep_run(c, body.get("kind", "light"), body.get("dry_run", True) is not False)

    @r.get("/v1/{ns}/sleep/latest")
    def sleep_latest(ns: str, kind: str = "light", c: Client = Depends(client)):
        return svc.sleep_report(c, ns, kind)

    @r.get("/v1/{ns}/sleep/{job_id}")
    def sleep_job(ns: str, job_id: int, c: Client = Depends(client)):
        return svc.sleep_report(c, ns, job_id=job_id)

    @r.post("/v1/recall")
    def recall_layers(body: dict = Body(...), c: Client = Depends(client)):
        return svc.recall_layers(c, body)

    # --- decision queue
    @r.get("/v1/review")
    def review_all(status: str = "open", c: Client = Depends(client)):
        return svc.review_all(c, status)        # 09, TM-21: across all namespaces with the review right

    @r.get("/v1/{ns}/review")
    def review_list(ns: str, status: str = "open", c: Client = Depends(client)):
        return svc.review_list(c, ns, status)

    @r.post("/v1/{ns}/review/{rid}/{decision}")
    def review_decide(ns: str, rid: int, decision: str, body: dict = Body(default={}), c: Client = Depends(client)):
        return svc.review_decide(c, ns, rid, decision, body.get("winner"))

    # --- write and read
    @r.post("/v1/{ns}/memories", status_code=201)
    def remember(ns: str, body: dict = Body(...), c: Client = Depends(client)):
        return svc.remember(c, ns, body)

    @r.get("/v1/{ns}/memories")
    def list_(ns: str, request: Request, c: Client = Depends(client)):
        return svc.list(c, ns, q(request))

    @r.get("/v1/{ns}/memories/{mid}")
    def get(ns: str, mid: str, c: Client = Depends(client)):
        return svc.get(c, ns, mid)

    @r.post("/v1/{ns}/memories/{mid}/evidence")
    def add_evidence(ns: str, mid: str, body: dict = Body(...), c: Client = Depends(client)):
        return svc.add_evidence(c, ns, mid, body)

    @r.post("/v1/{ns}/memories/{mid}/promote")
    def promote(ns: str, mid: str, body: dict = Body(...), c: Client = Depends(client)):
        return svc.promote(c, ns, mid, body)    # 09, TM-13: move to another layer through the curator queue

    @r.post("/v1/{ns}/aliases")
    def alias_set(ns: str, body: dict = Body(...), c: Client = Depends(client)):
        return svc.alias_set(c, ns, body.get("alias"), body.get("canonical"))

    @r.get("/v1/{ns}/memories/{mid}/history")
    def history(ns: str, mid: str, c: Client = Depends(client)):
        return svc.history(c, ns, mid)

    @r.post("/v1/{ns}/recall")
    def recall(ns: str, body: dict = Body(...), c: Client = Depends(client)):
        return svc.recall(c, ns, body)

    # --- signals
    @r.post("/v1/{ns}/memories/{mid}/used")
    def used(ns: str, mid: str, c: Client = Depends(client)):
        return svc.signal(c, ns, mid, "used")

    @r.post("/v1/{ns}/memories/{mid}/helpful")
    def helpful(ns: str, mid: str, c: Client = Depends(client)):
        return svc.signal(c, ns, mid, "helpful")

    @r.post("/v1/{ns}/memories/{mid}/confirm")
    def confirm(ns: str, mid: str, c: Client = Depends(client)):
        return svc.signal(c, ns, mid, "owner_confirm")

    @r.post("/v1/{ns}/memories/{mid}/pin")
    def pin(ns: str, mid: str, c: Client = Depends(client)):
        return svc.set_pin(c, ns, mid, True)

    @r.post("/v1/{ns}/memories/{mid}/unpin")
    def unpin(ns: str, mid: str, c: Client = Depends(client)):
        return svc.set_pin(c, ns, mid, False)

    @r.post("/v1/{ns}/memories/{mid}/close")
    def close(ns: str, mid: str, c: Client = Depends(client)):
        return svc.close(c, ns, mid)

    # --- supersession, archive, deletion
    @r.post("/v1/{ns}/memories/{mid}/revert-supersede")
    def revert(ns: str, mid: str, c: Client = Depends(client)):
        return svc.revert_supersede(c, ns, mid)

    @r.post("/v1/{ns}/memories/{mid}/archive")
    def archive(ns: str, mid: str, c: Client = Depends(client)):
        return svc.archive(c, ns, mid)

    @r.post("/v1/{ns}/memories/{mid}/unarchive")
    def unarchive(ns: str, mid: str, c: Client = Depends(client)):
        return svc.unarchive(c, ns, mid)

    @r.delete("/v1/{ns}/memories/{mid}")
    def delete(ns: str, mid: str, mode: str = "purge", c: Client = Depends(client)):
        if mode == "purge":
            return svc.purge(c, ns, mid)
        if mode == "archive":
            return svc.archive(c, ns, mid)
        raise BadRequest("mode: purge or archive")

    @r.post("/v1/{ns}/forget")
    def forget(ns: str, body: dict = Body(...), c: Client = Depends(client)):
        return svc.forget(c, ns, body)

    @r.post("/v1/{ns}/import")
    def import_(ns: str, body: dict = Body(...), c: Client = Depends(client)):
        return svc.import_records(c, ns, body.get("records"), body.get("dry_run", True) is not False)

    # --- service
    @r.get("/v1/{ns}/stats")
    def stats(ns: str, c: Client = Depends(client)):
        return svc.stats(c, ns)

    @r.get("/v1/{ns}/export")
    def export(ns: str, c: Client = Depends(client)):
        lines = list(svc.export(c, ns))   # rights are checked before the response starts
        return StreamingResponse(iter(lines), media_type="application/x-ndjson")

    return r
