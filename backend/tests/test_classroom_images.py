"""课堂图片管线回归（plan.md C03/C04/C05 + §8 退出门）。

覆盖：Pexels/Pixabay 显式参数与署名数据、限流状态机、24h 每 owner 缓存、
未知 CDN 候选丢弃、无 key 降级、服务层 provider 顺序/双语重试/预算、
下载白名单每跳重验、重定向 ≤3 跳、魔数嗅探、IP 固定传输层（SNI 保持
原域）、Pillow 重编码/EXIF 清理/透明保留/长边与像素上限、asset hash。
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

import httpcore  # noqa: E402
import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from tests.classroom_provider_mocks import (  # noqa: E402
    PEXELS_SEARCH_EMPTY, PEXELS_SEARCH_OK, PIXABAY_SEARCH_EMPTY,
    PIXABAY_SEARCH_OK, ProviderMockTransport, make_png_bytes)

from app.classroom import limits  # noqa: E402
from app.classroom.errors import ClassroomError  # noqa: E402
from app.classroom.media import base as media_base  # noqa: E402
from app.classroom.media import download as dl  # noqa: E402
from app.classroom.media.pexels import PexelsProvider  # noqa: E402
from app.classroom.media.pixabay import PixabayProvider  # noqa: E402
from app.classroom.media.service import (ImageSearchService,  # noqa: E402
                                         build_image_providers)
from app.classroom.netcheck import NetcheckError  # noqa: E402
from app.classroom.research.base import shared_research_cache  # noqa: E402
from app.core.config import settings  # noqa: E402

PNG_BYTES = make_png_bytes()


def _reset_media_state() -> None:
    shared_research_cache().clear()
    state = media_base.pexels_rate_state
    state.hourly_remaining = limits.PEXELS_HOURLY_LIMIT_DEFAULT
    state.monthly_remaining = limits.PEXELS_MONTHLY_LIMIT_DEFAULT
    media_base.pixabay_rate_counter._events.clear()


class PexelsProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_media_state()
        self.mocks = ProviderMockTransport()
        self.provider = PexelsProvider(
            api_key="pexels-test-key", transport=self.mocks.transport())

    async def asyncTearDown(self) -> None:
        await self.provider.aclose()
        _reset_media_state()

    def _route(self, json_body, *, headers: dict | None = None,
               status: int = 200) -> None:
        self.mocks.route("GET", "/v1/search", status=status,
                         json_body=json_body, handler=None)
        # route() 的 json 分支不带 headers：需要 headers 时用 handler
        if headers:
            def with_headers(request: httpx.Request) -> httpx.Response:
                return httpx.Response(status, json=json_body,
                                      headers=headers)
            self.mocks._routes[-1] = ("GET", "/v1/search", with_headers)

    async def test_search_explicit_params_and_attribution(self) -> None:
        self._route(PEXELS_SEARCH_OK)
        cands = await self.provider.search("碰撞", owner="u1")
        posts = [r for r in self.mocks.requests
                 if "api.pexels.com/v1/search" in r["url"]]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["headers"].get("authorization"),
                         "pexels-test-key", "Authorization 为原始 key")
        url = httpx.URL(posts[0]["url"])
        params = dict(url.params)
        self.assertEqual(params["query"], "碰撞")
        self.assertEqual(params["orientation"], "landscape")
        self.assertEqual(params["locale"], "zh-CN")
        self.assertEqual(params["per_page"], "8")
        self.assertEqual(len(cands), 2)
        first = cands[0]
        self.assertRegex(first.candidate_id, r"^cand_[0-9a-f]{24}$")
        self.assertEqual(first.provider, "pexels")
        self.assertEqual(first.provider_asset_id, "3140621")
        self.assertEqual(first.creator, "示例摄影师")
        self.assertEqual(first.license_url,
                         "https://www.pexels.com/license/")
        self.assertIn("images.pexels.com", first.download_url)
        self.assertIn("billiards", first.page_url)

    async def test_search_updates_rate_state_from_headers(self) -> None:
        self._route(PEXELS_SEARCH_OK, headers={
            "X-RateLimit-Remaining": "3",
            "X-RateLimit-Monthly-Remaining": "42"})
        await self.provider.search("碰撞", owner="u1")
        # consume() 先扣 1，再被响应头覆盖为观察值
        self.assertEqual(media_base.pexels_rate_state.hourly_remaining, 3)
        self.assertEqual(media_base.pexels_rate_state.monthly_remaining, 42)

    async def test_search_blocked_after_zero_remaining(self) -> None:
        _reset_media_state()
        self.mocks = ProviderMockTransport()
        self.provider = PexelsProvider(
            api_key="pexels-test-key", transport=self.mocks.transport())
        self._route(PEXELS_SEARCH_OK, headers={
            "X-RateLimit-Remaining": "0"})
        await self.provider.search("碰撞", owner="u1")
        self.assertEqual(media_base.pexels_rate_state.hourly_remaining, 0)
        self.mocks.requests.clear()
        with self.assertRaises(media_base.RateLimitExceeded):
            await self.provider.search("别的查询", owner="u1")
        self.assertEqual(self.mocks.requests, [],
                         "限流后不得再发起请求")

    async def test_search_429_marks_rate_limited(self) -> None:
        self._route(PEXELS_SEARCH_OK, status=429)
        with self.assertRaises(media_base.RateLimitExceeded):
            await self.provider.search("碰撞", owner="u1")
        self.assertEqual(media_base.pexels_rate_state.hourly_remaining,
                         limits.PEXELS_HOURLY_LIMIT_DEFAULT - 1)

    async def test_search_cache_per_owner_24h(self) -> None:
        self._route(PEXELS_SEARCH_OK)
        first = await self.provider.search("碰撞", owner="u1")
        second = await self.provider.search("碰撞", owner="u1")
        self.assertEqual(len(self.mocks.requests), 1)
        self.assertEqual([c.candidate_id for c in first],
                         [c.candidate_id for c in second])
        await self.provider.search("碰撞", owner="u2")
        self.assertEqual(len(self.mocks.requests), 2, "owner 间缓存隔离")

    async def test_unknown_cdn_candidate_dropped(self) -> None:
        payload = {"photos": [{
            "id": 1, "width": 10, "height": 10,
            "url": "https://www.pexels.com/photo/x-1/",
            "photographer": "p", "photographer_url": "",
            "alt": "x",
            "src": {"large2x": "https://evil-mirror.example/x.jpeg",
                    "medium": ""}}], "total_results": 1}
        self._route(payload)
        cands = await self.provider.search("碰撞", owner="u1")
        self.assertEqual(cands, [], "非批准 CDN 的候选必须丢弃")

    async def test_no_key_not_configured(self) -> None:
        with mock.patch.object(settings, "pexels_api_key", ""):
            self.assertFalse(PexelsProvider(api_key="").configured)


class PixabayProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_media_state()
        self.mocks = ProviderMockTransport()
        self.provider = PixabayProvider(
            api_key="pixabay-test-key", transport=self.mocks.transport())

    async def asyncTearDown(self) -> None:
        await self.provider.aclose()
        _reset_media_state()

    async def test_search_explicit_params_and_attribution(self) -> None:
        self.mocks.route("GET", "pixabay.com/api/", json_body=PIXABAY_SEARCH_OK)
        cands = await self.provider.search("碰撞 物理", owner="u1")
        self.assertEqual(len(self.mocks.requests), 1)
        url = httpx.URL(self.mocks.requests[0]["url"])
        params = dict(url.params)
        self.assertEqual(params["key"], "pixabay-test-key")
        self.assertEqual(params["safesearch"], "true")
        self.assertEqual(params["per_page"], "8")
        self.assertEqual(params["image_type"], "photo")
        self.assertEqual(params["orientation"], "horizontal")
        self.assertEqual(params["lang"], "zh")
        self.assertEqual(params["q"], "碰撞 物理")
        self.assertEqual(len(cands), 2)
        first = cands[0]
        self.assertEqual(first.provider, "pixabay")
        self.assertEqual(first.provider_asset_id, "8736128")
        self.assertEqual(first.creator, "sample_user")
        self.assertEqual(
            first.creator_url,
            "https://pixabay.com/users/sample_user-2345678/")
        self.assertEqual(first.license_url,
                         "https://pixabay.com/service/license-summary/")
        self.assertIn("cdn.pixabay.com", first.download_url)
        self.assertIn("collision", first.alt)

    async def test_query_truncated_to_100_chars(self) -> None:
        self.mocks.route("GET", "pixabay.com/api/", json_body=PIXABAY_SEARCH_OK)
        await self.provider.search("词" * 130, owner="u1")
        params = dict(httpx.URL(self.mocks.requests[0]["url"]).params)
        self.assertEqual(len(params["q"]), limits.PIXABAY_QUERY_MAX_CHARS)

    async def test_error_message_never_leaks_key(self) -> None:
        self.mocks.route("GET", "pixabay.com/api/", status=400,
                         json_body={"error": "bad"})
        with self.assertRaises(ClassroomError) as ctx:
            await self.provider.search("碰撞", owner="u1")
        self.assertNotIn("pixabay-test-key", str(ctx.exception))
        self.assertNotIn("pixabay-test-key", ctx.exception.message)

    async def test_rate_window_blocks_burst(self) -> None:
        counter = media_base.SlidingWindowCounter(2, 60.0)
        self.assertTrue(counter.allow())
        self.assertTrue(counter.allow())
        self.assertFalse(counter.allow())


class ImageSearchServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_media_state()
        self.mocks = ProviderMockTransport()
        self.mocks.route("GET", "api.pexels.com/v1/search",
                         json_body=PEXELS_SEARCH_OK)
        self.mocks.route("GET", "pixabay.com/api/",
                         json_body=PIXABAY_SEARCH_OK)

    def _service(self, *, only: str = "") -> ImageSearchService:
        with mock.patch.object(settings, "pexels_api_key", "pk"), \
                mock.patch.object(settings, "pixabay_api_key", "xk"), \
                mock.patch.object(settings, "classroom_image_providers",
                                  "pexels,pixabay"):
            providers = build_image_providers(
                transport=self.mocks.transport())
        return ImageSearchService(providers, only_provider=only)

    async def test_provider_order_and_fallback(self) -> None:
        _reset_media_state()
        self.mocks._routes.clear()
        self.mocks.route("GET", "api.pexels.com/v1/search",
                         json_body=PEXELS_SEARCH_EMPTY)
        self.mocks.route("GET", "pixabay.com/api/",
                         json_body=PIXABAY_SEARCH_OK)
        service = self._service()
        budget = media_base.ImageSearchBudget()
        cands = await service.search(["碰撞"], owner="u1", budget=budget)
        self.assertEqual(budget.calls, 2, "pexels 空 → 回退 pixabay")
        self.assertEqual({c.provider for c in cands}, {"pixabay"})

    async def test_bilingual_retry_merges(self) -> None:
        service = self._service()
        budget = media_base.ImageSearchBudget()
        cands = await service.search(["碰撞", "collision physics"],
                                     owner="u1", budget=budget)
        self.assertEqual(len(self.mocks.requests), 1,
                         "主查询首选命中：不回退也不做英文改写")
        self.assertEqual(budget.calls, 1)
        self.assertEqual({c.provider for c in cands}, {"pexels"})

    async def test_bilingual_retry_used_when_primary_empty(self) -> None:
        _reset_media_state()
        self.mocks._routes.clear()
        self.mocks.route("GET", "api.pexels.com/v1/search",
                         json_body=PEXELS_SEARCH_EMPTY)
        self.mocks.route("GET", "pixabay.com/api/",
                         json_body=PIXABAY_SEARCH_EMPTY)
        # 中文两家全空；英文改写时 pexels 命中
        def pexels_second(request: httpx.Request) -> httpx.Response:
            if dict(httpx.URL(request.url).params).get("query") == "collision":
                return httpx.Response(200, json=PEXELS_SEARCH_OK)
            return httpx.Response(200, json=PEXELS_SEARCH_EMPTY)
        self.mocks._routes[0] = ("GET", "api.pexels.com/v1/search",
                                 pexels_second)
        service = self._service()
        budget = media_base.ImageSearchBudget()
        cands = await service.search(["碰撞", "collision"],
                                     owner="u1", budget=budget)
        self.assertEqual(budget.calls, 3, "zh×2 家 + en 改写 1 次")
        self.assertEqual({c.provider for c in cands}, {"pexels"})

    async def test_budget_exhausted(self) -> None:
        service = self._service()
        budget = media_base.ImageSearchBudget(
            limits.IMAGE_SEARCH_BUDGET)
        with self.assertRaises(ClassroomError) as ctx:
            await service.search(["碰撞"], owner="u1", budget=budget)
        self.assertEqual(ctx.exception.code, "budget_exceeded")
        self.assertEqual(self.mocks.requests, [])

    async def test_no_keys_degrades_to_empty(self) -> None:
        _reset_media_state()
        with mock.patch.object(settings, "pexels_api_key", ""), \
                mock.patch.object(settings, "pixabay_api_key", ""), \
                mock.patch.object(settings, "classroom_image_providers",
                                  "pexels,pixabay"):
            service = ImageSearchService(build_image_providers())
        self.assertFalse(service.available)
        self.assertEqual(await service.search(["碰撞"], owner="u1"), [])


# ---------------------------------------------------------------------------
# C04：下载与清洗
# ---------------------------------------------------------------------------

class _FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self) -> None:
        self.tls_hostname: str | None = None

    async def read(self, max_bytes, timeout=None):
        return b""

    async def write(self, buffer, timeout=None):
        return None

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls_hostname = server_hostname
        return self

    async def aclose(self) -> None:
        return None

    def get_extra_info(self, info: str):
        return None


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None,
                          local_address=None, socket_options=None):
        self.connections.append((host, port))
        return _FakeStream()

    async def connect_unix_socket(self, path, timeout=None,
                                  socket_options=None):
        raise AssertionError("unix socket 不应被使用")

    async def sleep(self, seconds: float) -> None:
        return None


class PinnedBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_via_validated_ip_keeps_sni_domain(self) -> None:
        inner = _RecordingBackend()
        backend = dl.PinnedNetworkBackend(
            limits.IMAGE_DOWNLOAD_HOSTS,
            resolver=lambda host, port: ["203.0.113.10"],
            inner=inner)
        stream = await backend.connect_tcp("images.pexels.com", 443)
        self.assertEqual(inner.connections, [("203.0.113.10", 443)],
                         "TCP 必须连向核验过的 IP")
        tls_stream = await stream.start_tls(None)
        self.assertEqual(tls_stream._hostname, "images.pexels.com",
                         "TLS SNI/证书校验保持原域名")
        self.assertEqual(inner.connections, [("203.0.113.10", 443)],
                         "start_tls 不得触发第二次连接")

    async def test_rejects_hosts_outside_whitelist(self) -> None:
        inner = _RecordingBackend()
        backend = dl.PinnedNetworkBackend(
            limits.IMAGE_DOWNLOAD_HOSTS,
            resolver=lambda host, port: ["203.0.113.10"],
            inner=inner)
        with self.assertRaises(NetcheckError):
            await backend.connect_tcp("evil.example", 443)
        with self.assertRaises(NetcheckError):
            await backend.connect_tcp("fake.images.pexels.com", 443)
        self.assertEqual(inner.connections, [])

    def test_production_resolver_validates_ips(self) -> None:
        # 生产 resolver = netcheck.resolve_public_ips：私网答案整体拒绝
        from app.classroom.netcheck import resolve_public_ips
        with self.assertRaises(NetcheckError):
            resolve_public_ips("127.0.0.1", 443)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mocks = ProviderMockTransport()

    def _route(self, fragment: str, *, content: bytes = b"",
               json_body=None, status: int = 200,
               headers: dict | None = None):
        def handler(request: httpx.Request) -> httpx.Response:
            if json_body is not None:
                return httpx.Response(status, json=json_body,
                                      headers=headers or {})
            return httpx.Response(status, content=content,
                                  headers=headers or {})
        self.mocks.route("GET", fragment, handler=handler)

    async def test_whitelist_rejected_before_request(self) -> None:
        for url in ("https://evil.example/x.png",
                    "https://cdn.pixabay.com.evil.io/x.png",
                    "http://images.pexels.com/x.png",
                    "https://127.0.0.1/x.png"):
            with self.assertRaises(ClassroomError) as ctx:
                await dl.download_candidate_image(
                    url, transport=self.mocks.transport())
            self.assertEqual(ctx.exception.code, "image_unavailable")
        self.assertEqual(self.mocks.requests, [])

    async def test_download_sniffs_magic_not_extension(self) -> None:
        self._route("images.pexels.com/photos/1/x", content=PNG_BYTES)
        raw = await dl.download_candidate_image(
            "https://images.pexels.com/photos/1/x.jpeg",
            transport=self.mocks.transport())
        self.assertEqual(raw.sniffed, "image/png",
                         "以魔数而非扩展名判定内容")

        self.mocks = ProviderMockTransport()
        self._route("images.pexels.com/photos/2/x",
                    content=b"<svg onload=alert(1)>")
        with self.assertRaises(ClassroomError):
            await dl.download_candidate_image(
                "https://images.pexels.com/photos/2/x.png",
                transport=self.mocks.transport())

    async def test_redirects_each_hop_revalidated(self) -> None:
        self._route("images.pexels.com/a",
                    status=302,
                    headers={"Location": "https://cdn.pixabay.com/b"})
        self._route("cdn.pixabay.com/b", content=PNG_BYTES)
        raw = await dl.download_candidate_image(
            "https://images.pexels.com/a",
            transport=self.mocks.transport())
        self.assertEqual(raw.sniffed, "image/png")
        # 跳向非白名单主机：该跳被拒绝
        self.mocks = ProviderMockTransport()
        self._route("images.pexels.com/a",
                    status=302,
                    headers={"Location": "https://evil.io/c.png"})
        with self.assertRaises(ClassroomError) as ctx:
            await dl.download_candidate_image(
                "https://images.pexels.com/a",
                transport=self.mocks.transport())
        self.assertIn("白名单", ctx.exception.message)

    async def test_redirect_over_three_hops_rejected(self) -> None:
        for n in (1, 2, 3, 4):
            self._route(f"images.pexels.com/h{n}",
                        status=302,
                        headers={"Location":
                                 f"https://images.pexels.com/h{n + 1}"})
        with self.assertRaises(ClassroomError) as ctx:
            await dl.download_candidate_image(
                "https://images.pexels.com/h1",
                transport=self.mocks.transport())
        self.assertIn("3 跳", ctx.exception.message)

    async def test_size_cap_enforced(self) -> None:
        self._route("images.pexels.com/big",
                    content=b"\0" * (8 * 1024 * 1024 + 1))
        with self.assertRaises(ClassroomError) as ctx:
            await dl.download_candidate_image(
                "https://images.pexels.com/big",
                transport=self.mocks.transport())
        self.assertIn("MB 上限", ctx.exception.message)


class SanitizeTests(unittest.TestCase):
    def _jpeg(self, size=(320, 200), exif: bytes | None = None) -> bytes:
        img = Image.new("RGB", size)
        for x in range(0, size[0], 7):
            for y in range(0, size[1], 7):
                img.putpixel((x, y), (x % 256, y % 256, 64))
        out = BytesIO()
        kwargs = {"format": "JPEG", "quality": 90}
        if exif is not None:
            kwargs["exif"] = exif
        img.save(out, **kwargs)
        return out.getvalue()

    def test_sniff_variants(self) -> None:
        self.assertEqual(dl.sniff_image(PNG_BYTES), "image/png")
        self.assertEqual(dl.sniff_image(self._jpeg()), "image/jpeg")
        webp = BytesIO()
        Image.new("RGB", (8, 8)).save(webp, format="WEBP")
        self.assertEqual(dl.sniff_image(webp.getvalue()), "image/webp")
        self.assertIsNone(dl.sniff_image(b"<svg/>"))
        self.assertIsNone(dl.sniff_image(b"GIF89a"))

    def test_jpeg_reencoded_webp_exif_stripped(self) -> None:
        exif = Image.Exif()
        exif[0x010F] = "CameraMaker"
        exif[0x0132] = "2026:01:01 00:00:00"
        raw = self._jpeg(size=(400, 260), exif=exif.tobytes())
        out = dl.sanitize_image(raw, sniffed="image/jpeg")
        self.assertEqual(out.mime, "image/webp")
        reopened = Image.open(BytesIO(out.data))
        self.assertNotIn("exif", reopened.info,
                         "重编码后不得残留 EXIF")
        self.assertLessEqual(max(out.width, out.height),
                             limits.IMAGE_LONG_EDGE_MAX)
        self.assertEqual(out.sha256,
                         hashlib.sha256(out.data).hexdigest())
        self.assertLessEqual(len(out.data), limits.IMAGE_BYTES_HARD_MAX)

    def test_png_transparency_preserved(self) -> None:
        rgba = BytesIO()
        Image.new("RGBA", (100, 100), (10, 20, 30, 0)).save(
            rgba, format="PNG")
        out = dl.sanitize_image(rgba.getvalue(), sniffed="image/png")
        self.assertEqual(out.mime, "image/png")
        reopened = Image.open(BytesIO(out.data))
        self.assertEqual(reopened.mode, "RGBA", "透明通道必须保留")

    def test_long_edge_capped_and_ratio_kept(self) -> None:
        raw = self._jpeg(size=(3200, 1600))
        out = dl.sanitize_image(raw, sniffed="image/jpeg")
        self.assertLessEqual(max(out.width, out.height),
                             limits.IMAGE_LONG_EDGE_MAX)
        self.assertAlmostEqual(3200 / 1600, out.width / out.height,
                               places=1)

    def test_pixel_cap_rejects_oversized(self) -> None:
        with mock.patch.object(limits, "PIXEL_MAX", 100):
            with self.assertRaises(ClassroomError) as ctx:
                dl.sanitize_image(PNG_BYTES, sniffed="image/png")
            self.assertIn("MP", ctx.exception.message)


class DownloadAndSanitizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end(self) -> None:
        mocks = ProviderMockTransport()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=PNG_BYTES)
        mocks.route("GET", "cdn.pixabay.com/photo", handler=handler)
        out = await dl.download_and_sanitize(
            "https://cdn.pixabay.com/photo/2024/x.jpg",
            transport=mocks.transport())
        # RGB PNG（无透明）按 §8.3.5 转 WebP；尺寸保持
        self.assertEqual(out.mime, "image/webp")
        self.assertEqual((out.width, out.height), (64, 48))
        self.assertEqual(out.sha256,
                         hashlib.sha256(out.data).hexdigest())


if __name__ == "__main__":
    unittest.main()
