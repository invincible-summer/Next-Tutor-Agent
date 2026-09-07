"""Aggregates all v1 routers under /api/v1."""
from fastapi import APIRouter

from app.api.v1 import health, chat
from app.api.v1 import trace
from app.api.v1 import quiz
from app.api.v1 import workspace
from app.api.v1 import sidebar
from app.api.v1 import library
from app.api.v1 import assessment
from app.api.v1 import evaluation
from app.api.v1 import ux
from app.api.v1 import student
from app.api.v1 import knowledge
from app.api.v1 import memory
from app.api.v1 import orchestration
from app.api.v1 import auth
from app.api.v1 import user as user_router
from app.api.v1 import compat
from app.api.v1 import admin
from app.api.v1 import textbook
from app.api.v1 import trash
from app.api.v1 import notes
from app.api.v1 import docs
from app.api.v1 import voice

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(trace.router)
api_router.include_router(quiz.router)
api_router.include_router(workspace.router)
api_router.include_router(sidebar.router)
api_router.include_router(library.router)
api_router.include_router(assessment.router)
api_router.include_router(evaluation.router)
api_router.include_router(ux.router)
api_router.include_router(student.router)
api_router.include_router(knowledge.router)
api_router.include_router(memory.router)
api_router.include_router(orchestration.router)
api_router.include_router(auth.router)
api_router.include_router(user_router.router)
api_router.include_router(compat.router)
api_router.include_router(admin.router)
api_router.include_router(textbook.router)
api_router.include_router(trash.router)
api_router.include_router(notes.router)
api_router.include_router(docs.router)
api_router.include_router(voice.router)
