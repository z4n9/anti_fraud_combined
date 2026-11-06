"""Offline, reproducible rebasing of SynSEPA monetary features to KZT.

This is a change of numerical units, not retraining or KZT fraud validation.
The fixed factor preserves the former default model scale; it is not an FX rate.
Run from backend: python -m app.ml.adapt_units --source <antifraud-runtime>
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

UNIT_SCALE = 500
UNIT_VERSION = "kzt-native-v1"
MODEL_ID = "synsepa_s0_clean_kzt"
MODEL_VERSION = "1.1-kzt-units"
MONETARY_FEATURES = frozenset({"amount", "sender_avg_amount_7d", "sender_avg_amount_30d",
                                "amount_sum_1h", "amount_sum_24h"})
LIGHTGBM_INFINITY = 1e300  # Internal missing/infinity sentinel, not a monetary boundary.


def rebase_model_text(source_text):
    """Scale only monetary numeric thresholds/ranges and rebuild LF tree sizes."""
    text = source_text.replace("\r\n", "\n").replace("\r", "\n")
    names = re.search(r"(?m)^feature_names=(.*)$", text).group(1).split()
    indexes = {index for index, name in enumerate(names) if name in MONETARY_FEATURES}
    if len(indexes) != len(MONETARY_FEATURES):
        raise ValueError("Unexpected source monetary feature set")
    starts = [match.start() for match in re.finditer(r"(?m)^Tree=\d+\n", text)]
    end = text.index("end of trees\n", starts[-1])
    header, footer = text[:starts[0]], text[end:]
    infos = re.search(r"(?m)^feature_infos=(.*)$", header).group(1).split()
    if len(infos) != len(names):
        raise ValueError("Source feature ranges do not match feature names")
    for index in indexes:
        token = infos[index]
        if not (token.startswith("[") and token.endswith("]")):
            raise ValueError("Monetary features must be numeric")
        low, high = token[1:-1].split(":")
        infos[index] = "[" + format(float(low) * UNIT_SCALE, ".17g") + ":" + format(float(high) * UNIT_SCALE, ".17g") + "]"
    header = re.sub(r"(?m)^feature_infos=.*$", "feature_infos=" + " ".join(infos), header)
    trees = []
    for start, stop in zip(starts, starts[1:] + [end]):
        block = text[start:stop]
        split = re.search(r"(?m)^split_feature=(.*)$", block).group(1).split()
        thresholds = re.search(r"(?m)^threshold=(.*)$", block).group(1).split()
        types = re.search(r"(?m)^decision_type=(.*)$", block).group(1).split()
        if not len(split) == len(thresholds) == len(types):
            raise ValueError("Malformed source tree")
        for i, feature in enumerate(split):
            if int(feature) in indexes:
                if int(types[i]) & 1:
                    raise ValueError("Categorical monetary split is unsupported")
                value = float(thresholds[i])
                # LightGBM clamps this sentinel when parsing/serializing. It
                # represents infinity/missing-value routing, not a money amount.
                thresholds[i] = format(value if abs(value) >= LIGHTGBM_INFINITY else value * UNIT_SCALE, ".17g")
        block = re.sub(r"(?m)^threshold=.*$", "threshold=" + " ".join(thresholds), block)
        trees.append(block)
    header = re.sub(r"(?m)^tree_sizes=.*$", "tree_sizes=" + " ".join(str(len(t.encode("utf-8"))) for t in trees), header)
    return header + "".join(trees) + footer


def rebase_ood(source):
    result = deepcopy(source)
    for name, bounds in result["features"].items():
        if name in MONETARY_FEATURES:
            for statistic in ("min", "max", "p001", "p999"):
                if statistic in bounds:
                    bounds[statistic] *= UNIT_SCALE
    result.update(currency="KZT", unit_version=UNIT_VERSION,
                  validation_note="Numerical unit adaptation only; not validated on KZT bank data.")
    return result


def rebase_config(source):
    result = deepcopy(source)
    result["indicators"]["high_amount"] *= UNIT_SCALE
    for key in ("review_amount", "high_risk_amount"):
        result["rules"]["cold_start"][key] *= UNIT_SCALE
    for key in ("review", "high_risk"):
        result["rules"]["velocity"]["amount_sum_1h"][key] *= UNIT_SCALE
    reference = result["calibration"]["train_reference"]
    for name in ("amount_percentiles", "amount_sum_1h"):
        reference[name] = {key: value * UNIT_SCALE for key, value in reference[name].items()}
    result["currency"] = {"supported": ["KZT"], "assume_if_missing": "KZT"}
    result["units"] = {"currency": "KZT", "unit_version": UNIT_VERSION,
                       "monetary_features": sorted(MONETARY_FEATURES),
                       "adaptation": "Threshold rebasing only; not retrained or validated on KZT data."}
    return result


def adapt_assets(source, destination):
    """Build native assets from the untouched source repository, never in place."""
    import lightgbm as lgb
    import yaml

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Source and generated asset directories must be separate")
    destination.mkdir(parents=True, exist_ok=True)
    source_model = source / "models/S0_CLEAN_final.txt"
    rebased = rebase_model_text(source_model.read_text(encoding="utf-8"))
    booster = lgb.Booster(params={"num_threads": 1}, model_str=rebased)
    canonical = booster.model_to_string(num_iteration=-1)
    (destination / "S0_CLEAN_final.txt").write_text(canonical, encoding="utf-8", newline="\n")
    meta = json.loads((source / "models/S0_CLEAN_final.meta.json").read_text(encoding="utf-8"))
    meta.update(model_id=MODEL_ID, model_version=MODEL_VERSION, currency="KZT", unit_version=UNIT_VERSION)
    meta["unit_adaptation"] = {"source_model_id": "synsepa_s0_clean", "source_model_version": "1.0",
        "source_training_currency": "EUR", "fixed_unit_scale": UNIT_SCALE,
        "monetary_features": sorted(MONETARY_FEATURES), "retrained": False,
        "validated_on_kzt_data": False,
        "note": "Source metrics below describe the original synthetic model, not KZT validation."}
    (destination / "S0_CLEAN_final.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    ood = rebase_ood(json.loads((source / "models/S0_CLEAN_final.ood.json").read_text(encoding="utf-8")))
    (destination / "S0_CLEAN_final.ood.json").write_text(json.dumps(ood, indent=2) + "\n", encoding="utf-8", newline="\n")
    config = rebase_config(yaml.safe_load((source / "config/risk_config.yaml").read_text(encoding="utf-8")))
    (destination / "risk_config.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(destination.iterdir()) if p.is_file()}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path(__file__).parent / "assets")
    args = parser.parse_args()
    print(json.dumps(adapt_assets(args.source, args.destination), indent=2))
