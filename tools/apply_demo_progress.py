from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match in {path}, found {text.count(old)}")
    p.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "server.py",
    '''        n = len(up.get("chunks") or [])
        if n:
            kind = state.get("media_type", "")
            unit = "strip" if str(kind).startswith("image/") else "page"
            return [f"Document split into {_plural(n, unit)}"]
        return []
''',
    '''        # Chunking pages or image strips is an internal implementation
        # detail. Stay quiet until extraction produces a meaningful result.
        return []
''',
)

replace_once(
    "static/index.html",
    '''async function readStream(url,opts,onProgress=addLine){
  const r=await fetch(url,opts);
''',
    '''async function readStream(url,opts,onProgress=addLine){
  const startedAt=performance.now();
  const r=await fetch(url,opts);
''',
)

replace_once(
    "static/index.html",
    '''  if(!report)throw new Error("Processing ended without a report.");
  return report;
}
''',
    '''  if(!report)throw new Error("Processing ended without a report.");
  // The server supplies authoritative processing time for normal model-backed
  // runs. Direct CSV/spreadsheet uploads can finish without model metrics, so
  // use the browser wall clock only when the backend did not provide one.
  report.metrics=report.metrics||{};
  if(report.metrics.elapsed_seconds==null)
    report.metrics.elapsed_seconds=Math.round((performance.now()-startedAt)/100)/10;
  return report;
}
''',
)

test = Path("tests/test_demo_progress_ui.py")
test.write_text(
    '''from pathlib import Path
import re


def test_chunking_is_not_exposed_as_user_progress():
    server = Path("server.py").read_text(encoding="utf-8")
    assert "Document split into" not in server
    assert 'report["metrics"]["elapsed_seconds"]' in server


def test_direct_uploads_receive_client_elapsed_fallback():
    html = Path("static/index.html").read_text(encoding="utf-8")
    match = re.search(
        r"async function readStream\\(url,opts,onProgress=addLine\\)\\{(?P<body>.*?)\\n\\}",
        html,
        re.S,
    )
    assert match is not None
    body = match.group("body")
    assert "const startedAt=performance.now();" in body
    assert "if(report.metrics.elapsed_seconds==null)" in body
    assert "performance.now()-startedAt" in body
''',
    encoding="utf-8",
)

for path in (
    ".github/workflows/inspect-upload-timing.yml",
    ".ui-inspect-trigger",
    ".ui-inspect.txt",
    ".github/workflows/apply-demo-progress.yml",
    "tools/apply_demo_progress.py",
):
    Path(path).unlink(missing_ok=True)
