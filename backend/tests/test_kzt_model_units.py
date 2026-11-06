"""Numerical audit of the actual tree/rule/OOD unit adaptation, not a rename."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import pytest
import yaml

from app.ml import evaluate_synsepa, get_ml_status
from app.ml import service
from app.ml.adapt_units import (LIGHTGBM_INFINITY, MONETARY_FEATURES, UNIT_SCALE, UNIT_VERSION,
                                 adapt_assets, rebase_config, rebase_ood)
from app.ml.manifest import ASSET_SHA256

SOURCE = Path(__file__).resolve().parents[3] / "anti_fraud_1/antifraud-runtime"


@pytest.fixture(autouse=True)
def native_model_enabled(monkeypatch):
    monkeypatch.setenv("AMAN_ML_MODE", "auto")
    monkeypatch.delenv("AMAN_ML_ASSET_DIR", raising=False)


@pytest.fixture(scope="module")
def models():
    import lightgbm as lgb
    if not SOURCE.exists():
        pytest.skip("Original source is needed only for the numerical unit audit")
    old = lgb.Booster(params={"num_threads": 1},
                      model_str=(SOURCE / "models/S0_CLEAN_final.txt").read_text(encoding="utf-8"))
    native = lgb.Booster(params={"num_threads": 1},
                         model_str=(service.ASSET_DIR / "S0_CLEAN_final.txt").read_text(encoding="utf-8"))
    return old, native


def test_every_tree_keeps_topology_leaves_and_nonmonetary_thresholds(models):
    old, native = models
    assert old.feature_name() == native.feature_name()
    assert old.num_trees() == native.num_trees() == 981
    monetary_indexes = {i for i, name in enumerate(old.feature_name()) if name in MONETARY_FEATURES}
    assert monetary_indexes == {0, 11, 12, 17, 18}
    changed = 0

    def compare(before, after):
        nonlocal changed
        assert set(before) == set(after)
        for key, value in before.items():
            if key in {"left_child", "right_child"}:
                compare(value, after[key])
            elif key == "threshold" and before["split_feature"] in monetary_indexes:
                assert before["decision_type"] == "<="
                expected = value if abs(value) >= LIGHTGBM_INFINITY else float(value) * UNIT_SCALE
                assert after[key] == expected
                changed += 1
            else:
                assert after[key] == value

    old_dump, native_dump = old.dump_model(), native.dump_model()
    for before, after in zip(old_dump["tree_info"], native_dump["tree_info"]):
        compare(before["tree_structure"], after["tree_structure"])
        assert before["num_leaves"] == after["num_leaves"]
        assert before["shrinkage"] == after["shrinkage"]
    assert changed > 1000
    for name, limits in old_dump["feature_infos"].items():
        for key, value in limits.items():
            if key in {"min_value", "max_value"} and name in MONETARY_FEATURES:
                assert native_dump["feature_infos"][name][key] == value * UNIT_SCALE
            else:
                assert native_dump["feature_infos"][name][key] == value


def test_fixed_random_vectors_have_identical_predictions_in_scaled_units(models):
    old, native = models
    rng = np.random.default_rng(9042026)
    names = old.feature_name()
    source = rng.uniform(0, 20, (4096, len(names)))
    for index, name in enumerate(names):
        if name in MONETARY_FEATURES:
            source[:, index] = np.exp(rng.uniform(math.log(.00002), math.log(250000), len(source)))
        elif name == "hour":
            source[:, index] = rng.integers(0, 24, len(source))
        elif name == "day_of_week":
            source[:, index] = rng.integers(0, 7, len(source))
        elif name in {"is_new_recipient", "is_night_transaction", "is_weekend"}:
            source[:, index] = rng.integers(0, 2, len(source))
        elif name.startswith("time_since"):
            source[:, index] = rng.uniform(0, 9000, len(source))
    source[::7, names.index("sender_avg_amount_30d")] = np.nan
    source[::7, names.index("amount_vs_sender_avg")] = np.nan
    source[::11, names.index("sender_avg_amount_7d")] = np.nan
    adapted = source.copy()
    for name in MONETARY_FEATURES:
        adapted[:, names.index(name)] *= UNIT_SCALE
    np.testing.assert_array_equal(old.predict(source, num_threads=1), native.predict(adapted, num_threads=1))


def test_tiyn_points_either_side_of_tree_boundaries_match_source(models):
    old, native = models
    names = old.feature_name()
    boundaries = []

    def collect(node):
        if "split_feature" not in node:
            return
        if names[node["split_feature"]] in MONETARY_FEATURES and abs(node["threshold"]) < LIGHTGBM_INFINITY:
            boundaries.append((node["split_feature"], node["threshold"]))
        collect(node["left_child"])
        collect(node["right_child"])

    for tree in old.dump_model()["tree_info"]:
        collect(tree["tree_structure"])
    boundaries = sorted(set(boundaries))
    selected = [boundaries[i] for i in np.linspace(0, len(boundaries) - 1, 512, dtype=int)]
    data = []
    for feature, threshold in selected:
        center = math.floor(threshold * UNIT_SCALE * 100) / 100
        for step in (-.01, 0, .01):
            vector = np.ones(len(names), dtype=float)
            for name in MONETARY_FEATURES:
                vector[names.index(name)] = 100000
            vector[feature] = max(.01, center + step)
            data.append(vector)
    native_values = np.array(data)
    original_values = native_values.copy()
    for name in MONETARY_FEATURES:
        original_values[:, names.index(name)] /= UNIT_SCALE
    np.testing.assert_array_equal(old.predict(original_values, num_threads=1), native.predict(native_values, num_threads=1))


def test_config_and_ood_scale_money_only_and_preserve_source():
    if not SOURCE.exists():
        pytest.skip("Original source is needed only for the numerical unit audit")
    source_config = yaml.safe_load((SOURCE / "config/risk_config.yaml").read_text(encoding="utf-8"))
    source_ood = json.loads((SOURCE / "models/S0_CLEAN_final.ood.json").read_text(encoding="utf-8"))
    before_config, before_ood = deepcopy(source_config), deepcopy(source_ood)
    actual_config = yaml.safe_load((service.ASSET_DIR / "risk_config.yaml").read_text(encoding="utf-8"))
    actual_ood = json.loads((service.ASSET_DIR / "S0_CLEAN_final.ood.json").read_text(encoding="utf-8"))
    assert actual_config == rebase_config(source_config)
    assert actual_ood == rebase_ood(source_ood)
    assert source_config == before_config and source_ood == before_ood
    for name, stats in source_ood["features"].items():
        for key, value in stats.items():
            expected = value * UNIT_SCALE if name in MONETARY_FEATURES and key != "n" else value
            assert actual_ood["features"][name][key] == expected
    assert actual_config["thresholds"] == source_config["thresholds"]
    assert actual_config["rules"]["velocity"]["amount_sum_1h"]["min_transfers"] == 3
    assert actual_config["rules"]["out_of_distribution"]["amount_spike_ratio"] == 3
    assert actual_config["rules"]["cold_start"]["review_amount"] == 1_075_500
    assert actual_config["rules"]["cold_start"]["high_risk_amount"] == 18_863_000
    assert actual_config["rules"]["velocity"]["amount_sum_1h"]["review"] == 27_181_500
    assert actual_config["rules"]["velocity"]["amount_sum_1h"]["high_risk"] == 58_017_500


def test_offline_rebuild_is_byte_reproducible_and_tree_sizes_are_correct(tmp_path):
    if not SOURCE.exists():
        pytest.skip("Original source is needed only for the numerical unit audit")
    checksums = adapt_assets(SOURCE, tmp_path / "rebuilt")
    assert checksums == ASSET_SHA256
    raw = (service.ASSET_DIR / "S0_CLEAN_final.txt").read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    expected = [int(n) for n in re.search(r"(?m)^tree_sizes=(.*)$", text).group(1).split()]
    starts = [m.start() for m in re.finditer(r"(?m)^Tree=\d+\n", text)]
    end = text.index("end of trees\n", starts[-1])
    actual = [len(text[start:stop].encode("utf-8")) for start, stop in zip(starts, starts[1:] + [end])]
    assert actual == expected
    provenance = json.loads((service.ASSET_DIR.parent / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert provenance["fixed_unit_scale"] == 500 and provenance["scale_is_exchange_rate"] is False
    assert provenance["retrained"] is False and provenance["validated_on_kzt_data"] is False
    assert provenance["artifacts_sha256"] == ASSET_SHA256


@pytest.mark.parametrize("obsolete_rate", ["1", "500", "600", "invalid", "nan"])
def test_obsolete_exchange_rate_environment_cannot_change_native_inputs(monkeypatch, obsolete_rate):
    monkeypatch.setenv("AMAN_ML_KZT_PER_EUR", obsolete_rate)
    result = evaluate_synsepa([], sender_id="sender", receiver_id="new", amount_cents=1,
                              evaluated_at=datetime(2026, 11, 4, 7, tzinfo=timezone.utc))
    assert result["status"] == "available", result
    assert result["features"]["amount"] == .01 and result["amount_kzt"] == .01
    assert result["unit_version"] == UNIT_VERSION and result["model_currency"] == result["currency"] == "KZT"
    assert not {"amount_eur", "demo_kzt_per_eur", "kzt_per_eur", "exchange_rate_is_demo"}.intersection(result)
    assert get_ml_status()["monetary_thresholds_kzt"]["cold_start_review"] == 1_075_500
