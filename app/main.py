"""The only operational product entrypoint: Historical Motion Graphics Studio P1."""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware

from app.history.db import HistoryDB
from app.history.domain import ChannelCreate,ChannelResponse,ProjectCreate,ProjectResponse,ProjectStatusResponse,HealthResponse
from app.history.errors import HistoryError,history_exception_handler
from app.history.service import HistoryService
from app.history.authoring import HistoryAuthoring
from app.history.api_authoring import router as authoring_router, service as authoring_dependency


def create_app(db_path: Path | str | None = None) -> FastAPI:
    db=HistoryDB(db_path)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.bootstrap()
        app.state.history_service=HistoryService(db)
        app.state.history_authoring=HistoryAuthoring(db)
        yield

    app=FastAPI(title="Historical Motion Graphics Studio", version="1.0.0-p1", lifespan=lifespan)
    app.add_exception_handler(HistoryError,history_exception_handler)
    app.add_middleware(CORSMiddleware,allow_origins=["http://127.0.0.1:5173","http://localhost:5173"],
                       allow_credentials=False,allow_methods=["GET","POST","PUT"],allow_headers=["Content-Type"])
    def service()->HistoryService:
        return app.state.history_service

    @app.get("/health",response_model=HealthResponse)
    def health():
        return {"status":"ok","product":"historical-motion-graphics-studio","schema_version":1}

    @app.get("/api/history/channels",response_model=list[ChannelResponse])
    def channels(s:HistoryService=Depends(service)):
        return s.channels()

    @app.post("/api/history/channels",response_model=ChannelResponse,status_code=201)
    def create_channel(body:ChannelCreate,s:HistoryService=Depends(service)):
        return s.create_channel(body)

    @app.get("/api/history/projects",response_model=list[ProjectResponse])
    def projects(channel_id:str|None=Query(default=None),s:HistoryService=Depends(service)):
        return s.projects(channel_id)

    @app.post("/api/history/projects",response_model=ProjectResponse,status_code=201)
    def create_project(body:ProjectCreate,s:HistoryService=Depends(service)):
        return s.create_project(body)

    @app.get("/api/history/projects/{project_id}",response_model=ProjectResponse)
    def project(project_id:str,s:HistoryService=Depends(service)):
        return s.get_project(project_id)

    @app.get("/api/history/projects/{project_id}/status",response_model=ProjectStatusResponse)
    def project_status(project_id:str,s:HistoryService=Depends(service)):
        return s.get_status(project_id)

    app.include_router(authoring_router)
    app.dependency_overrides[authoring_dependency]=lambda: app.state.history_authoring

    return app

app=create_app()
