"""A transparent, scripted single-step image inspection agent."""

from pathlib import Path

from rs_agent.tools import ImageInspectionError, inspect_image


def run_image_inspection_agent(
    task: str, image_path: str | Path
) -> dict[str, object]:
    """Run the fixed policy once and return its complete structured trace."""
    path = str(image_path)
    trace: list[dict[str, object]] = [
        {"stage": "task", "task": task, "image_path": path},
        {
            "stage": "decision",
            "policy": "scripted",
            "tool": "inspect_image",
            "reason": "The scripted policy always selects inspect_image.",
        },
        {
            "stage": "action",
            "tool": "inspect_image",
            "arguments": {"path": path},
        },
    ]

    try:
        result = inspect_image(image_path)
    except ImageInspectionError as exc:
        trace.append(
            {
                "stage": "observation",
                "tool": "inspect_image",
                "ok": False,
                "error": str(exc),
            }
        )
        final_answer: dict[str, object] = {
            "ok": False,
            "message": f"Image inspection failed: {exc}",
        }
        status = "failed"
    else:
        trace.append(
            {
                "stage": "observation",
                "tool": "inspect_image",
                "ok": True,
                "result": result,
            }
        )
        final_answer = {
            "ok": True,
            "message": "Image inspection completed.",
            "image": result,
        }
        status = "completed"

    trace.append(
        {
            "stage": "final",
            "status": status,
            "answer": final_answer,
        }
    )
    return {"status": status, "trace": trace, "final_answer": final_answer}
