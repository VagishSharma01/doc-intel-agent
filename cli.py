#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.pipeline import run_pipeline  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Document Intelligence Agent CLI")
    p.add_argument("path", nargs="?", type=Path, default=ROOT / "samples" / "sample_invoice.txt")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    text = args.path.read_text(encoding="utf-8")
    result = run_pipeline(args.path.name, text)
    if args.json:
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "mode": result.mode,
                    "valid": result.valid,
                    "errors": result.errors,
                    "fields": result.fields,
                    "steps": [s.__dict__ for s in result.steps],
                    "report_markdown": result.report_markdown,
                },
                indent=2,
            )
        )
    else:
        print(result.report_markdown)


if __name__ == "__main__":
    main()