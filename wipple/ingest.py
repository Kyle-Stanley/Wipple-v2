"""
File-type ingestion: sniff uploads and, for spreadsheet formats, build the
raw_table DETERMINISTICALLY (no model call at all). xlsx and csv carry their
cell values natively, which beats vision extraction on both cost and
fidelity, so the extract node is skipped entirely for them.

PDFs and images go to the vision path with the correct media type.
"""

from __future__ import annotations

import csv
import io


def sniff(data: bytes, name: str = "") -> str:
    n = (name or "").lower()
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"PK\x03\x04" and (n.endswith((".xlsx", ".xlsm"))
                                      or b"xl/" in data[:4096]):
        return "xlsx"
    if n.endswith((".csv", ".txt", ".tsv")):
        return "csv"
    if n.endswith(".pdf"):
        return "application/pdf"
    return "unknown"


def _cell_str(v, fmt: str = "") -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if "%" in (fmt or ""):
            return f"{v * 100:.1f}%"
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}"
    if isinstance(v, int):
        return f"{v * 100:.1f}%" if "%" in (fmt or "") else str(v)
    return str(v).strip()


def _find_table(grid: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Header row = densest text row immediately above the numeric block."""
    def numericish(c: str) -> bool:
        t = c.replace(",", "").replace("$", "").replace("%", "")\
             .replace("(", "").replace(")", "").replace(".", "").replace("-", "")
        return bool(t) and t.isdigit()

    dense = [i for i, r in enumerate(grid)
             if sum(1 for c in r if numericish(c)) >= 4]
    if not dense:
        return [], [r for r in grid if any(c for c in r)]
    start = dense[0]
    header = []
    for i in range(start - 1, max(start - 8, -1), -1):
        nonblank = [c for c in grid[i] if c]
        if len(nonblank) >= 3 and not any(numericish(c) for c in nonblank):
            header = grid[i]
            break
    body = [r for r in grid[start:] if any(c for c in r)]
    width = max(len(r) for r in body + [header]) if body else len(header)
    pad = lambda r: list(r) + [""] * (width - len(r))
    return pad(header), [pad(r) for r in body]


def xlsx_to_raw_table(data: bytes) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    best, best_score, best_name = None, -1, ""
    for ws in wb.worksheets:
        grid = [[_cell_str(c.value, c.number_format) for c in row]
                for row in ws.iter_rows()]
        score = sum(1 for r in grid
                    if sum(1 for c in r if c and c[0].isdigit() or
                           (c.startswith("(") and len(c) > 1)) >= 4)
        if score > best_score:
            best, best_score, best_name = grid, score, ws.title
    headers, rows = _find_table(best or [])
    return {"headers": headers, "rows": rows, "page_count": 1,
            "metadata_texts": [c for row in (best or []) for c in row if c],
            "notes": [f"read directly from worksheet '{best_name}', "
                      "no model call"]}


def csv_to_raw_table(data: bytes) -> dict:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if text.strip():
        try:
            dialect = csv.Sniffer().sniff(text[:4096])
        except csv.Error:
            dialect = csv.excel
    else:
        dialect = csv.excel
    grid = [[(c or "").strip() for c in row]
            for row in csv.reader(io.StringIO(text), dialect)]
    headers, rows = _find_table(grid)
    return {"headers": headers, "rows": rows, "page_count": 1,
            "metadata_texts": [c for row in grid for c in row if c],
            "notes": ["read directly from CSV, no model call"]}
