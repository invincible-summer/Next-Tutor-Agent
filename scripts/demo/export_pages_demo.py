#!/usr/bin/env python3
"""Export the synthetic-only GitHub Pages demo.

Seeds an isolated runtime root from ``fixtures/demo/**`` (project-authored
synthetic content only — see fixtures/demo/README.md) and captures read-only
API responses plus assets into ``apps/web/public/demo``. Never reads real
user data, textbook files, traces or local accounts; the fictional textbook
library and knowledge graphs exist only inside the sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from urllib.parse import quote, urlencode, urlsplit, parse_qsl

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "services" / "api"
FIXTURES = ROOT / "fixtures" / "demo"
OUTPUT = ROOT / "apps" / "web" / "public" / "demo"

DEMO_ID = "usr_pagesdemo01"
DEMO_EMAIL = "example@example.com"
PUBLIC_NS = "public"
# Hard Pages budget: fail long before the 100 MB CI gate.
MAX_EXPORT_BYTES = 50_000_000


def key(path: str) -> str:
    url = urlsplit(path)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(url.query) if k != "student_id"))
    return url.path + ("?" + query if query else "")


def public_payload(value):
    """Provider reasoning and private credentials never enter a Pages artifact."""
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    if isinstance(value, dict):
        return {k: public_payload(v) for k, v in value.items()
                if k not in {"thinking", "reasoning_content", "password_hash", "trace_ids",
                             "raw_response", "raw_prompt", "api_key"}}
    return value


def _now() -> float:
    return time.time()


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Fixture → synthetic corpus
# --------------------------------------------------------------------------

def synthetic_text(tb: dict) -> str:
    """Compose the fictional textbook's full text from its fixture chapters."""
    parts: list[str] = [f"{tb['title']}", ""]
    parts.append(tb.get("summary", ""))
    parts.append("")
    for chapter in tb["chapters"]:
        parts.append(chapter["title"])
        parts.append("")
        for c in chapter["concepts"]:
            parts.append(f"## {c['name']}")
            parts.append(c["definition"])
            if c.get("example"):
                parts.append(f"例：{c['example']}")
            if c.get("common_errors"):
                parts.append("常见错误：" + "；".join(c["common_errors"]) + "。")
            parts.append("")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Sandbox seeding
# --------------------------------------------------------------------------

def seed_library_and_textbooks(fx) -> dict:
    """Write the fictional public library, textbook records and source texts."""
    from app.core import library as lib_mod
    from app.core import textbook as tb_mod
    from app.core.paths import runtime_paths

    texts: dict[str, str] = {}
    folder_id = "folder_fx_public"
    lib = lib_mod.Library(PUBLIC_NS)
    lib.folders = [{"id": folder_id, "name": "示例公共教材（合成）",
                    "created_at": _now(), "updated_at": _now()}]
    files = []
    for tb in fx["textbooks"]:
        fid = f"fx_{tb['key']}"
        text = synthetic_text(tb)
        texts[fid] = text
        files.append({
            "id": fid,
            "filename": tb["filename"],
            "original_filename": tb["filename"],
            "folder_id": folder_id,
            "kind": "textbook",
            "char_count": len(text),
            "chunk_count": sum(len(ch["concepts"]) for ch in tb["chapters"]),
            "has_original": True,
            "original_ext": ".txt",
            "topics": [tb["subject"]],
            "summary": tb.get("summary", ""),
            "source_scope": "public",
            "created_at": _now(),
            "updated_at": _now(),
        })
    lib.files = files
    lib_mod.save_library(lib)

    data_dir = runtime_paths().library_data / PUBLIC_NS
    data_dir.mkdir(parents=True, exist_ok=True)
    for fid, text in texts.items():
        (data_dir / f"{fid}.txt").write_text(text, encoding="utf-8")
        (data_dir / f"{fid}.orig.txt").write_text(text, encoding="utf-8")

    records: dict[str, dict] = {}
    for tb, meta in zip(fx["textbooks"], files):
        rec = tb_mod.create_textbook(
            PUBLIC_NS, file_id=meta["id"], title=tb["title"],
            subject=tb["subject"], level=tb["level"], scope="public")
        chapter_count = len(tb["chapters"])
        concept_count = sum(len(ch["concepts"]) for ch in tb["chapters"])
        tb_mod.update_textbook(
            PUBLIC_NS, rec["id"],
            status="ready",
            progress={"stage": "done", "done": 1, "total": 1},
            chapter_count=chapter_count,
            concept_count=concept_count,
            volumes=[{
                "file_id": meta["id"],
                "filename": meta["filename"],
                "original_filename": meta["original_filename"],
                "char_count": meta["char_count"],
                "has_original": True,
                "updated_at": _now(),
                "effective_limits": {"nodes": concept_count, "edges": 999,
                                     "depth": None},
                "coverage": {"chapters": chapter_count,
                             "concepts": concept_count},
            }],
            rag_index={"mode": "bm25", "chunks": meta["chunk_count"]},
        )
        records[tb["key"]] = tb_mod.find_textbook(PUBLIC_NS, rec["id"])
    return records


def seed_graphs(fx, records: dict) -> None:
    """Materialize each fictional textbook's knowledge graph + concept content."""
    from app.agents.knowledge import store as kgs

    for tb in fx["textbooks"]:
        rec = records[tb["key"]]
        topic_key = rec["topic_key"]
        fid = rec["file_id"]
        nodes, edges, contents = [], [], {}
        for order, chapter in enumerate(tb["chapters"], 1):
            chapter_id = f"custom.{topic_key}.ch{order}"
            nodes.append({
                "id": chapter_id, "name": chapter["title"],
                "subject": tb["subject"], "level": tb["level"],
                "kind": "chapter", "origin": "seed",
                "metadata": {"volume_id": fid, "chapter_order": order,
                             "fixture": "synthetic-demo"},
            })
            for c in chapter["concepts"]:
                cid = f"custom.{topic_key}.{c['key']}"
                nodes.append({
                    "id": cid, "name": c["name"],
                    "subject": tb["subject"], "level": tb["level"],
                    "difficulty": c.get("difficulty", 3),
                    "description": c.get("definition", ""),
                    "aliases": c.get("aliases", []),
                    "common_errors": c.get("common_errors", []),
                    "kind": "concept", "origin": "seed",
                    "metadata": {"volume_id": fid, "fixture": "synthetic-demo"},
                })
                edges.append({"source": cid, "target": chapter_id,
                              "type": "part_of", "weight": 1.0,
                              "provenance": "synthetic-demo"})
                contents[cid] = {
                    "concept_id": cid,
                    "definition": c.get("definition", ""),
                    "formula": "",
                    "example": c.get("example", ""),
                    "exercise_hint": "",
                    "source": "seed",
                }
        for e in tb.get("edges", []):
            edges.append({
                "source": f"custom.{topic_key}.{e['from']}",
                "target": f"custom.{topic_key}.{e['to']}",
                "type": e["type"], "weight": 1.0,
                "provenance": "synthetic-demo",
            })
        payload = {
            "version": 1,
            "topic": tb["title"], "topic_key": topic_key,
            "subject": tb["subject"], "level": tb["level"],
            "source": f"textbook:{fid}", "is_textbook": True,
            "volumes": [fid],
            "created_at": _now(), "updated_at": _now(),
            "nodes": nodes, "edges": edges, "contents": contents,
        }
        kgs.save_custom_graph(PUBLIC_NS, topic_key, payload)


def seed_own_graph(fx) -> None:
    """The demo account's own synthetic overview graph."""
    from app.agents.knowledge import store as kgs
    g = fx["knowledge_graph"]
    topic_key = "fx-overview"
    nodes = [{
        "id": f"custom.{topic_key}.{c['key']}", "name": c["name"],
        "subject": g.get("subject", "综合"), "level": g.get("level", "本科"),
        "difficulty": c.get("difficulty", 3),
        "description": c.get("definition", ""),
        "aliases": c.get("aliases", []),
        "kind": "concept", "origin": "seed",
        "metadata": {"fixture": "synthetic-demo"},
    } for c in g["concepts"]]
    edges = [{
        "source": f"custom.{topic_key}.{e['from']}",
        "target": f"custom.{topic_key}.{e['to']}",
        "type": e["type"], "weight": 1.0, "provenance": "synthetic-demo",
    } for e in g["edges"]]
    contents = {
        f"custom.{topic_key}.{c['key']}": {
            "concept_id": f"custom.{topic_key}.{c['key']}",
            "definition": c.get("definition", ""),
            "formula": "", "example": c.get("example", ""),
            "exercise_hint": "", "source": "seed",
        } for c in g["concepts"]
    }
    kgs.save_custom_graph(DEMO_ID, topic_key, {
        "version": 1, "topic": g["topic"], "topic_key": topic_key,
        "subject": g.get("subject", "综合"), "level": g.get("level", "本科"),
        "source": "synthetic-demo", "is_textbook": False,
        "created_at": _now(), "updated_at": _now(),
        "nodes": nodes, "edges": edges, "contents": contents,
    })


def seed_workspace_sessions_notes_quiz(fx, records: dict) -> tuple[list[str], list[str]]:
    from app.core import notes as notes_mod
    from app.core.notes import save_vault
    from app.core import session as session_mod
    from app.core import workspace as ws_mod
    from app.core.quiz_attempts import record_generated_quiz, record_quiz_attempt

    fid_by_key = {k: rec["file_id"] for k, rec in records.items()}
    folder_id = "folder_fx_public"

    workspace_ids: list[str] = []
    selected_by_ws: dict[str, list[str]] = {}
    for ws in fx["workspaces"]:
        ws_id = f"ws_fx_{ws['key'].removeprefix('ws-')}"
        ws_mod.save_workspace(ws_mod.Workspace(
            workspace_id=ws_id, name=ws["name"], student_id=DEMO_ID,
            selected_folder_ids=[folder_id],
            selected_file_ids=[fid_by_key[ws["textbook"]]],
            public_memory=ws.get("public_memory", ""),
            created_at=_now(), updated_at=_now()))
        workspace_ids.append(ws_id)
        selected_by_ws[ws_id] = [fid_by_key[ws["textbook"]]]
    ws_name_to_id = {w["key"]: wid for w, wid in zip(fx["workspaces"], workspace_ids)}

    session_ids: list[str] = []
    for conv in fx["conversations"]:
        sid = session_mod.new_session_id(conv["title"])
        ws_id = ws_name_to_id.get(conv.get("workspace", ""), "")
        messages = []
        base = _now() - len(conv["messages"]) * 120
        for i, (role, content) in enumerate(conv["messages"]):
            messages.append({"id": f"m_{sid[:8]}_{i:03d}", "role": role,
                             "content": content, "created_at": base + i * 120})
        sess = session_mod.TutorSession(
            session_id=sid, student_id=DEMO_ID, workspace_id=ws_id,
            messages=messages)
        session_mod.save_session(sess)
        session_ids.append(sid)

    vault = notes_mod.load_vault(DEMO_ID)
    folder_ids = {}
    for f in fx["notes"].get("folders", []):
        vault.folders.append({"id": f["id"], "name": f["name"],
                              "created_at": _now(), "updated_at": _now()})
        folder_ids[f["id"]] = f["id"]
    for note in fx["notes"]["notes"]:
        vault.create_note(note["title"], note["content"],
                          folder_id=folder_ids.get(note.get("folder", ""), ""),
                          tags=note.get("tags", []))
    save_vault(vault)

    for q in fx["learner_state"].get("quizzes", []):
        if not session_ids:
            break
        record_generated_quiz(session_ids[0], {
            "questions": [{"stem": q["stem"],
                           "concept": q.get("concept", "")}],
        })
        record_quiz_attempt(
            session_ids[0], stem=q["stem"], verdict=q["verdict"],
            student_answer="（示例作答）" if q["verdict"] == "correct" else "（示例错误作答）",
            concept=q.get("concept", ""), subject="综合", student_id=DEMO_ID,
            correct=q["verdict"] == "correct")
    return workspace_ids, session_ids, selected_by_ws


def seed_evaluation(fx, records: dict, workspace_ids: list[str]) -> None:
    """Commit synthetic concept judgments into the learning-evidence journal."""
    from app.agents.student_model.evaluation import schema as ES
    from app.agents.student_model.evaluation.store import get_journal

    journal = get_journal(DEMO_ID)
    ws_calculus, ws_physics = workspace_ids[0], workspace_ids[1]
    ws_by_textbook = {"fxcalc": ws_calculus, "fxphys": ws_physics, "fxalg": ws_calculus}
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    judgments = []
    for i, cs in enumerate(fx["learner_state"]["concept_states"]):
        rec = records[cs["textbook"]]
        judgments.append(ES.ConceptJudgment(
            judgment_id=f"jd_fx_{i:04d}",
            concept_ref=ES.ConceptRef(
                graph_owner_namespace=PUBLIC_NS,
                textbook_id=rec["id"],
                file_ids=[rec["file_id"]],
                concept_id=f"custom.{rec['topic_key']}.{cs['concept']}",
                concept_revision="r1",
                display_name=cs["concept"]),
            workspace_id=ws_by_textbook.get(cs["textbook"], ws_calculus),
            state=ES.ConceptEvalState(cs["state"]),
            statement=cs["statement"],
            evidence_watermark="fx-wm-0001",
            policy_version="v1", theory_version="v1",
            prompt_ref="synthetic-demo",
            created_at=now_iso,
            source_id=f"src_fx_{i:04d}",
            scope_revision="sr-fx-1",
        ))
    # 每个工作区一批，保持事务粒度与真实链路一致。
    per_ws: dict[str, list] = {}
    for j in judgments:
        per_ws.setdefault(j.workspace_id, []).append(j)
    for ws_id, items in per_ws.items():
        journal.append([ES.OpResultCommitted(
            job_id=f"job_fx_{ws_id}", source_id=items[0].source_id,
            source_revision=1, scope_revision="sr-fx-1",
            interpretation_id="itp_fx",
            interpretation=ES.LearnerInterpretation(applicable=True),
            judgments=items)])


def seed_classroom(fx) -> dict:
    """Publish the synthetic lessons (spec-authored, LLM-free)."""
    from app.schemas import classroom as sc
    from app.core import classroom_store as store

    store.ensure_owner(DEMO_ID)
    lessons: list[dict] = []
    for lesson_fx in fx["classroom"]["lessons"]:
        ws_id = f"ws_fx_{lesson_fx['workspace'].removeprefix('ws-')}"
        lesson_id = store.new_id("les")
        now = store.utcnow()
        store.save_lesson(sc.Lesson(
            lesson_id=lesson_id, owner_id=DEMO_ID, workspace_id=ws_id,
            title=lesson_fx["topic"][:120],
            created_at=now, updated_at=now))
        slides = []
        for n, slide_fx in enumerate(lesson_fx["slides"], 1):
            blocks: list = []
            if slide_fx.get("formula"):
                blocks.append(sc.FormulaBlock(
                    id=f"blk_{n * 3 + 1:024x}", latex=slide_fx["formula"],
                    spoken=slide_fx["spoken"]))
            if slide_fx.get("code"):
                blocks.append(sc.CodeBlock(
                    id=f"blk_{n * 3 + 2:024x}", language="python",
                    code=slide_fx["code"]))
            blocks.append(sc.ParagraphBlock(
                id=f"blk_{n * 3 + 3:024x}", spans=[sc.SpanText(text=slide_fx["para"])]))
            block_ids = [b.id for b in blocks]
            segments = [sc.NarrationSegment(
                segment_id=f"seg_{n:024x}", role=sc.SegmentRole.explain,
                display_text=slide_fx["spoken"], spoken_text=slide_fx["spoken"],
                show_block_ids=block_ids, focus_block_ids=block_ids[:1],
                pause_after_ms=500, source_ids=["src_" + "1" * 24],
                estimated_ms=9000)]
            layout = (sc.SlideLayout.key_points if slide_fx.get("code")
                      else sc.SlideLayout.derivation)
            slides.append(sc.SlideSpec(
                slide_id=f"s_{n:012x}", order=n, title=slide_fx["title"],
                learning_objective_ids=["objective_fx"],
                layout=layout, blocks=blocks,
                segments=segments, claims=[], source_ids=["src_" + "1" * 24],
                transition=sc.TransitionKind.auto, estimated_seconds=30))
        source = sc.SourceRecord(
            source_id="src_" + "1" * 24, kind=sc.SourceKind.textbook,
            title="示例来源（合成演示）",
            locator=sc.FileLocator(
                namespace=PUBLIC_NS, file_id="fx_source_demo",
                chunk_ids=["c" * 24], page=1, printed_page="1",
                section_path=["示例章节"], content_hash="f" * 64),
            excerpt="项目自写的合成来源摘录，仅用于课堂演示。",
            excerpt_hash="e" * 64, retrieved_at=now)
        brief = sc.LessonBrief(
            topic=lesson_fx["topic"], goals=["理解示例课堂的讲授主线"],
            source_selection=sc.SourceSelection(files=[]),
            source_policy=sc.SourcePolicy.web_topic,
            research=sc.ResearchBrief(enabled=False),
            duration_minutes=15, page_plan="auto", language=sc.LessonLanguage.zh,
            grade="本科", pedagogy_id="concept_deep@1", theme_id="academic_clear@1",
            image_density=sc.ImageDensity.balanced,
            checkpoint_density=sc.CheckpointDensity.standard,
            voice_preferences=sc.VoicePreferences(), custom_requirements="")
        revision = sc.LessonRevision(
            revision=1, schema_version=1, brief=brief,
            source_snapshot=[source], slides=slides,
            checkpoint_templates=[],
            objectives=[sc.Objective(objective_id="objective_fx",
                                     text="理解示例课堂的讲授主线",
                                     evidence_status=sc.ObjectiveEvidenceStatus.supported)],
            glossary=[sc.GlossaryEntry(term="示例",
                                       definition="项目自写的合成演示内容")],
            assets=[], renderer_version="1.0.0",
            prompt_versions={"classroom_slide": "synthetic-demo"},
            review_report=sc.ReviewReport(issues=[], summary="ok"),
            content_hash="a" * 64, created_at=now)
        staging = store.prepare_revision_staging(DEMO_ID, ws_id, lesson_id, 1)
        spec_text = store.canonical_json(revision.model_dump(mode="json", by_alias=True))
        store.stage_file(staging, "spec.private.json", spec_text)
        manifest = {
            "revision": 1, "schema_version": 1,
            "content_hash": revision.content_hash,
            "files": {"spec.private.json":
                      store.bytes_hash(spec_text.encode("utf-8"))},
            "assets": [], "renderer_version": "1.0.0",
            "created_at": revision.created_at.isoformat(),
        }
        store.commit_revision(DEMO_ID, ws_id, lesson_id, 1, manifest)
        lesson = store.load_lesson(DEMO_ID, ws_id, lesson_id)
        store.index_upsert_lesson(DEMO_ID, ws_id, lesson)
        runs = []
        for run_fx in lesson_fx.get("runs", []):
            annotations = [
                sc.RunAnnotation(
                    annotation_id=f"ann_fx_{i:018x}",
                    slide_id=slides[min(i, len(slides) - 1)].slide_id,
                    user_text=text, created_at=now)
                for i, text in enumerate(run_fx.get("annotations", []))
            ]
            run = sc.ClassroomRun(
                run_id=store.new_id("run"), owner_id=DEMO_ID,
                workspace_id=ws_id, lesson_id=lesson_id, lesson_revision=1,
                content_hash=revision.content_hash,
                status=sc.RunStatus.paused,
                cursor=sc.Cursor(slide_id=slides[0].slide_id,
                                 segment_id=slides[0].segments[0].segment_id),
                annotations=annotations,
                created_at=now, updated_at=now)
            store.save_run(run)
            runs.append(run.run_id)
        lessons.append({"workspace_id": ws_id, "lesson_id": lesson_id,
                        "runs": runs})
    return {"lessons": lessons}


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

def main() -> None:
    os.environ.update(AUTH_MODE="1", AUTH_JWT_SECRET="pages-export-isolated-build-only",
                      LLM_API_KEY="", LLM_BASE_URL="", CLASSROOM_ENABLED="1",
                      KNOWLEDGE_RETRIEVAL_MODE="bm25")
    sys.path.insert(0, str(API))
    from tests.storage_sandbox import patch_all_storage_roots, reset_shared_caches
    from app.identity.models import UserProfile
    from app.identity.store import create_user
    from app.identity.security import create_token
    from app.main import app
    from fastapi.testclient import TestClient
    from app.core.config import settings
    settings.classroom_enabled = True
    settings.llm_api_key = ""

    fx = {name: load_fixture(name) for name in
          ("profile", "textbooks", "knowledge_graph", "workspaces",
           "conversations", "notes", "learner_state", "classroom")}
    # Unwrap the top-level collection keys so seeders see plain lists.
    for name in ("textbooks", "workspaces", "conversations"):
        fx[name] = fx[name][name]

    shutil.rmtree(OUTPUT, ignore_errors=True)
    (OUTPUT / "responses").mkdir(parents=True)
    (OUTPUT / "assets").mkdir()
    responses: dict[str, str] = {}
    assets: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="pages_demo_") as directory:
        sandbox = Path(directory)
        patches = patch_all_storage_roots(sandbox)
        try:
            profile = fx["profile"]
            user = create_user(DEMO_EMAIL, "example", "unused-build-only",
                               user_id=DEMO_ID,
                               profile=UserProfile(name=profile.get("name", "example"),
                                                   grade=profile.get("grade", "本科"),
                                                   subjects=profile.get("subjects", [])))
            client = TestClient(app, headers={"Authorization": "Bearer " + create_token(DEMO_ID)})

            records = seed_library_and_textbooks(fx)
            seed_graphs(fx, records)
            seed_own_graph(fx)
            from app.agents.knowledge import manager as kn_manager
            kn_manager._INSTANCE = None
            workspace_ids, session_ids, selected_by_ws = seed_workspace_sessions_notes_quiz(fx, records)
            seed_evaluation(fx, records, workspace_ids)
            classroom = seed_classroom(fx)

            def save(path: str, payload=None):
                if payload is None:
                    result = client.get("/api/v1" + path)
                    if result.status_code != 200:
                        raise RuntimeError(f"Export {path}: HTTP {result.status_code}: {result.text[:240]}")
                    payload = result.json()
                if isinstance(payload, dict) and payload.get("status") == "error":
                    raise RuntimeError(f"Export {path}: {payload.get('message')}")
                name = hashlib.sha256(key(path).encode()).hexdigest()[:24] + ".json"
                (OUTPUT / "responses" / name).write_text(json.dumps(public_payload(payload), ensure_ascii=False, separators=(",", ":")))
                responses[key(path)] = "responses/" + name
                return payload

            def asset(path: str):
                result = client.get("/api/v1" + path)
                if result.status_code != 200:
                    raise RuntimeError(f"Export asset {path}: {result.status_code}: {result.text[:300]}")
                suffix = {"application/zip": ".zip", "text/markdown": ".md",
                          "text/html": ".html", "text/plain": ".txt"}.get(
                    result.headers.get("content-type", "").split(";")[0], ".bin")
                name = hashlib.sha256(path.encode()).hexdigest()[:24] + suffix
                (OUTPUT / "assets" / name).write_bytes(result.content)
                assets[key(path)] = {"url": "assets/" + name, "type": result.headers.get("content-type", "application/octet-stream")}

            def save_list(path: str):
                offset = 0
                items = []
                while True:
                    response = client.get("/api/v1" + path, params={"offset": offset, "limit": 100})
                    if response.status_code != 200:
                        raise RuntimeError(f"Export list {path}: {response.status_code}: {response.text[:160]}")
                    data = response.json()
                    batch = data.get("items", [])
                    items.extend(batch)
                    if len(items) >= data.get("total", 0):
                        break
                    if not batch:
                        raise RuntimeError(f"Incomplete list snapshot: {path}")
                    offset += len(batch)
                return save(path, {**data, "items": items, "offset": 0, "limit": len(items)})

            save("/auth/me", {"user": user.to_public_dict()})
            save("/auth/status", {"auth_required": True, "guest_allowed": False})
            save("/model-info", {"llm_model": "GitHub Pages · Read-only demo", "multimodal_configured": False, "multimodal_model": ""})
            for path in ("/sidebar", "/chat/sessions", "/workspaces", "/library", "/textbooks",
                         "/student/profile", "/student/teaching-log", "/student/learning-path", "/student/error-notebook",
                         "/knowledge/taxonomy", "/knowledge/catalog", "/knowledge/custom", "/knowledge/graph",
                         "/memory/semantic", "/memory/procedural", "/memory/prompt-profile", "/memory/episodes?limit=500",
                         "/evaluation/report", "/evaluation/traces?limit=500", "/evaluation/proposals", "/evaluation/guidance",
                         "/evaluation/context-budget?limit=500", "/orchestration/plan", "/orchestration/today",
                         "/orchestration/habit", "/orchestration/review", "/quiz/recent", "/user/profile",
                         "/ux/profile", "/ux/engagement", "/ux/motivation", "/ux/activity", "/ux/greeting",
                         "/notes/vault", "/notes/graph", "/notes/templates", "/notes/reviews/due",
                         "/notes/notes/_vault/agent", "/trash", "/trash/policy", "/assessment/active",
                         "/classroom/capabilities", "/classroom/templates", "/classroom/templates?lang=en",
                         "/docs/content?lang=zh", "/docs/content?lang=en"):
                save(path)
            save_list("/learner-evaluation/workspaces")
            snapshot = client.get("/api/v1/sidebar").json()
            session_routes = [s["session_id"] for s in snapshot["sessions"]]
            for sid in session_routes:
                s = save("/chat/sessions/" + quote(sid, safe=""))
                s["trace_ids"] = []
                save("/chat/sessions/" + quote(sid, safe=""), s)
                save("/memory/prompt-profile/sessions/" + quote(sid, safe=""))
            for wid in workspace_ids:
                w = quote(wid, safe="")
                save("/workspaces/" + w)
                for path in ("/student/learning-path", "/assessment/active", "/knowledge/graph"):
                    save(path + "?workspace_id=" + w)
                save(f"/workspaces/{w}/classroom/lessons",
                     {"items": [], "total": 0, "page": 1, "page_size": 5})
                base = "/learner-evaluation/workspaces/" + w
                save(base)
                concepts = save_list(base + "/concepts")
                sessions = save_list(base + "/sessions")
                evidence = save_list(base + "/evidence")
                for c in concepts.get("items", []):
                    ref = c.get("concept_key") or c.get("concept_ref", {}).get("key")
                    if ref:
                        save(base + "/concepts/" + quote(ref, safe=""))
                for s in sessions.get("items", []):
                    ref = s.get("source_session_ref")
                    if ref:
                        save(base + "/sessions/" + quote(ref, safe=""))
                for e in evidence.get("items", []):
                    ref = e.get("source_id")
                    if ref:
                        save("/learner-evaluation/evidence/" + quote(ref, safe=""))
            vault = client.get("/api/v1/notes/vault").json()
            note_ids = [n["id"] for n in vault["notes"]]
            for nid in note_ids:
                base = "/notes/notes/" + quote(nid, safe="")
                save(base)
                save(base + "/agent")
                revisions = save(base + "/revisions")
                for revision in revisions["revisions"]:
                    save(base + "/revisions/" + str(revision["revision"]))
                asset(base + "/export")
            asset("/notes/export")
            for folder in vault["folders"]:
                asset("/notes/export?folder_id=" + quote(folder["id"], safe=""))
            recent = client.get("/api/v1/quiz/recent").json()
            for question in recent.get("questions", []):
                qid = question.get("question_id")
                if qid:
                    save("/quiz/submission?" + urlencode({"question_id": qid, "question_revision": question.get("question_revision", 1)}))
            # Fictional textbook library: full graph + per-volume views + concepts.
            textbooks = client.get("/api/v1/textbooks").json()["textbooks"]
            tb_by_id = {tb["id"]: tb for tb in textbooks}
            for tb in textbooks:
                tid = quote(tb["id"], safe="")
                save("/textbooks/" + tid)
                save("/textbooks/" + tid + "/graph-policy")
                save("/textbooks/" + tid + "/figure-status")
                graph = save("/knowledge/graph?textbook_id=" + tid)
                for fid in tb.get("file_ids", []):
                    save("/knowledge/graph?" + urlencode({"textbook_id": tb["id"], "file_id": fid}))
                for wid in workspace_ids:
                    if any(f in selected_by_ws.get(wid, []) for f in tb.get("file_ids", [])):
                        save("/knowledge/graph?" + urlencode({"textbook_id": tb["id"], "workspace_id": wid}))
                from app.api.v1.knowledge import knowledge_concept
                for node in graph.get("nodes", []):
                    path = "/knowledge/concepts/" + quote(node["id"], safe="")
                    if key(path) not in responses:
                        save(path, knowledge_concept(node["id"], workspace_id="", student_id=DEMO_ID))
                # Synthetic .txt originals: the demo never ships real textbooks.
                for volume in tb.get("volumes", []):
                    fid = volume["file_id"]
                    src = sandbox / "chat_history/library/data/public" / f"{fid}.orig.txt"
                    if not src.is_file():
                        continue
                    name = f"{fid}.orig.txt"
                    shutil.copy2(src, OUTPUT / "assets" / name)
                    entry = {"url": "assets/" + name, "type": "text/plain"}
                    assets[key(f"/textbooks/{tid}/volumes/{fid}/download")] = entry
                    assets[key(f"/library/files/{fid}/download")] = entry
                    if fid == tb.get("file_id"):
                        assets[key(f"/textbooks/{tid}/download")] = entry
            # Synthetic classroom showcase: lessons, revisions, frames, runs.
            classroom_routes = {"lessons": [], "runs": []}
            for entry in classroom["lessons"]:
                wid, lid = entry["workspace_id"], entry["lesson_id"]
                base = "/workspaces/" + quote(wid, safe="") + "/classroom/lessons"
                listing = client.get("/api/v1" + base, params={"page_size": 20}).json()
                save(base, listing)
                lesson = base + "/" + lid
                detail = save(lesson)
                classroom_routes["lessons"].append({"workspaceId": wid, "lessonId": lid})
                for revision in detail["published_revisions"]:
                    query = lesson + "?revision=" + str(revision)
                    save(query)
                    for mode in ("presentation", "print"):
                        frame = lesson + f"/revisions/{revision}/frame?mode={mode}"
                        asset(frame)
                for rid in entry["runs"]:
                    run_path = lesson + "/runs/" + rid
                    run_view = client.get("/api/v1" + run_path).json()
                    save(run_path, run_view)
                    save(run_path + "/summary")
                    classroom_routes["runs"].append({"workspaceId": wid, "lessonId": lid, "runId": rid})
            manifest = {"schema_version": 1, "demo_id": DEMO_ID, "responses": responses, "assets": assets,
                        "routes": {"sessions": session_ids, "notes": note_ids, "workspaces": workspace_ids, **classroom_routes}}
            (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))
            size = sum(p.stat().st_size for p in OUTPUT.rglob("*") if p.is_file())
            if size > MAX_EXPORT_BYTES:
                raise RuntimeError(f"Demo export exceeds the Pages budget: {size / 1e6:.1f} MB")
            print(f"Exported {len(responses)} read views, {len(session_ids)} chats, {len(note_ids)} notes, "
                  f"{len(workspace_ids)} workspaces, {len(classroom_routes['lessons'])} lessons; {size / 1e6:.1f} MB")
        finally:
            for patch in reversed(patches):
                patch.stop()
            reset_shared_caches()


if __name__ == "__main__":
    main()
