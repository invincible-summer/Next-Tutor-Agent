"""文本层保真度分级：判定 PDF 文本层页是 good/corrupt/sparse/empty。

背景（2026-08-26 对一组公共教材卷的取证）：部分定制字体教材 PDF 使用无
ToUnicode 映射的数学字体，其文本层「又密又烂」——PUA 字符、全角字母与
斜体映射替换大量混入正文。这些页字符数远超 ``pdf_ocr`` 的 20 字/页稀疏
阈值，被误判为良好文本层而从未 OCR，以乱码形态进入 RAG 索引。

本模块给出确定性判定：只有 sparse（文本层不足）或 corrupt（乱码证据
充分）的页才需要 OCR；良好文本层页绝不降质重 OCR。纯函数、无 LLM、
永不抛出；阈值全部按取证数据标定（注释给出实测依据）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 稀疏阈值与 pdf_ocr._SCANNED_MIN_CHARS_PER_PAGE 对齐（一页正常教材正文
# 通常数百字符以上；扫描页 get_text() 基本为 0）。
_SPARSE_MIN_CHARS = 20

# corrupt 判定双门槛：乱码字符数 *且* 占比同时达标（缺一不可）。
# 实测乱码页两项指标同时远超门槛；好卷（OCR 产物/正常文本层）≈0。
# 下限 8 个 + 0.2% 保证「排版偶用全角数字」等合法情形不误判。
_GARBLE_MIN_COUNT = 8
_GARBLE_MIN_RATE = 0.002

# 辅助结构信号：孤立短行占比（矩阵被逐字炸成竖排的特征：１/⋱/ｋ 各占
# 一行）。单独不构成 corrupt（诗歌/LaTeX 定界符也是短行），必须叠加乱码
# 字符证据才生效。
_ISOLATED_LINE_RATIO = 0.35
_ISOLATED_LINE_GARBLE_MIN = 3

# 私用区（字体内部码位，无 Unicode 语义）：英语音标、定制数学字体大量落入。
_PUA_RE = re.compile(r"[\ue000-\uf8ff]")
# U+FFFD 替换字符（解码失败的兜底产物）。
_REPLACEMENT_RE = re.compile(r"\ufffd")
# 教材正文不应出现的文字系统：彝文（ꎬ=U+A8AC 实为 Saurashtra 区，一并
# 覆盖相邻稀有区段）、埃塞文（ቃ）、切罗基文（Ꮶ）等。取证中它们被用作
# 省略号/符号占位。
_UNEXPECTED_SCRIPT_RE = re.compile(
    "[\U000013a0-\U000013ff"    # Cherokee（Ꮶ）
    "\U00001200-\U0000137f"     # Ethiopic（ቃ）
    "\U0000a000-\U0000a4cf"     # Yi 音节 + 部首
    "\U0000a500-\U0000a63f"     # Vai
    "\U0000a880-\U0000a8df"     # Saurashtra（ꎬ = U+A8AC 在此）
    "]")
# 全角拉丁字母/数字（Ｅ(ｉ(ｋ)＝ 型公式文本）。注意全角标点（，（）！）
# 是正常中文排版，不计入。
_FULLWIDTH_ALNUM_RE = re.compile(r"[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]")
# 定制字体的「斜体拉丁字母」映射区（乱码页中大量出现 犃犅 型替换）。正常中文文本几乎不用这些字；
# 发现新的映射字符按需扩充本集合。
_FONT_SUBST_CHARS = frozenset(
    "犃犅犆犇犈犉犌犎犑犓犔犕犖犗犘犙犚犛犜犝犞犠犡犢犤犫"
    "犪犫犮犱犲犳犵犻犼犽犾犿狀狏狏")

_ISOLATED_LINE_RE = re.compile(r"^\s*\S{0,2}\s*$")


@dataclass(frozen=True)
class PageMetrics:
    """单页文本层的乱码指标（计数 + 比率），供路由与质检共用。"""
    chars: int
    cjk_chars: int
    pua_count: int
    replacement_count: int
    unexpected_script_count: int
    fullwidth_alnum_count: int
    font_subst_count: int
    isolated_line_ratio: float

    @property
    def garble_count(self) -> int:
        return (self.pua_count + self.replacement_count
                + self.unexpected_script_count + self.fullwidth_alnum_count
                + self.font_subst_count)

    @property
    def garble_rate(self) -> float:
        return self.garble_count / self.chars if self.chars else 0.0


def page_metrics(text: str) -> PageMetrics:
    """统计一页文本的乱码指标（空文本返回全零，永不抛出）。"""
    text = text or ""
    stripped = text.strip()
    if not stripped:
        return PageMetrics(0, 0, 0, 0, 0, 0, 0, 0.0)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    isolated = sum(1 for ln in lines if _ISOLATED_LINE_RE.match(ln))
    return PageMetrics(
        chars=len(stripped),
        cjk_chars=cjk,
        pua_count=len(_PUA_RE.findall(text)),
        replacement_count=len(_REPLACEMENT_RE.findall(text)),
        unexpected_script_count=len(_UNEXPECTED_SCRIPT_RE.findall(text)),
        fullwidth_alnum_count=len(_FULLWIDTH_ALNUM_RE.findall(text)),
        font_subst_count=sum(1 for ch in text if ch in _FONT_SUBST_CHARS),
        isolated_line_ratio=(isolated / len(lines)) if lines else 0.0,
    )


def classify_page(text: str) -> str:
    """判定单页文本层：``good``/``corrupt``/``sparse``/``empty``。

    corrupt = 稠密但乱码证据充分（定制字体无 ToUnicode 映射的典型产物）；
    sparse  = 字符量不足（扫描页/图片页）；empty = 无内容。
    """
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    if len(stripped) < _SPARSE_MIN_CHARS:
        return "sparse"
    m = page_metrics(text)
    dense_garble = (m.garble_count >= _GARBLE_MIN_COUNT
                    and m.garble_rate >= _GARBLE_MIN_RATE)
    structural_garble = (m.isolated_line_ratio >= _ISOLATED_LINE_RATIO
                         and m.garble_count >= _ISOLATED_LINE_GARBLE_MIN)
    return "corrupt" if (dense_garble or structural_garble) else "good"


def page_verdicts(page_texts: list[str]) -> list[str]:
    """逐页判定（与 ``pdf_ocr.sparse_page_indices`` 同形的批量入口）。"""
    return [classify_page(t) for t in (page_texts or [])]


def summarize_pages(page_texts: list[str]) -> dict[str, float]:
    """整卷 verdict 统计 + 加权乱码率（质量报告/staging 质检用）。"""
    verdicts = page_verdicts(page_texts)
    total = len(verdicts)
    out: dict[str, float] = {
        "total": total, "good": 0, "corrupt": 0, "sparse": 0, "empty": 0,
        "garble_rate": 0.0,
    }
    garble_total = 0
    char_total = 0
    for text, verdict in zip(page_texts or [], verdicts):
        out[verdict] += 1
        m = page_metrics(text)
        garble_total += m.garble_count
        char_total += m.chars
    if char_total:
        out["garble_rate"] = garble_total / char_total
    return out


def text_garble_ratio(text: str) -> float:
    """任意文本片段的乱码比率（staging 质检对 chunk 文本的采样入口）。"""
    m = page_metrics(text)
    return m.garble_rate


__all__ = ["PageMetrics", "page_metrics", "classify_page", "page_verdicts",
           "summarize_pages", "text_garble_ratio"]
