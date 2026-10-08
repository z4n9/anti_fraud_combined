from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.risk_ledger.services.transaction_rules import analyze_transaction_risk


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Combine transaction ML deviations, deterministic rules and graph signals."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=2_000)
    return parser


def main() -> None:
    args = _parser().parse_args()
    scores, manifest = analyze_transaction_risk(
        args.input,
        args.artifacts,
        output_dir=args.output,
        chunk_size=args.chunk_size,
    )
    print(json.dumps({
        "rows": len(scores),
        "review_rows": int(scores["requires_review"].sum()),
        "scenario_engine_version": manifest["scenario_engine_version"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
