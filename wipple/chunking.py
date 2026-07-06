"""
Physical chunking: document bytes -> chunks the extractor perceives one at a
time. After extraction, "page" ceases to be a processing unit and survives
only as provenance.

PDFs: 1 page = 1 chunk. Pages are natural boundaries (rows don't straddle
them) and a vision pass handles one page comfortably, so the mapping is
trivial rather than meaningless.

Images: overlapping horizontal strips -- pixel boundaries are arbitrary (a
row can be sliced mid-glyph), so consecutive strips share OVERLAP_PX and the
stitcher dedupes on the shared rows. The overlap is also a free witness: the
same physical rows extracted twice must match verbatim, and a disagreement
is direct evidence about extraction quality, for zero extra model calls.
"""

from __future__ import annotations

import io

STRIP_PX = 1600
OVERLAP_PX = 240
MIN_TAIL_PX = 300     # a final sliver merges into the previous strip


def chunk_pdf(data: bytes) -> list:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(data))
    chunks = []
    for i, page in enumerate(reader.pages):
        w = PdfWriter()
        w.add_page(page)
        buf = io.BytesIO()
        w.write(buf)
        chunks.append({"chunk_id": i, "pages": [i + 1],
                       "bytes": buf.getvalue(),
                       "media_type": "application/pdf"})
    return chunks


def chunk_image(data: bytes, media_type: str) -> list:
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    W, H = img.size
    if H <= STRIP_PX + MIN_TAIL_PX:
        return [{"chunk_id": 0, "pages": [1], "bytes": data,
                 "media_type": media_type, "strip": (0, H)}]
    chunks, top, i = [], 0, 0
    while top < H:
        bottom = min(top + STRIP_PX, H)
        if H - bottom < MIN_TAIL_PX:
            bottom = H
        buf = io.BytesIO()
        img.crop((0, top, W, bottom)).save(buf, format="PNG")
        chunks.append({"chunk_id": i, "pages": [i + 1],
                       "bytes": buf.getvalue(), "media_type": "image/png",
                       "strip": (top, bottom), "overlaps_prev": top > 0})
        if bottom >= H:
            break
        top = bottom - OVERLAP_PX
        i += 1
    return chunks


def chunk_document(data: bytes, media_type: str) -> list:
    if media_type == "application/pdf":
        return chunk_pdf(data)
    if media_type.startswith("image/"):
        return chunk_image(data, media_type)
    return [{"chunk_id": 0, "pages": [1], "bytes": data,
             "media_type": media_type}]
