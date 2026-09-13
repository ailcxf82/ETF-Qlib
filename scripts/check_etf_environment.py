"""Read-only preflight checks for the ETF Qlib and RD-Agent environment.

Run inside the qlib_zhengshi Conda environment, for example:

    conda run -n qlib_zhengshi python scripts/check_etf_environment.py \
        --smoke-train --docker-qlib

The script never prints secrets and never modifies the ETF dataset.  The
optional training check uses a temporary MLflow directory that is removed when
the process exits.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATA_PATH = r"D:\qlib_data\etf_qlib_data"
REQUIRED_MODEL_SETTINGS = (
    "CHAT_MODEL",
    "EMBEDDING_MODEL",
)
OPTIONAL_CREDENTIAL_SETTINGS = (
    "OPENAI_API_KEY",
    "AZURE_API_KEY",
    "DEEPSEEK_API_KEY",
    "LITELLM_PROXY_API_KEY",
)


@dataclass
class Check:
    name: str
    state: str
    detail: str


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def env_file_settings(path: Path) -> set[str]:
    """Return nonempty setting names without loading or displaying values."""
    if not path.is_file():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if value.strip().strip('"').strip("'"):
            names.add(name.strip())
    return names


def configured_settings() -> set[str]:
    local_values = env_file_settings(Path.cwd() / ".env")
    environment_values = {name for name, value in os.environ.items() if value}
    return local_values | environment_values


def check_qlib(data_path: Path) -> tuple[Check, list[str]]:
    try:
        import qlib
        from qlib.constant import REG_CN
        from qlib.data import D

        qlib.init(provider_uri=data_path, region=REG_CN)
        calendar = D.calendar()
        instruments = D.list_instruments(
            D.instruments("all"), start_time=calendar[0], end_time=calendar[-1], as_list=True
        )
        sample = D.features(
            instruments[:3], ["$close", "$volume"], start_time=str(calendar[-5].date()), end_time=str(calendar[-1].date())
        )
        if sample.empty:
            return Check("ETF Qlib data", "FAIL", "The latest price/volume sample is empty."), []
        return (
            Check(
                "ETF Qlib data",
                "PASS",
                f"{len(instruments)} instruments; {calendar[0].date()} to {calendar[-1].date()}; "
                f"latest sample {sample.shape[0]} rows",
            ),
            instruments,
        )
    except Exception as exc:  # Report preflight failures instead of hiding them.
        return Check("ETF Qlib data", "FAIL", f"{type(exc).__name__}: {exc}"), []


def check_lightgbm() -> Check:
    try:
        import lightgbm
        from qlib.contrib.model.gbdt import LGBModel

        _ = LGBModel
        return Check("LightGBM + Qlib LGBModel", "PASS", f"lightgbm {lightgbm.__version__}")
    except Exception as exc:
        return Check("LightGBM + Qlib LGBModel", "FAIL", f"{type(exc).__name__}: {exc}")


def smoke_train(data_path: Path) -> Check:
    """Train a deliberately small model, then remove all generated artifacts."""
    try:
        import qlib
        from qlib.constant import REG_CN
        from qlib.contrib.data.handler import Alpha158
        from qlib.contrib.model.gbdt import LGBModel
        from qlib.data import D
        from qlib.data.dataset import DatasetH

        qlib.init(provider_uri=data_path, region=REG_CN)
        instruments = D.list_instruments(D.instruments("all"), "2023-01-01", "2024-06-30", as_list=True)[:30]
        handler = Alpha158(
            instruments=instruments,
            start_time="2023-01-01",
            end_time="2024-06-30",
            fit_start_time="2023-01-01",
            fit_end_time="2023-12-31",
        )
        dataset = DatasetH(
            handler=handler,
            segments={"train": ("2023-01-01", "2023-12-31"), "valid": ("2024-01-01", "2024-06-30")},
        )
        original_dir = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="etf_qlib_smoke_") as temp_dir:
            os.environ["MLFLOW_TRACKING_URI"] = str(Path(temp_dir) / "mlruns")
            os.chdir(temp_dir)
            try:
                model = LGBModel(num_boost_round=10, early_stopping_rounds=5, num_leaves=8, learning_rate=0.1)
                model.fit(dataset, verbose_eval=0)
                prediction = model.predict(dataset, segment="valid")
            finally:
                os.chdir(original_dir)
                os.environ.pop("MLFLOW_TRACKING_URI", None)
        if prediction.empty or prediction.isna().any():
            return Check("Qlib LGB training smoke test", "FAIL", "Prediction is empty or contains missing values.")
        return Check("Qlib LGB training smoke test", "PASS", f"30 instruments; {len(prediction)} validation predictions")
    except Exception as exc:
        return Check("Qlib LGB training smoke test", "FAIL", f"{type(exc).__name__}: {exc}")


def check_docker() -> Check:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}/{{.OSType}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return Check("Docker", "PASS", result.stdout.strip())
    except Exception as exc:
        return Check("Docker", "FAIL", f"{type(exc).__name__}: {exc}")


def check_python_dependencies() -> Check:
    """Surface resolver conflicts without treating optional packages as a Qlib failure."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "check"], capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return Check("Python dependency integrity", "PASS", "pip check passed")
        detail = (result.stdout + result.stderr).strip().replace("\n", " | ")
        return Check("Python dependency integrity", "WARN", detail)
    except Exception as exc:
        return Check("Python dependency integrity", "WARN", f"{type(exc).__name__}: {exc}")


def check_docker_qlib(data_path: Path) -> Check:
    command = [
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={data_path},target=/etf_qlib_data,readonly",
        "-e",
        "QLIB_PROVIDER_URI=/etf_qlib_data",
        "rdagent-qlib:latest",
        "python",
        "-c",
        (
            "import qlib; from qlib.constant import REG_CN; from qlib.data import D; "
            "qlib.init(provider_uri='/etf_qlib_data', region=REG_CN); "
            "cal=D.calendar(); inst=D.list_instruments(D.instruments('all'), cal[0], cal[-1], as_list=True); "
            "print(f'{len(inst)} instruments; {cal[0].date()} to {cal[-1].date()}')"
        ),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        return Check("Docker-mounted ETF Qlib", "PASS", result.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return Check("Docker-mounted ETF Qlib", "FAIL", f"{type(exc).__name__}: {exc}")


def check_rdagent(settings: set[str]) -> list[Check]:
    checks = [Check("RD-Agent", "PASS", f"rdagent {package_version('rdagent')}")]
    missing_model = [name for name in REQUIRED_MODEL_SETTINGS if name not in settings]
    has_credential = any(name in settings for name in OPTIONAL_CREDENTIAL_SETTINGS)
    if missing_model or not has_credential:
        missing = missing_model + ([] if has_credential else ["a provider credential"])
        checks.append(Check("RD-Agent model configuration", "WARN", "Missing: " + ", ".join(missing)))
    else:
        checks.append(Check("RD-Agent model configuration", "PASS", "Model and provider credentials are configured."))
    try:
        from rdagent.utils.env import QlibCondaConf

        default_name = QlibCondaConf().conda_env_name
        detail = f"Default Qlib runner environment: {default_name}"
        if default_name != "qlib_zhengshi":
            detail += "; requires an ETF adapter to reuse qlib_zhengshi"
        checks.append(Check("RD-Agent ETF adapter", "WARN", detail))
    except Exception as exc:
        checks.append(Check("RD-Agent ETF adapter", "FAIL", f"{type(exc).__name__}: {exc}"))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-path", default=os.getenv("ETF_QLIB_PROVIDER_URI", DEFAULT_DATA_PATH))
    parser.add_argument("--smoke-train", action="store_true", help="Run a small Alpha158 + LGBModel training job.")
    parser.add_argument("--docker-qlib", action="store_true", help="Verify the rdagent-qlib image can read the ETF dataset read-only.")
    args = parser.parse_args()
    data_path = Path(args.data_path)

    checks = [
        Check("Python", "PASS", sys.version.split()[0]),
        Check("pyqlib", "PASS" if package_version("pyqlib") != "missing" else "FAIL", package_version("pyqlib")),
    ]
    if not data_path.is_dir():
        checks.append(Check("ETF data directory", "FAIL", f"Not found: {data_path}"))
        instruments: list[str] = []
    else:
        checks.append(Check("ETF data directory", "PASS", str(data_path)))
        qlib_check, instruments = check_qlib(data_path)
        checks.append(qlib_check)
    checks.append(check_lightgbm())
    checks.append(check_python_dependencies())
    checks.append(check_docker())
    if args.docker_qlib and data_path.is_dir():
        checks.append(check_docker_qlib(data_path))
    checks.extend(check_rdagent(configured_settings()))
    if args.smoke_train and instruments:
        checks.append(smoke_train(data_path))

    for check in checks:
        print(f"[{check.state}] {check.name}: {check.detail}")
    return 1 if any(check.state == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
