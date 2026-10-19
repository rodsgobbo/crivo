"""Expurgo das vagas que nenhuma busca traz ha mais de trinta dias."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from crivo.config import load_config
from crivo.store.expurgo import CHAVE, ExpurgoDeVagas
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

AGORA = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def _ha(dias: int) -> str:
    return (AGORA - timedelta(days=dias)).isoformat(timespec="seconds")


@pytest.fixture
def banco(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    for user_id in ("ana", "bia"):
        repo.insert(
            "users",
            {"user_id": user_id, "subject_google": f"sub-{user_id}",
             "criado_em": "2026-01-01"},
        )
    repo.insert(
        "runs",
        {"run_id": "run-1", "user_id": "ana", "estado": "concluido",
         "janela": "incremental", "solicitado_em": _ha(40)},
    )
    yield connection
    connection.close()


def vaga(connection, job_id, vista_ha, user_id="ana", estado="novo",
         descricao=True, nota=True):
    repo = Repository(connection)
    repo.for_user(user_id).insert(
        "jobs",
        {"job_id": job_id, "titulo": "SRE", "empresa": "Acme",
         "url": f"https://x/{job_id}", "estado": estado, "flags": "[]",
         "primeira_vez_em": _ha(vista_ha + 5), "ultima_vez_em": _ha(vista_ha)},
    )
    if descricao and not connection.execute(
        "SELECT 1 FROM job_descriptions WHERE job_id = ?", (job_id,)
    ).fetchone():
        repo.insert(
            "job_descriptions",
            {"job_id": job_id, "texto": "Kubernetes", "coletada_em": _ha(vista_ha)},
        )
    if nota:
        repo.for_user(user_id).insert(
            "scores",
            {"job_id": job_id, "run_id": "run-1", "passada": "final",
             "score": 70, "componentes": json.dumps({}), "criado_em": _ha(vista_ha)},
        )


def existe(connection, tabela, job_id, user_id=None):
    sql = f"SELECT 1 FROM {tabela} WHERE job_id = ?"
    params = [job_id]
    if user_id:
        sql += " AND user_id = ?"
        params.append(user_id)
    return connection.execute(sql, params).fetchone() is not None


# ------------------------------------------------------------- criterio
def test_a_job_no_search_brought_for_30_days_is_purged_with_its_trail(banco):
    vaga(banco, "li-velha", vista_ha=31)
    resultado = ExpurgoDeVagas(banco, 30).executar(AGORA)
    assert resultado.vagas == 1
    assert not existe(banco, "jobs", "li-velha")
    assert not existe(banco, "scores", "li-velha")
    assert not existe(banco, "job_descriptions", "li-velha")


def test_an_old_posting_still_showing_up_in_searches_stays(banco):
    """Publicada ha meses, mas a busca de ontem a trouxe: ainda esta no ar."""
    vaga(banco, "li-viva", vista_ha=1)
    banco.execute(
        "UPDATE jobs SET publicada_em = ?, primeira_vez_em = ? WHERE job_id = 'li-viva'",
        (_ha(90)[:10], _ha(80)),
    )
    ExpurgoDeVagas(banco, 30).executar(AGORA)
    assert existe(banco, "jobs", "li-viva")


def test_a_job_seen_29_days_ago_stays(banco):
    vaga(banco, "li-quase", vista_ha=29)
    ExpurgoDeVagas(banco, 30).executar(AGORA)
    assert existe(banco, "jobs", "li-quase")


# ------------------------------------------------------------- protecoes
def test_a_job_the_user_applied_to_is_never_purged(banco):
    vaga(banco, "li-aplicada", vista_ha=200, estado="aplicado")
    ExpurgoDeVagas(banco, 30).executar(AGORA)
    assert existe(banco, "jobs", "li-aplicada")
    assert existe(banco, "job_descriptions", "li-aplicada")


def test_a_job_waiting_for_its_description_is_not_purged(banco):
    vaga(banco, "li-na-fila", vista_ha=40, descricao=False)
    Repository(banco).for_user("ana").insert(
        "enrichment_requests",
        {"request_id": "r1", "run_id": "run-1", "job_id": "li-na-fila",
         "url": "https://x", "estado": "pendente", "criado_em": _ha(40)},
    )
    ExpurgoDeVagas(banco, 30).executar(AGORA)
    assert existe(banco, "jobs", "li-na-fila")


def test_a_shared_description_stays_while_another_user_has_the_job(banco):
    """A descricao e uma so para todos; sai quando o ultimo dono a perde."""
    vaga(banco, "li-comum", vista_ha=40, user_id="ana")
    vaga(banco, "li-comum", vista_ha=2, user_id="bia", nota=False)
    ExpurgoDeVagas(banco, 30).executar(AGORA)
    assert not existe(banco, "jobs", "li-comum", "ana")
    assert existe(banco, "jobs", "li-comum", "bia")
    assert existe(banco, "job_descriptions", "li-comum")


def test_a_description_of_an_unrelated_job_is_left_alone(banco):
    Repository(banco).insert(
        "job_descriptions",
        {"job_id": "li-sem-vaga", "texto": "x", "coletada_em": _ha(90)},
    )
    vaga(banco, "li-velha", vista_ha=31)
    ExpurgoDeVagas(banco, 30).executar(AGORA)
    assert existe(banco, "job_descriptions", "li-sem-vaga")


# ------------------------------------------------------------- cadencia
def test_it_runs_once_a_day(banco):
    expurgo = ExpurgoDeVagas(banco, 30)
    vaga(banco, "li-a", vista_ha=31)
    assert expurgo.talvez_executar(AGORA).vagas == 1

    vaga(banco, "li-b", vista_ha=31)
    assert expurgo.talvez_executar(AGORA + timedelta(hours=3)) is None
    assert existe(banco, "jobs", "li-b"), "segunda chamada no mesmo dia nao apaga"

    assert expurgo.talvez_executar(AGORA + timedelta(days=1)).vagas == 1
    assert banco.execute(
        "SELECT valor FROM schema_meta WHERE chave = ?", (CHAVE,)
    ).fetchone()[0] == (AGORA + timedelta(days=1)).date().isoformat()


def test_zero_days_turns_the_purge_off(banco):
    vaga(banco, "li-velha", vista_ha=400)
    assert ExpurgoDeVagas(banco, 0).talvez_executar(AGORA) is None
    assert existe(banco, "jobs", "li-velha")


def test_the_default_configuration_purges_after_30_days():
    assert load_config().retention.vagas_sumidas_dias == 30
