"""The offline sample helper must preserve source data on invalid requests."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def prepare():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_momtsim_sample.py"
    spec = importlib.util.spec_from_file_location("offline_momtsim_sample", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_sample


@pytest.mark.parametrize("collision", ["input_prepared", "input_alternative", "outputs"])
def test_output_collision_never_overwrites_input(prepare, tmp_path, collision):
    source = tmp_path / "source.csv"
    original = b"unparsed original dataset\n"
    source.write_bytes(original)
    prepared, alternative = tmp_path / "prepared.csv", tmp_path / "alternative.csv"
    if collision == "input_prepared":
        prepared = tmp_path / "subdir" / ".." / "source.csv"
    elif collision == "input_alternative":
        alternative = source
    else:
        alternative = prepared
    with pytest.raises(ValueError, match="distinct paths"):
        prepare(source, prepared, alternative)
    assert source.read_bytes() == original
    assert not (tmp_path / "prepared.csv").exists()


@pytest.mark.parametrize("size", [0, -1])
def test_invalid_size_fails_before_reading_or_writing(prepare, tmp_path, size):
    source = tmp_path / "source.csv"
    source.write_text("not a valid CSV", encoding="utf-8")
    output = tmp_path / "output.csv"
    with pytest.raises(ValueError, match="positive"):
        prepare(source, output, target_size=size)
    assert not output.exists()


def test_no_positive_transactions_does_not_create_outputs(prepare, tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("step,amount\n0,0\n1,-1\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    with pytest.raises(ValueError, match="No positive-amount"):
        prepare(source, output)
    assert not output.exists()
