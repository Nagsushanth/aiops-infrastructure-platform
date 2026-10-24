import logging

from fastapi import HTTPException
from backend.app.core.database import check_database_connection

logger = logging.getLogger(__name__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api.conversations import router as conversations_router
from backend.app.api.websocket import router as websocket_router
from backend.app.api.requirements import router as requirements_router
from backend.app.api.terraform_preview import router as terraform_preview_router
from backend.app.api.auth import router as auth_router

from typing import Annotated

from fastapi import Depends
from backend.app.core.auth_dependencies import get_current_user
from backend.app.models.user import User


app = FastAPI(
    title="AIOps Platform for Infrastructure Support",
    version="0.1.0",
    description="AI-powered infrastructure provisioning with Terraform and approval workflows.",
)
app.include_router(websocket_router)
app.include_router(conversations_router)
app.include_router(requirements_router)
app.include_router(terraform_preview_router)
app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "aiops-backend"}


@app.get("/")
async def root():
    return {"message": "AIOps Infrastructure Support API", "docs": "/docs"}

@app.get("/health/database")
def database_health_check():
    if not check_database_connection():
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )

    return {
        "status": "healthy",
        "database": "postgresql",
        "query_test": "SELECT 1 successful",
    }


