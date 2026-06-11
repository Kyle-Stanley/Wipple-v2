#!/usr/bin/env python3
"""CLI runner: python run_wipple.py <wip.pdf>

Needs GOOGLE_API_KEY (and/or ANTHROPIC_API_KEY if you point a tier at
Claude). Prints the report JSON and a per-call cost breakdown.
"""
import json
import sys
from pathlib import Path

from wipple.graph import run_pipeline

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    p = Path(sys.argv[1])
    report, metrics = run_pipeline(p.read_bytes(), source_name=p.name)
    print(json.dumps(report, indent=2, default=str))
    print(f"\n-- {metrics['api_calls']} call(s), "
          f"${metrics['cost_usd']:.6f}", file=sys.stderr)
