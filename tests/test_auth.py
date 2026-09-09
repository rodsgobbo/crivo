from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from crivo.store.migrations import open_database
from crivo.web.auth import (
    AuthError,
    AuthService,
    GoogleIdentityProvider,
    Identity,
    _digest,
    _s256,
)

REDIRECT = "https://app.exemplo.br/auth/callback"


class FakeProvider:
    name = "google"

    def __init__(self, identity=None):
        self.identity = identity or Identity(subject="sub-ana", email="ana@exemplo.br")
        self.exchanges = []

    def authorization_url(self, state, code_challenge, redirect_uri):
        return f"https://provedor/auth?state={state}&cc={code_challenge}"

    def exchange(self, code, code_verifier, redirect_uri):
        self.exchanges.append((code, code_verifier, redirect_uri))
        return self.identity


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    provider = FakeProvider()
    yield connection, provider, AuthService(connection, provider, 3600)
    connection.close()


def enter(service, provider=None, code="codigo"):
    request = service.begin(REDIRECT)
    return service.complete(state=request.state, code=code)


def test_entering_creates_a_session_for_a_new_user(env):
    connection, _provider, service = env
    session = enter(service)
    assert session.user_id
    row = connection.execute("SELECT * FROM users").fetchone()
    assert row["subject_google"] == "sub-ana"


def test_identity_comes_from_the_subject_not_the_email(env):
    connection, provider, service = env
    first = enter(service)
    provider.identity = Identity(subject="sub-ana", email="ana.nova@outra.br")
    second = enter(service)
    assert first.user_id == second.user_id
    assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_a_reused_email_on_a_new_subject_is_a_different_user(env):
    connection, provider, service = env
    first = enter(service)
    provider.identity = Identity(subject="sub-bruno", email="ana@exemplo.br")
    second = enter(service)
    assert first.user_id != second.user_id
    assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 2


def test_the_database_never_stores_the_token_in_clear(env):
    connection, _provider, service = env
    session = enter(service)
    stored = connection.execute("SELECT testemunho_hash FROM sessions").fetchone()[0]
    assert stored != session.token
    assert stored == _digest(session.token)


def test_a_valid_token_resolves_to_its_session(env):
    _connection, _provider, service = env
    session = enter(service)
    assert service.session_for(session.token)["user_id"] == session.user_id


def test_an_unknown_or_absent_token_resolves_to_nothing(env):
    _connection, _provider, service = env
    enter(service)
    assert service.session_for("inventado") is None
    assert service.session_for(None) is None


def test_an_expired_session_stops_resolving(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    service = AuthService(connection, FakeProvider(), session_ttl_seconds=-1)
    session = enter(service)
    assert service.session_for(session.token) is None
    assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    connection.close()


def test_a_cancelled_authorization_names_the_cause(env):
    _connection, _provider, service = env
    request = service.begin(REDIRECT)
    with pytest.raises(AuthError) as err:
        service.complete(state=request.state, error="access_denied")
    assert "access_denied" in str(err.value)


def test_a_return_without_a_code_is_refused(env):
    _connection, _provider, service = env
    request = service.begin(REDIRECT)
    with pytest.raises(AuthError):
        service.complete(state=request.state)


def test_an_unknown_state_is_refused(env):
    _connection, _provider, service = env
    service.begin(REDIRECT)
    with pytest.raises(AuthError) as err:
        service.complete(state="estado-forjado", code="c")
    assert "desconhecido" in str(err.value)


def test_a_state_cannot_be_used_twice(env):
    _connection, _provider, service = env
    request = service.begin(REDIRECT)
    service.complete(state=request.state, code="codigo")
    with pytest.raises(AuthError):
        service.complete(state=request.state, code="codigo")


def test_a_cancelled_flow_also_consumes_its_state(env):
    _connection, _provider, service = env
    request = service.begin(REDIRECT)
    with pytest.raises(AuthError):
        service.complete(state=request.state, error="access_denied")
    with pytest.raises(AuthError) as err:
        service.complete(state=request.state, code="codigo")
    assert "desconhecido" in str(err.value)


def test_the_verifier_matches_the_challenge_that_was_sent(env):
    _connection, provider, service = env
    request = service.begin(REDIRECT)
    challenge = parse_qs(urlparse(request.url).query)["cc"][0]
    service.complete(state=request.state, code="codigo")
    _code, verifier, _redirect = provider.exchanges[0]
    assert _s256(verifier) == challenge


def test_logging_out_removes_only_the_session(env):
    connection, _provider, service = env
    session = enter(service)
    connection.execute(
        "INSERT INTO linkedin_connections (user_id, campos_identidade, "
        "escopos_concedidos, campos_indisponiveis, estado, token_cifrado, "
        "conectada_em) VALUES (?, '{}', '[]', '[]', 'ativa', X'00', '2026-01-01')",
        (session.user_id,),
    )
    assert service.logout(session.token) is True
    assert service.session_for(session.token) is None
    assert connection.execute(
        "SELECT count(*) FROM linkedin_connections"
    ).fetchone()[0] == 1


def test_logging_out_an_unknown_token_changes_nothing(env):
    _connection, _provider, service = env
    assert service.logout("inventado") is False


def test_purging_removes_expired_sessions_and_flows(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    service = AuthService(connection, FakeProvider(), session_ttl_seconds=-1)
    enter(service)
    service.begin(REDIRECT)
    connection.execute("UPDATE auth_flows SET expira_em = '2020-01-01T00:00:00+00:00'")
    assert service.purge_expired() == 1
    assert connection.execute("SELECT count(*) FROM auth_flows").fetchone()[0] == 0
    connection.close()


def test_the_google_authorization_url_carries_the_expected_parameters():
    provider = GoogleIdentityProvider("id-cliente", "segredo")
    url = provider.authorization_url("estado", "desafio", REDIRECT)
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["estado"]
    assert query["redirect_uri"] == [REDIRECT]
    assert "openid" in query["scope"][0]
