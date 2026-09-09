import sqlite3

import pytest

from crivo.store.migrations import (
    SchemaError,
    TARGET_VERSION,
    apply_migrations,
    open_database,
    read_version,
)

EXPECTED_TABLES = {
    "schema_meta", "users", "sessions", "linkedin_connections",
    "provider_credentials", "resumes", "resume_extractions", "profile_versions",
    "runs", "jobs", "job_descriptions", "scores", "discards", "daily_quota",
    "collection_blocks", "data_subject_ops", "access_denials",
}

USER_SCOPED_TABLES = {
    "sessions", "linkedin_connections", "provider_credentials", "resumes",
    "resume_extractions", "profile_versions", "runs", "jobs", "scores",
    "discards", "daily_quota",
}


@pytest.fixture
def db(tmp_path):
    connection = open_database(tmp_path / "sub" / "crivo.db")
    yield connection
    connection.close()


def columns(connection, table):
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def seed_user(connection, user_id="u1", subject="sub-1"):
    connection.execute(
        "INSERT INTO users (user_id, subject_google, email, criado_em) "
        "VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00')",
        (user_id, subject, f"{user_id}@exemplo.br"),
    )


def test_schema_creates_the_expected_tables(db):
    found = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row["name"].startswith("sqlite_")
    }
    assert EXPECTED_TABLES <= found


def test_every_user_scoped_table_carries_the_user_identifier(db):
    for table in USER_SCOPED_TABLES:
        assert "user_id" in columns(db, table), table


def test_shared_description_table_has_no_user_identifier(db):
    assert "user_id" not in columns(db, "job_descriptions")


def test_referential_integrity_and_write_ahead_mode_are_active(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_version_is_recorded_after_opening(db):
    assert read_version(db) == TARGET_VERSION


def test_reopening_is_idempotent(tmp_path):
    path = tmp_path / "crivo.db"
    first = open_database(path)
    first.close()
    second = open_database(path)
    assert read_version(second) == TARGET_VERSION
    second.close()


def test_a_newer_schema_refuses_to_open(tmp_path):
    path = tmp_path / "crivo.db"
    connection = open_database(path)
    connection.execute(
        "UPDATE schema_meta SET valor = ? WHERE chave = 'versao_esquema'",
        (str(TARGET_VERSION + 5),),
    )
    connection.close()
    with pytest.raises(SchemaError) as err:
        open_database(path)
    assert str(TARGET_VERSION + 5) in str(err.value)


def test_deleting_a_user_removes_their_rows(db):
    seed_user(db)
    db.execute(
        "INSERT INTO resumes (resume_id, user_id, texto, origem, hash_conteudo, "
        "importado_em) VALUES ('r1', 'u1', 'texto', 'pdf', 'h', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO jobs (job_id, user_id, titulo, url, estado, primeira_vez_em, "
        "ultima_vez_em) VALUES ('j1', 'u1', 'SRE', 'http://x', 'novo', 'a', 'b')"
    )
    db.execute("DELETE FROM users WHERE user_id = 'u1'")
    assert db.execute("SELECT count(*) FROM resumes").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_deleting_a_user_preserves_the_shared_description(db):
    seed_user(db)
    db.execute(
        "INSERT INTO jobs (job_id, user_id, titulo, url, estado, primeira_vez_em, "
        "ultima_vez_em) VALUES ('j1', 'u1', 'SRE', 'http://x', 'novo', 'a', 'b')"
    )
    db.execute(
        "INSERT INTO job_descriptions (job_id, texto, coletada_em) "
        "VALUES ('j1', 'descricao integral', '2026-01-01')"
    )
    db.execute("DELETE FROM users WHERE user_id = 'u1'")
    assert db.execute("SELECT count(*) FROM job_descriptions").fetchone()[0] == 1


def test_deleting_a_user_preserves_the_record_that_it_happened(db):
    seed_user(db)
    db.execute(
        "INSERT INTO data_subject_ops (op_id, user_id, tipo, instante) "
        "VALUES ('o1', 'u1', 'exclusao', '2026-01-01')"
    )
    db.execute("DELETE FROM users WHERE user_id = 'u1'")
    assert db.execute("SELECT count(*) FROM data_subject_ops").fetchone()[0] == 1


def test_score_outside_the_valid_range_is_rejected(db):
    seed_user(db)
    db.execute(
        "INSERT INTO runs (run_id, user_id, estado, janela, solicitado_em) "
        "VALUES ('run1', 'u1', 'enfileirado', 'incremental', '2026-01-01')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO scores (job_id, run_id, passada, user_id, score, "
            "componentes, criado_em) VALUES "
            "('j1', 'run1', 'final', 'u1', 140, '{}', '2026-01-01')"
        )


def test_two_scoring_passes_coexist_for_the_same_job_and_run(db):
    seed_user(db)
    db.execute(
        "INSERT INTO runs (run_id, user_id, estado, janela, solicitado_em) "
        "VALUES ('run1', 'u1', 'enfileirado', 'incremental', '2026-01-01')"
    )
    for passada, score in (("provisoria", 40), ("final", 72)):
        db.execute(
            "INSERT INTO scores (job_id, run_id, passada, user_id, score, "
            "componentes, criado_em) VALUES (?, ?, ?, ?, ?, '{}', '2026-01-01')",
            ("j1", "run1", passada, "u1", score),
        )
    assert db.execute("SELECT count(*) FROM scores").fetchone()[0] == 2


def test_an_unknown_run_state_is_rejected(db):
    seed_user(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runs (run_id, user_id, estado, janela, solicitado_em) "
            "VALUES ('run1', 'u1', 'inventado', 'incremental', '2026-01-01')"
        )
