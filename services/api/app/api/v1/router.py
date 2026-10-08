"""Aggregates all v1 routers under /api/v1."""
from fastapi import APIRouter, Depends
from app.identity.access import require_api_access
from app.api.v1 import guest

from app.api.v1 import health, chat
from app.api.v1 import trace
from app.api.v1 import quiz
from app.api.v1 import workspace
from app.api.v1 import sidebar
from app.api.v1 import library
from app.api.v1 import assessment
from app.api.v1 import assessment_illustration
from app.api.v1 import diagram_library
from app.api.v1 import diagram_materials
from app.api.v1 import illustration_jobs
from app.api.v1 import tool_chem_lab
from app.api.v1 import tool_illustration
from app.api.v1 import learner_evaluation
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
from app.api.v1 import speech
from app.api.v1 import capabilities
from app.api.v1 import classroom
from app.api.v1 import assistant
from app.api.v1 import tool_image
from app.api.v1 import worksheet

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_access)])
api_router.include_router(guest.router)
api_router.include_router(health.router)
api_router.include_router(chat.router)
api_router.include_router(trace.router)
api_router.include_router(quiz.router)
api_router.include_router(workspace.router)
api_router.include_router(sidebar.router)
api_router.include_router(library.router)
api_router.include_router(assessment.router)
api_router.include_router(assessment_illustration.router)
api_router.include_router(diagram_library.router)
api_router.include_router(diagram_materials.router)
api_router.include_router(illustration_jobs.router)
api_router.include_router(tool_chem_lab.router)
api_router.include_router(tool_illustration.router)
api_router.include_router(learner_evaluation.router)
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
api_router.include_router(speech.router)
api_router.include_router(capabilities.router)
api_router.include_router(classroom.router)
api_router.include_router(assistant.router)
api_router.include_router(tool_image.router)
api_router.include_router(worksheet.router)
