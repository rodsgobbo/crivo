import pytest

from crivo.config import load_config
from crivo.secrets_vault import SecretError, SecretsVault


def test_reads_a_secret_from_the_environment():
    vault = SecretsVault({"CRIVO_MASTER_KEY": "chave-mestra-longa"})
    assert vault.require("CRIVO_MASTER_KEY") == "chave-mestra-longa"


def test_absent_secret_names_the_variable():
    vault = SecretsVault({})
    with pytest.raises(SecretError) as err:
        vault.require("GOOGLE_CLIENT_SECRET")
    assert "GOOGLE_CLIENT_SECRET" in str(err.value)


def test_blank_secret_is_treated_as_absent():
    vault = SecretsVault({"GOOGLE_CLIENT_SECRET": "   "})
    with pytest.raises(SecretError):
        vault.require("GOOGLE_CLIENT_SECRET")


def test_optional_secret_falls_back_to_the_default():
    vault = SecretsVault({})
    assert vault.optional("OPERATIONAL_SESSION_PATH", "padrao") == "padrao"


def test_sensitive_values_are_collected_and_public_ones_are_not():
    vault = SecretsVault(
        {"GOOGLE_CLIENT_SECRET": "segredo-do-cliente", "GOOGLE_CLIENT_ID": "id-publico"}
    )
    vault.require("GOOGLE_CLIENT_SECRET")
    vault.require("GOOGLE_CLIENT_ID")
    assert "segredo-do-cliente" in vault.sensitive_values
    assert "id-publico" not in vault.sensitive_values


def test_unknown_variables_are_treated_as_sensitive():
    vault = SecretsVault({"ALGO_NOVO": "valor-desconhecido"})
    vault.require("ALGO_NOVO")
    assert "valor-desconhecido" in vault.sensitive_values


def test_missing_lists_only_the_absent_names():
    vault = SecretsVault({"CRIVO_MASTER_KEY": "presente"})
    assert vault.missing(["CRIVO_MASTER_KEY", "GOOGLE_CLIENT_SECRET"]) == [
        "GOOGLE_CLIENT_SECRET"
    ]


def test_configuration_file_carries_no_secret_shaped_key():
    config = load_config()
    rendered = repr(config).lower()
    for forbidden in ("secret", "password", "senha", "token", "api_key", "apikey"):
        assert forbidden not in rendered
