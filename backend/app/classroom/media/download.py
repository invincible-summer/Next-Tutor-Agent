"""图片 CDN 下载与清洗（plan.md §8.3.4–§8.3.7 + §7.5，C04）。

安全模型（§7.5"图片下载每跳重验"）：
  1. URL 层：只接受批准 CDN 白名单内的完整 hostname（§8.3 初始名单
     images.pexels.com / pixabay.com / cdn.pixabay.com），公网 https 边界
     全套校验；重定向手动逐跳处理（≤3 跳），每跳重新过同样的校验。
  2. 连接层：``PinnedNetworkBackend`` 在每次 TCP 建连时重新解析域名并
     校验全部解析地址为公网，随后用**经核验的 IP** 建连；TLS SNI 与证书
     校验保持原域名（``_PinnedStream.start_tls`` 强制 server_hostname），
     HTTP Host 头来自原始 URL，不被 IP 替换——"先 DNS 检查、默认客户端
     重新解析"的 TOCTOU/DNS-rebinding 缺口由此封死。

清洗（§8.3.5）：魔数识别（不以扩展名定内容）→ Pillow 解码 → ≤20MP →
长边 ≤1600px → EXIF 随重编码丢弃 → 透明保留（PNG）否则 WebP →
目标 ≤700KB、硬上限 1.5MB。外部 SVG 一律拒绝（图库不经处理的矢量不下载）。
"""
from __future__ import annotations

import asyncio
import hashlib
import ssl
from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Iterable
from urllib.parse import urlsplit

import httpcore
import httpx

from .. import limits
from ..errors import ClassroomError
from ..netcheck import NetcheckError, host_allowed, parse_public_https_url

MAX_REDIRECT_HOPS = 3


# ---------------------------------------------------------------------------
# IP 固定传输层
# ---------------------------------------------------------------------------

class _PinnedStream(httpcore.AsyncNetworkStream):
    """强制 TLS server_hostname 为原始域名的流包装。

    TCP 已连到核验过的 IP；SNI/证书校验/Host 语义都保持原域名。"""

    def __init__(self, inner: httpcore.AsyncNetworkStream,
                 hostname: str) -> None:
        self._inner = inner
        self._hostname = hostname

    async def read(self, max_bytes: int, timeout=None):
        return await self._inner.read(max_bytes, timeout=timeout)

    async def write(self, buffer, timeout=None):
        await self._inner.write(buffer, timeout=timeout)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        stream = await self._inner.start_tls(
            ssl_context, server_hostname=self._hostname, timeout=timeout)
        return _PinnedStream(stream, self._hostname)

    async def aclose(self) -> None:
        await self._inner.aclose()

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """每跳重验的连接层：白名单 → DNS 全量公网校验 → 固定 IP 建连。

    ``resolver``/``inner`` 仅供测试注入；生产路径用真实解析与 anyio 后端。"""

    def __init__(self, allow_hosts: Iterable[str], *,
                 resolver: Callable[[str, int], list[str]] | None = None,
                 inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self.allow_hosts = tuple(allow_hosts)
        self._resolver = resolver
        self._inner = inner

    async def connect_tcp(self, host: str, port: int, timeout=None,
                          local_address=None, socket_options=None,
                          ) -> httpcore.AsyncNetworkStream:
        # 白名单按完整 hostname 比较（无后缀漏洞，§7.5）
        if not host_allowed(host, self.allow_hosts):
            raise NetcheckError(f"连接目标主机不在下载白名单：{host}")
        if self._resolver is not None:
            ips = self._resolver(host, port)
        else:
            loop = asyncio.get_running_loop()
            from ..netcheck import resolve_public_ips
            ips = await loop.run_in_executor(
                None, resolve_public_ips, host, port)
        if not ips or not ips[0]:
            raise NetcheckError(f"主机 {host} 无可用公网解析")
        backend = self._inner
        if backend is None:
            backend = httpcore.AnyIOBackend()
        stream = await backend.connect_tcp(
            ips[0], port, timeout=timeout,
            local_address=local_address, socket_options=socket_options)
        return _PinnedStream(stream, host)

    async def connect_unix_socket(self, path, timeout=None,
                                  socket_options=None):
        raise NetcheckError("课堂图片下载不允许 unix socket")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def build_pinned_transport(allow_hosts: Iterable[str], *,
                           verify: bool = True) -> httpx.AsyncHTTPTransport:
    """构造下载专用 transport：底层连接池的 DNS/建连全部走 PinnedBackend。"""
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - certifi 是 httpx 依赖，仅在异常环境缺席
        ctx = ssl.create_default_context()
    pool = httpcore.AsyncConnectionPool(
        ssl_context=ctx,
        max_connections=limits.IMAGE_DOWNLOAD_CONCURRENCY,
        network_backend=PinnedNetworkBackend(tuple(allow_hosts)))
    transport = httpx.AsyncHTTPTransport(verify=verify)
    transport._pool = pool  # httpx 0.28/httpcore 1.0 固定组合（lockfile 锁定）
    return transport


# ---------------------------------------------------------------------------
# 下载（URL 白名单 + 每跳重验 + 魔数嗅探）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawImage:
    url: str
    data: bytes
    sniffed: str          # image/jpeg | image/png | image/webp


def sniff_image(data: bytes) -> str | None:
    """魔数识别；SVG/GIF 等不在接受范围（§8.3.5 只收 JPEG/PNG/WebP）。"""
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if (len(data) >= 12 and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"):
        return "image/webp"
    return None


async def download_candidate_image(
    url: str,
    *,
    allow_hosts: Iterable[str] = limits.IMAGE_DOWNLOAD_HOSTS,
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int = limits.IMAGE_BYTES_MAX_DOWNLOAD,
) -> RawImage:
    """下载单张：白名单 + 手动重定向 ≤3 跳 + 每跳 URL 校验 + 8MB 上限。

    ``transport`` 注入仅用于测试；生产默认 IP 固定传输层。"""
    allow = tuple(allow_hosts)
    current = (url or "").strip()
    client = httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=30.0,
        transport=transport if transport is not None
        else build_pinned_transport(allow))
    try:
        for _hop in range(MAX_REDIRECT_HOPS + 1):
            try:
                host, target, port = parse_public_https_url(current)
            except NetcheckError as exc:
                raise ClassroomError("image_unavailable",
                                     f"下载 URL 不合规：{exc}") from exc
            if not host_allowed(host, allow):
                raise ClassroomError(
                    "image_unavailable",
                    f"图片主机不在批准 CDN 白名单：{host}")
            try:
                resp = await client.get(current)
            except httpx.HTTPError as exc:
                raise ClassroomError(
                    "image_unavailable",
                    f"图片下载失败：{type(exc).__name__}",
                    retryable=True) from exc
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "")
                if not location:
                    raise ClassroomError("image_unavailable",
                                         "重定向缺少 Location")
                current = str(httpx.URL(current).join(location))
                continue
            if resp.status_code != 200:
                raise ClassroomError(
                    "image_unavailable",
                    f"图片下载返回 HTTP {resp.status_code}",
                    retryable=True)
            data = resp.content
            if len(data) > max_bytes:
                raise ClassroomError("image_unavailable",
                                     f"图片超过 {max_bytes // (1024 * 1024)}MB 上限")
            sniffed = sniff_image(data)
            if sniffed is None:
                raise ClassroomError(
                    "image_unavailable",
                    "图片内容不是受支持的 JPEG/PNG/WebP")
            return RawImage(url=current, data=data, sniffed=sniffed)
        raise ClassroomError("image_unavailable",
                             "图片下载重定向超过 3 跳")
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# 清洗（Pillow）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str


def sanitize_image(raw: bytes, *, sniffed: str | None = None) -> ProcessedImage:
    """解码 → 尺寸/像素上限 → 缩边 → EXIF 丢弃 → WebP/PNG 重编码。

    输出必定满足：长边 ≤1600、无元数据、≤1.5MB 硬上限；体积目标
    ≤700KB 通过质量阶梯+缩边协商达成。"""
    from PIL import Image

    kind = sniffed or sniff_image(raw)
    if kind is None:
        raise ClassroomError("image_unavailable",
                             "图片内容不是受支持的 JPEG/PNG/WebP")
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ClassroomError("image_unavailable",
                             "图片解码失败") from exc

    width, height = img.size
    if width < 1 or height < 1:
        raise ClassroomError("image_unavailable", "图片尺寸无效")
    if width * height > limits.PIXEL_MAX:
        raise ClassroomError("image_unavailable",
                             f"图片超过 {limits.PIXEL_MAX // 1_000_000}MP 上限")

    # 透明保留：带 alpha 通道（或 P 模式透明索引）的图保持 PNG，其余转 WebP
    has_alpha = bool(img.mode in ("RGBA", "LA")
                     or (img.mode == "P"
                         and "transparency" in (img.info or {})))
    if has_alpha:
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    def _scale_to_long_edge(image: Image.Image, edge: int) -> Image.Image:
        w, h = image.size
        if max(w, h) <= edge:
            return image
        ratio = edge / max(w, h)
        return image.resize((max(1, round(w * ratio)),
                             max(1, round(h * ratio))),
                            Image.Resampling.LANCZOS)

    img = _scale_to_long_edge(img, limits.IMAGE_LONG_EDGE_MAX)

    def _encode(image: Image.Image, fmt: str, quality: int) -> bytes:
        out = BytesIO()
        params: dict = {"format": fmt}
        if fmt == "WEBP":
            params.update(quality=quality, method=4)
        image.save(out, **params)
        return out.getvalue()

    # 体积协商：目标 ≤700KB。透明图 PNG 无损，只缩边；其余 WebP 先走
    # 质量阶梯（85→70→55），仍超目标再逐级缩边。两档都到不了目标时，
    # 只要 ≤1.5MB 硬上限仍可接受（发布层按 warning 记录）。
    attempt = img
    if has_alpha:
        mime = "image/png"
        for _step in range(5):
            data = _encode(attempt, "PNG", 0)
            if len(data) <= limits.IMAGE_BYTES_TARGET:
                break
            attempt = _scale_to_long_edge(
                attempt, int(max(attempt.size) * 0.8))
    else:
        mime = "image/webp"
        data = _encode(attempt, "WEBP", 85)
        for quality in (70, 55):
            if len(data) <= limits.IMAGE_BYTES_TARGET:
                break
            data = _encode(attempt, "WEBP", quality)
        for _step in range(4):
            if len(data) <= limits.IMAGE_BYTES_TARGET:
                break
            attempt = _scale_to_long_edge(
                attempt, int(max(attempt.size) * 0.85))
            data = _encode(attempt, "WEBP", 70)
    if len(data) > limits.IMAGE_BYTES_HARD_MAX:
        raise ClassroomError("image_unavailable",
                             "清洗后图片仍超过 1.5MB 硬上限")

    return ProcessedImage(
        data=data,
        mime=mime,
        width=attempt.size[0],
        height=attempt.size[1],
        sha256=hashlib.sha256(data).hexdigest(),
    )


async def download_and_sanitize(
    url: str,
    *,
    allow_hosts: Iterable[str] = limits.IMAGE_DOWNLOAD_HOSTS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProcessedImage:
    raw = await download_candidate_image(url, allow_hosts=allow_hosts,
                                         transport=transport)
    return sanitize_image(raw.data, sniffed=raw.sniffed)


def url_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
