from __future__ import annotations

import asyncio
import sys

from fastapi import FastAPI

from base import configure_logging
from config import get_settings
from routes.ingestion import router as ingestion_router


if sys.platform == "win32":
	try:
		asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
	except Exception:
		pass


configure_logging()
settings = get_settings()

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)
app.include_router(ingestion_router)


@app.get("/health")
def healthcheck() -> dict[str, str]:
	return {"status": "ok"}
