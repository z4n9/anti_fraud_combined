"""Проверка выхода за пределы обучающих данных (границы - из train-части, STEP 9).

out_of_distribution НЕ означает fraud: это сигнал, что оценке LightGBM на этой операции
нельзя доверять. Верхняя граница проверяется по всем ключевым признакам; нижняя - только
для суммы (маленькие суммы не считаются рискованными).
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OODResult:
    out_of_distribution: bool = False
    reasons: list = field(default_factory=list)
    high_side: bool = False        # значение ВЫШЕ train-максимума (для правила эскалации)


class OODChecker:
    def __init__(self, bounds_path):
        p = Path(bounds_path)
        if not p.exists():
            raise FileNotFoundError(
                f"нет {p}: выполните `python src/export_ood_bounds.py` (границы берутся из train)")
        self.bounds = json.loads(p.read_text(encoding="utf-8"))["features"]

    def check(self, features: dict) -> OODResult:
        res = OODResult()
        for name, b in self.bounds.items():
            v = features.get(name)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            if v > b["max"]:
                res.out_of_distribution = res.high_side = True
                res.reasons.append("amount_outside_training_range" if name == "amount"
                                   else f"{name}_outside_training_range")
            elif name == "amount" and v < b["min"]:
                res.out_of_distribution = True
                res.reasons.append("amount_below_training_range")
        return res
