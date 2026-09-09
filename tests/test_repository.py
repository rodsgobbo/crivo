import pytest

from crivo.store.migrations import StoreError, open_database
from crivo.store.repository import (
    CrossUserAccessError,
    Repository,
    ScopeRequiredError,
    UnknownTableError,
)


@pytest.fixture
def repo(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repository = Repository(connection)
    for user_id, subject in (("ana", "sub-ana"), ("bruno", "sub-bruno")):
        repository.insert(
            "users",
            {
                "user_id": user_id,
                "subject_google": subject,
                "email": f"{user_id}@exemplo.br",
                "criado_em": "2026-01-01T00:00:00+00:00",
            },
        )
    yield repository
    connection.close()


def add_job(scope, job_id, titulo="SRE Manager"):
    scope.insert(
        "jobs",
        {
            "job_id": job_id,
            "titulo": titulo,
            "url": f"https://exemplo.br/{job_id}",
            "estado": "novo",
            "primeira_vez_em": "2026-01-01",
            "ultima_vez_em": "2026-01-01",
        },
    )


def test_insert_fills_the_user_identifier_without_being_asked(repo):
    ana = repo.for_user("ana")
    add_job(ana, "j1")
    row = repo.connection.execute("SELECT user_id FROM jobs").fetchone()
    assert row["user_id"] == "ana"


def test_a_query_never_returns_another_users_rows(repo):
    add_job(repo.for_user("ana"), "j1")
    add_job(repo.for_user("bruno"), "j2")
    assert [r["job_id"] for r in repo.for_user("ana").select("jobs")] == ["j1"]
    assert [r["job_id"] for r in repo.for_user("bruno").select("jobs")] == ["j2"]


def test_an_explicit_where_clause_cannot_widen_the_scope(repo):
    add_job(repo.for_user("ana"), "j1")
    add_job(repo.for_user("bruno"), "j2")
    found = repo.for_user("ana").select("jobs", where="1 = 1")
    assert [r["job_id"] for r in found] == ["j1"]


def test_a_user_table_cannot_be_read_without_a_scope(repo):
    with pytest.raises(ScopeRequiredError) as err:
        repo.select("jobs")
    assert "jobs" in str(err.value)


def test_naming_another_user_is_refused(repo):
    with pytest.raises(CrossUserAccessError) as err:
        repo.for_user("ana").insert(
            "jobs",
            {
                "job_id": "j9",
                "user_id": "bruno",
                "titulo": "x",
                "url": "u",
                "estado": "novo",
                "primeira_vez_em": "a",
                "ultima_vez_em": "b",
            },
        )
    assert "ana" in str(err.value) and "bruno" in str(err.value)


def test_a_refused_cross_user_attempt_is_recorded(repo):
    with pytest.raises(CrossUserAccessError):
        repo.for_user("ana").insert(
            "jobs",
            {
                "job_id": "j9",
                "user_id": "bruno",
                "titulo": "x",
                "url": "u",
                "estado": "novo",
                "primeira_vez_em": "a",
                "ultima_vez_em": "b",
            },
        )
    denials = repo.select("access_denials")
    assert len(denials) == 1
    assert denials[0]["user_id"] == "ana"
    assert "bruno" in denials[0]["recurso"]


def test_update_only_reaches_the_scoped_user(repo):
    add_job(repo.for_user("ana"), "j1")
    add_job(repo.for_user("bruno"), "j1")
    changed = repo.for_user("ana").update("jobs", {"estado": "aplicado"})
    assert changed == 1
    estados = {
        (r["user_id"], r["estado"])
        for r in repo.connection.execute("SELECT user_id, estado FROM jobs")
    }
    assert estados == {("ana", "aplicado"), ("bruno", "novo")}


def test_delete_only_reaches_the_scoped_user(repo):
    add_job(repo.for_user("ana"), "j1")
    add_job(repo.for_user("bruno"), "j1")
    assert repo.for_user("ana").delete("jobs") == 1
    assert repo.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_the_shared_description_table_is_readable_from_any_scope(repo):
    repo.insert(
        "job_descriptions",
        {"job_id": "j1", "texto": "descricao", "coletada_em": "2026-01-01"},
    )
    assert len(repo.for_user("ana").select("job_descriptions")) == 1
    assert len(repo.for_user("bruno").select("job_descriptions")) == 1


def test_an_unclassified_table_is_refused(repo):
    with pytest.raises(UnknownTableError) as err:
        repo.for_user("ana").select("tabela_inventada")
    assert "tabela_inventada" in str(err.value)


def test_an_empty_scope_is_refused(repo):
    with pytest.raises(ScopeRequiredError):
        repo.for_user("")


def test_a_failed_write_names_the_operation_and_the_cause(repo):
    add_job(repo.for_user("ana"), "j1")
    with pytest.raises(StoreError) as err:
        add_job(repo.for_user("ana"), "j1")
    assert "INSERT INTO jobs" in str(err.value)
    assert "IntegrityError" in str(err.value)


def test_an_update_without_columns_is_refused(repo):
    with pytest.raises(StoreError):
        repo.for_user("ana").update("jobs", {"user_id": "ana"})
