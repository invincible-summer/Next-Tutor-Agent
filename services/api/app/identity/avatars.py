"""Private, normalized account avatars; no public URL or client-chosen owner."""
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
import re
import shutil
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.atomic import atomic_write_bytes, file_lock
from .config import USERS_DIR
from .store import account_record_lock, get_by_id, update_profile_fields

from app.core import paths as _paths

_AVATARS_DIR = _paths.bind_storage_path(
    __name__, "_AVATARS_DIR", "users", "avatars")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
AVATAR_SIZE = 256


def owner_dir(owner: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", owner):
        raise ValueError("invalid_avatar_owner")
    path = _AVATARS_DIR / owner
    if path.is_symlink() or not path.resolve().is_relative_to(_AVATARS_DIR.resolve()):
        raise ValueError("invalid_avatar_owner")
    return path


def lifecycle_lock(owner: str):
    """Serialize avatar IO with this owner's deletion without blocking other accounts."""
    return file_lock(owner_dir(owner))


def normalize(data: bytes) -> bytes:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("avatar_too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("avatar_invalid_image")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("avatar_too_large")
                if getattr(image, "is_animated", False):
                    raise ValueError("avatar_invalid_image")
                image.load()
                fitted = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGBA"),
                                      (AVATAR_SIZE, AVATAR_SIZE),
                                      method=Image.Resampling.LANCZOS)
                # Copy pixel data into a fresh image to strip EXIF/ICC/text metadata.
                clean = Image.new("RGBA", fitted.size)
                clean.paste(fitted)
                output = BytesIO()
                clean.save(output, format="PNG", optimize=True)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("avatar_invalid_image") from exc


def save(owner: str, data: bytes):
    encoded = normalize(data)
    directory = owner_dir(owner)
    with lifecycle_lock(owner), account_record_lock():
        if get_by_id(owner) is None:
            raise ValueError("account_not_found")
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(directory / "avatar.png", encoded)
        revision = "avatar:" + hashlib.sha256(encoded).hexdigest()[:24]
        return update_profile_fields(owner, {"avatar": revision})


def remove(owner: str):
    with lifecycle_lock(owner), account_record_lock():
        if get_by_id(owner) is None:
            raise ValueError("account_not_found")
        purge(owner)
        return update_profile_fields(owner, {"avatar": ""})


def read(owner: str) -> bytes | None:
    with lifecycle_lock(owner):
        user = get_by_id(owner)
        if user is None or not user.profile.avatar.startswith("avatar:"):
            return None
        try:
            path = owner_dir(owner) / "avatar.png"
            if path.is_symlink():
                return None
            return path.read_bytes()
        except FileNotFoundError:
            return None


def purge(owner: str) -> None:
    directory = owner_dir(owner)
    if directory.exists():
        shutil.rmtree(directory)
