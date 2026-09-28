"""公网 URL 校验与解析地址检查（plan.md §7.5）。

课堂所有出站 URL（研究提取、用户手输 URL、图片 CDN 下载）共用这一套
边界：scheme/host/端口/长度/userinfo 检查、IP literal 检查、DNS 解析后
逐地址复核。域名白名单比较完整 hostname，绝不用后缀匹配。

封禁范围（显式列出，不依赖标准库版本差异）：
  - 回环 / 私网 / 链路本地（含云 metadata 169.254.169.254）
  - CGNAT 100.64.0.0/10（部分 Python 版本 is_private 不覆盖）
  - 组播 / 保留 / 未指定 / 0.0.0.0/8 / 198.18.0.0/15 基准网段
  - IPv4-mapped IPv6（按内嵌 v4 地址判定）
  - localhost 及常见 metadata 主机名
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_ZERO = ipaddress.ip_network("0.0.0.0/8")
_BENCH = ipaddress.ip_network("198.18.0.0/15")

_BLOCKED_HOSTNAMES = {
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal",
}


class NetcheckError(ValueError):
    """URL/地址不满足公网边界。消息不含任何凭证。"""


def _ip_is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            addr = mapped
    if (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified):
        return False
    if addr.version == 4 and (addr in _CGNAT or addr in _ZERO or addr in _BENCH):
        return False
    return True


def parse_public_https_url(url: str, *, max_length: int = MAX_URL_LENGTH
                           ) -> tuple[str, str, int]:
    """校验并拆解公开 HTTPS URL → (host, path+query, port)。

    http/file/data/javascript/javascript 等非 https scheme、userinfo、
    超长、空 host、非法端口、IP literal 私网地址全部拒绝。"""
    text = (url or "").strip()
    if not text or len(text) > max_length:
        raise NetcheckError("URL 为空或超过长度上限")
    if any(ch.isspace() for ch in text):
        raise NetcheckError("URL 含空白字符")
    parts = urlsplit(text)
    if parts.scheme.lower() != "https":
        raise NetcheckError(f"仅允许 https URL（收到 {parts.scheme or '空'} 协议）")
    host = (parts.hostname or "").lower()
    if not host:
        raise NetcheckError("URL 缺少主机名")
    if "@" in (parts.netloc or ""):
        raise NetcheckError("URL 不允许携带 userinfo")
    if host in _BLOCKED_HOSTNAMES:
        raise NetcheckError("主机名被禁止")
    try:
        port = parts.port
    except ValueError as exc:
        raise NetcheckError("URL 端口非法") from exc
    if port is None:
        port = 443
    # IP literal：直接按地址判定，不做 DNS
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not _ip_is_public(literal):
        raise NetcheckError("IP literal 地址不在公网范围")
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return host, target, port


def resolve_public_ips(host: str, port: int = 443) -> list[str]:
    """DNS 解析并逐地址复核；任一解析结果非公网即拒绝（防 rebinding 的
    第一步：连接方必须使用这里返回的地址，见 media/download.py 的每跳重验）。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetcheckError(f"主机名解析失败：{host}") from exc
    ips: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        raw = sockaddr[0]
        try:
            addr = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            continue
        if not _ip_is_public(addr):
            raise NetcheckError("解析结果包含非公网地址")
        ips.add(str(addr))
    if not ips:
        raise NetcheckError("主机名没有可用解析结果")
    return sorted(ips)


def host_allowed(host: str, allowlist: tuple[str, ...] | list[str]) -> bool:
    """完整 hostname 白名单比较（大小写归一；无后缀匹配漏洞）。"""
    return host.lower() in {h.lower() for h in allowlist}
