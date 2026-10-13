"""Agentic document pipeline: plan → extract → validate → persist."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "runs.db"


INVOICE_SCHEMA = {
    "vendor": "string",
    "invoice_number": "string",
    "invoice_date": "YYYY-MM-DD",
    "total": "number",
    "currency": "string",
    "line_items": "list[{description, amount}]",
}


@dataclass
class Step:
    agent: str
    action: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    run_id: str
    mode: str
    filename: str
    steps: list[Step]
    fields: dict[str, Any]
    valid: bool
    errors: list[str]
    report_markdown: str


def _ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              filename TEXT,
              created_at TEXT,
              mode TEXT,
              valid INTEGER,
              fields_json TEXT,
              report TEXT
            )
            """
        )


def ocr_tool(text: str) -> dict[str, Any]:
    """Simulate OCR cleanup — normalize whitespace / fix common OCR noise."""
    cleaned = text.replace("\r\n", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return {
        "chars": len(cleaned),
        "lines": len(cleaned.splitlines()),
        "text": cleaned.strip(),
        "note": "OCR normalization (demo). Swap for Azure Document Intelligence / Tesseract in production.",
    }


def extract_fields_mock(text: str) -> dict[str, Any]:
    vendor = None
    m = re.search(r"(?:Vendor|From|Bill From)\s*[:\-]\s*(.+)", text, re.I)
    if m:
        vendor = m.group(1).strip()
    else:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
        vendor = first

    inv = None
    m = re.search(r"(?:Invoice\s*(?:#|No\.?|Number)?)\s*[:\-]?\s*([A-Za-z0-9\-]+)", text, re.I)
    if m:
        inv = m.group(1)

    date = None
    m = re.search(r"(?:Date)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.I)
    if m:
        date = m.group(1)

    total = None
    currency = "USD"
    m = re.search(r"(?:Total|Amount Due)\s*[:\-]?\s*([€$£])?\s*([0-9,]+\.?[0-9]*)", text, re.I)
    if m:
        sym = m.group(1)
        total = float(m.group(2).replace(",", ""))
        currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(sym or "$", "USD")

    items = []
    for line in text.splitlines():
        im = re.search(r"^[\-\*•]\s*(.+?)\s+([0-9]+\.?[0-9]*)$", line.strip())
        if im:
            items.append({"description": im.group(1), "amount": float(im.group(2))})

    return {
        "vendor": vendor,
        "invoice_number": inv,
        "invoice_date": date,
        "total": total,
        "currency": currency,
        "line_items": items,
        "doc_type": "invoice",
    }


def extract_fields_llm(text: str) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract invoice fields as JSON matching keys: "
                    f"{json.dumps(INVOICE_SCHEMA)}. Use null when missing."
                ),
            },
            {"role": "user", "content": text[:12000]},
        ],
    )
    return json.loads(resp.choices[0].message.content or "{}")


def validate_fields(fields: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not fields.get("vendor"):
        errors.append("missing vendor")
    if not fields.get("invoice_number"):
        errors.append("missing invoice_number")
    if fields.get("total") is None:
        errors.append("missing total")
    elif not isinstance(fields.get("total"), (int, float)):
        errors.append("total must be numeric")
    items = fields.get("line_items") or []
    if items:
        summed = sum(float(i.get("amount") or 0) for i in items)
        total = fields.get("total")
        if isinstance(total, (int, float)) and abs(summed - float(total)) > 0.05:
            errors.append(f"line_items sum {summed} != total {total}")
    return errors


def persist_run(result: PipelineResult) -> None:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
            (
                result.run_id,
                result.filename,
                datetime.now(timezone.utc).isoformat(),
                result.mode,
                1 if result.valid else 0,
                json.dumps(result.fields),
                result.report_markdown,
            ),
        )


def run_pipeline(filename: str, raw_text: str) -> PipelineResult:
    force_mock = os.getenv("FORCE_MOCK", "0") == "1"
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    mode = "mock" if force_mock or not has_key else "openai"
    run_id = str(uuid.uuid4())[:8]
    steps: list[Step] = []

    steps.append(
        Step("planner", "plan", "Detect document type and choose extraction tools", {"doc_type": "invoice"})
    )

    ocr = ocr_tool(raw_text)
    steps.append(Step("ocr", "ocr_normalize", "Clean text for extraction", {"chars": ocr["chars"], "lines": ocr["lines"]}))

    try:
        fields = extract_fields_llm(ocr["text"]) if mode == "openai" else extract_fields_mock(ocr["text"])
        if mode == "openai" and not fields:
            fields = extract_fields_mock(ocr["text"])
    except Exception as exc:  # noqa: BLE001
        mode = "mock-fallback"
        fields = extract_fields_mock(ocr["text"])
        steps.append(Step("extractor", "fallback", f"LLM extract failed: {exc}", {}))

    steps.append(Step("extractor", "extract_fields", "Structured field extraction", fields))

    errors = validate_fields(fields)
    valid = not errors
    steps.append(
        Step(
            "validator",
            "validate_schema",
            "Check required fields and line-item totals",
            {"valid": valid, "errors": errors},
        )
    )

    report = f"""# Document intelligence run `{run_id}`

**File:** `{filename}`  
**Mode:** `{mode}`  
**Valid:** {'yes' if valid else 'no'}

## Extracted fields
```json
{json.dumps(fields, indent=2)}
```

## Validation
{('- ' + '\n- '.join(errors)) if errors else 'All checks passed.'}

## Agent path
1. Planner → invoice schema  
2. OCR normalize  
3. Extractor ({'LLM' if mode == 'openai' else 'rules'})  
4. Validator  
5. Persist run to SQLite
"""

    result = PipelineResult(
        run_id=run_id,
        mode=mode,
        filename=filename,
        steps=steps,
        fields=fields,
        valid=valid,
        errors=errors,
        report_markdown=report,
    )
    steps.append(Step("memory", "persist", f"Store run in {DB_PATH.name}", {"run_id": run_id}))
    persist_run(result)
    return result
