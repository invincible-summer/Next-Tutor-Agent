"""课堂图片资源管线（plan.md §8）。无 key 即降级，不伪联网。"""
from __future__ import annotations

from .base import (ImageCandidate, ImageSearchBudget, ImageSearchProvider,
                   RateLimitExceeded, new_candidate_id)
from .download import (ProcessedImage, download_and_sanitize,
                       download_candidate_image, sanitize_image)
from .pexels import PexelsProvider
from .pixabay import PixabayProvider
from .service import ImageSearchService, build_image_providers

__all__ = [
    "ImageCandidate", "ImageSearchBudget", "ImageSearchProvider",
    "RateLimitExceeded", "new_candidate_id", "ProcessedImage",
    "download_and_sanitize", "download_candidate_image", "sanitize_image",
    "PexelsProvider", "PixabayProvider", "ImageSearchService",
    "build_image_providers",
]
