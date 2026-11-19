"""Template creation use cases for the desktop shell."""

import contextlib
import io
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import pyperclip
from PIL import Image

from wosutil.config import TEMPLATES_WRITE_DIR
from wosutil.emulator.emulator_manager import take_screenshot


class TemplateError(RuntimeError):
    """Raised when a template operation cannot be completed."""


class TemplateService:
    """Hold the current source image and save template crops."""

    def __init__(self):
        """Start with no image loaded."""
        self._image: Optional[Image.Image] = None
        self._rev = 0
        self._source = ""
        self._lock = threading.RLock()

    def info(self) -> dict:
        """Return the current image metadata and the writable directory."""
        with self._lock:
            if self._image is None:
                return {"rev": 0, "width": 0, "height": 0, "source": "", "directory": TEMPLATES_WRITE_DIR}
            return {
                "rev": self._rev,
                "width": self._image.width,
                "height": self._image.height,
                "source": self._source,
                "directory": TEMPLATES_WRITE_DIR,
            }

    def set_image(self, image: Image.Image, source: str) -> dict:
        """Replace the current image and bump the revision."""
        with self._lock:
            self._image = image.convert("RGB")
            self._rev += 1
            self._source = source
        return self.info()

    def load(self, path: str) -> dict:
        """Load an image from disk."""
        source = Path(path)
        if not source.is_file():
            raise TemplateError(f"Image not found: {path}.")
        try:
            with Image.open(source) as image:
                return self.set_image(image, str(source))
        except OSError as exc:
            raise TemplateError(f"Could not read the image: {exc}") from exc

    def capture(self, index: int) -> dict:
        """Capture a screenshot from an emulator instance."""
        try:
            path = take_screenshot(index)
        except Exception as exc:
            raise TemplateError(f"Could not capture instance {index}: {exc}") from exc
        if not path or not os.path.exists(path):
            raise TemplateError(f"Could not capture instance {index}. Is the emulator running?")
        try:
            with Image.open(path) as image:
                return self.set_image(image, f"Instance {index}")
        except OSError as exc:
            raise TemplateError(f"Could not read the capture: {exc}") from exc
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)

    def png(self) -> bytes:
        """Return the current image encoded as PNG."""
        with self._lock:
            if self._image is None:
                raise TemplateError("Load or capture an image first.")
            buffer = io.BytesIO()
            self._image.save(buffer, format="PNG")
            return buffer.getvalue()

    def save_crop(self, roi, path: str) -> str:
        """Crop the current image and save it under the templates directory."""
        x, y, width, height = self._validated_roi(roi)
        with self._lock:
            if self._image is None:
                raise TemplateError("Load or capture an image first.")
            target = self._target(path, "template.png")
            try:
                self._image.crop((x, y, x + width, y + height)).save(target, format="PNG")
            except OSError as exc:
                raise TemplateError(f"Could not save the template: {exc}") from exc
        return str(target)

    def save_full(self, path: str) -> str:
        """Save the full source image under the templates directory."""
        with self._lock:
            if self._image is None:
                raise TemplateError("Load or capture an image first.")
            name = (path or "").strip() or f"screenshots/screen_{time.strftime('%Y%m%d_%H%M%S')}.png"
            target = self._target(name, "screenshot.png")
            try:
                self._image.save(target, format="PNG")
            except OSError as exc:
                raise TemplateError(f"Could not save the screenshot: {exc}") from exc
        return str(target)

    def copy(self, text: str) -> None:
        """Copy text to the system clipboard."""
        try:
            pyperclip.copy(text)
        except Exception as exc:
            raise TemplateError(f"Could not copy to the clipboard: {exc}") from exc

    def _validated_roi(self, roi) -> tuple[int, int, int, int]:
        """Validate a ``[x, y, width, height]`` region against the image."""
        if not isinstance(roi, (list, tuple)) or len(roi) != 4:
            raise TemplateError("Select a region first.")
        try:
            x, y, width, height = (int(value) for value in roi)
        except (TypeError, ValueError) as exc:
            raise TemplateError("Invalid region.") from exc
        with self._lock:
            if self._image is None:
                raise TemplateError("Load or capture an image first.")
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > self._image.width or y + height > self._image.height:
                raise TemplateError("The region is outside the image.")
        return x, y, width, height

    def _target(self, name: str, default: str) -> Path:
        """Resolve a safe PNG path under the writable templates directory."""
        value = (name or default).replace("\\", "/").strip()
        if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value) or ".." in value.split("/"):
            raise TemplateError(f"Invalid save path: {name}.")
        root = Path(TEMPLATES_WRITE_DIR).resolve()
        target = (root / value).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise TemplateError(f"Invalid save path: {name}.") from exc
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TemplateError(f"Could not create the save folder: {exc}") from exc
        return target
