"""Image tools exposed to the demo agent."""

from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError
from PIL.Image import DecompressionBombError


class ImageInspectionError(Exception):
    """Raised when an image cannot be inspected safely."""


def inspect_image(path: str | Path) -> dict[str, object]:
    """Return JSON-serializable metadata and per-band statistics for an image."""
    display_path = str(path)
    image_path = Path(path)

    try:
        path_exists = image_path.exists()
        path_is_file = image_path.is_file()
    except OSError as exc:
        raise ImageInspectionError(f"Cannot access image path: {display_path}") from exc

    if not path_exists:
        raise ImageInspectionError(f"Image path does not exist: {display_path}")
    if not path_is_file:
        raise ImageInspectionError(f"Image path is not a regular file: {display_path}")

    try:
        with Image.open(image_path) as image:
            image.load()
            image_format = image.format
            width, height = image.size
            mode = image.mode
            bands = list(image.getbands())
            statistics = _calculate_statistics(image, bands)
    except UnidentifiedImageError as exc:
        raise ImageInspectionError(f"File is not a readable image: {display_path}") from exc
    except DecompressionBombError as exc:
        raise ImageInspectionError(f"Image exceeds safety limits: {display_path}") from exc
    except OSError as exc:
        raise ImageInspectionError(f"Could not read image data: {display_path}") from exc

    return {
        "ok": True,
        "path": display_path,
        "format": image_format,
        "width": width,
        "height": height,
        "mode": mode,
        "bands": bands,
        "statistics": statistics,
    }


# Modes whose mean must be computed from raw pixels: ImageStat.Stat derives the
# mean from a 256-bin histogram, which silently corrupts high-bit-depth data.
_HIGH_BIT_DEPTH_MODES = {"I", "I;16", "I;16L", "I;16B", "F"}


def _calculate_statistics(
    image: Image.Image, bands: list[str]
) -> dict[str, dict[str, int | float]]:
    """Calculate minimum, maximum, and mean values for each image band."""
    statistics: dict[str, dict[str, int | float]] = {}

    for band_name, band_image in zip(bands, image.split(), strict=True):
        minimum, maximum = band_image.getextrema()
        statistics[band_name] = {
            "min": minimum,
            "max": maximum,
            "mean": round(_band_mean(band_image), 4),
        }

    return statistics


def _band_mean(band_image: Image.Image) -> int | float:
    """Return the exact per-pixel mean, preserving the source bit depth."""
    if band_image.mode in _HIGH_BIT_DEPTH_MODES:
        width, height = band_image.size
        return sum(band_image.getdata()) / (width * height)
    return ImageStat.Stat(band_image).mean[0]
