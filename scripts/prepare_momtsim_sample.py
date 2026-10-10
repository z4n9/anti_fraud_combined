"""MoMTSim Sample Preparation Script.

Extracts a demonstration sample with quotas by step and transaction type
from MoMTSim_20240722202413_1000_dataset.csv (4,225,958 rows) to fit within
Risk Ledger's 1,000,000 row ingestion limit.

Transforms the abstract simulation `step` column into standard ISO 8601 UTC timestamps:
- Assumption for this demo: 1 step = 1 hour; not verified against MoMTSim documentation.
- Artificial base timestamp: 2024-07-22 00:00:00 UTC.
- Artificial intra-hour timestamps change velocity features; not evidence of fraud accuracy.

Outputs:
1. MoMTSim_sample_prepared.csv: Canonical and analyst-optimized CSV matching Risk Ledger schema.
2. MoMTSim_sample_original_schema.csv: Exact MoMTSim 10-column schema with step converted to timestamp.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_DATETIME = datetime(2024, 7, 22, 0, 0, 0, tzinfo=timezone.utc)
TARGET_SAMPLE_SIZE = 100_000
RANDOM_SEED = 42


def prepare_sample(
    input_path: str | Path,
    output_prepared_path: str | Path,
    output_original_schema_path: str | Path | None = None,
    target_size: int = TARGET_SAMPLE_SIZE,
    random_seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    if target_size <= 0:
        raise ValueError("Sample size must be positive")
    paths = [input_file.resolve(), Path(output_prepared_path).resolve()]
    if output_original_schema_path:
        paths.append(Path(output_original_schema_path).resolve())
    if len(set(paths)) != len(paths):
        raise ValueError("Input and output files must have distinct paths")

    print(f"Reading and sampling from {input_file.name}...")
    np.random.seed(random_seed)

    # First pass: determine sampling rates per step and collect sampled indices/data
    chunksize = 250_000
    step_groups: dict[int, list[pd.DataFrame]] = {}

    total_input_rows = 0
    for chunk in pd.read_csv(input_file, chunksize=chunksize):
        chunk = chunk[chunk["amount"] > 0]
        total_input_rows += len(chunk)
        for step, group in chunk.groupby("step"):
            step_groups.setdefault(step, []).append(group)
        print(f"  Processed {total_input_rows:,} rows...", end="\r", flush=True)
    print(f"\nCompleted initial scan: {total_input_rows:,} rows across {len(step_groups)} steps.")

    num_steps = len(step_groups)
    if not num_steps:
        raise ValueError("No positive-amount transactions with a step were found")
    base_per_step = target_size // num_steps
    remainder = target_size % num_steps

    all_sampled: list[pd.DataFrame] = []

    for idx, step in enumerate(sorted(step_groups.keys())):
        step_df = pd.concat(step_groups[step], ignore_index=True)
        quota = base_per_step + (1 if idx < remainder else 0)

        # Stratified sampling by isFraud and transactionType within the step
        if len(step_df) <= quota:
            sampled_step = step_df.copy()
        else:
            # Preserve all rare transaction types (DEPOSIT, WITHDRAWAL, DEBIT) if available
            rare_mask = step_df["transactionType"].isin(["DEPOSIT", "WITHDRAWAL", "DEBIT"])
            rare_df = step_df[rare_mask]
            common_df = step_df[~rare_mask]

            if len(rare_df) >= quota:
                sampled_step = step_df.sample(n=quota, random_state=random_seed + step)
            else:
                remaining_quota = quota - len(rare_df)
                # Stratify common by isFraud
                fraud_ratio = common_df["isFraud"].mean()
                fraud_quota = int(round(remaining_quota * fraud_ratio))
                legit_quota = remaining_quota - fraud_quota

                fraud_df = common_df[common_df["isFraud"] == 1]
                legit_df = common_df[common_df["isFraud"] == 0]

                n_fraud = min(len(fraud_df), fraud_quota)
                n_legit = min(len(legit_df), legit_quota)

                # If one group is exhausted, top up from the other
                if n_fraud < fraud_quota:
                    n_legit = min(len(legit_df), remaining_quota - n_fraud)
                elif n_legit < legit_quota:
                    n_fraud = min(len(fraud_df), remaining_quota - n_legit)

                sampled_parts = [rare_df]
                if n_fraud > 0:
                    sampled_parts.append(fraud_df.sample(n=n_fraud, random_state=random_seed + step))
                if n_legit > 0:
                    sampled_parts.append(legit_df.sample(n=n_legit, random_state=random_seed + step))

                sampled_step = pd.concat(sampled_parts, ignore_index=True)

        # Sort sampled rows to maintain chronological sequence within step
        sampled_step = sampled_step.sort_index().reset_index(drop=True)
        k_step = len(sampled_step)

        # Generate timestamps for this step:
        # 1 step = 1 hour, distributed evenly over 3600 seconds
        timestamps = []
        for j in range(k_step):
            sec_offset = int(j * 3600 / max(k_step, 1))
            t = BASE_DATETIME + timedelta(hours=int(step), seconds=sec_offset)
            timestamps.append(t.strftime("%Y-%m-%dT%H:%M:%SZ"))

        sampled_step["transaction_timestamp"] = timestamps
        all_sampled.append(sampled_step)

    # Free memory
    del step_groups

    final_df = pd.concat(all_sampled, ignore_index=True)
    print(f"Sample generated: {len(final_df):,} rows.")

    # Generate canonical columns
    final_df["transaction_id"] = [f"TX-{i + 1:07d}" for i in range(len(final_df))]
    final_df["client_id"] = [f"CLI-{x}" for x in final_df["initiator"]]
    final_df["sender_account_id"] = [f"ACC-{x}" for x in final_df["initiator"]]
    final_df["recipient_account_id"] = [f"ACC-{x}" for x in final_df["recipient"]]
    final_df["transaction_amount"] = final_df["amount"]
    final_df["balance_before"] = final_df["oldBalInitiator"]
    final_df["channel"] = final_df["transactionType"]
    final_df["transaction_fraud_label"] = final_df["isFraud"].astype(int)

    # Order of columns for prepared CSV (canonical, zero-ambiguity):
    prepared_columns = [
        "transaction_id",
        "transaction_timestamp",
        "client_id",
        "sender_account_id",
        "recipient_account_id",
        "transaction_amount",
        "balance_before",
        "channel",
        "transaction_fraud_label",
    ]

    out_prepared = Path(output_prepared_path)
    out_prepared.parent.mkdir(parents=True, exist_ok=True)
    final_df[prepared_columns].to_csv(out_prepared, index=False, encoding="utf-8")
    print(f"Saved canonical prepared sample to: {out_prepared} ({out_prepared.stat().st_size:,} bytes)")

    if output_original_schema_path:
        out_orig = Path(output_original_schema_path)
        out_orig.parent.mkdir(parents=True, exist_ok=True)
        orig_columns = [
            "transaction_timestamp",
            "transactionType",
            "amount",
            "initiator",
            "oldBalInitiator",
            "newBalInitiator",
            "recipient",
            "oldBalRecipient",
            "newBalRecipient",
            "isFraud",
        ]
        final_df[orig_columns].to_csv(out_orig, index=False, encoding="utf-8")
        print(f"Saved original schema sample to: {out_orig} ({out_orig.stat().st_size:,} bytes)")

    stats = {
        "total_rows": len(final_df),
        "steps_count": int(final_df["step"].nunique()),
        "min_step": int(final_df["step"].min()),
        "max_step": int(final_df["step"].max()),
        "fraud_count": int((final_df["isFraud"] == 1).sum()),
        "fraud_ratio": float(final_df["isFraud"].mean()),
        "transaction_types": final_df["transactionType"].value_counts().to_dict(),
        "start_timestamp": str(final_df["transaction_timestamp"].iloc[0]),
        "end_timestamp": str(final_df["transaction_timestamp"].iloc[-1]),
    }
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare MoMTSim sample for Risk Ledger ingestion.")
    parser.add_argument("--input", default="data/source-datasets/MoMTSim_20240722202413_1000_dataset.csv", help="Input dataset path")
    parser.add_argument("--output-prepared", default="examples/MoMTSim_sample_prepared.csv", help="Output prepared path")
    parser.add_argument("--output-orig", default="data/source-datasets/MoMTSim_sample_original_schema.csv", help="Output alternate schema path")
    parser.add_argument("--sample-size", type=int, default=TARGET_SAMPLE_SIZE, help="Target sample row count")
    args = parser.parse_args()

    stats = prepare_sample(
        input_path=args.input,
        output_prepared_path=args.output_prepared,
        output_original_schema_path=args.output_orig,
        target_size=args.sample_size,
    )
    print("\nSample Statistics Summary:")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
