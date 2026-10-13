"""FastAPI — Document Intelligence Agent."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.pipeline import run_pipeline

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"

app = FastAPI(
    title="Document Intelligence Agent",
    description="Agentic OCR → extract → validate → persist pipeline for invoices/docs.",
    version="1.0.0",
)


@app.get("/", response_class=HTMLResponse)
async def home():
    mode = "mock" if os.getenv("FORCE_MOCK") == "1" or not os.getenv("OPENAI_API_KEY") else "openai"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Document Intelligence Agent</title>
<style>
:root{{--void:#090d10;--panel:#131921;--mist:#e1e6ef;--muted:#929db0;--line:#29313b;--signal:#53e2cb;--ember:#e5b477}}
body{{margin:0;font-family:IBM Plex Sans,Segoe UI,sans-serif;background:var(--void);color:var(--mist);line-height:1.6}}
.shell{{width:min(860px,calc(100% - 2rem));margin:0 auto;padding:2.5rem 0 4rem}}
.kicker{{font-family:ui-monospace,monospace;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--signal)}}
.lead{{color:var(--muted);max-width:58ch}} .mode{{color:var(--ember);font-family:ui-monospace,monospace;font-size:12px}}
.panel{{margin-top:1.2rem;padding:1.1rem;border:1px solid var(--line);border-radius:4px;background:var(--panel)}}
textarea,input{{width:100%;background:#0c1014;border:1px solid var(--line);border-radius:4px;color:var(--mist);padding:.7rem;font:inherit}}
button{{margin-top:.7rem;border:1px solid var(--signal);background:var(--signal);color:#06241d;font-weight:600;border-radius:4px;padding:.65rem 1rem;cursor:pointer}}
pre{{white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:13px;color:#c7d0de}}
a{{color:var(--signal)}}
</style></head>
<body><div class="shell">
<p class="kicker">Agentic · OCR · Extraction · Validation · Memory</p>
<h1>Document Intelligence Agent</h1>
<p class="lead">Upload invoice text (or use the sample). The agent plans extraction, normalizes OCR text, pulls structured fields, validates them, and stores the run.</p>
<p class="mode">Runtime mode: <strong>{mode}</strong></p>
<form class="panel" id="form">
<label>Paste document text</label>
<textarea id="text" rows="12" placeholder="Paste invoice text…"></textarea>
<label style="display:block;margin-top:.7rem">Or upload a .txt file</label>
<input type="file" id="file" accept=".txt,.md,.csv" />
<button type="submit">Run pipeline</button>
<button type="button" id="sample">Load sample invoice</button>
</form>
<section class="panel"><h2>Result</h2><pre id="out">Waiting…</pre></section>
<p><a href="/docs">API docs</a> · <a href="https://github.com/VagishSharma01/doc-intel-agent">GitHub</a></p>
</div>
<script>
const out = document.getElementById('out');
document.getElementById('sample').onclick = async () => {{
  const r = await fetch('/api/sample');
  const d = await r.json();
  document.getElementById('text').value = d.text;
}};
document.getElementById('form').onsubmit = async (e) => {{
  e.preventDefault();
  out.textContent = 'Agent running…';
  const fd = new FormData();
  const file = document.getElementById('file').files[0];
  if (file) {{ fd.append('file', file); }}
  fd.append('text', document.getElementById('text').value);
  const r = await fetch('/api/extract', {{ method:'POST', body: fd }});
  const d = await r.json();
  out.textContent = d.report_markdown || JSON.stringify(d, null, 2);
}};
</script></body></html>"""
    )


@app.get("/health")
async def health():
    return {
        "ok": True,
        "mode": "mock" if os.getenv("FORCE_MOCK") == "1" or not os.getenv("OPENAI_API_KEY") else "openai",
    }


@app.get("/api/sample")
async def sample():
    path = SAMPLES / "sample_invoice.txt"
    return {"filename": path.name, "text": path.read_text(encoding="utf-8")}


@app.post("/api/extract")
async def extract(text: str = Form(default=""), file: UploadFile | None = File(default=None)):
    filename = "pasted.txt"
    content = text or ""
    if file is not None and file.filename:
        filename = file.filename
        raw = await file.read()
        content = raw.decode("utf-8", errors="replace")
    if not content.strip():
        return JSONResponse({"error": "empty document"}, status_code=400)
    result = run_pipeline(filename, content)
    return {
        "run_id": result.run_id,
        "mode": result.mode,
        "filename": result.filename,
        "valid": result.valid,
        "errors": result.errors,
        "fields": result.fields,
        "steps": [s.__dict__ for s in result.steps],
        "report_markdown": result.report_markdown,
    }
