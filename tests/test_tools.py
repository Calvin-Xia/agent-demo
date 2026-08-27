from pathlib import Path

import pytest
from PIL import Image

from rs_agent.tools import ImageInspectionError, inspect_image


def test_inspect_image_returns_metadata_and_band_statistics(tmp_path: Path) -> None:
    image_path = tmp_path / "known.png"
    with Image.new("RGB", (2, 2)) as image:
        image.putdata(
            [
                (0, 10, 20),
                (255, 20, 0),
                (0, 30, 10),
                (255, 40, 30),
            ]
        )
        image.save(image_path)

    result = inspect_image(image_path)

    assert result == {
        "ok": True,
        "path": str(image_path),
        "format": "PNG",
        "width": 2,
        "height": 2,
        "mode": "RGB",
        "bands": ["R", "G", "B"],
        "statistics": {
            "R": {"min": 0, "max": 255, "mean": 127.5},
            "G": {"min": 10, "max": 40, "mean": 25.0},
            "B": {"min": 0, "max": 30, "mean": 15.0},
        },
    }


def test_inspect_image_rejects_missing_path(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.png"

    with pytest.raises(ImageInspectionError, match="does not exist"):
        inspect_image(missing_path)


def test_inspect_image_rejects_non_file_path(tmp_path: Path) -> None:
    with pytest.raises(ImageInspectionError, match="not a regular file"):
        inspect_image(tmp_path)


def test_inspect_image_rejects_text_file(tmp_path: Path) -> None:
    text_path = tmp_path / "not-an-image.txt"
    text_path.write_text("not an image", encoding="utf-8")

    with pytest.raises(ImageInspectionError, match="not a readable image"):
        inspect_image(text_path)


def test_inspect_image_preserves_16bit_mean(tmp_path: Path) -> None:
    image_path = tmp_path / "sixteen-bit.png"
    with Image.new("I;16", (2, 1)) as image:
        image.putdata([0, 65535])
        image.save(image_path)

    result = inspect_image(image_path)

    assert result["statistics"] == {"I": {"min": 0, "max": 65535, "mean": 32767.5}}


def test_inspect_image_rejects_decompression_bomb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "bomb.png"
    with Image.new("RGB", (3, 2)) as image:
        image.save(image_path)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 2)

    with pytest.raises(ImageInspectionError, match="exceeds safety limits"):
        inspect_image(image_path)
