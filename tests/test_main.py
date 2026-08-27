import json
from pathlib import Path

import pytest
from PIL import Image

from rs_agent.__main__ import main


def _make_test_image(path: Path) -> None:
    with Image.new("RGB", (1, 1), (10, 20, 30)) as image:
        image.save(path)


def test_inspect_command_outputs_trace_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image_path = tmp_path / "image.png"
    _make_test_image(image_path)

    exit_code = main(["inspect", str(image_path)])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert [item["stage"] for item in output["trace"]] == [
        "task",
        "decision",
        "action",
        "observation",
        "final",
    ]


def test_inspect_command_returns_one_for_missing_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_path = tmp_path / "missing.png"

    exit_code = main(["inspect", str(missing_path)])

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    assert output["trace"][3]["ok"] is False
