"""FastAPI application entry point.

Serves the JSON API under /api and the static PWA frontend at /. Everything
runs on one port bound to 0.0.0.0 so your phone can reach it over LAN.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, scheduler
from .db import init_db
from .routes.api import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("moviebeli")

app = FastAPI(title="movieBeli", version="0.1.0")
app.include_router(api_router)


@app.on_event("startup")
def _startup() -> None:
    log.info("Initialising database...")
    init_db()
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()


# Serve the PWA. Registered last so /api/* wins. html=True serves index.html
# at "/" and for unknown paths (single-page app).
app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")


def run() -> None:
    import uvicorn

    log.info("Starting movieBeli on http://%s:%s", config.HOST, config.PORT)
    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    run()
