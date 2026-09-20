from __future__ import annotations

from etf_ml.contracts import ModelSpec
from etf_ml.errors import ConfigurationError, DataNotReady


def create_model(spec: ModelSpec):
    # Keep the declared experimental seed authoritative, including framework aliases.
    for key in ("seed", "random_state", "random_seed"):
        if key in spec.constructor and spec.constructor[key] != spec.seed:
            raise ConfigurationError("Constructor seed conflicts with ModelSpec.seed")
    try:
        if spec.name == "ridge":
            from qlib.contrib.model.linear import LinearModel
            params = {"estimator": "ridge", "alpha": 1, "fit_intercept": True,
                      "include_valid": False}
            params.update(spec.constructor)
            if params.get("include_valid"):
                raise ConfigurationError("Ridge must fit only the train segment")
            return LinearModel(**params)
        if spec.name == "lightgbm":
            from qlib.contrib.model.gbdt import LGBModel
            params = {"loss": "mse", "learning_rate": 0.03, "num_leaves": 15,
                      "max_depth": 4, "min_data_in_leaf": 100, "lambda_l2": 10,
                      "num_boost_round": 500, "early_stopping_rounds": 50,
                      "seed": spec.seed, "num_threads": 4, "deterministic": True,
                      "force_col_wise": True}
            params.update(spec.constructor)
            objective = params.get("objective", "mse")
            if (params.get("loss") != "mse" or not isinstance(objective, str) or
                    objective not in {"mse", "regression", "l2"}):
                raise ConfigurationError("The return task requires single-label mse")
            return LGBModel(**params)
        if spec.name == "xgboost":
            from etf_ml.models.xgboost import ETFXGBModel
            params = {"seed": spec.seed, "objective": "reg:squarederror", **spec.constructor}
            if (not isinstance(params["objective"], str) or
                    not params["objective"].startswith("reg:")):
                raise ConfigurationError("The return task requires an XGBoost regression objective")
            return ETFXGBModel(**params)
    except ImportError as exc:
        raise DataNotReady("Required dependency missing for " + spec.name) from exc
    raise ConfigurationError("Unknown model")
