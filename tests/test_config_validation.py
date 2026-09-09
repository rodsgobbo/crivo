import tomllib
from pathlib import Path

import pytest

from crivo.config import ConfigError, load_config

BASE = Path("config") / "default.toml"


def write_variant(tmp_path, mutate):
    with BASE.open("rb") as handle:
        raw = tomllib.load(handle)
    mutate(raw)
    target = tmp_path / "variant.toml"
    target.write_text(_dump(raw), encoding="utf-8")
    return target


def _dump(value, prefix=""):
    scalars, tables = [], []
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, dict):
            tables.append(f"[{path}]\n{_dump(item, path + '.')}")
        else:
            scalars.append(f"{key} = {_scalar(item)}")
    return "\n".join(scalars) + ("\n" if scalars else "") + "\n".join(tables)


def _scalar(item):
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, str):
        return f'"{item}"'
    if isinstance(item, list):
        return "[" + ", ".join(_scalar(x) for x in item) + "]"
    return str(item)


def test_default_configuration_loads_and_is_typed():
    config = load_config(BASE)
    assert config.report.limiar_destaque == 70
    assert config.resume.formatos_aceitos == ("gdoc", "docx", "pdf")
    assert config.synthesis.modo_deterministico is False


def test_absent_key_names_the_key_and_expected_type(tmp_path):
    path = write_variant(tmp_path, lambda raw: raw["report"].pop("limiar_destaque"))
    with pytest.raises(ConfigError) as err:
        load_config(path)
    assert "report.limiar_destaque" in str(err.value)
    assert "int" in str(err.value)


def test_absent_section_names_the_section(tmp_path):
    path = write_variant(tmp_path, lambda raw: raw.pop("retention"))
    with pytest.raises(ConfigError) as err:
        load_config(path)
    assert "retention" in str(err.value)


def test_wrong_type_names_the_key_and_expected_type(tmp_path):
    def mutate(raw):
        raw["report"]["limiar_destaque"] = "setenta"

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "report.limiar_destaque" in str(err.value)
    assert "esperado int" in str(err.value)


def test_boolean_is_not_accepted_where_an_integer_is_expected(tmp_path):
    def mutate(raw):
        raw["report"]["limiar_destaque"] = True

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "booleano" in str(err.value)


def test_value_above_range_names_the_bound(tmp_path):
    def mutate(raw):
        raw["report"]["limiar_destaque"] = 500

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "report.limiar_destaque" in str(err.value)
    assert "<= 100" in str(err.value)


def test_value_below_range_names_the_bound(tmp_path):
    def mutate(raw):
        raw["run"]["duracao_maxima_s"] = 5

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "run.duracao_maxima_s" in str(err.value)
    assert ">= 60" in str(err.value)


def test_inverted_enrichment_interval_is_rejected(tmp_path):
    def mutate(raw):
        raw["collection"]["intervalo_enriquecimento_min_s"] = 30
        raw["collection"]["intervalo_enriquecimento_max_s"] = 10

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "intervalo_enriquecimento_min_s" in str(err.value)


def test_score_weights_must_sum_to_one(tmp_path):
    def mutate(raw):
        raw["scoring"]["pesos"]["competencias"] = 0.9

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "scoring.pesos" in str(err.value)


def test_unknown_profile_source_is_rejected(tmp_path):
    def mutate(raw):
        raw["profile"]["precedencia_origens"] = ["manual", "telepatia"]

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "telepatia" in str(err.value)


def test_empty_resume_format_list_is_rejected(tmp_path):
    def mutate(raw):
        raw["resume"]["formatos_aceitos"] = []

    with pytest.raises(ConfigError) as err:
        load_config(write_variant(tmp_path, mutate))
    assert "resume.formatos_aceitos" in str(err.value)
