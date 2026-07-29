"""Production adapter for the WIP2 validator.

The legacy validator remains importable from :mod:`wipple.accounting.wip` for
A/B comparisons. The pipeline calls this adapter instead. Exact all-zero
columns are omitted before WIP2 search because they carry no identifying
information and can otherwise audition for clipped or percentage variables.
Every public column reference is then translated back to the original table
coordinates.
"""

from __future__ import annotations

import re
from typing import Optional

import numpy as np

from .wip import (
    FAILED,
    SUCCESS,
    Config,
    ValidationResult,
    _ingest,
    validate_wip as _validate_wip1,
)
from .wip2 import validate_wip as _validate_wip2


def _is_all_zero(column: np.ndarray) -> bool:
    """Whether a physical column is finite and exactly zero on every row."""
    values = np.asarray(column, dtype=float)
    return bool(
        values.size
        and np.all(np.isfinite(values))
        and np.all(values == 0.0)
    )


def _original_column(column: Optional[int], kept: tuple[int, ...]) -> Optional[int]:
    if column is None:
        return None
    index = int(column)
    if 0 <= index < len(kept):
        return int(kept[index])
    return index


def _remap_mapping(mapping: Optional[dict], kept: tuple[int, ...]):
    if mapping is None:
        return None
    return {
        int(_original_column(column, kept)): variable
        for column, variable in mapping.items()
    }


def _remap_orientation(text: str, kept: tuple[int, ...]) -> str:
    if not text:
        return text

    def replace(match: re.Match) -> str:
        return f"col {_original_column(int(match.group(1)), kept)}"

    return re.sub(r"\bcol\s+(\d+)\b", replace, text)


def _remap_diagnostics(diagnostics: dict, kept: tuple[int, ...]) -> dict:
    """Remap the stable column-bearing diagnostic fields exposed by WIP2."""
    remapped = dict(diagnostics)

    for key in ("shadow_columns_promoted", "uncertified_best_mapping"):
        value = remapped.get(key)
        if isinstance(value, dict):
            remapped[key] = _remap_mapping(value, kept)

    for key in (
        "incomplete_full_document_checks",
        "incomplete_excluded_row_checks",
        "excluded_row_certification",
    ):
        value = remapped.get(key)
        if not isinstance(value, list):
            continue
        items = []
        for item in value:
            if not isinstance(item, dict) or "column" not in item:
                items.append(item)
                continue
            copied = dict(item)
            copied["column"] = _original_column(copied.get("column"), kept)
            items.append(copied)
        remapped[key] = items

    return remapped


def _restore_original_columns(
    result: ValidationResult,
    kept: tuple[int, ...],
    ignored: tuple[int, ...],
) -> ValidationResult:
    result.mapping = _remap_mapping(result.mapping, kept) or {}
    result.mapping_named = _remap_mapping(result.mapping_named, kept) or {}
    result.competing_mapping = _remap_mapping(result.competing_mapping, kept)
    result.estimate_orientation = _remap_orientation(
        result.estimate_orientation, kept)

    for witness in result.witnesses:
        witness.column = _original_column(witness.column, kept)

    for failure in result.failures:
        failure.column = _original_column(failure.column, kept)

    for finding in result.findings:
        finding.culprit_column = _original_column(
            finding.culprit_column, kept)
        finding.transplant_sources = [
            (int(row), int(_original_column(column, kept)))
            for row, column in finding.transplant_sources
        ]

    diagnostics = _remap_diagnostics(result.diagnostics, kept)
    diagnostics["pipeline_validator"] = "wip2"
    diagnostics["ignored_all_zero_columns"] = [int(c) for c in ignored]
    result.diagnostics = diagnostics
    return result


def _run_validator(physical_columns, labels, config):
    """Run WIP2 first and retain legacy only as an insufficient-case safety net."""
    rebuilt = _validate_wip2(
        physical_columns, job_labels=labels, config=config)
    if rebuilt.status in {SUCCESS, FAILED}:
        rebuilt.diagnostics = {
            **rebuilt.diagnostics,
            "pipeline_validator": "wip2",
            "validator_result_source": "wip2",
        }
        return rebuilt

    legacy = _validate_wip1(
        physical_columns, job_labels=labels, config=config)
    if legacy.status in {SUCCESS, FAILED}:
        legacy.diagnostics = {
            **legacy.diagnostics,
            "pipeline_validator": "wip2",
            "validator_result_source": "legacy_insufficient_fallback",
            "wip2_primary_status": rebuilt.status,
            "wip2_primary_reason": rebuilt.reason,
            "wip2_primary_mapping": {
                int(column): variable
                for column, variable in rebuilt.mapping.items()
            },
        }
        return legacy

    rebuilt.diagnostics = {
        **rebuilt.diagnostics,
        "pipeline_validator": "wip2",
        "validator_result_source": "wip2",
        "legacy_insufficient_fallback_status": legacy.status,
    }
    return rebuilt


def validate_wip(columns, job_labels=None,
                 config: Optional[Config] = None) -> ValidationResult:
    """Run WIP2 for the production pipeline with inert-column protection.

    Only columns that are finite and exactly zero across every row are removed.
    Signed zero is treated as zero. Sparse, clipped, or mostly-zero columns are
    retained because they may contain real under/overbilling evidence.
    """
    physical_columns, labels = _ingest(columns, job_labels)
    ignored = tuple(
        index for index, column in enumerate(physical_columns)
        if _is_all_zero(column)
    )

    if not ignored:
        result = _run_validator(physical_columns, labels, config)
        result.diagnostics = {
            **result.diagnostics,
            "ignored_all_zero_columns": [],
        }
        return result

    ignored_set = set(ignored)
    kept = tuple(
        index for index in range(len(physical_columns))
        if index not in ignored_set
    )
    filtered = [physical_columns[index] for index in kept]
    result = _run_validator(filtered, labels, config)
    return _restore_original_columns(result, kept, ignored)
