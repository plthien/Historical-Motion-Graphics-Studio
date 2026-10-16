from __future__ import annotations
from fastapi import Request
from fastapi.responses import JSONResponse


class HistoryError(Exception):
    def __init__(self, code: str, status: int = 400, message: str | None = None):
        super().__init__(code + ": " + (message or code))
        self.code, self.status, self.message = code, status, message or code


async def history_exception_handler(_request: Request, error: HistoryError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message}})
