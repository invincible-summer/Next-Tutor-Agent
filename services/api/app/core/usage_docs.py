"""Usage docs: the admin-editable, all-user-readable /docs page content.

Global markdown documents, stored at chat_history/settings/usage_docs.json
(Chinese, including legacy edits) and usage_docs.en.json (English)
(alongside the OCR runtime policy — the established admin-settings root, no
per-owner attribution so the orphan scanner never touches it). Storage
contract mirrors ocr_policy: defensive read (missing/corrupt -> bootstrap
default), file_lock + atomic write. The document is version content, not user
runtime data, so it is safe to rewrite wholesale on save.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from typing import Literal

from .atomic import atomic_write_text, file_lock

from app.core import paths

_DOCS_FILE = paths.bind_storage_path(__name__, "_DOCS_FILE", "policies", "usage_docs.json")
_DEFAULT_MARKDOWN_EN = Path(__file__).with_name("usage_docs_en.md").read_text(encoding="utf-8")

DocsLang = Literal["zh", "en"]


def _docs_file(lang: DocsLang) -> Path:
    return _DOCS_FILE.with_name("usage_docs.en.json") if lang == "en" else _DOCS_FILE

# The rendered PDF manual (docs/show/html, produced by
# scripts/build_show_html.py) rides along with the markdown doc on /docs.
# Absent build output simply hides the manual tab; nothing else depends on it.
_SHOW_MANUAL_DIR = paths.repo_root() / "docs" / "show" / "html"


def show_manual_available() -> bool:
    return (_SHOW_MANUAL_DIR / "index.html").is_file()


def show_manual_dir() -> Path:
    return _SHOW_MANUAL_DIR

# size cap: a usage doc well beyond this is almost certainly a paste error
_MAX_MARKDOWN_CHARS = 200_000

_DEFAULT_MARKDOWN = """# 让每一次学习，都有下一步

从一本教材、一个问题，或一堂想学的课开始。这里介绍你在 Next Tutor Agent 中可以做什么，以及怎样把一次学习顺畅地进行下去。

> 初次使用，先读「快速上手」。已有明确目标，可以从右侧目录直接找到对应场景。部分功能需要管理员开启，请以页面提示为准。

## 快速上手

1. **登录自己的账号。** 保存会话、课程和笔记，方便下次继续。
2. **准备学习材料。** 打开「资料中心」，选择公共教材，或上传自己的教材。在工作学习区中关联本次要用的教材。
3. **开始一次学习。** 在「聊天辅导」提出具体问题；想系统地学一节内容，可以去「备课上课」准备课程。
4. **留下一点积累。** 做一道练习，把关键解释整理成笔记，再到「学习总览」查看记录。

不必一次用完所有功能。先选一本教材、问一个问题，就能开始。

## 聊天辅导：把问题问明白

打开「聊天辅导」，选择相应的工作学习区与会话，输入问题。说明你正在学什么、哪里卡住，以及希望怎样讲解，通常比只输入一个知识点更有帮助。

可以直接试试这些问法：

- **理解概念：**「根据这本教材，用一个生活中的例子解释导数。」
- **看懂过程：**「这一步为什么可以这样变形？请补上中间步骤。」
- **得到提示：**「先不要给答案，只提示我第一步应该考虑什么。」
- **检查思路：**「这是我的解法，请指出哪一步有问题，并让我自己改正。」
- **边学边练：**「围绕刚才的内容出一道练习，等我回答后再讲解。」

也可以上传题目图片提问。图片是否能够识别取决于实例配置；图片尽量清晰、完整，保留题干和图中的标注。

需要按教材回答时，请先关联教材，并在问题中说明。看到教材来源时可回看原文；没有找到足够依据时，可以补充章节、页码或题目内容。

### 用语音交流

如果会话显示语音入口，可以进入语音模式，允许浏览器使用麦克风，按住说话、松开发送。听讲时可结合页面上的文字和公式阅读，也可以停止播报，回到文字交流。

语音需要浏览器与实例支持。如果页面提示不可用，继续输入文字即可。

## 备课上课：把章节变成一堂课

适合想连续学习一个主题，或先整理好内容再开始讲授的场景。

1. 打开「备课上课」，选择或新建工作学习区。
2. 从关联教材中选择章节，或输入想学习的主题；没有教材时，可按页面选项使用通识资料。
3. 按表单补充课程要求并开始准备，等待课件与讲稿生成。
4. 进入课程检查内容，预览课件、调整讲稿，确认适合本次学习后开始上课。
5. 在学习页面跟随课件与讲稿推进；未完成的课程可从课程列表继续。

课件和讲稿可以通过课程中的导出入口下载。语音讲授取决于可用的语音服务；无法播放语音时，仍可阅读课件与讲稿。

> 准备课程时，具体的目标更好用。例如：「用一堂入门课讲清牛顿第二定律，先解释概念，再安排一道受力分析练习。」

## 资料中心：让学习围绕你的教材

「资料中心」用于管理教材与学习文件。公共教材供大家阅读，个人上传的材料属于自己的账号。

- **使用已有教材：** 浏览公共教材，找到适合的学科与版本，再关联到工作学习区。
- **上传自己的教材：** 使用教材上传入口，按提示填写信息、选择文件，并等待解析状态更新。
- **区分学习主题：** 为不同学科或目标建立工作学习区，分别关联材料，减少不同课程之间的混淆。

教材处理需要时间。教材可以检索后即可开始提问，知识图谱可能仍在后台准备。扫描文件的清晰度、页数和模型服务都会影响处理速度。

## 练习与测评：用自己的答案验证理解

想练习刚学的内容，可以直接在聊天中请求出题。手上有一道参考题时，可以说「请围绕这道题的考点出一道变式」，再尝试独立作答。

想集中检查某个概念，打开「测评中心」：

1. 选择工作区与要检查的概念，按需要补充学科、学段等选项。
2. 开始测评，阅读题目并提交答案；简答题尽量写出自己的思路。
3. 查看反馈与解析，再继续下一题。题目难度会结合本轮作答调整。
4. 结束后阅读总结，找到仍需澄清的内容，回到聊天中追问或继续练习。

近期习题和错题本可帮助你回看、重练。需要图形辅助时，可检查题目插图选项；并非每道题都需要配图。

测评总结反映本轮任务中的表现，不等于长期掌握程度。看过提示、答案后完成的题，也应与独立完成的题分开理解。

## 笔记仓库：留下自己的理解

打开「笔记仓库」新建笔记，记录关键概念、解题过程与自己的疑问。也可以从对话、教材或错题整理笔记，生成后再用自己的话补充、修正。

- **按主题整理：** 用文件夹归类，用标签标记课程、章节或待复习内容。
- **连接相关概念：** 在笔记中用 `[[笔记标题]]` 建立双向链接，方便来回查阅。
- **安排复习：** 将需要反复回顾的内容加入复习，按照到期安排重温。
- **找回误删笔记：** 到「归档中心」检查可恢复的内容。

一篇好用的学习笔记，可以只写三件事：我理解了什么、容易错在哪里、下次怎样检查自己。

## 学习总览与知识图谱：找到下一处需要学习的地方

「学习总览」汇集学习记录、当前评价与任务；「知识图谱」帮助你浏览教材中的概念及其关系。可以从关心的概念出发，查看相关信息，再进行针对性提问。

阅读学习评价时，留意它依据的表现与条件：

| 看到的状态 | 可以怎样理解 |
| --- | --- |
| 尚无学习证据 | 还没有足够记录，可以先做一道题或尝试解释概念。 |
| 已有局部证据 | 已表现出部分理解，适合继续补充练习。 |
| 当前范围内有支持 | 在已有任务与帮助条件下表现良好，可以换一种情境再试。 |
| 存在待解决点或证据冲突 | 回看相关回答，针对卡点再讲解、练习。 |

从右上角「我的账户」可以进入「账户资料」「学习画像」「系统洞察」，也可以登录或退出登录。「账户资料」显示个人资料、注册时间与最近登录等账户信息；「学习画像」和「记忆」帮助你查看系统记录的学习信息。学习记录需要逐渐积累，新账号暂时没有评价是正常的。

偏好集中在右上角头像左侧的「设置」，可分别调整通用、学习与回答、语音与课堂、助手和资料处理选项。账户注销在「账户与数据」分类；学习总览继续从左侧导航进入。主题支持跟随系统；默认学段在「学习与回答」中调整并保存到账户，新对话会使用该学段。语音自动模式优先本地，模型信息在「关于」中查看。头像可在账户资料页上传并拖动、缩放裁剪；头像随账户删除一并清除。

## 学习编排：把目标放进日常

打开「学习编排」，添加一个清晰的目标，例如「两周内复习完导数基础，并能独立完成典型题」。结合页面安排查看周计划和今日任务，逐项开始学习，并按实际情况更新进度。

到期复习也可以进入日常安排。完成任务后，再结合练习反馈决定是否继续巩固；勾选完成本身不代表已经学会。

## 站内学习助手：不知道从哪里开始时

如果右下角显示「学习助手」，可以随时打开它：

- 「这个网站能帮我做什么？」——了解入口与使用方式。
- 「带我去备课上课。」——根据提示确认后打开相应页面。
- 「最近一周我学得怎么样？」——查看已有记录支持的学习近况。
- 「继续上次课程。」——确认目标课程后继续学习。

助手适合导览和查看近况；需要深入讲解、做题时，进入「聊天辅导」。报告提供依据入口时，可以点开查看相关记录。

## 常见问题

### 教材上传后，为什么暂时查不到？

先在资料中心查看处理状态。正在解析时请稍等；已经可用时，检查当前工作学习区是否关联了这本教材。问题中补充章节或原文也有助于定位内容。

### 为什么没有语音、课堂或助手入口？

这些能力取决于实例配置、服务是否就绪，以及浏览器支持情况。按页面提示检查，或联系管理员。

### 怎样获得更贴合自己的讲解？

告诉 AI 你的基础、具体卡点和期望方式，例如「我刚接触这个概念，请先用图景解释，暂时不要引入复杂公式」。遇到不懂的步骤及时追问。

### 删除的内容都能恢复吗？

请以删除前的提示为准。进入归档中心的内容可在其中恢复；永久删除和账号删除不能依靠归档恢复。

### 怎样切换语言与主题？

通过界面中的语言和主题控件切换中英文、浅色或深色外观。模型回复的语言也可以在提问时明确说明。

---

建议从一个小目标开始：问清一个概念，独立做一道题，留下一篇笔记。下一次学习，就从这里继续。
"""


def _bootstrap(lang: DocsLang = "zh") -> dict[str, Any]:
    markdown = _DEFAULT_MARKDOWN_EN if lang == "en" else _DEFAULT_MARKDOWN
    return {"markdown": markdown, "updated_at": 0.0, "updated_by": ""}


def read_docs(lang: DocsLang = "zh") -> dict[str, Any]:
    """Read the usage doc; missing/corrupt file -> bootstrap default.

    Never raises (the page must render for everyone even with a bad file).
    """
    try:
        data = json.loads(_docs_file(lang).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _bootstrap(lang)
        md = str(data.get("markdown") or "")
        return {
            "markdown": md if md else _bootstrap(lang)["markdown"],
            "updated_at": float(data.get("updated_at") or 0.0),
            "updated_by": str(data.get("updated_by") or ""),
        }
    except Exception:
        return _bootstrap(lang)


def write_docs(markdown: str, *, updated_by: str = "", lang: DocsLang = "zh") -> dict[str, Any]:
    """Persist a new doc version (atomic + lock). Returns the stored payload.

    Raises ValueError when the markdown exceeds the size cap; other failures
    raise OSError so the API can surface a 500 rather than silently dropping
    an admin edit.
    """
    md = str(markdown or "")
    if len(md) > _MAX_MARKDOWN_CHARS:
        raise ValueError("document too large")
    payload = {"markdown": md, "updated_at": time.time(),
               "updated_by": str(updated_by or "")}
    docs_file = _docs_file(lang)
    docs_file.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(docs_file):
        atomic_write_text(docs_file, json.dumps(payload, ensure_ascii=False))
    return payload
