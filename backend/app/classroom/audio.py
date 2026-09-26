"""课堂音频引擎（plan.md §11.4/§11.5，阶段 F03/F04）。

职责边界：
- synthesis key = owner + hash(规范化朗读文本) + provider/version + voice +
  language + synthesis_speed + speech_normalizer_version；不含 playbackRate。
  讲稿改动只使相关段缓存失效；换视觉主题讲稿不变 → 命中同 owner 缓存；
  跨账号不共享（owner 入 key）。
- single-flight：同 key 并发请求合并为一次合成（跨 run 共享结果，各 run
  只更新自己的 audio_refs）。
- WAV 原子写：PCM 打包 WAV → ``store.write_bytes``（tmp+fsync+replace）→
  记录内容 hash → metadata 最后提交；meta ready 而 WAV 缺失视为未就绪。
- 有界队列：全局 pending ≤ AUDIO_QUEUE_MAX，满时 ``audio_busy``（可重试）；
  每 run 串行 lane，最多 1 个新合成在途。
- 预取窗口：当前段+后 2 段、最多跨下一页、不跨未解决 checkpoint。
- 云端失败回退：按 run policy 回退本地并锁定该 run（提示一次）；回退 clip
  的 key 与 metadata 一律改用实际本地 provider/voice，绝不把 Melo 音频
  缓存到 Azure key 下。
- 纯 GET：status/content 只读已落盘内容，永不触发合成。
- usage counters：owner.json 记每日云合成字符/请求数（不硬编码价格）。
- LRU/配额：owner 音频预算（含 voice-previews）按最近访问淘汰；lesson
  预算独立；TTL 清理由 lifecycle 周期调用 ``sweep_expired_audio``。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from ..core import classroom_store as store
from ..schemas import classroom as sc
from ..voice.base import TTSOptions, VoiceProviderError
from ..voice.speak_text import to_speakable
from ..voice.tts import service as tts_service
from . import limits
from .errors import ClassroomError

log = logging.getLogger(__name__)

# spoken_text → 朗读文本的规范化版本；改变规范化行为必须递增并重建缓存
SPEECH_NORMALIZER_VERSION = "speak-text@1"
_PROVIDER_VERSIONS = {"azure": "azure-rest-1", "melo": "melo-sidecar-1"}

_VOICE_PREVIEW_SENTENCES = {
    "zh": "你好，这是课堂语音试听。动量守恒指出，不受外力时总动量保持不变。",
    "en": ("Hello, this is a classroom voice preview. "
           "Momentum stays conserved without external forces."),
}


def normalize_spoken_text(spoken_text: str) -> str:
    """朗读规范化（与电话路径同一 to_speakable；讲稿已是口语，这里兜底
    残留符号/公式，保证 key 与合成输入确定性一致）。"""
    return to_speakable(spoken_text or "").strip()


_QA_SENTENCE_MAX_CHARS = 200
_QA_SENTENCE_MAX_COUNT = 24


def split_reply_sentences(reply_text: str) -> list[str]:
    """答疑回复按句切片（§12.5）：确定性句界切分 + 长度/数量上限。

    不截断公式语义以外的内容——句子超长时按逗号/分号再切，仍超长按
    空格硬切；每片朗读规范化后为空则丢弃。"""
    text = normalize_spoken_text(reply_text)
    if not text:
        return []
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。！？!?；;":
            parts.append(buf)
            buf = ""
    if buf.strip():
        parts.append(buf)

    out: list[str] = []
    for piece in parts:
        piece = piece.strip()
        if not piece:
            continue
        while len(piece) > _QA_SENTENCE_MAX_CHARS:
            cut = -1
            for sep in ("，", "、", "：", ",", " "):
                idx = piece.rfind(sep, 0, _QA_SENTENCE_MAX_CHARS)
                if idx > 20:
                    cut = idx + len(sep)
                    break
            if cut <= 0:
                cut = _QA_SENTENCE_MAX_CHARS
            out.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            out.append(piece)
    return out[:_QA_SENTENCE_MAX_COUNT]


def synthesis_key(owner_id: str, normalized_text: str, *, provider: str,
                  voice_id: str, language: str,
                  synthesis_speed: float) -> str:
    payload = {
        "owner": owner_id,
        "text_sha256": hashlib.sha256(
            normalized_text.encode("utf-8")).hexdigest(),
        "provider": provider,
        "provider_version": _PROVIDER_VERSIONS.get(provider, f"{provider}-1"),
        "voice": voice_id,
        "language": language,
        "synthesis_speed": round(float(synthesis_speed), 3),
        "normalizer": SPEECH_NORMALIZER_VERSION,
    }
    return store.canonical_hash(payload)


def pcm16_to_wav(pcm16: bytes, sample_rate: int) -> bytes:
    """PCM16 mono → RIFF WAV（§11.4：统一打包 WAV 落盘）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm16)
    return buf.getvalue()


def clip_id_for(run_id: str, kind: str, segment_id: str,
                chunk_index: int = 0) -> str:
    """clip_id：run+段级确定性 ID；到 synthesis_hash 的映射存 run.audio_refs
    （§16.1，防拿同 owner 另一课的 hash 绕过路径绑定）。"""
    digest = hashlib.sha256(
        f"{run_id}|{kind}|{segment_id}|{int(chunk_index)}".encode("utf-8")
    ).hexdigest()[:32]
    return f"clp_{digest}"


# ---------------------------------------------------------------------------
# 段序与预取窗口（§11.4）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SegmentSlot:
    slide_id: str
    segment_id: str
    order: int


def _slide_checkpoints(slide: Any) -> list[str]:
    return [b.checkpoint_id for b in slide.blocks
            if getattr(b, "kind", "") == "checkpoint"]


def build_segment_order(spec: Any) -> list[SegmentSlot]:
    """课件线性段序（slide.order → 段顺序）。"""
    slots: list[SegmentSlot] = []
    for slide in sorted(spec.slides, key=lambda s: s.order):
        for seg in slide.segments:
            slots.append(SegmentSlot(slide_id=slide.slide_id,
                                     segment_id=seg.segment_id,
                                     order=len(slots)))
    return slots


def _slide_gates(spec: Any, run: sc.ClassroomRun) -> dict[str, bool]:
    """slide_id → 是否含未解决 checkpoint（pending 即未解决）。"""
    states = {ref.checkpoint_id: str(getattr(ref.state, "value", ref.state))
              for ref in run.checkpoint_refs}
    gates: dict[str, bool] = {}
    for slide in spec.slides:
        unresolved = [cp for cp in _slide_checkpoints(slide)
                      if states.get(cp, "pending") not in
                      ("answered", "skipped")]
        gates[slide.slide_id] = bool(unresolved)
    return gates


def validate_request_window(spec: Any, run: sc.ClassroomRun,
                            segment_ids: list[str]) -> None:
    """POST 音频段的窗口校验：连续、当前段起 ≤3、最多跨下一页、不越过未
    解决 checkpoint 所在页（§11.4：检查点页自身可读，读不到它之后）。"""
    slots = build_segment_order(spec)
    by_id = {s.segment_id: s for s in slots}
    if any(sid not in by_id for sid in segment_ids):
        raise ClassroomError("content_invalid", "段不存在或不在当前课件")
    if len(segment_ids) > limits.AUDIO_REQUEST_SEGMENTS_MAX:
        raise ClassroomError(
            "content_invalid",
            f"单次请求段数超过 {limits.AUDIO_REQUEST_SEGMENTS_MAX}")
    orders = sorted(by_id[sid].order for sid in segment_ids)
    if orders != list(range(orders[0], orders[0] + len(orders))):
        raise ClassroomError("content_invalid", "请求段必须连续")
    cursor_order = by_id[run.cursor.segment_id].order \
        if run.cursor.segment_id in by_id else 0
    if orders[0] < cursor_order:
        raise ClassroomError("scope_changed", "请求段早于当前游标")
    if orders[-1] > cursor_order + limits.AUDIO_PREFETCH_SEGMENTS:
        raise ClassroomError("scope_changed", "请求段超出预取窗口")
    slide_order = {slide.slide_id: slide.order for slide in spec.slides}
    cursor_slide_order = slide_order.get(run.cursor.slide_id, 0)
    last_segment = max(segment_ids, key=lambda s: by_id[s].order)
    last_slide_order = slide_order.get(by_id[last_segment].slide_id,
                                       cursor_slide_order)
    slides_touched = {by_id[sid].slide_id for sid in segment_ids} \
        | {run.cursor.slide_id}
    if len(slides_touched) > 2:
        raise ClassroomError("scope_changed", "预取最多跨下一页")
    gates = _slide_gates(spec, run)
    for slide_id, order in slide_order.items():
        if (gates.get(slide_id)
                and cursor_slide_order <= order < last_slide_order):
            raise ClassroomError("scope_changed", "预取不能跨未解决的检查点")


# ---------------------------------------------------------------------------
# usage counters（owner.json quota.tts；不硬编码价格，只记实际用量）
# ---------------------------------------------------------------------------

def _tts_counters(owner_id: str) -> dict:
    record = store.read_json(store.owner_meta_path(owner_id)) or {}
    tts = (record.get("quota") or {}).get("tts")
    return tts if isinstance(tts, dict) else {}


def _mutate_tts_counters(owner_id: str, mutate) -> None:
    with store.file_lock(store.owner_meta_path(owner_id)):
        record = store.read_json(store.owner_meta_path(owner_id)) or {}
        record.setdefault("quota", {})
        tts = record["quota"].get("tts")
        tts = tts if isinstance(tts, dict) else {}
        mutate(tts)
        record["quota"]["tts"] = tts
        store.write_json(store.owner_meta_path(owner_id), record)


def _chars_today(tts: dict) -> int:
    by_day = tts.get("cloud_chars_by_day")
    if not isinstance(by_day, dict):
        return 0
    return int(by_day.get(date.today().isoformat()) or 0)


def assert_cloud_char_budget(owner_id: str, extra_chars: int) -> None:
    used = _chars_today(_tts_counters(owner_id))
    if used + extra_chars > limits.TTS_DAILY_CLOUD_CHARS:
        raise ClassroomError(
            "quota_exceeded",
            f"今日云合成字符已达上限（{limits.TTS_DAILY_CLOUD_CHARS}）")


def record_usage(owner_id: str, *, chars: int, provider: str) -> None:
    today = date.today().isoformat()

    def mutate(tts: dict) -> None:
        if provider == "azure":
            by_day = tts.get("cloud_chars_by_day")
            by_day = by_day if isinstance(by_day, dict) else {}
            by_day[today] = int(by_day.get(today) or 0) + int(chars)
            # 有界保留：只留最近 35 天
            if len(by_day) > 35:
                for k in sorted(by_day)[:-35]:
                    by_day.pop(k, None)
            tts["cloud_chars_by_day"] = by_day
            tts["cloud_requests_total"] = \
                int(tts.get("cloud_requests_total") or 0) + 1
        else:
            tts["local_requests_total"] = \
                int(tts.get("local_requests_total") or 0) + 1

    _mutate_tts_counters(owner_id, mutate)


def tts_usage(owner_id: str) -> dict:
    return dict(_tts_counters(owner_id))


# ---------------------------------------------------------------------------
# LRU / 预算 / TTL（wav+meta 成对管理；meta mtime = 最近访问）
# ---------------------------------------------------------------------------

def _iter_audio_pairs(base: Path) -> Iterator[tuple[Path | None, Path | None]]:
    """(wav, meta) 成对枚举；孤儿单边同样返回（一并计大小/清理）。"""
    if not base.is_dir():
        return
    wavs: dict[str, Path] = {}
    metas: dict[str, Path] = {}
    try:
        for p in base.iterdir():
            if p.suffix == ".wav":
                wavs[p.stem] = p
            elif p.suffix == ".json":
                metas[p.stem] = p
    except OSError:
        return
    for key in sorted(set(wavs) | set(metas)):
        yield wavs.get(key), metas.get(key)


def _pair_size(wav: Path | None, meta: Path | None) -> int:
    total = 0
    for p in (wav, meta):
        if p is not None and p.exists():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _pair_mtime(wav: Path | None, meta: Path | None) -> float:
    for p in (meta, wav):
        if p is not None and p.exists():
            try:
                return p.stat().st_mtime
            except OSError:
                pass
    return 0.0


def _delete_pair(wav: Path | None, meta: Path | None) -> None:
    for p in (wav, meta):
        if p is not None:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []


def enforce_owner_audio_budget(owner_id: str,
                               inflight: set[str] | None = None) -> None:
    """owner 级音频预算（含 voice-previews；§11.4 每分钟约 2.9MB 不能无限
    保留）。超出按最近访问淘汰，in-flight key 受保护。"""
    inflight = inflight or set()
    root = store.owner_root(owner_id)
    pairs = list(_iter_audio_pairs(root / "voice-previews"))
    workspaces = root / "workspaces"
    if workspaces.is_dir():
        for ws in _safe_iterdir(workspaces):
            lessons = ws / "lessons"
            if not lessons.is_dir():
                continue
            for lesson in _safe_iterdir(lessons):
                pairs.extend(_iter_audio_pairs(lesson / "audio"))
    budget = limits.AUDIO_OWNER_MB * 1024 * 1024
    sized = [(wav, meta, _pair_size(wav, meta)) for wav, meta in pairs]
    total = sum(size for _, _, size in sized)
    if total <= budget:
        return
    sized.sort(key=lambda item: _pair_mtime(item[0], item[1]))
    for wav, meta, size in sized:
        if total <= budget:
            break
        key = (wav or meta).stem
        if key in inflight:
            continue
        _delete_pair(wav, meta)
        total -= size


def enforce_lesson_audio_budget(owner_id: str, workspace_id: str,
                                lesson_id: str) -> None:
    lesson_audio = store.lesson_root(owner_id, workspace_id, lesson_id) / "audio"
    if not lesson_audio.is_dir():
        return
    pairs = list(_iter_audio_pairs(lesson_audio))
    budget = limits.AUDIO_LESSON_MB * 1024 * 1024
    sized = [(wav, meta, _pair_size(wav, meta)) for wav, meta in pairs]
    total = sum(size for _, _, size in sized)
    if total <= budget:
        return
    sized.sort(key=lambda item: _pair_mtime(item[0], item[1]))
    for wav, meta, size in sized:
        if total <= budget:
            break
        _delete_pair(wav, meta)
        total -= size


def sweep_expired_audio(owner_id: str) -> int:
    """未访问超过 AUDIO_TTL_DAYS 的音频成对清除（lifecycle 周期调用）。"""
    cutoff = time.time() - limits.AUDIO_TTL_DAYS * 86400
    root = store.owner_root(owner_id)
    removed = _sweep_dir(root / "voice-previews", cutoff)
    workspaces = root / "workspaces"
    if workspaces.is_dir():
        for ws in _safe_iterdir(workspaces):
            lessons = ws / "lessons"
            if not lessons.is_dir():
                continue
            for lesson in _safe_iterdir(lessons):
                removed += _sweep_dir(lesson / "audio", cutoff)
    return removed


def _sweep_dir(base: Path, cutoff: float) -> int:
    count = 0
    for wav, meta in list(_iter_audio_pairs(base)):
        if _pair_mtime(wav, meta) < cutoff:
            _delete_pair(wav, meta)
            count += 1
    return count


# ---------------------------------------------------------------------------
# 引擎（single-flight + per-run lane + 回退）
# ---------------------------------------------------------------------------

@dataclass
class _PendingClip:
    owner_id: str
    workspace_id: str
    lesson_id: str
    run_id: str
    kind: str
    segment_id: str
    chunk_index: int
    clip_id: str
    text: str        # 规范化后的朗读文本
    key: str         # 请求侧计算的 synthesis key（single-flight 分组）
    provider: str
    voice_id: str
    language: str
    allow_local_fallback: bool


@dataclass
class _Lane:
    queue: deque = field(default_factory=deque)
    task: asyncio.Task | None = None


def effective_profile(
        run: sc.ClassroomRun,
        profile: tts_service.ClassroomVoiceProfile
) -> tts_service.ClassroomVoiceProfile:
    """run 级回退锁（§11.5）：云失败一次后该 run 后续段全部本地。"""
    if run.tts_local_locked and profile.provider == "azure":
        from ..voice.tts.melotts import MeloTTS
        return tts_service.ClassroomVoiceProfile(
            policy=profile.policy, provider="melo",
            voice_id=MeloTTS.VOICE_ID if profile.language == "zh-CN" else "",
            language=profile.language, synthesis_speed=1.0,
            allow_local_fallback=profile.allow_local_fallback,
            cloud_configured=profile.cloud_configured,
            local_enabled=profile.local_enabled, local_locked=True)
    return profile


class AudioEngine:
    """进程级单例；全部 async 方法须在事件循环内调用（route/worker 同循环）。"""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task] = {}
        self._lanes: dict[str, _Lane] = {}
        self._inflight_keys: set[str] = set()   # 淘汰保护

    # -- 提交与查询 --------------------------------------------------------

    def _pending_total(self) -> int:
        return len(self._inflight) + sum(len(lane.queue)
                                         for lane in self._lanes.values())

    async def request_narration_clips(
            self, run: sc.ClassroomRun, spec: Any,
            profile: tts_service.ClassroomVoiceProfile,
            segment_ids: list[str]) -> list[dict]:
        """POST R/audio 语义：窗口校验 → 缓存命中直读 → 缺失段入队合成。"""
        validate_request_window(spec, run, segment_ids)
        profile = effective_profile(run, profile)
        if not profile.voice_id:
            raise ClassroomError("voice_unavailable",
                                 "当前语言无可用音色（文字课堂）")
        segments_by_id: dict[str, Any] = {}
        for slide in spec.slides:
            for seg in slide.segments:
                segments_by_id[seg.segment_id] = seg
        wanted: list[tuple[str, str, str, str]] = []
        # (segment_id, clip_id, text, key)
        for sid in segment_ids:
            text = normalize_spoken_text(
                segments_by_id[sid].spoken_text)
            if not text:
                raise ClassroomError("content_invalid",
                                     f"段 {sid} 讲稿为空，无法合成")
            key = synthesis_key(run.owner_id, text, provider=profile.provider,
                                voice_id=profile.voice_id,
                                language=profile.language,
                                synthesis_speed=profile.synthesis_speed)
            clip_id = clip_id_for(run.run_id, "narration", sid, 0)
            wanted.append((sid, clip_id, text, key))

        missing = [item for item in wanted
                   if not self._meta_ready(run.owner_id, run.workspace_id,
                                           run.lesson_id, item[3])]
        if missing:
            missing_chars = sum(len(item[2]) for item in missing)
            if profile.provider == "azure":
                assert_cloud_char_budget(run.owner_id, missing_chars)
            # run 级预算（§15.4 TTS_CHARS_PER_RUN）：提交时预扣（缓存命中不扣）
            reloaded = store.load_run(run.owner_id, run.workspace_id,
                                      run.lesson_id, run.run_id) or run
            charged = int(getattr(reloaded, "tts_chars_used", 0) or 0)
            if charged + missing_chars > limits.TTS_CHARS_PER_RUN:
                raise ClassroomError("quota_exceeded", "本课堂合成字符已达上限")
            if missing_chars:
                try:
                    store.update_run(
                        run.owner_id, run.workspace_id, run.lesson_id,
                        run.run_id,
                        lambda r: setattr(
                            r, "tts_chars_used",
                            int(getattr(r, "tts_chars_used", 0) or 0)
                            + missing_chars),
                        bump_revision=False)
                    run.tts_chars_used = charged + missing_chars
                except Exception:
                    log.warning("tts_chars_used update failed", exc_info=True)
        if missing and self._pending_total() + len(missing) > \
                limits.AUDIO_QUEUE_MAX:
            raise ClassroomError("audio_busy", "音频合成队列已满，请稍后重试",
                                 retryable=True)

        results: list[dict] = []
        for sid, clip_id, text, key in wanted:
            meta = self._load_meta(run.owner_id, run.workspace_id,
                                   run.lesson_id, key)
            if meta is not None and meta.get("state") == "ready":
                self._touch_key(run, key)
                results.append(self._clip_dict(meta, clip_id))
                continue
            self._remember_ref(run, clip_id, key)
            results.append({"clip_id": clip_id, "state": "pending",
                            "provider": "", "voice_id": "",
                            "sample_rate": 0, "sample_count": 0, "bytes": 0,
                            "duration_seconds": 0.0, "error": None})
            if key not in self._inflight:
                self._enqueue(_PendingClip(
                    owner_id=run.owner_id, workspace_id=run.workspace_id,
                    lesson_id=run.lesson_id, run_id=run.run_id,
                    kind="narration", segment_id=sid, chunk_index=0,
                    clip_id=clip_id, text=text, key=key,
                    provider=profile.provider,
                    voice_id=profile.voice_id, language=profile.language,
                    allow_local_fallback=profile.allow_local_fallback))
        return results

    async def request_qa_clips(
            self, run: sc.ClassroomRun, reply_text: str,
            profile: tts_service.ClassroomVoiceProfile) -> list[dict]:
        """POST R/qa-audio 语义（§12.5）：已保存的答疑回复按句切片合成。

        正文由服务端从 run 绑定的答疑 session 读取（route 层校验
        reply_message_id），引擎不接任意客户端 text。clip_id 按句序
        确定性派生；预算/队列/缓存语义与 narration 完全一致。
        """
        profile = effective_profile(run, profile)
        if not profile.voice_id:
            raise ClassroomError("voice_unavailable",
                                 "当前语言无可用音色（文字课堂）")
        sentences = split_reply_sentences(reply_text)
        if not sentences:
            raise ClassroomError("content_invalid", "回复正文为空，无法合成")
        wanted: list[tuple[str, str, str]] = []   # (clip_id, text, key)
        for idx, sentence in enumerate(sentences):
            key = synthesis_key(run.owner_id, sentence,
                                provider=profile.provider,
                                voice_id=profile.voice_id,
                                language=profile.language,
                                synthesis_speed=profile.synthesis_speed)
            wanted.append((clip_id_for(run.run_id, "qa", "reply", idx),
                           sentence, key))

        missing = [item for item in wanted
                   if not self._meta_ready(run.owner_id, run.workspace_id,
                                           run.lesson_id, item[2])]
        if missing:
            missing_chars = sum(len(item[1]) for item in missing)
            if profile.provider == "azure":
                assert_cloud_char_budget(run.owner_id, missing_chars)
            reloaded = store.load_run(run.owner_id, run.workspace_id,
                                      run.lesson_id, run.run_id) or run
            charged = int(getattr(reloaded, "tts_chars_used", 0) or 0)
            if charged + missing_chars > limits.TTS_CHARS_PER_RUN:
                raise ClassroomError("quota_exceeded", "本课堂合成字符已达上限")
            if missing_chars:
                try:
                    store.update_run(
                        run.owner_id, run.workspace_id, run.lesson_id,
                        run.run_id,
                        lambda r: setattr(
                            r, "tts_chars_used",
                            int(getattr(r, "tts_chars_used", 0) or 0)
                            + missing_chars),
                        bump_revision=False)
                    run.tts_chars_used = charged + missing_chars
                except Exception:
                    log.warning("tts_chars_used update failed (qa)",
                                exc_info=True)
        if missing and self._pending_total() + len(missing) > \
                limits.AUDIO_QUEUE_MAX:
            raise ClassroomError("audio_busy", "音频合成队列已满，请稍后重试",
                                 retryable=True)

        results: list[dict] = []
        for clip_id, text, key in wanted:
            meta = self._load_meta(run.owner_id, run.workspace_id,
                                   run.lesson_id, key)
            if meta is not None and meta.get("state") == "ready":
                self._touch_key(run, key)
                results.append(self._clip_dict(meta, clip_id))
                continue
            self._remember_ref(run, clip_id, key)
            results.append({"clip_id": clip_id, "state": "pending",
                            "provider": "", "voice_id": "",
                            "sample_rate": 0, "sample_count": 0, "bytes": 0,
                            "duration_seconds": 0.0, "error": None})
            if key not in self._inflight:
                self._enqueue(_PendingClip(
                    owner_id=run.owner_id, workspace_id=run.workspace_id,
                    lesson_id=run.lesson_id, run_id=run.run_id,
                    kind="qa", segment_id="reply",
                    chunk_index=0, clip_id=clip_id, text=text, key=key,
                    provider=profile.provider,
                    voice_id=profile.voice_id, language=profile.language,
                    allow_local_fallback=profile.allow_local_fallback))
        return results

    def clip_status(self, run: sc.ClassroomRun, clip_id: str) -> dict:
        """纯 GET：不合成；clip_id 必须在本 run audio_refs 中（§16.1）。"""
        key = run.audio_refs.get(clip_id)
        if not key:
            raise ClassroomError("source_not_found", "音频片段不存在")
        meta = self._load_meta(run.owner_id, run.workspace_id, run.lesson_id,
                               key)
        if meta is None:
            raise ClassroomError("source_not_found", "音频片段不存在")
        self._touch_key(run, key)
        return self._clip_dict(meta, clip_id)

    def clip_content(self, run: sc.ClassroomRun,
                     clip_id: str) -> tuple[bytes, dict]:
        """认证内容读取：owner 隔离 + run 绑定校验；GET 不合成。"""
        status = self.clip_status(run, clip_id)
        if status.get("state") != "ready":
            raise ClassroomError("audio_busy", "音频尚未就绪", retryable=True)
        key = run.audio_refs[clip_id]
        wav_path = store.audio_file_path(run.owner_id, run.workspace_id,
                                         run.lesson_id, key)
        if not wav_path.exists():
            raise ClassroomError("source_not_found", "音频内容缺失")
        data = wav_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if status.get("sha256") and digest != status["sha256"]:
            raise ClassroomError("damaged", "音频内容校验失败")
        return data, status

    # -- voice preview（§11.5：owner 独立 voice-previews 缓存） --------------

    async def voice_preview(self, owner_id: str, language: str,
                            prefs: Any) -> dict:
        profile = tts_service.resolve_classroom_tts(prefs, language)
        if not profile.voice_id:
            raise ClassroomError("voice_unavailable", "该语言无可用音色")
        sentence = _VOICE_PREVIEW_SENTENCES[
            "en" if str(language).startswith("en") else "zh"]
        text = normalize_spoken_text(sentence)
        key = synthesis_key(owner_id, text, provider=profile.provider,
                            voice_id=profile.voice_id,
                            language=profile.language, synthesis_speed=1.0)
        clip_id = f"vp_{key[:24]}"
        wav_path = store.voice_preview_path(owner_id, key, "wav")
        meta_path = store.voice_preview_path(owner_id, key, "json")
        if wav_path.exists() and meta_path.exists():
            _touch_path(meta_path)
            meta = store.read_json(meta_path) or {}
            return self._preview_dict(clip_id, key, meta)
        if profile.provider == "azure":
            assert_cloud_char_budget(owner_id, len(text))
        try:
            result = await self._synthesize_preview(text, profile)
        except VoiceProviderError as exc:
            raise ClassroomError("tts_unavailable",
                                 f"试听失败: {exc}") from exc
        wav = pcm16_to_wav(result.pcm16, result.sample_rate)
        meta = {
            "clip_id": clip_id, "kind": "preview", "synthesis_key": key,
            "provider": result.provider or profile.provider,
            "voice_id": result.voice_id or profile.voice_id,
            "language": profile.language, "sample_rate": result.sample_rate,
            "sample_count": len(result.pcm16) // 2, "bytes": len(wav),
            "sha256": hashlib.sha256(wav).hexdigest(), "state": "ready",
            "created_at": store.utcnow().isoformat(),
        }
        store.write_bytes(wav_path, wav)
        store.write_json(meta_path, meta)
        record_usage(owner_id, chars=len(text),
                     provider=meta["provider"])
        enforce_owner_audio_budget(owner_id, self._inflight_keys)
        return self._preview_dict(clip_id, key, meta)

    @staticmethod
    def _preview_dict(clip_id: str, key: str, meta: dict) -> dict:
        return {"clip_id": clip_id, "key": key,
                "state": meta.get("state") or "ready",
                "provider": meta.get("provider") or "",
                "voice_id": meta.get("voice_id") or "",
                "sample_rate": int(meta.get("sample_rate") or 0),
                "bytes": int(meta.get("bytes") or 0)}

    def voice_preview_content(self, owner_id: str, clip_id: str) -> bytes:
        """试听内容：voice-previews 下按 clip_id 定位（owner 隔离）。"""
        if not clip_id.startswith("vp_") or len(clip_id) < 8:
            raise ClassroomError("content_invalid", "非法试听 ID")
        previews_root = store.owner_root(owner_id) / "voice-previews"
        for wav, meta in _iter_audio_pairs(previews_root):
            if meta is None:
                continue
            data = store.read_json(meta) or {}
            if data.get("clip_id") == clip_id:
                if wav is None or not wav.exists():
                    raise ClassroomError("source_not_found", "试听内容缺失")
                _touch_path(meta)
                return wav.read_bytes()
        raise ClassroomError("source_not_found", "试听不存在")

    async def _synthesize_preview(self, text: str, profile: Any) -> Any:
        """无 run 上下文（试听）的云→本地回退；run 内回退见 _synthesize。"""
        if profile.provider == "azure":
            try:
                return await tts_service.cloud_synthesize(text, TTSOptions(
                    voice_id=profile.voice_id, language=profile.language,
                    synthesis_speed=1.0))
            except VoiceProviderError:
                if not (profile.allow_local_fallback
                        and tts_service.local_tts_enabled()
                        and profile.language == "zh-CN"):
                    raise
                return await tts_service.local_synthesize(text, TTSOptions(
                    voice_id="melo-zh", language="zh-CN",
                    synthesis_speed=1.0))
        return await tts_service.local_synthesize(text, TTSOptions(
            voice_id=profile.voice_id, language=profile.language,
            synthesis_speed=1.0))

    # -- 内部：lane / single-flight / 合成落盘 ------------------------------

    def _enqueue(self, item: _PendingClip) -> None:
        lane = self._lanes.setdefault(item.run_id, _Lane())
        lane.queue.append(item)
        if lane.task is None or lane.task.done():
            lane.task = asyncio.create_task(self._run_lane(item.run_id))

    async def _run_lane(self, run_id: str) -> None:
        lane = self._lanes.get(run_id)
        while lane is not None and lane.queue:
            item = lane.queue.popleft()
            try:
                await self._process(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("audio clip %s failed: %s", item.clip_id, exc)
            lane = self._lanes.get(run_id)
        current = self._lanes.get(run_id)
        if current is not None and not current.queue:
            self._lanes.pop(run_id, None)

    async def _process(self, item: _PendingClip) -> None:
        """single-flight：同 key 只合成一次，等待者共享结果。"""
        task = self._inflight.get(item.key)
        if task is None:
            task = asyncio.create_task(self._synthesize(item))
            self._inflight[item.key] = task
            self._inflight_keys.add(item.key)

            def _done(_t, key=item.key) -> None:
                self._inflight.pop(key, None)
                self._inflight_keys.discard(key)
            task.add_done_callback(_done)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # 等待者被取消（跳页/暂停）：外部调用可能已执行，任务继续跑完
            # 并缓存；不取消 task 本身（§11.4）。
            raise
        except Exception:
            pass   # 失败 meta 已由 _synthesize 落盘

    async def _synthesize(self, item: _PendingClip) -> None:
        try:
            await self._synthesize_inner(item)
        except VoiceProviderError as exc:
            self._store_failed(item, str(exc))
        except Exception as exc:   # 未知异常也要落定，不能永远 pending
            log.exception("audio synthesis crashed")
            self._store_failed(item, f"合成异常: {exc}")

    async def _synthesize_inner(self, item: _PendingClip) -> None:
        if item.provider == "azure":
            try:
                result = await tts_service.cloud_synthesize(
                    item.text, TTSOptions(voice_id=item.voice_id,
                                          language=item.language,
                                          synthesis_speed=1.0))
            except VoiceProviderError as exc:
                can_local = (item.allow_local_fallback
                             and tts_service.local_tts_enabled()
                             and item.language == "zh-CN")
                if not can_local:
                    raise
                await self._fallback_local(item, reason=str(exc))
                return
            self._store_ready(item, result.provider or "azure",
                              result.voice_id or item.voice_id,
                              item.language, result)
            record_usage(item.owner_id, chars=len(item.text),
                         provider="azure")
        else:
            result = await tts_service.local_synthesize(
                item.text, TTSOptions(voice_id=item.voice_id,
                                      language=item.language,
                                      synthesis_speed=1.0))
            self._store_ready(item, result.provider or "melo",
                              result.voice_id or item.voice_id,
                              item.language, result)
            record_usage(item.owner_id, chars=len(item.text),
                         provider="melo")
        self._post_write_budget(item)

    async def _fallback_local(self, item: _PendingClip, *,
                              reason: str) -> None:
        """云失败 → 本地（§11.5）：key/metadata 一律换本地 provider/voice，
        并把该 run 后续音色锁本地、只提示一次。"""
        from ..voice.tts.melotts import MeloTTS
        try:
            result = await tts_service.local_synthesize(
                item.text, TTSOptions(voice_id=MeloTTS.VOICE_ID,
                                      language="zh-CN", synthesis_speed=1.0))
        except VoiceProviderError as exc:
            # 本地也失败：以云端 key 落失败（下次显式重试再走策略）
            self._store_failed(item, f"云端失败（{reason}）且本地失败（{exc}）")
            return
        self._store_ready(item, "melo", MeloTTS.VOICE_ID, "zh-CN", result)
        record_usage(item.owner_id, chars=len(item.text), provider="melo")
        self._post_write_budget(item)
        self._lock_run_local(item)

    def _lock_run_local(self, item: _PendingClip) -> None:
        def mutate(run: sc.ClassroomRun) -> None:
            run.tts_local_locked = True
            run.tts_fallback_notified = True

        try:
            store.update_run(item.owner_id, item.workspace_id, item.lesson_id,
                             item.run_id, mutate, bump_revision=False)
        except Exception:
            log.warning("lock run %s to local tts failed", item.run_id,
                        exc_info=True)

    def _store_ready(self, item: _PendingClip, provider: str, voice_id: str,
                     language: str, result: Any) -> str:
        key = synthesis_key(item.owner_id, item.text, provider=provider,
                            voice_id=voice_id, language=language,
                            synthesis_speed=1.0)
        wav = pcm16_to_wav(result.pcm16, result.sample_rate)
        meta = {
            "clip_id": item.clip_id, "owner_id": item.owner_id,
            "lesson_id": item.lesson_id, "kind": item.kind,
            "segment_id": item.segment_id, "chunk_index": item.chunk_index,
            "synthesis_key": key, "provider": provider, "voice_id": voice_id,
            "language": language, "sample_rate": result.sample_rate,
            "sample_count": len(result.pcm16) // 2, "bytes": len(wav),
            "sha256": hashlib.sha256(wav).hexdigest(), "state": "ready",
            "created_at": store.utcnow().isoformat(),
        }
        store.write_bytes(store.audio_file_path(
            item.owner_id, item.workspace_id, item.lesson_id, key), wav)
        store.write_json(store.audio_meta_path(
            item.owner_id, item.workspace_id, item.lesson_id, key), meta)
        self._set_ref(item, key)
        return key

    def _store_failed(self, item: _PendingClip, error: str) -> None:
        key = synthesis_key(item.owner_id, item.text, provider=item.provider,
                            voice_id=item.voice_id,
                            language=item.language, synthesis_speed=1.0)
        meta = {
            "clip_id": item.clip_id, "owner_id": item.owner_id,
            "lesson_id": item.lesson_id, "kind": item.kind,
            "segment_id": item.segment_id, "chunk_index": item.chunk_index,
            "synthesis_key": key, "provider": item.provider,
            "voice_id": item.voice_id, "language": item.language,
            "sample_rate": 0, "sample_count": 0, "bytes": 0, "sha256": None,
            "state": "failed", "error": error[:500],
            "created_at": store.utcnow().isoformat(),
        }
        store.write_json(store.audio_meta_path(
            item.owner_id, item.workspace_id, item.lesson_id, key), meta)
        self._set_ref(item, key)

    def _post_write_budget(self, item: _PendingClip) -> None:
        try:
            enforce_lesson_audio_budget(item.owner_id, item.workspace_id,
                                        item.lesson_id)
            enforce_owner_audio_budget(item.owner_id, self._inflight_keys)
        except Exception:
            log.warning("audio budget enforcement failed", exc_info=True)

    def _remember_ref(self, run: sc.ClassroomRun, clip_id: str,
                      key: str) -> None:
        if run.audio_refs.get(clip_id) == key:
            return
        clip_id_c, key_c = clip_id, key

        def mutate(r: sc.ClassroomRun) -> None:
            if len(r.audio_refs) < limits.AUDIO_REFS_MAX:
                r.audio_refs[clip_id_c] = key_c

        try:
            store.update_run(run.owner_id, run.workspace_id, run.lesson_id,
                             run.run_id, mutate, bump_revision=False)
            run.audio_refs[clip_id] = key
        except Exception:
            log.warning("audio_refs update failed", exc_info=True)

    def _set_ref(self, item: _PendingClip, key: str) -> None:
        clip_id, key_c = item.clip_id, key

        def mutate(r: sc.ClassroomRun) -> None:
            if len(r.audio_refs) < limits.AUDIO_REFS_MAX:
                r.audio_refs[clip_id] = key_c

        try:
            store.update_run(item.owner_id, item.workspace_id, item.lesson_id,
                             item.run_id, mutate, bump_revision=False)
        except Exception:
            log.warning("audio_refs update failed (post)", exc_info=True)

    # -- 元数据小工具 --------------------------------------------------------

    def _load_meta(self, owner_id: str, workspace_id: str, lesson_id: str,
                   key: str) -> dict | None:
        meta = store.read_json(store.audio_meta_path(
            owner_id, workspace_id, lesson_id, key))
        if meta is None:
            return None
        if meta.get("state") == "ready":
            # WAV 缺失时 meta ready 不可信（中断/半写）：视为未就绪
            if not store.audio_file_path(owner_id, workspace_id, lesson_id,
                                         key).exists():
                return None
        return meta

    def _meta_ready(self, owner_id: str, workspace_id: str, lesson_id: str,
                    key: str) -> bool:
        meta = self._load_meta(owner_id, workspace_id, lesson_id, key)
        return bool(meta and meta.get("state") == "ready")

    def _touch_key(self, run: sc.ClassroomRun, key: str) -> None:
        _touch_path(store.audio_meta_path(run.owner_id, run.workspace_id,
                                          run.lesson_id, key))

    @staticmethod
    def _clip_dict(meta: dict, clip_id: str) -> dict:
        sample_rate = int(meta.get("sample_rate") or 0)
        sample_count = int(meta.get("sample_count") or 0)
        return {
            "clip_id": clip_id,
            "state": meta.get("state") or "pending",
            "provider": meta.get("provider") or "",
            "voice_id": meta.get("voice_id") or "",
            "sample_rate": sample_rate,
            "sample_count": sample_count,
            "bytes": int(meta.get("bytes") or 0),
            "duration_seconds": round(sample_count / sample_rate, 3)
            if sample_rate else 0.0,
            "sha256": meta.get("sha256"),
            "error": meta.get("error"),
        }

    # -- 取消与生命周期 ------------------------------------------------------

    def cancel_pending(self, run_id: str, keep: set[str] | None = None) -> int:
        """暂停/跳页：撤销该 run 排队中的等待者；在途合成继续（结果仍缓存，
        但不会送入旧播放队列——引擎不持有播放端）。"""
        keep = keep or set()
        lane = self._lanes.get(run_id)
        if not lane:
            return 0
        before = len(lane.queue)
        lane.queue = deque(item for item in lane.queue
                           if item.segment_id in keep)
        removed = before - len(lane.queue)
        if not lane.queue and (lane.task is None or lane.task.done()):
            self._lanes.pop(run_id, None)
        return removed

    async def wait_run(self, run_id: str) -> None:
        """等待该 run 排队合成全部落定（测试/优雅关停用）。"""
        lane = self._lanes.get(run_id)
        if lane and lane.task is not None and not lane.task.done():
            await asyncio.shield(lane.task)

    async def aclose(self) -> None:
        for lane in list(self._lanes.values()):
            lane.queue.clear()
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        self._lanes.clear()
        self._inflight_keys.clear()


def _touch_path(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


_ENGINE: AudioEngine | None = None


def get_audio_engine() -> AudioEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = AudioEngine()
    return _ENGINE


def reset_audio_engine() -> None:
    """测试重置（storage_sandbox 调用）；要求调用点无在途任务。"""
    global _ENGINE
    _ENGINE = None
