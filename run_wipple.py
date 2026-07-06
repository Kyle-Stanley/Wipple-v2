#!/usr/bin/env python3
"""CLI runner: python run_wipple.py <document.(pdf|png|jpg|xlsx|csv)>

Needs GOOGLE_API_KEY (and/or ANTHROPIC_API_KEY if you point a tier at
Claude) for vision documents; spreadsheets run modelless. Prints the full
report JSON, then a per-section verdict summary and the cost line.
"""
import json
import sys
from pathlib import Path

from wipple.docgraph import run_document

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    p = Path(sys.argv[1])
    report, metrics = run_document(p.read_bytes(), source_name=p.name)
    print(json.dumps(report, indent=2, default=str))

    err = sys.stderr
    for ti, t in enumerate(report.get("tables") or []):
        for s in t.get("sections") or []:
            r = s.get("report", {})
            pages = ",".join(map(str, s.get("pages") or []))
            print(f"-- table {ti} [{s['type']}] pages {pages}: "
                  f"{r.get('overall_status')} "
                  f"({len(r.get('witnesses') or [])} identities, "
                  f"{len(r.get('findings') or [])} findings)", file=err)
        for mf in t.get("misalignment_findings") or []:
            print(f"--   block misalignment on pages "
                  f"{mf.get('pages')} (repaired)", file=err)
    doc = report.get("document") or {}
    disc = (doc.get("concordance") or {}).get("discordant") or []
    for d in disc:
        print(f"--   header '{d.get('header')}' discordant with certified "
              f"{d.get('variable')}", file=err)
    print(f"\n-- {metrics['api_calls']} call(s), "
          f"${metrics['cost_usd']:.6f}", file=err)
