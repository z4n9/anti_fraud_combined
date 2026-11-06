"""Загрузка модели ОДИН раз при старте и скоринг вектора признаков."""
import json
from pathlib import Path

import numpy as np


class ModelService:
    def __init__(self, model_path, meta_path):
        import lightgbm as lgb  # Optional dependency: imported only when ML is enabled.
        self.meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        # Git's Windows CRLF conversion changes file offsets in tree_sizes.
        # Universal-newline decoding restores the original LF model text while
        # preserving every tree value. Raw asset bytes are checksum-verified.
        self.booster = lgb.Booster(params={"num_threads": 1},
                                   model_str=Path(model_path).read_text(encoding="utf-8"))
        self.feature_names = list(self.meta["features"])
        if self.booster.feature_name() != self.feature_names:
            raise RuntimeError("признаки в meta.json не совпадают с признаками модели: "
                               f"{self.booster.feature_name()} != {self.feature_names}")
        self.model_id = self.meta["model_id"]
        self.model_version = self.meta["model_version"]

    def predict(self, features: dict) -> float:
        """features: dict в порядке модели. NaN передаются в LightGBM как есть (как при
        обучении), 0 вместо NaN не подставляется."""
        if list(features) != self.feature_names:
            raise ValueError("порядок/состав онлайн-признаков не совпадает с моделью")
        x = np.array([[features[k] for k in self.feature_names]], dtype=float)
        return float(self.booster.predict(x, num_threads=1)[0])
