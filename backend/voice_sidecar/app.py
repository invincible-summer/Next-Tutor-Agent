"""MeloTTS-Chinese sidecar: POST /tts -> WAV (44.1 kHz).

MeloTTS code and the selected MeloTTS-Chinese model revision are recorded as
MIT in docs/VOICE_LICENSES.md; runtime dependencies retain their own licenses.

MeloTTS ships only a Python class (melo.api.TTS) — no HTTP server — so
this tiny FastAPI app wraps it in its own process with its own venv,
keeping torch out of the main backend (the backend calls it via
app/voice/tts/melotts.py over localhost HTTP).

The MeloTTS repo is used from backend/vendor/MeloTTS via sys.path instead
of `pip install`: its setup.py post-install hook would download the 1 GB
unidic dictionary (Japanese-only and outside the default install scope; see
docs/VOICE_LICENSES.md §4).

Run (start.sh does this automatically when VOICE_TTS_PROVIDER=melo):
  cd backend/voice_sidecar && HF_HUB_OFFLINE=1 \
  HF_HOME=../models/voice/hf .venv/bin/python -m uvicorn app:app \
  --host 127.0.0.1 --port 8130
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 挂载 backend/vendor/MeloTTS，并为未启用的非中文 cleaner 安装 fail-loud
# import stubs（见 melo_bootstrap.py 与 docs/VOICE_LICENSES.md §4）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from melo_bootstrap import bootstrap as _melo_bootstrap  # noqa: E402

_melo_bootstrap()

from fastapi import FastAPI, HTTPException, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI(title="edu-agent-melotts-sidecar", docs_url=None, redoc_url=None)

# Model load happens at import time (uvicorn serves nothing until ready).
# language="ZH" maps to the ZH_MIX_EN frontend: Chinese text with embedded
# English words, the only voice path installed by the default script.
from melo.api import TTS  # noqa: E402

_model = TTS(language="ZH", device="cpu")
_SPEAKER_ID = _model.hps.data.spk2id["ZH"]
_SAMPLE_RATE = int(_model.hps.data.sampling_rate)

# The backend force-splits everything to <=120 chars; this cap is a
# defensive bound against a runaway request pinning the CPU for minutes.
_MAX_CHARS = 400


class TTSRequest(BaseModel):
    text: str
    speed: float = 1.0


@app.get("/health")
def health() -> dict:
    return {"ok": True, "language": "ZH", "sample_rate": _SAMPLE_RATE}


@app.post("/tts")
def tts(req: TTSRequest) -> Response:
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    if len(text) > _MAX_CHARS:
        raise HTTPException(400, f"text too long (> {_MAX_CHARS} chars)")
    speed = min(2.0, max(0.5, float(req.speed or 1.0)))
    # melo 的 replace_punctuation 白名单放行了 "_"，但 "_" 不在其
    # punctuation 集合里：下划线（LaTeX 下标、snake_case 标识符）进入
    # 中文 g2p 会触发上游 assert（500）。替换成空格即可安全朗读。
    text = text.replace("_", " ").strip()
    if not text:
        raise HTTPException(400, "text has no speakable characters")
    with tempfile.NamedTemporaryFile(suffix=".wav", prefix="melo-") as tmp:
        _model.tts_to_file(text=text, speaker_id=_SPEAKER_ID,
                           output_path=tmp.name, speed=speed)
        audio = Path(tmp.name).read_bytes()
    return Response(content=audio, media_type="audio/wav")
