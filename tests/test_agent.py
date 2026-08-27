from pathlib import Path

from PIL import Image

from rs_agent.agent import run_image_inspection_agent


def _make_test_image(path: Path) -> None:
    with Image.new("RGB", (1, 1), (10, 20, 30)) as image:
        image.save(path)


def test_success_trace_contains_all_five_stages(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    _make_test_image(image_path)

    result = run_image_inspection_agent("Inspect the supplied image.", image_path)

    assert [item["stage"] for item in result["trace"]] == [
        "task",
        "decision",
        "action",
        "observation",
        "final",
    ]


def test_success_uses_inspect_image_and_completes(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    _make_test_image(image_path)

    result = run_image_inspection_agent("Inspect the supplied image.", image_path)

    assert result["status"] == "completed"
    assert result["trace"][2] == {
        "stage": "action",
        "tool": "inspect_image",
        "arguments": {"path": str(image_path)},
    }
    observation = result["trace"][3]
    assert observation["tool"] == "inspect_image"
    assert observation["ok"] is True
    assert observation["result"]["format"] == "PNG"


def test_invalid_path_is_observed_and_fails_without_raising(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.png"

    result = run_image_inspection_agent("Inspect the supplied image.", missing_path)

    assert result["status"] == "failed"
    observation = result["trace"][3]
    assert observation["stage"] == "observation"
    assert observation["ok"] is False
    assert "does not exist" in observation["error"]
    assert result["trace"][4]["status"] == "failed"
