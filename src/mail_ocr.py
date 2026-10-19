"""Read Kobo's weekday banner images with macOS Vision (scripts/ocr_bars.swift)."""

from __future__ import annotations

import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWIFT_SOURCE = ROOT / "scripts" / "ocr_bars.swift"
COMPILED = ROOT / "bin" / "ocr_bars"
DOWNLOAD_TIMEOUT_SECONDS = 20
OCR_TIMEOUT_SECONDS = 300
MAX_IMAGE_BYTES = 2_000_000
USER_AGENT = "Mozilla/5.0 (compatible; kobo99-digest/1.0)"


class OcrError(RuntimeError):
    """Banner images could not be downloaded or read."""


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
    except OSError as error:
        raise OcrError(f"Cannot download {url}: {error}") from error
    if len(data) > MAX_IMAGE_BYTES:
        raise OcrError(f"Banner image too large: {url}")
    target.write_bytes(data)


def _command() -> list[str]:
    return [str(COMPILED)] if COMPILED.exists() else ["swift", str(SWIFT_SOURCE)]


def read_bars(urls: list[str]) -> list[str]:
    """Return the recognised text of each banner, in input order."""
    with tempfile.TemporaryDirectory(prefix="kobo99-bars-") as tmp:
        paths = [Path(tmp) / f"bar{index}.img" for index in range(len(urls))]
        for url, path in zip(urls, paths):
            _download(url, path)
        try:
            result = subprocess.run([*_command(), *map(str, paths)], capture_output=True, text=True,
                                    timeout=OCR_TIMEOUT_SECONDS, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise OcrError(f"OCR failed: {error}") from error
        by_path = dict(line.split("\t", 1) for line in result.stdout.splitlines() if "\t" in line)
        return [by_path.get(str(path), "") for path in paths]
