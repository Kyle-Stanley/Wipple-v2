from pathlib import Path

from tests.test_cleanup_payload import test_build_repository_cleanup_payload


def test_emit_cleanup_payload() -> None:
    test_build_repository_cleanup_payload()
    raise AssertionError(Path("cleanup_payload.txt").read_text(encoding="utf-8"))
