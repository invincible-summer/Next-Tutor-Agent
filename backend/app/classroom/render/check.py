"""受控 Node/Chromium 排版检查进程封装（plan.md §9.6）。

全局最多 1 个 headless 校验进程；超时 kill；报告只含
block_id/尺寸/错误码。禁外网由脚本内部 route abort 保证。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ...core.config import settings

_render_lock: asyncio.Lock | None = None


def _global_lock() -> asyncio.Lock:
    global _render_lock
    if _render_lock is None:
        _render_lock = asyncio.Lock()
    return _render_lock


class LayoutCheckError(RuntimeError):
    code = "layout_overflow"


@dataclass
class LayoutReport:
    ok: bool
    issues: list[dict] = field(default_factory=list)
    viewports: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "LayoutReport":
        return cls(ok=bool(data.get("ok")),
                   issues=list(data.get("issues") or []),
                   viewports=list(data.get("viewports") or []))

    def overflow_issue_summary(self, limit: int = 8) -> str:
        parts = []
        for issue in self.issues[:limit]:
            parts.append(
                f"{issue.get('viewport')}/slide{issue.get('slide_order')}"
                f"/{issue.get('block_id')}:{issue.get('code')}")
        return "; ".join(parts)


async def run_layout_check(html_text: str,
                           *, timeout: float | None = None) -> LayoutReport:
    """编译产物 → 临时文件 → node checker → 结构化报告。"""
    timeout = timeout or settings.classroom_render_timeout_seconds
    loop = asyncio.get_running_loop()
    async with _global_lock():
        with tempfile.TemporaryDirectory(prefix="classroom_render_") as tmp:
            html_path = Path(tmp) / "frame.html"
            report_path = Path(tmp) / "report.json"
            await loop.run_in_executor(None, _write_sync, html_path, html_text)
            cmd = [
                settings.classroom_node_bin,
                settings.classroom_render_script,
                "--html", str(html_path),
                "--json-out", str(report_path),
                "--timeout-ms", str(int(timeout * 1000)),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(Path(settings.classroom_render_script).parent),
                )
            except (OSError, FileNotFoundError) as exc:
                raise LayoutCheckError(f"排版检查器不可用: {exc}") from exc
            try:
                _stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout + 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise LayoutCheckError("排版检查超时") from None
            if not report_path.is_file():
                detail = (stderr or b"").decode("utf-8", "replace")[-300:]
                raise LayoutCheckError(f"排版检查失败: {detail}")
            try:
                data = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LayoutCheckError("排版报告损坏") from exc
            return LayoutReport.from_dict(data)


def _write_sync(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
