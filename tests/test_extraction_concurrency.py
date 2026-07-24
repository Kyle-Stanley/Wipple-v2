"""Concurrent page reads must not change what a document reads as.

Pages are transcribed in parallel, so the guarantees worth pinning are the
ones parallelism could plausibly break: fragment and attempt order follow the
document rather than whichever page answered first, one page's failure stays
its own, and a re-extraction still touches only the pending pages.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

import wipple.documents.extraction as extraction
from wipple.core.model_client import Metrics


def chunks(n: int) -> list[dict]:
    return [{"chunk_id": i, "pages": [i + 1], "bytes": f"page{i}".encode(),
             "media_type": "application/pdf"} for i in range(n)]


def state(chs: list[dict], **kw) -> dict:
    base = {"chunks": chs, "fragments": [], "extraction_tier": "primary",
            "extraction_attempts": [], "reporting_period_texts": [],
            "media_type": "application/pdf", "_metrics": Metrics()}
    base.update(kw)
    return base


def page_payload(chunk_id: int) -> str:
    return json.dumps({
        "reporting_period_text": None,
        "tables": [{"title_text": None, "headers": ["Job #", "Contract"],
                    "rows": [[f"J-{chunk_id}", "1,000"]]}],
    })


class FakeClient:
    """generate() body is supplied per test; signature matches the real one."""

    def __init__(self, body):
        self._body = body

    def generate(self, _prompt, **kwargs):
        # purpose carries the chunk id: extract[chunk=3,primary]
        purpose = kwargs.get("purpose", "")
        chunk_id = int(purpose.split("chunk=")[1].split(",")[0])
        return self._body(chunk_id)


def install(monkeypatch, body) -> None:
    monkeypatch.setattr(extraction, "get_client", lambda: FakeClient(body))


def test_pages_of_one_document_are_read_concurrently(monkeypatch):
    # Every page blocks until all four have started. A serial reader can never
    # release this barrier, so a regression to sequential reads fails here.
    barrier = threading.Barrier(4, timeout=15)

    def body(chunk_id):
        barrier.wait()
        return page_payload(chunk_id)

    install(monkeypatch, body)
    out = extraction.extract_chunks_node(state(chunks(4)))

    assert out["failed_chunks"] == []
    assert len(out["fragments"]) == 4


def test_results_follow_document_order_not_completion_order(monkeypatch):
    # Page 0 answers last, page 3 first. Assembly sorts on page then fragment
    # ordinal, so completion order must not leak into the fragment list.
    def body(chunk_id):
        time.sleep(0.05 * (4 - chunk_id))
        return page_payload(chunk_id)

    install(monkeypatch, body)
    out = extraction.extract_chunks_node(state(chunks(4)))

    assert [f["chunk_id"] for f in out["fragments"]] == [0, 1, 2, 3]
    assert [f["rows"][0][0] for f in out["fragments"]] == [
        "J-0", "J-1", "J-2", "J-3"]
    assert [a["chunk"] for a in out["extraction_attempts"]] == [0, 1, 2, 3]


def test_one_unreadable_page_does_not_take_down_the_others(monkeypatch):
    def body(chunk_id):
        if chunk_id == 2:
            raise RuntimeError("vision call exploded")
        return page_payload(chunk_id)

    install(monkeypatch, body)
    out = extraction.extract_chunks_node(state(chunks(4)))

    assert out["failed_chunks"] == [2]
    assert [f["chunk_id"] for f in out["fragments"]] == [0, 1, 3]
    attempts = out["extraction_attempts"]
    assert [a["chunk"] for a in attempts] == [0, 1, 2, 3]
    assert [a["ok"] for a in attempts] == [True, True, False, True]
    assert "vision call exploded" in attempts[2]["error"]


def test_unparseable_page_output_is_a_page_failure(monkeypatch):
    install(monkeypatch, lambda cid: "sorry, no table here"
            if cid == 1 else page_payload(cid))
    out = extraction.extract_chunks_node(state(chunks(3)))

    assert out["failed_chunks"] == [1]
    assert [f["chunk_id"] for f in out["fragments"]] == [0, 2]


def test_re_extraction_reads_only_pending_pages(monkeypatch):
    read = []
    lock = threading.Lock()

    def body(chunk_id):
        with lock:
            read.append(chunk_id)
        return page_payload(chunk_id)

    install(monkeypatch, body)
    survivors = [{"chunk_id": 0, "pages": [1], "table_index": 0,
                  "title_text": None, "headers": ["Job #"], "rows": [["J-0"]],
                  "overlaps_prev": False}]
    out = extraction.extract_chunks_node(state(
        chunks(3), fragments=survivors, bad_chunks=[2],
        extraction_tier="escalated",
        reporting_period_texts=[{"chunk_id": 0, "pages": [1], "text": "kept"}],
    ))

    assert sorted(read) == [2]
    assert [f["chunk_id"] for f in out["fragments"]] == [0, 2]
    assert out["reporting_period_texts"] == [
        {"chunk_id": 0, "pages": [1], "text": "kept"}]
    assert out["bad_chunks"] is None


def test_progress_counts_every_page_exactly_once(monkeypatch):
    install(monkeypatch, page_payload)
    lines = []
    lock = threading.Lock()

    def progress(message):
        with lock:
            lines.append(message)

    extraction.extract_chunks_node(state(chunks(5), _progress=progress))

    assert lines == [f"Read {i} of 5 pages" for i in range(1, 6)]


def test_single_page_document_stays_quiet_and_serial(monkeypatch):
    install(monkeypatch, page_payload)
    lines = []
    out = extraction.extract_chunks_node(
        state(chunks(1), _progress=lines.append))

    assert lines == []
    assert [f["chunk_id"] for f in out["fragments"]] == [0]


def test_image_strips_are_narrated_as_strips(monkeypatch):
    install(monkeypatch, page_payload)
    lines = []
    extraction.extract_chunks_node(state(
        chunks(2), media_type="image/png", _progress=lines.append))

    assert lines == ["Read 1 of 2 strips", "Read 2 of 2 strips"]


@pytest.mark.parametrize("workers", [1, 4])
def test_serial_and_parallel_reads_agree(monkeypatch, workers):
    monkeypatch.setattr(extraction, "EXTRACT_CONCURRENCY", workers)
    install(monkeypatch, page_payload)
    out = extraction.extract_chunks_node(state(chunks(4)))

    assert [f["chunk_id"] for f in out["fragments"]] == [0, 1, 2, 3]
    assert [a["chunk"] for a in out["extraction_attempts"]] == [0, 1, 2, 3]
