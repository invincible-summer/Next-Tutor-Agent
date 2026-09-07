"""Mount MeloTTS while keeping unused language paths out of the sidecar.

The pinned MeloTTS release imports every language module from
``melo.text.cleaner`` even though this service selects only ``ZH`` (the
Chinese/English mixed frontend). This sidecar never executes the other
language paths, so it installs small, fail-loud modules before MeloTTS is
imported instead of installing their language dictionaries, phonemizers,
tokenizers, or unrelated model-download dependency trees.

If a future change selects a non-Chinese language, the relevant package must
be added deliberately and its license/data terms must be audited again.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
import warnings

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
MELO_ROOT = os.environ.get("MELO_ROOT") or os.path.abspath(
    os.path.join(_THIS_DIR, "..", "vendor", "MeloTTS"))


def _silence_upstream_deprecation_noise() -> None:
    """Silence the two FutureWarnings the pinned MeloTTS stack always emits.

    Vendored MeloTTS calls ``hf_hub_download(..., resume_download=...)`` and
    builds its modules with ``torch.nn.utils.weight_norm``; the pinned
    huggingface_hub/torch versions deprecation-warn both. The warnings fire
    during model load — exactly when start.sh is printing the frontend
    build output — and read like build errors. Filtered by message (not
    blanket-ignored) so genuinely new deprecations still surface.
    """
    warnings.filterwarnings(
        "ignore", message="`resume_download` is deprecated", category=FutureWarning)
    warnings.filterwarnings(
        "ignore", message="`torch.nn.utils.weight_norm` is deprecated",
        category=FutureWarning)


class _UnavailableLanguagePath(RuntimeError):
    """Raised if an excluded non-Chinese language path is accidentally used."""


class _KakasiConverterStub:
    def do(self, text: str) -> str:
        raise _UnavailableLanguagePath("Japanese text conversion is not installed")


class _KakasiStub:
    def setMode(self, *args, **kwargs) -> None:
        pass

    def getConverter(self) -> _KakasiConverterStub:
        return _KakasiConverterStub()


class _MeCabTaggerStub:
    def __init__(self, *args, **kwargs):
        pass

    def parse(self, *args, **kwargs):
        raise _UnavailableLanguagePath("Japanese MeCab parsing is not installed")


def _unavailable_num2words(*args, **kwargs):
    raise _UnavailableLanguagePath("Non-Chinese number normalization is not installed")


def _unavailable_anyascii(*args, **kwargs):
    raise _UnavailableLanguagePath("Korean transliteration is not installed")


def _unavailable_hangul_to_jamo(*args, **kwargs):
    raise _UnavailableLanguagePath("Korean jamo conversion is not installed")


def _unavailable_gruut(*args, **kwargs):
    raise _UnavailableLanguagePath("Non-Chinese phonemization is not installed")


def _unavailable_cached_path(*args, **kwargs):
    raise _UnavailableLanguagePath(
        "MeloTTS non-Hugging-Face model download path is not enabled"
    )


class _UnavailableIPA:
    @staticmethod
    def without_stress(*args, **kwargs):
        raise _UnavailableLanguagePath("Non-Chinese IPA processing is not installed")


def _make_language_module(name: str) -> types.ModuleType:
    module = types.ModuleType(f"melo.text.{name}")
    message = f"MeloTTS language path {name!r} is not installed"

    def unavailable(*args, **kwargs):
        raise _UnavailableLanguagePath(message)

    module.text_normalize = unavailable
    module.g2p = unavailable
    module.get_bert_feature = unavailable
    module.distribute_phone = unavailable
    return module


def _make_japanese_module() -> types.ModuleType:
    """Stub Japanese imports while retaining English's phone distribution helper."""
    module = _make_language_module("japanese")

    def distribute_phone(n_phone: int, n_word: int) -> list[int]:
        phones_per_word = [0] * n_word
        for _ in range(n_phone):
            index = phones_per_word.index(min(phones_per_word))
            phones_per_word[index] += 1
        return phones_per_word

    module.distribute_phone = distribute_phone
    return module


def _install_stubs() -> None:
    # These names are referenced by excluded Japanese/Korean cleaners.
    # Do not import installed copies even if a caller contaminated the venv.
    pykakasi = types.ModuleType("pykakasi")
    pykakasi.kakasi = _KakasiStub
    sys.modules["pykakasi"] = pykakasi

    num2words = types.ModuleType("num2words")
    num2words.num2words = _unavailable_num2words
    sys.modules["num2words"] = num2words

    mecab = types.ModuleType("MeCab")
    mecab.Tagger = _MeCabTaggerStub
    sys.modules["MeCab"] = mecab

    anyascii = types.ModuleType("anyascii")
    anyascii.anyascii = _unavailable_anyascii
    sys.modules["anyascii"] = anyascii

    jamo = types.ModuleType("jamo")
    jamo.hangul_to_jamo = _unavailable_hangul_to_jamo
    sys.modules["jamo"] = jamo

    gruut = types.ModuleType("gruut")
    gruut.__version__ = "excluded"
    gruut.sentences = _unavailable_gruut
    gruut.is_language_supported = _unavailable_gruut
    gruut.get_supported_languages = _unavailable_gruut
    sys.modules["gruut"] = gruut

    gruut_ipa = types.ModuleType("gruut_ipa")
    gruut_ipa.IPA = _UnavailableIPA
    sys.modules["gruut_ipa"] = gruut_ipa

    # The active ZH path always downloads from Hugging Face. Upstream imports
    # cached_path only for its alternative S3/non-HF path; stub it so that the
    # unused Google Cloud/Boto dependency tree is not installed accidentally.
    cached_path = types.ModuleType("cached_path")
    cached_path.cached_path = _unavailable_cached_path
    sys.modules["cached_path"] = cached_path

    # get_bert() imports every language backend, and the upstream French,
    # Spanish and Korean modules construct tokenizers at import time.  Only
    # Chinese mixed English is supported here; pre-installing these modules
    # avoids downloading unrelated language models while retaining a loud
    # failure if a caller selects one accidentally.
    importlib.import_module("melo.text")
    sys.modules["melo.text.japanese"] = _make_japanese_module()
    for name in ("korean", "french", "spanish", "english_bert",
                 "japanese_bert", "french_bert", "spanish_bert"):
        sys.modules[f"melo.text.{name}"] = _make_language_module(name)


def bootstrap() -> str:
    """Put the pinned MeloTTS source on ``sys.path`` and install stubs."""
    _silence_upstream_deprecation_noise()
    if os.path.isdir(MELO_ROOT) and MELO_ROOT not in sys.path:
        sys.path.insert(0, MELO_ROOT)
    _install_stubs()
    return MELO_ROOT
