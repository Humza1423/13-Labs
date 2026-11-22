"""Router registration. Lanes add their router import inside register_routers.

Owners: routes/runs.py (E1), routes/voice.py (V1), routes/ledger.py (P1).
Imports are optional so main stays bootable while lanes land independently.
"""

from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI

log = logging.getLogger(__name__)

LANE_ROUTERS = ("runs", "voice", "ledger")


def register_routers(app: FastAPI) -> None:
    for name in LANE_ROUTERS:
        try:
            module = importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"{__name__}.{name}":
                continue
            raise
        app.include_router(module.router)
        log.info("registered router %s", name)
