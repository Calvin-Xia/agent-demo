"""Inspection tools exposed by the demo project."""

import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageStat, UnidentifiedImageError
from PIL.Image import DecompressionBombError


class ImageInspectionError(Exception):
    """Raised when an image cannot be inspected safely."""


class RasterInspectionError(Exception):
    """Raised when raster metadata cannot be inspected safely."""


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


_GDALINFO_TIMEOUT_SECONDS = 15


def inspect_raster(path: str | Path) -> dict[str, object]:
    """Return a compact, JSON-serializable view of GDAL raster metadata."""
    display_path = str(path)
    raster_path = Path(path)

    try:
        path_exists = raster_path.exists()
        path_is_file = raster_path.is_file()
    except OSError as exc:
        raise RasterInspectionError(
            f"Cannot access raster path: {display_path}"
        ) from exc

    if not path_exists:
        raise RasterInspectionError(f"Raster path does not exist: {display_path}")
    if not path_is_file:
        raise RasterInspectionError(
            f"Raster path is not a regular file: {display_path}"
        )

    execution_path = str(raster_path.absolute())
    command = [
        "gdalinfo",
        "-json",
        "-nogcp",
        "-nomd",
        "-norat",
        "-noct",
        "-nofl",
        execution_path,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GDALINFO_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise RasterInspectionError(
            "gdalinfo is not installed or not available on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RasterInspectionError(
            f"gdalinfo timed out after {_GDALINFO_TIMEOUT_SECONDS} seconds."
        ) from exc
    except OSError as exc:
        raise RasterInspectionError("Could not run gdalinfo.") from exc

    if completed.returncode != 0:
        detail = _first_meaningful_line(completed.stderr)
        if detail is None:
            detail = f"exit code {completed.returncode}"
        raise RasterInspectionError(f"gdalinfo failed: {detail}")

    try:
        raw_info = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RasterInspectionError("gdalinfo returned invalid JSON.") from exc

    return _normalize_raster_info(raw_info, display_path)


def _normalize_raster_info(raw_info: object, display_path: str) -> dict[str, object]:
    """Validate required GDAL fields and retain only the public raster contract."""
    if not isinstance(raw_info, dict):
        raise RasterInspectionError("gdalinfo JSON root must be an object.")

    driver_short_name = raw_info.get("driverShortName")
    driver_long_name = raw_info.get("driverLongName")
    if not _is_non_empty_string(driver_short_name) or not _is_non_empty_string(
        driver_long_name
    ):
        raise _invalid_required_field("driver")

    size = raw_info.get("size")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or not all(_is_positive_integer(value) for value in size)
    ):
        raise _invalid_required_field("size")
    width, height = size

    raw_bands = raw_info.get("bands")
    if not isinstance(raw_bands, list):
        raise _invalid_required_field("bands")

    warnings: list[str] = []
    bands: list[dict[str, object]] = []
    for position, raw_band in enumerate(raw_bands, start=1):
        if not isinstance(raw_band, dict):
            raise _invalid_required_field(f"bands[{position}]")

        band_index = raw_band.get("band")
        data_type = raw_band.get("type")
        if not _is_positive_integer(band_index) or not _is_non_empty_string(data_type):
            raise _invalid_required_field(f"bands[{position}]")

        color_interpretation = raw_band.get("colorInterpretation")
        if not _is_non_empty_string(color_interpretation):
            color_interpretation = None
            warnings.append(
                f"Band {band_index} color interpretation is unavailable."
            )

        nodata = raw_band.get("noDataValue")
        if not _is_number(nodata):
            nodata = None
            warnings.append(f"Band {band_index} NoData value is unavailable.")

        bands.append(
            {
                "index": band_index,
                "data_type": data_type,
                "color_interpretation": color_interpretation,
                "nodata": nodata,
            }
        )

    coordinate_system = raw_info.get("coordinateSystem")
    crs_wkt: str | None = None
    if isinstance(coordinate_system, dict):
        raw_wkt = coordinate_system.get("wkt")
        if _is_non_empty_string(raw_wkt):
            crs_wkt = raw_wkt
    if crs_wkt is None:
        warnings.append("CRS is unavailable.")

    raw_geotransform = raw_info.get("geoTransform")
    geotransform: list[int | float] | None = None
    pixel_size: dict[str, float] | None = None
    if (
        isinstance(raw_geotransform, list)
        and len(raw_geotransform) == 6
        and all(_is_number(value) for value in raw_geotransform)
    ):
        pixel_size_x = math.hypot(raw_geotransform[1], raw_geotransform[4])
        pixel_size_y = math.hypot(raw_geotransform[2], raw_geotransform[5])
        if math.isfinite(pixel_size_x) and math.isfinite(pixel_size_y):
            geotransform = raw_geotransform
            pixel_size = {"x": pixel_size_x, "y": pixel_size_y}
        else:
            warnings.append("Geotransform and pixel size are unavailable.")
    else:
        warnings.append("Geotransform and pixel size are unavailable.")

    corners = _normalize_corners(raw_info.get("cornerCoordinates"))
    if corners is None:
        warnings.append("Corner coordinates are unavailable.")

    return {
        "ok": True,
        "path": display_path,
        "driver": {
            "short_name": driver_short_name,
            "long_name": driver_long_name,
        },
        "width": width,
        "height": height,
        "band_count": len(bands),
        "bands": bands,
        "crs_wkt": crs_wkt,
        "geotransform": geotransform,
        "pixel_size": pixel_size,
        "corners": corners,
        "warnings": warnings,
    }


def _normalize_corners(raw_corners: object) -> dict[str, list[float]] | None:
    """Return the four GDAL corner coordinates with snake-case names."""
    if not isinstance(raw_corners, dict):
        return None

    key_map = {
        "upper_left": "upperLeft",
        "lower_left": "lowerLeft",
        "lower_right": "lowerRight",
        "upper_right": "upperRight",
    }
    corners: dict[str, list[float]] = {}
    for output_key, gdal_key in key_map.items():
        coordinate = raw_corners.get(gdal_key)
        if (
            not isinstance(coordinate, list)
            or len(coordinate) != 2
            or not all(_is_number(value) for value in coordinate)
        ):
            return None
        corners[output_key] = [float(coordinate[0]), float(coordinate[1])]
    return corners


def _first_meaningful_line(message: str) -> str | None:
    """Return one bounded, non-empty diagnostic line."""
    for line in message.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None


def _invalid_required_field(field: str) -> RasterInspectionError:
    return RasterInspectionError(
        f"gdalinfo JSON has an invalid required field: {field}."
    )


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


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
