from pathlib import Path
import re


def test_chunking_is_not_exposed_as_user_progress():
    server = Path("server.py").read_text(encoding="utf-8")
    assert "Document split into" not in server
    assert 'report["metrics"]["elapsed_seconds"]' in server


def test_direct_uploads_receive_client_elapsed_fallback():
    html = Path("static/index.html").read_text(encoding="utf-8")
    match = re.search(
        r"async function readStream\(url,opts,onProgress=addLine\)\{(?P<body>.*?)\n\}",
        html,
        re.S,
    )
    assert match is not None
    body = match.group("body")
    assert "const startedAt=performance.now();" in body
    assert "if(report.metrics.elapsed_seconds==null)" in body
    assert "performance.now()-startedAt" in body
