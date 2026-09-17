"""Command-line entry point for the demo agent."""

import argparse
import json
from collections.abc import Sequence

from rs_agent.agent import run_image_inspection_agent
from rs_agent.tools import RasterInspectionError, inspect_raster


def build_parser() -> argparse.ArgumentParser:
    """Build the small command-line interface."""
    parser = argparse.ArgumentParser(prog="python -m rs_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="inspect one image with the scripted agent"
    )
    inspect_parser.add_argument("image_path", help="path to an image file")
    inspect_raster_parser = subparsers.add_parser(
        "inspect-raster", help="inspect low-cost raster metadata with gdalinfo"
    )
    inspect_raster_parser.add_argument("raster_path", help="path to a raster file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested command and return a process exit code."""
    args = build_parser().parse_args(argv)

    if args.command == "inspect":
        task = f"Inspect image metadata and per-band statistics for {args.image_path}."
        result = run_image_inspection_agent(task, args.image_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 1

    if args.command == "inspect-raster":
        try:
            result = inspect_raster(args.raster_path)
        except RasterInspectionError as exc:
            result = {"ok": False, "error": str(exc)}
            exit_code = 1
        else:
            exit_code = 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
