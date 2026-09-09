import json

import pytest

from crivo.providers.vault import CryptoError, EnvelopeCipher, generate_master_key
from crivo.store.migrations import open_database
from crivo.store.repository import Repository
from crivo.web.auth import AuthError
from crivo.web.linkedin import (
    ATIVA,
    EXPIRADA,
    CredentialSubmissionError,
    LinkedInConnector,
    LinkedInIdentity,
)

REDIRECT = "https://app.exemplo.br/linkedin/callback"


class FakeLinkedIn:
    name = "linkedin"

    def __init__(self, escopos=("email", "openid", "profile")):
        self.escopos = escopos
        self.revoked = []

    def authorization_url(self, state, code_challenge, redirect_uri):
        return f"https://linkedin/auth?state={state}"

    def exchange(self, code, code_verifier, redirect_uri):
        identity = LinkedInIdentity(
            subject="li-ana",
            campos={"nome": "Ana", "foto": "https://f", "email": "ana@exemplo.br"},
            escopos=tuple(sorted(self.escopos)),
        )
        return identity, "testemunho-secreto-123", 3600

    def revoke(self, token):
        self.revoked.append(token)


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    Repository(connection).insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana", "criado_em": "2026-01-01"},
    )
    provider = FakeLinkedIn()
    cipher = EnvelopeCipher(generate_master_key())
    yield connection, provider, LinkedInConnector(connection, provider, cipher), cipher
    connection.close()


def connect(connector):
    url = connector.begin("ana", REDIRECT)
    state = url.split("state=")[1]
    return connector.complete(state=state, code="codigo")


# ------------------------------------------------------------ envelope
def test_a_sealed_secret_opens_back_to_the_original():
    cipher = EnvelopeCipher(generate_master_key())
    sealed = cipher.seal("chave-super-secreta", associated="ana")
    assert cipher.open(sealed, associated="ana") == "chave-super-secreta"


def test_the_ciphertext_does_not_contain_the_secret():
    cipher = EnvelopeCipher(generate_master_key())
    sealed = cipher.seal("chave-super-secreta")
    assert b"chave-super-secreta" not in sealed.ciphertext


def test_only_the_suffix_is_exposed():
    sealed = EnvelopeCipher(generate_master_key()).seal("sk-abcdef1234")
    assert sealed.suffix == "1234"


def test_another_master_key_cannot_open_it():
    sealed = EnvelopeCipher(generate_master_key()).seal("segredo-longo")
    with pytest.raises(CryptoError):
        EnvelopeCipher(generate_master_key()).open(sealed)


def test_a_secret_sealed_for_one_user_does_not_open_for_another():
    cipher = EnvelopeCipher(generate_master_key())
    sealed = cipher.seal("segredo-longo", associated="ana")
    with pytest.raises(CryptoError):
        cipher.open(sealed, associated="bruno")


def test_a_malformed_master_key_is_refused():
    with pytest.raises(CryptoError):
        EnvelopeCipher("curta-demais")


# ------------------------------------------------------------ conexao
def test_connecting_stores_identity_and_granted_scopes(env):
    connection, _p, connector, _c = env
    connect(connector)
    row = connector.connection_for("ana")
    assert json.loads(row["campos_identidade"])["nome"] == "Ana"
    assert json.loads(row["escopos_concedidos"]) == ["email", "openid", "profile"]
    assert row["estado"] == ATIVA


def test_fields_the_scopes_cannot_reach_are_marked_unavailable(env):
    _c, _p, connector, _ci = env
    connect(connector)
    indisponiveis = json.loads(connector.connection_for("ana")["campos_indisponiveis"])
    assert set(indisponiveis) == {"headline", "experiencias", "competencias"}


def test_a_narrower_consent_widens_the_unavailable_list(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    Repository(connection).insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana", "criado_em": "2026-01-01"},
    )
    connector = LinkedInConnector(
        connection, FakeLinkedIn(escopos=("openid",)), EnvelopeCipher(generate_master_key())
    )
    connect(connector)
    indisponiveis = json.loads(connector.connection_for("ana")["campos_indisponiveis"])
    assert "email" in indisponiveis and "nome" in indisponiveis
    connection.close()


def test_the_stored_token_is_never_in_clear(env):
    connection, _p, connector, _c = env
    connect(connector)
    blob = connector.connection_for("ana")["token_cifrado"]
    assert b"testemunho-secreto-123" not in blob


def test_a_state_cannot_be_reused(env):
    _c, _p, connector, _ci = env
    url = connector.begin("ana", REDIRECT)
    state = url.split("state=")[1]
    connector.complete(state=state, code="codigo")
    with pytest.raises(AuthError):
        connector.complete(state=state, code="codigo")


def test_a_refused_consent_names_the_cause(env):
    _c, _p, connector, _ci = env
    url = connector.begin("ana", REDIRECT)
    state = url.split("state=")[1]
    with pytest.raises(AuthError) as err:
        connector.complete(state=state, error="user_cancelled_login")
    assert "user_cancelled_login" in str(err.value)


# ------------------------------------------------------------ fronteira
@pytest.mark.parametrize(
    "payload",
    [
        "li_at=AQEDAT...",
        {"cookie": "JSESSIONID=ajax:123"},
        {"senha": "minha-senha"},
        "Set-Cookie: li_at=abc",
        {"password": "x"},
    ],
)
def test_credential_material_is_refused_at_the_boundary(payload):
    with pytest.raises(CredentialSubmissionError):
        LinkedInConnector.reject_credential_material(payload)


def test_ordinary_payloads_pass_the_boundary():
    LinkedInConnector.reject_credential_material({"code": "abc", "state": "xyz"})


# ------------------------------------------------------------ ciclo de vida
def test_disconnecting_removes_the_data_and_revokes_the_token(env):
    _c, provider, connector, _ci = env
    connect(connector)
    assert connector.disconnect("ana") is True
    assert connector.connection_for("ana") is None
    assert provider.revoked == ["testemunho-secreto-123"]


def test_disconnecting_without_a_connection_changes_nothing(env):
    _c, _p, connector, _ci = env
    assert connector.disconnect("ana") is False


def test_an_expired_connection_asks_for_reconnection(env):
    _c, _p, connector, _ci = env
    connect(connector)
    connector.mark_expired("ana")
    row = connector.connection_for("ana")
    assert row["estado"] == EXPIRADA
    assert row["token_cifrado"] is None
    assert connector.needs_reconnection("ana") is True


def test_an_active_connection_does_not_ask_for_reconnection(env):
    _c, _p, connector, _ci = env
    connect(connector)
    assert connector.needs_reconnection("ana") is False
