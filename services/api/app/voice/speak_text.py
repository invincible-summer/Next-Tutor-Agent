"""Answer text -> speakable plain text for TTS.

Chat answers are markdown with inline LaTeX; reading raw markup aloud is
unintelligible. Normalization: fenced code collapses to a short spoken
placeholder (inline code keeps its inner text), math fragments get common
LaTeX commands/symbols mapped to Chinese readings, markdown emphasis,
links and table pipes are stripped. Deliberately conservative — anything
unmapped survives as plain characters rather than being deleted (unknown
``\\command`` names keep their letters; they read as English words, which
beats silence or a raw backslash).

Math readings run in ordered stages (see ``_read_math``): number sets and
SI units first, then semantic groups with arguments — nested
``\\frac``/``\\sqrt`` collapse inside-out in a fixpoint loop — then
``^\\circ``/``\\%`` (they must beat the generic superscript rule),
combined ``\\sum``/``\\int`` bounds, named functions and relations,
intervals / absolute values / brackets, and finally the keep-the-letters
fallback.

The model emits all four math delimiter styles (``$...$``, ``$$...$$``,
``\\(...\\)``, ``\\[...\\]``); :func:`normalize_math_delimiters` folds the
paren/bracket forms into dollar spans so both the sentence splitter and
the reading rules only ever see one delimiter family.
"""
from __future__ import annotations

import re

_CODE_FENCE = re.compile(r"```.*?```", re.S)
_CODE_INLINE = re.compile(r"`([^`\n]+)`")
# Tool-call markup a weak model sometimes narrates into the answer instead
# of using native function calling (<tool_call><function=…><parameter=…>…).
# PseudoToolGuard stops it at the chat layer; this strip is the TTS-side
# backstop so a leak through any other path is never read aloud. Blocks
# remove with their content (unclosed tail included); leftover tag shells
# remove alone.
_TOOL_BLOCK = re.compile(
    r"<(?:tool_call|knowledge_search)[^>]*>.*?(?:</(?:tool_call|knowledge_search)>|$)",
    re.S | re.I)
_TOOL_SHELL = re.compile(
    r"</?(?:tool_call|knowledge_search|function|parameter)(?:=[^<>\s]*)?[^<>]*>",
    re.I)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MATH_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)
_MATH_INLINE = re.compile(r"\$([^$\n]+)\$")
# Trailing unclosed math (streaming cuts, unpaired $ from the model): read
# the fragment as math instead of dropping the delimiter and reciting raw
# LaTeX characters.
_MATH_TAIL = re.compile(r"\$\$?(.+)$", re.S)
_MATH_PAREN = re.compile(r"(?<!\\)\\\((.+?)\\\)", re.S)
_MATH_BRACKET = re.compile(r"(?<!\\)\\\[(.+?)\\\]", re.S)
_HEADING = re.compile(r"^#{1,6}\s*", re.M)
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.、)])\s+", re.M)
_QUOTE = re.compile(r"^>\s?", re.M)
_EMPH = re.compile(r"\*\*|__|\*|~~")
# Table separator rows (|---|---|) must go before pipe stripping, or they
# collapse into a bare "------" dash run that reads as 傻杠杠.
_TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t:|\-]+\|?[ \t]*$", re.M)
_TABLE_PIPE = re.compile(r"\|")
_HR = re.compile(r"^ {0,3}(?:-{3,}|\*{3,})$", re.M)

# One brace group's content: any run of single characters and/or nested
# brace groups (two levels deep), so \frac{\frac{1}{2}}{3} and \sqrt{x^{2}+1}
# match as a whole; deeper spans still collapse because the fixpoint loop
# re-runs the substitution inside-out.
_GROUP = r"(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})+"

# A single braceless argument: one non-space character that is not a brace
# or backslash — LaTeX allows \frac12, \sqrt2, \vec L without braces.
_CHAR_ARG = r"([^\s{}\\])"


def normalize_math_delimiters(text: str) -> str:
    r"""Fold \(...\) / \[...\] math spans into $...$ / $$...$$.

    Mirrors the frontend markdown pipeline so downstream stages only see
    dollar delimiters. Idempotent, so the streaming splitter can re-run it
    on its cumulative buffer every delta. Lone delimiters left over by a
    streaming cut (opener arrived, closer still in flight) fold too — an
    unclosed \[ must hold the sentence buffer exactly like an unclosed $$.
    The lookbehind keeps \\[2mm]-style row breaks (backslash, bracket)
    from being mistaken for a display opener.
    """
    text = _MATH_BRACKET.sub(lambda m: "$$" + m.group(1) + "$$", text)
    text = _MATH_PAREN.sub(lambda m: "$" + m.group(1) + "$", text)
    text = re.sub(r"(?<!\\)\\\[", "$$", text)
    text = re.sub(r"(?<!\\)\\\]", "$$", text)
    text = re.sub(r"(?<!\\)\\\(", "$", text)
    text = re.sub(r"(?<!\\)\\\)", "$", text)
    return text


# ---------------------------------------------------------------------------
# SI units: \text{m/s} / \mathrm{kg} read as 米每秒 / 千克, not letter soup.
# ---------------------------------------------------------------------------
_UNIT_PREFIXES = {"k": "千", "M": "兆", "G": "吉", "m": "毫", "c": "厘",
                  "d": "分", "μ": "微", "n": "纳", "p": "皮"}
_UNIT_BASES = {
    "m": "米", "s": "秒", "g": "克", "N": "牛", "J": "焦", "W": "瓦",
    "A": "安", "V": "伏", "F": "法拉", "C": "库仑", "T": "特斯拉",
    "H": "亨利", "Wb": "韦伯", "Ω": "欧姆", "Hz": "赫兹", "Pa": "帕斯卡",
    "mol": "摩尔", "K": "开尔文", "L": "升", "l": "升", "t": "吨",
    "eV": "电子伏", "rad": "弧度", "min": "分钟", "h": "小时",
    "dB": "分贝", "atm": "大气压", "°C": "摄氏度",
}
_UNIT_ATOM = re.compile(r"^([A-Za-zμΩ°]+?)(?:\^(-?\d+))?$")
_UNIT_SEP = re.compile(r"[·*\s]+")
# \text{...}/\mathrm{...} plus an optional exponent hanging OUTSIDE the
# group (\text{m}^2, \mathrm{s}^{-1}).
_UNIT_TEXT = re.compile(
    r"\\(?:operatorname\*?|text(?:rm|bf|it|sf|tt)?|math(?:rm|bf|it|sf|tt|cal|scr|frak)|boldsymbol)"
    r"\s*\{([^{}]*)\}\s*(?:\^\s*\{(-?\d+)\}|\^\s*(-?\d+))?")


def _read_unit_token(token: str) -> str | None:
    match = _UNIT_ATOM.match(token)
    if not match:
        return None
    base, power = match.group(1), match.group(2)
    name = _UNIT_BASES.get(base)
    if name is None and len(base) > 1 and base[0] in _UNIT_PREFIXES:
        suffix = _UNIT_BASES.get(base[1:])
        if suffix is not None:
            name = _UNIT_PREFIXES[base[0]] + suffix
    if name is None:
        return None
    if not power:
        return name
    exponent = int(power)
    if exponent == 2:
        return "平方" + name
    if exponent == 3:
        return "立方" + name
    if exponent == -1:
        return "每" + name
    if exponent == -2:
        return "每平方" + name
    return f"{name}的{power}次方"


def _read_units(content: str, exponent: str = "") -> str | None:
    """Parse a unit expression (m/s^2, kg·m, kPa, s^{-1}); None if it is
    not pure units (CJK words, variables, anything unknown falls back to
    the plain \\text keeper)."""
    text = content.strip().replace("(", "").replace(")", "")
    text = text.replace("{", "").replace("}", "")
    if not text or re.search(r"[\u4e00-\u9fff\\]", text):
        return None
    if exponent:
        text += "^" + exponent
    readings: list[str] = []
    for group in text.split("/"):
        atoms = [atom for atom in _UNIT_SEP.split(group) if atom]
        if not atoms:
            return None
        parts = []
        for atom in atoms:
            token = _read_unit_token(atom)
            if token is None:
                return None
            parts.append(token)
        readings.append("".join(parts))
    return "每".join(readings)


def _unit_text_sub(match: re.Match) -> str:
    exponent = match.group(2) if match.group(2) is not None else (match.group(3) or "")
    reading = _read_units(match.group(1), exponent)
    return reading if reading is not None else match.group(0)


# Number sets: \mathbb{R} and the braceless \mathbb R. Must run before the
# \text-family keeper (which would otherwise flatten \mathbb{R} to a bare
# letter).
_MATHBB_SETS = {"R": "实数集", "Z": "整数集", "N": "自然数集", "Q": "有理数集", "C": "复数集"}
_MATHBB = re.compile(r"\\mathbb\s*\{?\s*([RrZzNnQqCc])\s*\}?")

# \text{...} and friends keep only their inner content.
_TEXT_KEEPER = re.compile(
    r"\\(?:operatorname\*?|text(?:rm|bf|it|sf|tt)?|math(?:rm|bf|it|sf|tt|cal|scr|frak)|boldsymbol)\s*\{(" + _GROUP + r")\}")

# Braceless text-family form the physics textbook favors: \mathrm d x,
# \mathrm dt — the whole letter run is the kept content.
_TEXT_FAMILY_BARE = re.compile(
    r"\\(?:operatorname\*?|text(?:rm|bf|it|sf|tt)?|math(?:rm|bf|it|sf|tt)|boldsymbol)\s+([A-Za-z]+)(?![A-Za-z])")

# Old-style font switches take no argument: {\rm ext} / \rm ext.
_FONT_SWITCH = re.compile(r"\\(?:rm|bf|it|cal|sf|tt)(?![a-zA-Z])")

# Commands whose name only makes sense with arguments: when the structured
# rule could not fire (arguments truncated by a cut message), drop the name
# instead of reading it as an English word.
_MATH_NAME_ONLY = re.compile(
    r"\\(?:[dt]?frac|sqrt|binom|boxed|overline|underline|overrightarrow|vec|bar|hat|widehat|tilde|dot|ddot"
    r"|mathbb|mathrm|text|textbf|textit|textrm|operatorname|begin|end|left|right"
    r"|bigl|bigr|biggl|biggr|Bigl|Bigr|Biggl|Biggr)\s*")


def _arg(match: re.Match, braced: int, bare: int) -> str:
    return match.group(braced) if match.group(braced) is not None else match.group(bare)


def _frac_sub(match: re.Match) -> str:
    # \frac{a}{b} reads 「b分之a」; braceless \frac12 likewise.
    return f"{_arg(match, 3, 4)}分之{_arg(match, 1, 2)}"


def _power_sub(match: re.Match) -> str:
    body = match.group(1)
    if body.startswith("-"):
        return f"的负{body[1:]}次方"
    # Nested power reads naturally: e^{x^2} -> e的x平方次方.
    nested = re.fullmatch(r"([A-Za-z0-9]+)\^(\d+)", body)
    if nested:
        spoken = {"2": "平方", "3": "立方"}.get(nested.group(2))
        if spoken:
            return f"的{nested.group(1)}{spoken}次方"
    return f"的{body}次方"


def _interval_sub(kind: str):
    def sub(match: re.Match) -> str:
        def spoken(bound: str) -> str:
            bound = bound.strip()
            return "负" + bound[1:] if bound.startswith("-") else bound
        return f"从{spoken(match.group(1))}到{spoken(match.group(2))}的{kind}"
    return sub


# Semantic argument groups, applied in a fixpoint loop (nested constructs
# like \frac{\sqrt{2}}{2} resolve over successive passes). Braced forms
# first, braceless single-character forms after.
_MATH_READINGS_PASS: tuple[tuple[re.Pattern, str], ...] = (
    # \frac{\partial f}{\partial x} -> f对x的偏导数 (before the generic frac).
    # Exponent form first: \frac{\partial^2 z}{\partial x\partial y} ->
    # z对x、y的2阶偏导数.
    (re.compile(r"\\[dt]?frac\s*\{\\partial\s*\^\s*\{?(\d+)\}?\s*(" + _GROUP + r")\}\s*\{\\partial\s*(" + _GROUP + r")}"),
     r"\2对\3的\1阶偏导数，"),
    (re.compile(r"\\[dt]?frac\s*\{\\partial\s*(" + _GROUP + r")\}\s*\{\\partial\s*(" + _GROUP + r")}"),
     r"\1对\2的偏导数"),
    (re.compile(r"\\[dt]?frac\s*(?:\{(" + _GROUP + r")\}|" + _CHAR_ARG + r")"
                r"\s*(?:\{(" + _GROUP + r")\}|" + _CHAR_ARG + ")"), _frac_sub),
    (re.compile(r"\\sqrt\s*\[([^\]]+)\]\s*\{(" + _GROUP + r")}"), r"\1次根号\2"),
    (re.compile(r"\\sqrt\s*\{(" + _GROUP + r")}"), r"根号\1"),
    (re.compile(r"\\sqrt\s*([A-Za-z0-9])"), r"根号\1"),
    (re.compile(r"\\binom\s*\{(" + _GROUP + r")\}\s*\{(" + _GROUP + r")}"),
     r"从\1中选\2"),
    (re.compile(r"\\boxed\s*\{(" + _GROUP + r")}"), r"\1"),
    (re.compile(r"\\overline\s*\{(" + _GROUP + r")}"), r"\1拔"),
    (re.compile(r"\\bar\s*\{(" + _GROUP + r")}"), r"\1拔"),
    (re.compile(r"\\bar\s*([A-Za-z0-9])"), r"\1拔"),
    (re.compile(r"\\hat\s*\{(" + _GROUP + r")}"), r"\1帽"),
    (re.compile(r"\\hat\s*([A-Za-z])"), r"\1帽"),
    (re.compile(r"\\widehat\s*\{(" + _GROUP + r")}"), r"\1帽"),
    (re.compile(r"\\tilde\s*\{(" + _GROUP + r")}"), r"\1波浪"),
    (re.compile(r"\\overrightarrow\s*\{(" + _GROUP + r")}"), r"向量\1"),
    (re.compile(r"\\overrightarrow\s*(\\[a-zA-Z]+)"), r"向量\1"),
    (re.compile(r"\\vec\s*\{(" + _GROUP + r")}"), r"向量\1"),
    (re.compile(r"\\vec\s*(\\[a-zA-Z]+)"), r"向量\1"),
    (re.compile(r"\\vec\s*([A-Za-z0-9])"), r"向量\1"),
    (re.compile(r"\\dot\s*\{(" + _GROUP + r")}"), r"\1点"),
    (re.compile(r"\\ddot\s*\{(" + _GROUP + r")}"), r"\1两点"),
)

_MATH_READINGS: tuple[tuple[re.Pattern, str], ...] = (
    # ---- positioned markers, before the generic ^/_ rules ----------------
    # 30^\circ / 30^{\circ} reads 度, NOT 的圈次方.
    (re.compile(r"\^\s*\{?\s*\\circ\s*\}?"), "度"),
    (re.compile(r"\\(?:degree|deg)(?![a-zA-Z])"), "度"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*\\%"), r"百分之\1"),
    (re.compile(r"\\%"), "百分号"),
    (re.compile(r"\\pmod\s*\{(" + _GROUP + r")}"), r"模\1"),
    # \lim_{x \to \infty} f(x) -> "当x 趋于 无穷 时..." (before the generic
    # subscript rule mangles it into "下标").
    (re.compile(r"\\lim\s*_?\s*\{(" + _GROUP + r")}"), r"当\1"),
    # Layout no-ops first so they never wedge between an operator and its
    # bounds (\sum\limits_{i=1}^{n}, \displaystyle\lim_{x\to 0}).
    (re.compile(r"\\(?:displaystyle|textstyle|limits|nolimits)"), ""),
    # Combined bounds: \sum_{i=1}^{n} -> 求和，从i等于1到n（先于通用上下标）.
    (re.compile(r"\\sum\s*_\s*\{?([-+\d\w.=]+|\\infty)\}?\s*\^\s*\{?([-+\d\w.=]+|\\infty)\}?"),
     r"求和，从\1到\2，"),
    (re.compile(r"\\prod\s*_\s*\{?([-+\d\w.=]+|\\infty)\}?\s*\^\s*\{?([-+\d\w.=]+|\\infty)\}?"),
     r"连乘，从\1到\2，"),
    (re.compile(r"\\oint"), "环路积分"),
    (re.compile(r"\\int\s*_\s*\{?([-+\d\w.=]+|\\infty)\}?\s*\^\s*\{?([-+\d\w.=]+|\\infty)\}?"),
     r"积分，从\1到\2，"),
    (re.compile(r"\\log\s*_\s*\{?(\w+)\}?"), r"以\1为底的对数"),
    # Prime: f'(x) -> f撇(x).
    (re.compile(r"\\prime(?![a-zA-Z])"), "撇"),
    (re.compile(r"([\w\}])'"), r"\1撇"),
    # ---- relations / set operators (lookahead-guarded: \le must not eat
    # \left; \ne1 and \sin2x must still match before a digit) --------------
    (re.compile(r"\\(?:le|leq|leqslant)(?![a-zA-Z])"), "小于等于"),
    (re.compile(r"\\(?:ge|geq|geqslant)(?![a-zA-Z])"), "大于等于"),
    (re.compile(r"\\(?:neq?)(?![a-zA-Z])"), "不等于"),
    (re.compile(r"\\equiv"), "恒等于"),
    (re.compile(r"\\cong"), "全等于"),
    (re.compile(r"\\(?:simeq|approx|thickapprox)"), "约等于"),
    (re.compile(r"\\sim(?![a-zA-Z])"), "相似于"),
    (re.compile(r"\\propto"), "成正比"),
    (re.compile(r"\\(?:perp)"), "垂直于"),
    (re.compile(r"\\(?:parallel|shortparallel)"), "平行于"),
    (re.compile(r"\\notin(?![a-zA-Z])"), "不属于"),
    (re.compile(r"\\(?:in|isin)(?![a-zA-Z])"), "属于"),
    (re.compile(r"\\(?:subseteqq?|subsetneq(?:q)?|subset)"), "包含于"),
    (re.compile(r"\\(?:supseteqq?|supset)"), "包含"),
    (re.compile(r"\\(?:cup|uplus)"), "并"),
    (re.compile(r"\\(?:cap|capwedge)"), "交"),
    (re.compile(r"\\setminus"), "差"),
    (re.compile(r"\\(?:emptyset|varnothing)"), "空集"),
    (re.compile(r"\\forall"), "任意"),
    (re.compile(r"\\(?:exists|exist)"), "存在"),
    (re.compile(r"\\(?:neg|lnot)"), "非"),
    (re.compile(r"\\(?:land|wedge|sqcap)"), "且"),
    (re.compile(r"\\(?:lor|vee|sqcup)"), "或"),
    (re.compile(r"\\(?:because)"), "因为"),
    (re.compile(r"\\(?:therefore)"), "所以"),
    (re.compile(r"\\(?:Rightarrow|Longrightarrow|implies|impl)"), "推出"),
    (re.compile(r"\\(?:Leftrightarrow|Longleftrightarrow|leftrightarrow|longleftrightarrow|iff)"), "等价于"),
    (re.compile(r"\\mapsto"), "映射到"),
    (re.compile(r"\\mid(?![a-zA-Z])"), "满足"),
    (re.compile(r"\\nmid"), "不整除"),
    # ASCII comparisons the model writes directly: \varepsilon>0, x>=0.
    (re.compile(r">="), "大于等于"),
    (re.compile(r"<="), "小于等于"),
    (re.compile(r">"), "大于"),
    (re.compile(r"<"), "小于"),
    # ---- geometry ---------------------------------------------------------
    (re.compile(r"\\(?:angle|measuredangle)"), "角"),
    (re.compile(r"\\(?:triangle|vartriangle)"), "三角形"),
    # ---- operators --------------------------------------------------------
    (re.compile(r"\\(?:pm)"), "正负"),
    (re.compile(r"\\(?:mp)"), "负正"),
    (re.compile(r"\\(?:times|cdot)(?![a-zA-Z])"), "乘"),
    (re.compile(r"\\div(?![a-zA-Z])"), "除以"),
    (re.compile(r"\\(?:ast|star)"), "乘"),
    (re.compile(r"\\(?:cdots|dots|ldots|vdots|ddots|adots)"), "等等"),
    # Factorial: 2! -> 2的阶乘 (the spacing form \! is already gone).
    (re.compile(r"(?<=[0-9A-Za-z\}\)\]])!(?![a-zA-Z])"), "的阶乘"),
    (re.compile(r"\\partial"), "偏"),
    # Mixed partial denominator reads 对x、y, not 对x偏y.
    (re.compile(r"([A-Za-z0-9])偏\s*(?=[A-Za-z0-9])"), r"\1、"),
    (re.compile(r"\\nabla"), "纳布拉"),
    (re.compile(r"\\(?:to(?![a-zA-Z])|rightarrow|longrightarrow)"), "趋于"),
    (re.compile(r"\\infty"), "无穷"),
    (re.compile(r"\\lim(?![a-zA-Z])"), "极限"),
    (re.compile(r"\\int"), "积分"),
    (re.compile(r"\\sum"), "求和"),
    (re.compile(r"\\prod"), "连乘"),
    # ---- named functions --------------------------------------------------
    (re.compile(r"\\(?:arcsin|asin)"), "反正弦"),
    (re.compile(r"\\(?:arccos|acos)"), "反余弦"),
    (re.compile(r"\\(?:arctan|atan)"), "反正切"),
    (re.compile(r"\\sinh(?![a-zA-Z])"), "双曲正弦"),
    (re.compile(r"\\cosh(?![a-zA-Z])"), "双曲余弦"),
    (re.compile(r"\\tanh(?![a-zA-Z])"), "双曲正切"),
    (re.compile(r"\\sin(?![a-zA-Z])"), "正弦"),
    (re.compile(r"\\cos(?![a-zA-Z])"), "余弦"),
    (re.compile(r"\\tan(?![a-zA-Z])"), "正切"),
    (re.compile(r"\\cot(?![a-zA-Z])"), "余切"),
    (re.compile(r"\\sec(?![a-zA-Z])"), "正割"),
    (re.compile(r"\\csc(?![a-zA-Z])"), "余割"),
    (re.compile(r"\\ln(?![a-zA-Z])"), "自然对数"),
    (re.compile(r"\\lg(?![a-zA-Z])"), "常用对数"),
    (re.compile(r"\\log(?![a-zA-Z])"), "对数"),
    (re.compile(r"\\exp(?![a-zA-Z])"), "指数函数"),
    (re.compile(r"\\max(?![a-zA-Z])"), "最大值"),
    (re.compile(r"\\min(?![a-zA-Z])"), "最小值"),
    (re.compile(r"\\sup(?![a-zA-Z])"), "上确界"),
    (re.compile(r"\\inf(?![a-zA-Z])"), "下确界"),
    (re.compile(r"\\gcd(?![a-zA-Z])"), "最大公约数"),
    (re.compile(r"\\lcm(?![a-zA-Z])"), "最小公倍数"),
    (re.compile(r"\\det(?![a-zA-Z])"), "行列式"),
    (re.compile(r"\\dim(?![a-zA-Z])"), "维数"),
    (re.compile(r"\\(?:bmod|mod)(?![a-zA-Z])"), "模"),
    # ---- Greek letters: spoken Chinese names (math-class oral form) -------
    (re.compile(r"\\(?:alpha|upalpha)"), "阿尔法"),
    (re.compile(r"\\(?:beta|upbeta)"), "贝塔"),
    (re.compile(r"\\(?:gamma|Gamma|upgamma|upGamma)"), "伽马"),
    (re.compile(r"\\(?:delta|Delta|updelta|upDelta)"), "德尔塔"),
    (re.compile(r"\\(?:varepsilon|epsilon|upepsilon)"), "艾普西隆"),
    (re.compile(r"\\(?:zeta|upzeta)"), "泽塔"),
    (re.compile(r"\\(?:eta|upeta)"), "伊塔"),
    (re.compile(r"\\(?:theta|Theta|uptheta|upTheta)"), "西塔"),
    (re.compile(r"\\(?:iota|upiota)"), "伊奥塔"),
    (re.compile(r"\\(?:kappa|upkappa)"), "卡帕"),
    (re.compile(r"\\(?:lambda|Lambda|uplambda|upLambda)"), "拉姆达"),
    (re.compile(r"\\(?:mu|upmu)(?![a-zA-Z])"), "缪"),
    (re.compile(r"\\(?:nu|upnu)"), "纽"),
    (re.compile(r"\\(?:xi|Xi|upxi|upXi)"), "克西"),
    (re.compile(r"\\pi(?![a-zA-Z])|\\uppi(?![a-zA-Z])"), "圆周率"),
    (re.compile(r"\\(?:rho|uprho)"), "柔"),
    (re.compile(r"\\(?:sigma|Sigma|upsigma|upSigma)"), "西格玛"),
    (re.compile(r"\\(?:tau|uptau)"), "陶"),
    (re.compile(r"\\(?:phi|varphi|Phi|upphi|upvarphi|upPhi)"), "斐"),
    (re.compile(r"\\(?:chi|upchi)"), "凯"),
    (re.compile(r"\\(?:psi|Psi|uppsi|upPsi)"), "普西"),
    (re.compile(r"\\(?:omega|Omega|upomega|upOmega)"), "欧米伽"),
    (re.compile(r"\\hbar"), "h拔"),
    # ---- matrix / delimiter scaffolding ----------------------------------
    (re.compile(r"\\(?:begin|end)\s*\{[a-zA-Z*]+\}"), ""),
    (re.compile(r"\\\{"), "集合"),
    (re.compile(r"\\\}"), ""),
    (re.compile(r"&"), "，"),
    (re.compile(r"\\\\(?:\s*\[\w+])?"), "，"),
    (re.compile(r"\\(?:bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|left|right)(?![a-zA-Z])"), ""),
    (re.compile(r"\\(?:quad|qquad|[,!;:>|<~]|\\|;|\s)"), " "),
    # ---- intervals, absolute values, brackets ------------------------------
    # After \left/\right stripping: [-1,1] reads as a closed range (square
    # brackets are interval-only notation). Paren pairs stay 括号 — in this
    # domain they are more often coordinates or function calls.
    (re.compile(r"(?<![A-Za-z0-9_}\\])\[\s*([^,\[\]\\+*=]+?)\s*,\s*([^,\[\]\\+*=]+?)\s*\]"),
     _interval_sub("闭区间")),
    (re.compile(r"\\[lr]?vert(?![a-zA-Z])"), "|"),
    (re.compile(r"\\\|"), "|"),
    (re.compile(r"\|([^|]+)\|"), r"\1的绝对值"),
    # Classroom oral form: (a+b)c reads 括号a加b括号乘c.
    (re.compile(r"[()]"), "括号"),
    (re.compile(r"[\[\]]"), "中括号"),
    # ---- generic superscript / subscript ----------------------------------
    # Signed exponents/subscripts first: 0^+ / 0^- (side of a limit) and
    # f'_+ (one-sided derivative) read 正/负, not "上标加号".
    (re.compile(r"\^\s*\+"), "正"),
    (re.compile(r"\^\s*-"), "负"),
    (re.compile(r"_\s*\+"), "正"),
    (re.compile(r"_\s*-"), "负"),
    # Minus inside math: binary between operands reads 减 (b-a, x^2-9),
    # unary (=-1, 之-x after a \frac rewrite, leading -x) reads 负.
    (re.compile(r"(?<=[A-Za-z0-9\}\)\]])-(?=[A-Za-z0-9(])"), "减"),
    (re.compile(r"(?<![A-Za-z0-9\}\)\]])-(?=[A-Za-z0-9(\\])"), "负"),
    (re.compile(r"\^\s*\{([^{}]+)\}"), _power_sub),
    (re.compile(r"\^\s*(\w)"), r"的\1次方"),
    # Subscripts concatenate with their base instead of reading the word
    # 下标: W_0 / W_{max} read as W0 / Wmax (2026-08-31 feedback —
    # "W下标0" sounded robotic).
    (re.compile(r"_\s*\{([^{}]+)\}"), r"\1"),
    (re.compile(r"_\s*(\w)"), r"\1"),
    # Simple inline ratio after units are gone: 3/2 -> 2分之3, 1/n -> n分之1.
    (re.compile(r"([0-9A-Za-z]+)\s*/\s*([0-9A-Za-z]+)"), r"\2分之\1"),
    # Structured commands whose arguments never arrived (cut messages):
    # the bare name would read as an English word — drop it.
    (_MATH_NAME_ONLY, ""),
    # Unknown commands keep their letters (English words beat deletion).
    (re.compile(r"\\([a-zA-Z]+)\s*"), r"\1 "),
    # Anything still holding a brace/caret/underscore/backslash was an
    # unmatched construct; dropping the scaffolding beats reading it aloud.
    (re.compile(r"[{}^_\\]"), ""),
)

_SYMBOL_READINGS: tuple[tuple[re.Pattern, str], ...] = (
    # Glue spaced "+" to its operands FIRST so the alnum rule below then
    # turns "a + b" into "a加b" (math fragments keep operator spacing).
    (re.compile(r"\s*\+\s*"), "+"),
    # Bare subscripts in prose (W_{0} / W_0 -> W0), same reading as the
    # math segment; __ emphasis pairs were already stripped by _EMPH.
    (re.compile(r"_\s*\{([^{}]+)\}"), r"\1"),
    (re.compile(r"_\s*(\w)"), r"\1"),
    # A hyphen reads as 减 only when a digit/CJK is on either side, so
    # "x-1" -> "x减1" while the compound-word hyphen in "well-known"
    # survives; "+" between alnum/CJK always reads 加.
    (re.compile(r"(?<=[0-9\u4e00-\u9fff])-(?=[0-9A-Za-z\u4e00-\u9fff])"), "减"),
    (re.compile(r"(?<=[0-9A-Za-z\u4e00-\u9fff])-(?=[0-9\u4e00-\u9fff])"), "减"),
    (re.compile(r"(?<=[0-9A-Za-z\u4e00-\u9fff])\+(?=[0-9A-Za-z\u4e00-\u9fff])"), "加"),
    (re.compile(r"="), "等于"),
    (re.compile(r"×|·"), "乘"),
    (re.compile(r"÷"), "除以"),
    (re.compile(r"≤|≦"), "小于等于"),
    (re.compile(r"≥|≧"), "大于等于"),
    (re.compile(r"≠"), "不等于"),
    (re.compile(r"≈|≒"), "约等于"),
    (re.compile(r"≡"), "恒等于"),
    (re.compile(r"→|->|⟶"), "趋于"),
    (re.compile(r"±"), "正负"),
    (re.compile(r"∂"), "偏"),
    (re.compile(r"π"), "圆周率"),
    (re.compile(r"∑"), "求和"),
    (re.compile(r"∫"), "积分"),
    (re.compile(r"√"), "根号"),
    (re.compile(r"∞"), "无穷"),
    (re.compile(r"∈"), "属于"),
    (re.compile(r"∪"), "并"),
    (re.compile(r"∩"), "交"),
    (re.compile(r"⊂|⊆"), "包含于"),
    (re.compile(r"⊥"), "垂直于"),
    (re.compile(r"∥"), "平行于"),
    (re.compile(r"∠"), "角"),
    (re.compile(r"△"), "三角形"),
    (re.compile(r"∵"), "因为"),
    (re.compile(r"∴"), "所以"),
    (re.compile(r"°"), "度"),
    (re.compile(r"′"), "撇"),
    # Greek letters pasted as Unicode (answers sometimes skip LaTeX).
    (re.compile(r"α"), "阿尔法"), (re.compile(r"β"), "贝塔"),
    (re.compile(r"γ"), "伽马"), (re.compile(r"Δ|δ"), "德尔塔"),
    (re.compile(r"ε"), "艾普西隆"), (re.compile(r"θ"), "西塔"),
    (re.compile(r"λ"), "拉姆达"), (re.compile(r"μ"), "缪"),
    (re.compile(r"ρ"), "柔"), (re.compile(r"σ"), "西格玛"),
    (re.compile(r"τ"), "陶"), (re.compile(r"φ"), "斐"),
    (re.compile(r"ω"), "欧米伽"), (re.compile(r"Ω"), "欧米伽"),
)

def to_speakable(text: str) -> str:
    s = _TOOL_BLOCK.sub("", text)
    s = _TOOL_SHELL.sub("", s)
    s = normalize_math_delimiters(s)
    s = _CODE_FENCE.sub("（这段是代码，具体请看屏幕）", s)
    s = _CODE_INLINE.sub(r"\1", s)
    s = _IMAGE.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = _MATH_DISPLAY.sub(lambda m: _read_math(m.group(1)), s)
    s = _MATH_INLINE.sub(lambda m: _read_math(m.group(1)), s)
    # Trailing unclosed math (streaming cuts, oversized formulas forced
    # apart, unpaired $ from the model) still gets math readings; then any
    # survivor $/delimiter residue is dropped — reading "美元" or a stray
    # backslash-paren aloud would be worse than silence.
    s = _MATH_TAIL.sub(lambda m: _read_math(m.group(1)), s)
    s = s.replace("$", "")
    # A lone backslash can survive a delta boundary cut (\ arrived, [ did
    # not); reading "反斜杠" or letting the TTS guess is worse than silence.
    s = s.replace("\\", "")
    s = _HEADING.sub("", s)
    s = _LIST_MARKER.sub("", s)
    s = _QUOTE.sub("", s)
    s = _HR.sub("", s)
    s = _EMPH.sub("", s)
    s = _TABLE_SEP.sub("", s)
    s = _TABLE_PIPE.sub("，", s)
    for pattern, reading in _SYMBOL_READINGS:
        s = pattern.sub(reading, s)
    # A leftover underscore is unmatched scaffolding (emphasis tail cut
    # mid-stream, brace pair that never closed); reading "下划线" aloud is
    # worse than dropping it — same philosophy as the math-side cleanup.
    s = s.replace("_", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "，", s)
    s = re.sub(r"，{2,}", "，", s)
    return s.strip("， ")


def _read_math(expr: str) -> str:
    # Number sets before the generic \text keeper flattens them; units
    # before the keeper flattens \text{m/s} into bare letters.
    out = _MATHBB.sub(lambda m: _MATHBB_SETS.get(m.group(1).upper(), m.group(0)), expr)
    out = _UNIT_TEXT.sub(_unit_text_sub, out)
    out = _TEXT_KEEPER.sub(r"\1", out)
    out = _TEXT_FAMILY_BARE.sub(r"\1", out)
    out = _FONT_SWITCH.sub(" ", out)
    # Argument groups collapse inside-out: \frac{\frac{1}{2}}{3} needs a
    # second pass over the substituted text. Bounded so a pathological
    # input can never loop forever.
    for _ in range(8):
        changed = False
        for pattern, reading in _MATH_READINGS_PASS:
            replaced = pattern.sub(reading, out)
            if replaced != out:
                changed = True
                out = replaced
        if not changed:
            break
    for pattern, reading in _MATH_READINGS:
        out = pattern.sub(reading, out)
    return out
