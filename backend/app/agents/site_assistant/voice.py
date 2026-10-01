"""§24 助手语音：朗读文本提取、分段、合成任务与音频片段（B11）。

边界（plan.md §24.2/24.3/24.4）：
- 文本只取已存本人消息的可见 markdown（控制卡/URL/表格/公式降级为提示）。
- 片段按句边界切分，目标 100–250 字符；单回答 ≤6000 字符、≤40 片段。
- 合成复用 voice/tts 通用 resolve_tts_profile 与 provider（并发/缓存共享）；
  不依赖 CLASSROOM_ENABLED。策略 local 只用本地 provider，不暗中送云端。
- 内容 hash + voice profile 去重；每用户每分钟 ≤10 个合成请求。
- 片段与任务存 ASSISTANT_DIR 下 audio/（§26.3；随账号清理一次覆盖），
  24 小时无访问懒清理；原文修订/删除后音频失效。
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.assistant_store import _student_root

MAX_CHARS_PER_ANSWER = 6000
MAX_CLIPS = 40
CLIP_MIN_CHARS = 100
CLIP_MAX_CHARS = 250
JOB_TTL_SECONDS = 24 * 3600.0
REQUESTS_PER_MINUTE = 10

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])\s*")
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_FORMULA_RE = re.compile(r"\$\$[^$]+\$\$|\$[^$\n]+\$|\\\(|\\\[")
_URL_RE = re.compile(r"https?://\S+")
# 任务 id 白名单：服务端生成 aj_<hex16>；宽松到任意 hex/连字符前缀以便迁移。
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CLIP_TTL_NOTE = "clip"

# 每用户限流窗（进程内；多实例部署由 nginx/网关限额兜底）。
_rate_windows: dict[str, list[float]] = {}


class VoiceRejected(Exception):
    """语音请求拒绝（route 层映射为 4xx/429）。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _now() -> float:
    return time.time()


def _utc_iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# -- 文本提取与分段（§24.4） --------------------------------------------------

def readable_text_from_message(message: dict[str, Any]) -> str:
    """从已存 assistant 消息取可朗读文本：只取 markdown 块的纯文字。"""
    parts: list[str] = []
    for block in message.get("blocks") or []:
        if str(block.get("type") or "") != "markdown":
            continue  # 控制卡/选择卡/来源卡不朗读
        parts.append(str(block.get("text") or ""))
    text = "\n\n".join(p for p in parts if p.strip())
    text = _URL_RE.sub("", text)
    text = _TABLE_RE.sub("详细数据请看表格。", text)
    text = _FORMULA_RE.sub("公式见文字。", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def segment_text(text: str) -> tuple[list[str], bool]:
    """句边界分段；返回 (片段, 是否超限只读前段)。"""
    if not text:
        return [], False
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    clips: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(buffer) + len(sentence) > CLIP_MAX_CHARS and buffer:
            clips.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
        if len(buffer) >= CLIP_MIN_CHARS:
            clips.append(buffer)
            buffer = ""
        if len(clips) >= MAX_CLIPS or sum(map(len, clips)) >= \
                MAX_CHARS_PER_ANSWER:
            return clips[:MAX_CLIPS], True
    if buffer:
        clips.append(buffer)
    truncated = sum(map(len, clips)) > MAX_CHARS_PER_ANSWER \
        or len(clips) > MAX_CLIPS
    return clips[:MAX_CLIPS], truncated


# -- 存取（ASSISTANT_DIR/audio；§26.3） ---------------------------------------

def _audio_dir(student_id: str):
    return _student_root(student_id) / "audio"


def _jobs_dir(student_id: str):
    return _audio_dir(student_id) / "jobs"


def _clips_dir(student_id: str):
    return _audio_dir(student_id) / "clips"


def _job_path(student_id: str, job_id: str):
    # job_id 直接拼进文件名：白名单校验（服务端生成格式 aj_<hex16>），
    # 拒绝任何路径片段/控制字符，与 clip_content 的 ac_ 正则同级防护。
    if not _JOB_ID_RE.fullmatch(str(job_id or "")):
        raise ValueError(f"invalid job_id: {job_id!r}")
    return _jobs_dir(student_id) / f"{job_id}.json"


def _load_json(path):
    import json
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path, data) -> None:
    import json
    from app.core.atomic import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False))


def load_job(student_id: str, job_id: str) -> dict[str, Any] | None:
    try:
        path = _job_path(student_id, job_id)
    except ValueError:
        return None  # 非法 id 一律按"任务不存在"处理（404，不泄露细节）
    job = _load_json(path)
    if job is not None:
        # 访问即续期（§26.3：24h 无访问清理）。
        job["last_access_at"] = _now()
        _write_json(path, job)
    return job


def save_job(student_id: str, job: dict[str, Any]) -> None:
    _write_json(_job_path(student_id, str(job.get("job_id"))), job)


def cleanup_expired(student_id: str) -> int:
    """删除超期任务与无引用片段（懒清理；调用点：任务读写时）。"""
    jobs_dir = _jobs_dir(student_id)
    removed = 0
    now = _now()
    if not jobs_dir.exists():
        return 0
    for path in list(jobs_dir.glob("*.json")):
        job = _load_json(path)
        if job is None:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        if now - float(job.get("last_access_at") or job.get("created_at")
                       or 0) > JOB_TTL_SECONDS:
            for clip in job.get("clips") or []:
                clip_id = str(clip.get("clip_id") or "")
                if clip_id:
                    for ext in (".wav",):
                        (_clips_dir(student_id)
                         / f"{clip_id}{ext}").unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            removed += 1
    return removed


# -- 限流与去重（§24.3） ------------------------------------------------------

def _check_rate(student_id: str) -> None:
    now = _now()
    window = [t for t in _rate_windows.get(student_id, [])
              if now - t < 60.0]
    if len(window) >= REQUESTS_PER_MINUTE:
        raise VoiceRejected(429, "rate_limited",
                            "语音合成请求过于频繁，请稍后再试。")
    window.append(now)
    _rate_windows[student_id] = window


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def find_cached_job(student_id: str, text_sha: str,
                    profile_key: str) -> dict[str, Any] | None:
    jobs_dir = _jobs_dir(student_id)
    if not jobs_dir.exists():
        return None
    now = _now()
    for path in jobs_dir.glob("*.json"):
        job = _load_json(path)
        if job is None:
            continue
        if (str(job.get("text_sha")) == text_sha
                and str(job.get("profile_key")) == profile_key
                and str(job.get("state")) in ("ready", "queued",
                                                "running")
                and now - float(job.get("last_access_at")
                                or job.get("created_at") or 0)
                < JOB_TTL_SECONDS):
            job["last_access_at"] = now
            _write_json(path, job)
            return job
    return None


# -- 任务创建与执行 ------------------------------------------------------------

@dataclass
class SynthesisFn:
    """合成函数注入（测试替换；生产用 tts provider）。"""

    fn: Any  # async (text, options) -> TTSResult


def create_job(student_id: str, *, message: dict[str, Any],
               policy: str, voice_id: str, language: str,
               allow_local_fallback: bool,
               client_request_id: str) -> tuple[dict[str, Any], bool]:
    """受理合成（§24.3 POST /assistant/audio/jobs）。

    返回 (job, cached)：cached=True 表示命中同内容+档案的既有结果。
    消息必须是本人会话中已存的 assistant 消息（不允许伪造文本）。
    """
    from app.voice.tts.service import resolve_tts_profile

    class _Prefs:
        pass
    prefs = _Prefs()
    prefs.voice_policy = policy
    prefs.voice_id = voice_id or None
    prefs.allow_local_fallback = allow_local_fallback
    profile = resolve_tts_profile("assistant", prefs, language)
    if profile.policy == "silent":
        raise VoiceRejected(422, "voice_policy_silent",
                            "当前语音策略为静音，不合成音频。")
    if not profile.provider:
        raise VoiceRejected(503, "voice_unavailable",
                            "当前策略与语言没有可用语音服务，保留文字。")

    text = readable_text_from_message(message)
    if not text:
        raise VoiceRejected(422, "nothing_to_read", "该消息没有可朗读文字。")
    clips, truncated = segment_text(text)
    if not clips:
        raise VoiceRejected(422, "nothing_to_read", "该消息没有可朗读文字。")

    _check_rate(student_id)
    sha = _text_sha(text)
    profile_key = f"{profile.provider}:{profile.voice_id}:{profile.language}"
    cached = find_cached_job(student_id, sha, profile_key)
    if cached is not None:
        return cached, True

    job_id = "aj_" + uuid.uuid4().hex[:16]
    now = _now()
    job: dict[str, Any] = {
        "job_id": job_id,
        "conversation_id": str(message.get("conversation_id") or ""),
        "message_id": str(message.get("message_id") or ""),
        "message_revision": int(message.get("revision") or 0),
        "client_request_id": client_request_id,
        "policy": profile.policy,
        "provider": profile.provider,
        "voice_id": profile.voice_id,
        "language": profile.language,
        "profile_key": profile_key,
        "text_sha": sha,
        "state": "queued",
        "truncated": truncated,
        "clips": [{
            "clip_id": f"ac_{job_id[3:]}_{i:03d}",
            "seq": i,
            "chars": len(seg),
            "text": seg,
            "state": "queued",
            "media_type": "",
        } for i, seg in enumerate(clips)],
        "error": "",
        "created_at": now,
        "updated_at": now,
        "last_access_at": now,
        "expires_at": now + JOB_TTL_SECONDS,
    }
    save_job(student_id, job)
    cleanup_expired(student_id)
    return job, False


async def run_job(student_id: str, job_id: str) -> dict[str, Any]:
    """执行合成（route 受理后调度；逐片段写盘，失败保留已完成片段）。"""
    job = load_job(student_id, job_id)
    if job is None:
        raise VoiceRejected(404, "job_not_found", "合成任务不存在。")
    if str(job.get("state")) in ("ready", "cancelled"):
        return job
    if str(job.get("state")) == "running":
        return job  # 单飞：已在执行
    job["state"] = "running"
    job["updated_at"] = _now()
    save_job(student_id, job)

    from app.voice.tts import service as tts_service
    from app.voice.base import TTSOptions, TTSUnavailable
    provider = str(job.get("provider"))
    try:
        for clip in job["clips"]:
            if clip.get("state") in ("ready", "failed"):
                continue
            if str(job.get("state")) == "cancelled":
                break  # 取消：未开始片段保持 queued
            options = TTSOptions(voice_id=str(job.get("voice_id")),
                                 language=str(job.get("language")),
                                 synthesis_speed=1.0)
            if provider == "azure":
                result = await tts_service.cloud_synthesize(
                    str(clip.get("text")), options)
            else:
                result = await tts_service.local_synthesize(
                    str(clip.get("text")), options)
            from app.classroom.audio import pcm16_to_wav
            clip_path = _clips_dir(student_id) / \
                f"{clip.get('clip_id')}.wav"
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            clip_path.write_bytes(pcm16_to_wav(
                result.pcm16, result.sample_rate))
            clip["state"] = "ready"
            clip["media_type"] = "audio/wav"
            clip["ext"] = "wav"
            job["updated_at"] = _now()
            save_job(student_id, job)
        pending = [c for c in job["clips"] if c.get("state") == "queued"]
        failed = [c for c in job["clips"] if c.get("state") == "failed"]
        if str(job.get("state")) != "cancelled":
            if failed and not pending:
                job["state"] = "failed"
                job["error"] = "synthesis_failed"
            else:
                job["state"] = "ready"
        job["updated_at"] = _now()
        save_job(student_id, job)
        return job
    except TTSUnavailable as exc:
        job["state"] = "failed"
        job["error"] = f"provider_unavailable: {str(exc)[:120]}"
        job["updated_at"] = _now()
        save_job(student_id, job)
        return job
    except Exception as exc:  # noqa: BLE001
        job["state"] = "failed"
        job["error"] = f"synthesis_error: {str(exc)[:120]}"
        job["updated_at"] = _now()
        save_job(student_id, job)
        return job


def cancel_job(student_id: str, job_id: str) -> dict[str, Any]:
    """§24.3 cancel：未开始片段停止；已生成片段按缓存策略保留。"""
    job = load_job(student_id, job_id)
    if job is None:
        raise VoiceRejected(404, "job_not_found", "合成任务不存在。")
    if str(job.get("state")) in ("ready", "failed", "cancelled"):
        return public_job(job)
    job["state"] = "cancelled"
    job["updated_at"] = _now()
    save_job(student_id, job)
    return public_job(job)


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """对外投影：不带片段正文（正文只在服务端合成用）。"""
    return {
        "job_id": job.get("job_id"),
        "conversation_id": job.get("conversation_id"),
        "message_id": job.get("message_id"),
        "state": job.get("state"),
        "provider": job.get("provider"),
        "voice_id": job.get("voice_id"),
        "language": job.get("language"),
        "policy": job.get("policy"),
        "truncated": bool(job.get("truncated")),
        "clips": [{
            "clip_id": c.get("clip_id"), "seq": c.get("seq"),
            "chars": c.get("chars"), "state": c.get("state"),
            "media_type": c.get("media_type"),
        } for c in (job.get("clips") or [])],
        "error": job.get("error"),
        "expires_at": _utc_iso(float(job.get("expires_at") or 0)),
    }


def clip_content(student_id: str, clip_id: str) -> tuple[bytes, str, dict]:
    """读取片段二进制；校验所属任务仍有效（消息未改/未删）。

    返回 (bytes, media_type, job)；clip_id 只映射到本用户 audio 目录，
    不接受任意路径。§24.4：原文修订/删除后音频失效。
    """
    safe = str(clip_id)
    if not re.fullmatch(r"ac_[a-f0-9]{3,}_[0-9]{3}", safe):
        raise VoiceRejected(404, "clip_not_found", "音频片段不存在。")
    clips_dir = _clips_dir(student_id)
    for ext in (".wav",):
        path = clips_dir / f"{safe}{ext}"
        if not path.exists():
            continue
        job = _owning_job(student_id, safe)
        if job is None or str(job.get("state")) in ("cancelled", "failed"):
            path.unlink(missing_ok=True)
            raise VoiceRejected(410, "clip_expired", "音频已失效，请重新生成。")
        if not _message_still_valid(student_id, job):
            raise VoiceRejected(410, "clip_expired",
                                "原文已修改或删除，音频已失效。")
        job["last_access_at"] = _now()
        save_job(student_id, job)
        media = "audio/wav"
        clip = next((c for c in job["clips"]
                     if str(c.get("clip_id")) == safe), {})
        return path.read_bytes(), str(clip.get("media_type") or media), job
    raise VoiceRejected(404, "clip_not_found", "音频片段不存在。")


def _owning_job(student_id: str, clip_id: str) -> dict[str, Any] | None:
    jobs_dir = _jobs_dir(student_id)
    if not jobs_dir.exists():
        return None
    for path in jobs_dir.glob("*.json"):
        job = _load_json(path)
        if job is None:
            continue
        if any(str(c.get("clip_id")) == clip_id
               for c in (job.get("clips") or [])):
            return job
    return None


def _message_still_valid(student_id: str, job: dict[str, Any]) -> bool:
    """§24.4：消息 revision 变化或消息删除 → 音频失效。"""
    from app.core import assistant_store as store
    cid = str(job.get("conversation_id") or "")
    mid = str(job.get("message_id") or "")
    want_rev = int(job.get("message_revision") or 0)
    record = store.load_conversation(student_id, cid)
    if record is None:
        return False
    message = next((m for m in (record.get("messages") or [])
                    if str(m.get("message_id")) == mid), None)
    if message is None:
        return False
    return int(message.get("revision") or 0) == want_rev


# -- 试听（§24.3 preview） ------------------------------------------------------

PREVIEW_SAMPLE = "这是一段语音试听样例，用来确认你选择的音色与语速。"


async def synthesize_preview(student_id: str, *, policy: str,
                             voice_id: str, language: str,
                             allow_local_fallback: bool) -> dict[str, Any]:
    """固定短样例合成（限流复用每分钟窗口；不接受任意长文本）。"""
    _check_rate(student_id)
    from app.voice.tts.service import resolve_tts_profile

    class _Prefs:
        pass
    prefs = _Prefs()
    prefs.voice_policy = policy
    prefs.voice_id = voice_id or None
    prefs.allow_local_fallback = allow_local_fallback
    profile = resolve_tts_profile("assistant", prefs, language)
    if profile.policy == "silent":
        raise VoiceRejected(422, "voice_policy_silent",
                            "当前策略为静音。")
    if not profile.provider:
        raise VoiceRejected(503, "voice_unavailable",
                            "当前策略与语言没有可用语音服务。")
    from app.voice.tts import service as tts_service
    from app.voice.base import TTSOptions
    options = TTSOptions(voice_id=profile.voice_id,
                         language=profile.language,
                         synthesis_speed=1.0)
    if profile.provider == "azure":
        result = await tts_service.cloud_synthesize(PREVIEW_SAMPLE, options)
    else:
        result = await tts_service.local_synthesize(PREVIEW_SAMPLE, options)
    clip_id = "ac_preview_" + uuid.uuid4().hex[:12]
    from app.classroom.audio import pcm16_to_wav
    path = _clips_dir(student_id) / f"{clip_id}wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pcm16_to_wav(
        result.pcm16, result.sample_rate))
    # 试听片段由任务目录外的 TTL 清理兜底：记录访问时间到旁路文件。
    (path.with_suffix(".meta.json")).write_text(
        f'{{"t": {_now()}}}', encoding="utf-8")
    return {"clip_id": clip_id, "provider": profile.provider,
            "voice_id": profile.voice_id,
            "media_type": "audio/wav",
            "expires_in": 600}
