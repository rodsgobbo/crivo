import dataclasses

import pytest

from crivo.providers.registry import ProviderRegistry, load_providers
from crivo.providers.vault import (
    CredentialValidationError,
    CredentialVault,
    EnvelopeCipher,
    StoredCredential,
    generate_master_key,
)
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

ORDEM_PADRAO = ("groq", "google-ai-studio", "mistral")


def aceita(provider, secret):
    return True


def recusa(provider, secret):
    return False


@pytest.fixture
def vault(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    for user_id, subject in (("ana", "sub-ana"), ("bruno", "sub-bruno")):
        repo.insert(
            "users",
            {
                "user_id": user_id,
                "subject_google": subject,
                "criado_em": "2026-01-01T00:00:00+00:00",
            },
        )
    registry = ProviderRegistry(load_providers())
    cofre = CredentialVault(connection, EnvelopeCipher(generate_master_key()), registry)
    yield cofre, connection
    connection.close()


# ------------------------------------------------------------------ validacao
def test_a_credential_is_validated_before_being_stored(vault):
    cofre, _connection = vault
    with pytest.raises(CredentialValidationError) as err:
        cofre.store("ana", "groq", "chave-ruim", validate=recusa)
    assert "recusou a credencial" in str(err.value)
    assert cofre.list("ana") == []


def test_a_provider_error_surfaces_the_cause(vault):
    cofre, _connection = vault

    def explode(provider, secret):
        raise TimeoutError("sem resposta")

    with pytest.raises(CredentialValidationError) as err:
        cofre.store("ana", "groq", "chave", validate=explode)
    assert "TimeoutError" in str(err.value)


def test_a_provider_outside_the_registry_is_refused(vault):
    cofre, _connection = vault
    with pytest.raises(CredentialValidationError) as err:
        cofre.store("ana", "provedor-inventado", "chave", validate=aceita)
    assert "provedor-inventado" in str(err.value)


def test_a_provider_that_needs_a_key_refuses_an_empty_one(vault):
    cofre, _connection = vault
    with pytest.raises(CredentialValidationError) as err:
        cofre.store("ana", "groq", "   ", validate=aceita)
    assert "exige chave" in str(err.value)


def test_a_local_provider_accepts_no_key(vault):
    cofre, _connection = vault
    guardada = cofre.store("ana", "ollama-local", "", validate=aceita)
    assert guardada.provedor == "ollama-local"


# ------------------------------------------------------------------ segredo
def test_the_stored_key_is_never_in_clear(vault):
    cofre, connection = vault
    cofre.store("ana", "groq", "gsk-segredo-do-usuario", validate=aceita)
    row = connection.execute("SELECT * FROM provider_credentials").fetchone()
    assert b"gsk-segredo-do-usuario" not in row["chave_cifrada"]


def test_listing_never_exposes_the_value(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "gsk-segredo-do-usuario", validate=aceita)
    guardada = cofre.list("ana")[0]
    campos = {f.name for f in dataclasses.fields(StoredCredential)}
    assert "chave" not in campos and "secret" not in campos
    assert guardada.sufixo == "ario"
    assert "gsk-segredo-do-usuario" not in repr(guardada)


def test_the_value_can_be_opened_for_immediate_use(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "gsk-segredo-do-usuario", validate=aceita)
    assert cofre.reveal("ana", "groq") == "gsk-segredo-do-usuario"


def test_opening_a_credential_the_user_does_not_have_is_refused(vault):
    cofre, _connection = vault
    with pytest.raises(CredentialValidationError):
        cofre.reveal("ana", "groq")


def test_one_users_credential_never_opens_for_another(vault):
    cofre, connection = vault
    cofre.store("ana", "groq", "gsk-da-ana", validate=aceita)
    assert cofre.chain("bruno") == []
    with pytest.raises(CredentialValidationError):
        cofre.reveal("bruno", "groq")


# ------------------------------------------------------------------ ciclo
def test_removing_a_credential_takes_it_out_of_the_chain(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "chave-a", validate=aceita)
    cofre.store("ana", "mistral", "chave-b", validate=aceita)
    assert cofre.remove("ana", "groq") is True
    assert cofre.chain("ana") == ["mistral"]


def test_removing_something_absent_changes_nothing(vault):
    cofre, _connection = vault
    assert cofre.remove("ana", "groq") is False


def test_storing_the_same_provider_twice_replaces_it(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "chave-antiga", validate=aceita)
    cofre.store("ana", "groq", "chave-nova-xyz", validate=aceita)
    assert len(cofre.list("ana")) == 1
    assert cofre.reveal("ana", "groq") == "chave-nova-xyz"


# ------------------------------------------------------------------ ordem
def test_the_first_credential_is_seeded_from_the_operator_default(vault):
    cofre, _connection = vault
    guardada = cofre.store(
        "ana", "google-ai-studio", "chave", validate=aceita,
        default_order=ORDEM_PADRAO,
    )
    assert guardada.ordem == ORDEM_PADRAO.index("google-ai-studio")


def test_a_provider_outside_the_default_order_goes_last(vault):
    cofre, _connection = vault
    guardada = cofre.store(
        "ana", "openrouter", "chave", validate=aceita, default_order=ORDEM_PADRAO
    )
    assert guardada.ordem == len(ORDEM_PADRAO)


def test_later_credentials_append_to_the_chain(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "a", validate=aceita, default_order=ORDEM_PADRAO)
    cofre.store("ana", "mistral", "b", validate=aceita, default_order=ORDEM_PADRAO)
    assert cofre.chain("ana") == ["groq", "mistral"]


def test_the_user_can_reorder_the_chain(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "a", validate=aceita)
    cofre.store("ana", "mistral", "b", validate=aceita)
    cofre.set_order("ana", ["mistral", "groq"])
    assert cofre.chain("ana") == ["mistral", "groq"]


def test_ordering_an_unknown_provider_is_refused(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "a", validate=aceita)
    with pytest.raises(CredentialValidationError) as err:
        cofre.set_order("ana", ["groq", "mistral"])
    assert "mistral" in str(err.value)


def test_each_user_keeps_their_own_order(vault):
    cofre, _connection = vault
    cofre.store("ana", "groq", "a", validate=aceita)
    cofre.store("ana", "mistral", "b", validate=aceita)
    cofre.store("bruno", "mistral", "c", validate=aceita)
    cofre.set_order("ana", ["mistral", "groq"])
    assert cofre.chain("ana") == ["mistral", "groq"]
    assert cofre.chain("bruno") == ["mistral"]


# ------------------------------------------------------------------ aviso
def test_the_disclosure_names_the_destination_and_the_limit(vault):
    cofre, _connection = vault
    aviso = cofre.disclosure_for("groq")
    assert "servidores da Groq" in aviso
    assert "cota gratuita" in aviso
    assert "nunca e exibida" in aviso


def test_the_local_provider_discloses_that_data_stays_put(vault):
    cofre, _connection = vault
    assert "nao sai da maquina" in cofre.disclosure_for("ollama-local")


def test_the_disclosure_for_an_unknown_provider_is_refused(vault):
    cofre, _connection = vault
    with pytest.raises(CredentialValidationError):
        cofre.disclosure_for("inventado")


def test_the_first_store_is_detectable_for_the_prior_notice(vault):
    cofre, _connection = vault
    assert cofre.has_any("ana") is False
    cofre.store("ana", "groq", "a", validate=aceita)
    assert cofre.has_any("ana") is True
