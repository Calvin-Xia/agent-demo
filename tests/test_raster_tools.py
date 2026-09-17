import json
import math
import subprocess
from pathlib import Path

import pytest

from rs_agent.tools import RasterInspectionError, inspect_raster


def _gdal_info() -> dict[str, object]:
    return {
        "description": "sample.tif",
        "driverShortName": "GTiff",
        "driverLongName": "GeoTIFF",
        "files": ["sample.tif"],
        "size": [4, 3],
        "coordinateSystem": {"wkt": 'PROJCRS["Example"]'},
        "geoTransform": [0, 1, 0, 0, 0, -1],
        "cornerCoordinates": {
            "upperLeft": [0, 0],
            "lowerLeft": [0, -3],
            "lowerRight": [4, -3],
            "upperRight": [4, 0],
            "center": [2, -1.5],
        },
        "bands": [
            {
                "band": 1,
                "type": "UInt16",
                "colorInterpretation": "Gray",
                "noDataValue": 0,
                "metadata": {"ignored": "value"},
                "statistics": {"minimum": 0, "maximum": 10},
            },
            {
                "band": 2,
                "type": "Float32",
                "colorInterpretation": "Undefined",
                "noDataValue": -9999.0,
            },
        ],
        "metadata": {"ignored": "value"},
        "stac": {"proj:shape": [3, 4]},
        "gcps": {"gcpList": []},
    }


def _mock_gdalinfo(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> list[tuple[list[str], dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=json.dumps(payload),
            stderr=stderr,
        )

    monkeypatch.setattr("rs_agent.tools.subprocess.run", fake_run)
    return calls


def test_inspect_raster_crops_and_normalizes_gdal_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    calls = _mock_gdalinfo(monkeypatch, _gdal_info())

    result = inspect_raster(raster_path)

    assert result == {
        "ok": True,
        "path": str(raster_path),
        "driver": {"short_name": "GTiff", "long_name": "GeoTIFF"},
        "width": 4,
        "height": 3,
        "band_count": 2,
        "bands": [
            {
                "index": 1,
                "data_type": "UInt16",
                "color_interpretation": "Gray",
                "nodata": 0,
            },
            {
                "index": 2,
                "data_type": "Float32",
                "color_interpretation": "Undefined",
                "nodata": -9999.0,
            },
        ],
        "crs_wkt": 'PROJCRS["Example"]',
        "geotransform": [0, 1, 0, 0, 0, -1],
        "pixel_size": {"x": 1.0, "y": 1.0},
        "corners": {
            "upper_left": [0.0, 0.0],
            "lower_left": [0.0, -3.0],
            "lower_right": [4.0, -3.0],
            "upper_right": [4.0, 0.0],
        },
        "warnings": [],
    }
    command, options = calls[0]
    assert command == [
        "gdalinfo",
        "-json",
        "-nogcp",
        "-nomd",
        "-norat",
        "-noct",
        "-nofl",
        str(raster_path),
    ]
    assert options["capture_output"] is True
    assert options["timeout"] == 15
    assert options["shell"] is False


def test_inspect_raster_preserves_band_order_types_and_nodata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    payload = _gdal_info()
    payload["bands"] = list(reversed(payload["bands"]))
    _mock_gdalinfo(monkeypatch, payload)

    result = inspect_raster(raster_path)

    assert [band["index"] for band in result["bands"]] == [2, 1]
    assert [band["data_type"] for band in result["bands"]] == [
        "Float32",
        "UInt16",
    ]
    assert [band["nodata"] for band in result["bands"]] == [-9999.0, 0]


def test_inspect_raster_warns_for_missing_optional_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    payload = _gdal_info()
    payload.pop("coordinateSystem")
    payload.pop("geoTransform")
    payload.pop("cornerCoordinates")
    del payload["bands"][0]["noDataValue"]
    del payload["bands"][0]["colorInterpretation"]
    _mock_gdalinfo(monkeypatch, payload)

    result = inspect_raster(raster_path)

    assert result["crs_wkt"] is None
    assert result["geotransform"] is None
    assert result["pixel_size"] is None
    assert result["corners"] is None
    assert result["bands"][0]["nodata"] is None
    assert result["bands"][0]["color_interpretation"] is None
    assert result["warnings"] == [
        "Band 1 color interpretation is unavailable.",
        "Band 1 NoData value is unavailable.",
        "CRS is unavailable.",
        "Geotransform and pixel size are unavailable.",
        "Corner coordinates are unavailable.",
    ]


def test_inspect_raster_uses_rotated_pixel_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    payload = _gdal_info()
    payload["geoTransform"] = [0, 3, 4, 0, 4, -3]
    _mock_gdalinfo(monkeypatch, payload)

    result = inspect_raster(raster_path)

    assert math.isclose(result["pixel_size"]["x"], 5.0)
    assert math.isclose(result["pixel_size"]["y"], 5.0)


@pytest.mark.parametrize(
    "geotransform",
    [
        [0, 1.3e308, 0, 0, 1.3e308, 0],
        [0, 10**1000, 0, 0, 0, -1],
    ],
)
def test_inspect_raster_warns_for_unusable_geotransform_numbers(
    geotransform: list[int | float],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    payload = _gdal_info()
    payload["geoTransform"] = geotransform
    _mock_gdalinfo(monkeypatch, payload)

    result = inspect_raster(raster_path)

    assert result["geotransform"] is None
    assert result["pixel_size"] is None
    assert "Geotransform and pixel size are unavailable." in result["warnings"]


def test_inspect_raster_uses_absolute_execution_path_for_dash_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raster_path = Path("-stats.tif")
    raster_path.write_bytes(b"placeholder")
    calls = _mock_gdalinfo(monkeypatch, _gdal_info())

    result = inspect_raster(raster_path)

    assert result["path"] == "-stats.tif"
    assert calls[0][0][-1] == str(raster_path.absolute())


def test_inspect_raster_rejects_missing_path(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.tif"

    with pytest.raises(RasterInspectionError, match="does not exist"):
        inspect_raster(missing_path)


def test_inspect_raster_rejects_non_file_path(tmp_path: Path) -> None:
    with pytest.raises(RasterInspectionError, match="not a regular file"):
        inspect_raster(tmp_path)


def test_inspect_raster_handles_missing_gdalinfo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")

    def missing_command(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr("rs_agent.tools.subprocess.run", missing_command)

    with pytest.raises(RasterInspectionError, match="not available on PATH"):
        inspect_raster(raster_path)


def test_inspect_raster_handles_gdalinfo_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["gdalinfo"], timeout=15)

    monkeypatch.setattr("rs_agent.tools.subprocess.run", timeout)

    with pytest.raises(RasterInspectionError, match="timed out after 15 seconds"):
        inspect_raster(raster_path)


def test_inspect_raster_reports_only_first_stderr_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    _mock_gdalinfo(
        monkeypatch,
        {},
        returncode=1,
        stderr="\nERROR 4: raster cannot be opened\nverbose follow-up details\n",
    )

    with pytest.raises(RasterInspectionError) as error:
        inspect_raster(raster_path)

    assert str(error.value) == "gdalinfo failed: ERROR 4: raster cannot be opened"


def test_inspect_raster_rejects_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")

    def invalid_json(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, returncode=0, stdout="not-json", stderr=""
        )

    monkeypatch.setattr("rs_agent.tools.subprocess.run", invalid_json)

    with pytest.raises(RasterInspectionError, match="invalid JSON"):
        inspect_raster(raster_path)


@pytest.mark.parametrize("field", ["driverShortName", "size", "bands"])
def test_inspect_raster_rejects_missing_required_fields(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    payload = _gdal_info()
    payload.pop(field)
    _mock_gdalinfo(monkeypatch, payload)

    with pytest.raises(RasterInspectionError, match="invalid required field"):
        inspect_raster(raster_path)


def test_inspect_raster_rejects_malformed_required_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raster_path = tmp_path / "sample.tif"
    raster_path.write_bytes(b"placeholder")
    payload = _gdal_info()
    payload["size"] = ["4", 3]
    _mock_gdalinfo(monkeypatch, payload)

    with pytest.raises(RasterInspectionError, match="size"):
        inspect_raster(raster_path)
