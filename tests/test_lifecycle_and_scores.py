from datetime import datetime, timezone

import pytest

from crivo.config import load_config
from crivo.pipeline.sources.guest import PRESENCIAL, Card
from crivo.scoring.ontology import load_ontology
from crivo.scoring.scorer import FINAL, PROVISORIA, Scorer
from crivo.store.lifecycle import (
    APLICADO,
    EXPIRADO,
    NOVO,
    JobLifecycle,
    LifecycleError,
)
from crivo.store.migrations import open_database
from crivo.store.repository import Repository
from crivo.store.scores import ScoreStore
from crivo.worker.enrichment_queue import EnrichmentQueue

AGORA = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def card(job_id="li-1"):
    return Card(
        job_id=job_id, titulo="SRE Manager", empresa="Fintech",
        url=f"https://exemplo.br/{job_id}", local="Sao Paulo, SP",
        modelo_trabalho=PRESENCIAL, publicada_em="2026-08-20",
    )


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    repo.insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana", "criado_em": "2026-01-01"},
    )
    repo.insert(
        "runs",
        {
            "run_id": "run-1", "user_id": "ana", "estado": "em_andamento",
            "janela": "incremental", "solicitado_em": "2026-08-21T09:00:00+00:00",
        },
    )
    yield connection, repo
    connection.close()


def add_job(repo, job_id="li-1", publicada="2026-08-20", estado=NOVO):
    repo.for_user("ana").insert(
        "jobs",
        {
            "job_id": job_id, "titulo": "SRE Manager",
            "url": f"https://exemplo.br/{job_id}", "estado": estado,
            "publicada_em": publicada,
            "primeira_vez_em": "2026-08-01", "ultima_vez_em": "2026-08-01",
        },
    )


# ------------------------------------------------------------------ estados
def test_the_user_choice_changes_the_state(env):
    connection, repo = env
    add_job(repo)
    ciclo = JobLifecycle(connection)
    ciclo.mark("ana", "li-1", APLICADO)
    assert ciclo.state_of("ana", "li-1") == APLICADO


def test_a_state_the_user_cannot_choose_is_refused(env):
    connection, repo = env
    add_job(repo)
    with pytest.raises(LifecycleError) as err:
        JobLifecycle(connection).mark("ana", "li-1", EXPIRADO)
    assert "expirado" in str(err.value)


def test_marking_an_unknown_job_is_refused(env):
    connection, _repo = env
    with pytest.raises(LifecycleError):
        JobLifecycle(connection).mark("ana", "inventada", APLICADO)


# ------------------------------------------------------------------ expiracao
def test_a_job_absent_from_many_runs_is_never_expired_for_that(env):
    """Ausencia nao prova que a vaga fechou: cada termo traz no maximo 25.

    A expiracao por ausencia foi aposentada em 23/10/2026 sem nunca ter rodado.
    Ligada, ela esconderia vaga aberta -- e reaparecer nao desfaz o estado.
    """
    from crivo.pipeline.collector import Collector
    from crivo.pipeline.sources.guest import SearchOutcome
    from test_collector import AQUI, FakeSource

    connection, repo = env
    add_job(repo, publicada="2026-08-21")
    for _ in range(5):
        fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card("li-2")])})
        Collector(connection, fonte).collect("ana", ["A"], AQUI, 24)
    assert JobLifecycle(connection).state_of("ana", "li-1") == NOVO


def _com_descricao(repo, job_id="li-1", emails=("rh@fintech.br",)):
    import json

    repo.insert(
        "job_descriptions",
        {
            "job_id": job_id, "texto": "Sobre a vaga",
            "emails_contato": json.dumps(list(emails)),
            "coletada_em": "2026-08-21",
        },
    )


def _emails(connection, job_id="li-1"):
    import json

    linha = connection.execute(
        "SELECT emails_contato FROM job_descriptions WHERE job_id = ?", (job_id,)
    ).fetchone()
    return json.loads(linha[0] or "[]")


def _encerrar(connection, job_id="li-1"):
    """A origem declarou a vaga encerrada (marca "nao aceita mais candidaturas")."""
    from crivo.pipeline.enricher import expirar_encerrada

    return expirar_encerrada(Repository(connection), job_id)


def test_a_closed_job_forgets_the_recruiter_contacts(env):
    """O e-mail do recrutador e dado de terceiro; vaga encerrada nao tem candidatura."""
    connection, repo = env
    add_job(repo)
    _com_descricao(repo)
    assert _encerrar(connection) is True
    assert JobLifecycle(connection).state_of("ana", "li-1") == EXPIRADO
    assert _emails(connection) == []


def test_a_closed_job_keeps_the_description_text(env):
    connection, repo = env
    add_job(repo)
    _com_descricao(repo)
    _encerrar(connection)
    texto = connection.execute(
        "SELECT texto FROM job_descriptions WHERE job_id = 'li-1'"
    ).fetchone()[0]
    assert texto == "Sobre a vaga"


def test_contacts_survive_while_someone_who_applied_still_has_the_job(env):
    """O registro e compartilhado: quem se candidatou mantem a vaga, e o contato."""
    connection, repo = env
    add_job(repo)
    _com_descricao(repo)
    repo.insert(
        "users",
        {"user_id": "bruno", "subject_google": "sub-bruno", "criado_em": "2026-01-01"},
    )
    repo.for_user("bruno").insert(
        "jobs",
        {
            "job_id": "li-1", "titulo": "SRE Manager",
            "url": "https://exemplo.br/li-1", "estado": APLICADO,
            "publicada_em": "2026-08-21",
            "primeira_vez_em": "2026-08-01", "ultima_vez_em": "2026-08-01",
        },
    )
    _encerrar(connection)
    assert _emails(connection) == ["rh@fintech.br"]
    assert JobLifecycle(connection).state_of("bruno", "li-1") == APLICADO


# ------------------------------------------------------------------ scores
@pytest.fixture
def scorer():
    return Scorer(load_config(), load_ontology())


def test_both_passes_coexist_for_the_same_run(env, scorer):
    connection, repo = env
    add_job(repo)
    store = ScoreStore(connection)
    perfil = {"competencias": ["k8s"], "nivel_inferido": "manager"}

    provisoria = scorer.score(perfil, {"titulo": "SRE Manager"}, passada=PROVISORIA)
    final = scorer.score(
        perfil, {"titulo": "SRE Manager"}, descricao="Kubernetes e Terraform",
        sinais=["top applicant"], passada=FINAL,
    )
    store.record("ana", "run-1", "li-1", provisoria)
    store.record("ana", "run-1", "li-1", final)

    passadas = {s.passada for s in store.history("ana", "li-1")}
    assert passadas == {PROVISORIA, FINAL}


def test_the_history_is_never_overwritten(env, scorer):
    connection, repo = env
    add_job(repo)
    repo.insert(
        "runs",
        {
            "run_id": "run-2", "user_id": "ana", "estado": "em_andamento",
            "janela": "incremental", "solicitado_em": "2026-08-22T09:00:00+00:00",
        },
    )
    store = ScoreStore(connection)
    perfil = {"competencias": ["k8s"], "nivel_inferido": "manager"}
    breakdown = scorer.score(perfil, {"titulo": "SRE Manager"}, passada=FINAL)

    store.record("ana", "run-1", "li-1", breakdown)
    store.record("ana", "run-2", "li-1", breakdown)
    assert len(store.history("ana", "li-1")) == 2


def test_the_ranking_orders_by_score(env, scorer):
    connection, repo = env
    for job_id in ("li-1", "li-2"):
        add_job(repo, job_id=job_id)
    store = ScoreStore(connection)
    perfil = {"competencias": ["k8s"], "nivel_inferido": "manager"}

    forte = scorer.score(perfil, {"titulo": "SRE Manager", "remoto": True},
                         descricao="Kubernetes", passada=PROVISORIA)
    fraco = scorer.score({"competencias": []}, {"titulo": "Analista", "blocker": "longe"},
                         passada=PROVISORIA)
    store.record("ana", "run-1", "li-1", fraco)
    store.record("ana", "run-1", "li-2", forte)
    assert store.ranking("ana", "run-1", PROVISORIA)[0] == "li-2"


def test_gaps_and_differentials_are_stored_with_the_score(env, scorer):
    connection, repo = env
    add_job(repo)
    store = ScoreStore(connection)
    breakdown = scorer.score({"competencias": ["k8s"]}, {"titulo": "SRE"}, passada=FINAL)
    guardado = store.record(
        "ana", "run-1", "li-1", breakdown,
        lacunas=("terraform",), diferenciais=("kubernetes",),
    )
    assert guardado.lacunas == ("terraform",)
    assert store.for_run("ana", "run-1", FINAL)[0].diferenciais == ("kubernetes",)


def test_the_run_average_is_the_kpi_that_can_be_tracked(env, scorer):
    connection, repo = env
    for job_id in ("li-1", "li-2"):
        add_job(repo, job_id=job_id)
    store = ScoreStore(connection)
    perfil = {"competencias": ["k8s"], "nivel_inferido": "manager"}
    for job_id in ("li-1", "li-2"):
        store.record(
            "ana", "run-1", job_id,
            scorer.score(perfil, {"titulo": "SRE Manager"}, passada=FINAL),
        )
    assert store.average("ana", "run-1", FINAL) is not None


def test_a_run_without_scores_has_no_average(env):
    connection, _repo = env
    assert ScoreStore(connection).average("ana", "run-1") is None


# ------------------------------------------------------ ponto de suspensao
def test_survivors_become_pending_enrichment_requests(env):
    connection, repo = env
    add_job(repo)
    fila = EnrichmentQueue(connection)
    criados = fila.enqueue("ana", "run-1", [card()])
    assert len(criados) == 1
    assert [p.job_id for p in fila.pending_for("run-1")] == ["li-1"]
    assert fila.is_drained("run-1") is False


def test_enqueueing_the_same_job_twice_creates_one_request(env):
    connection, repo = env
    add_job(repo)
    fila = EnrichmentQueue(connection)
    fila.enqueue("ana", "run-1", [card()])
    assert fila.enqueue("ana", "run-1", [card()]) == []


def test_a_run_is_drained_once_every_request_is_settled(env):
    connection, repo = env
    add_job(repo)
    fila = EnrichmentQueue(connection)
    fila.enqueue("ana", "run-1", [card()])
    pedido = fila.pending_for("run-1")[0]
    fila.settle(pedido.request_id)
    assert fila.is_drained("run-1") is True


def test_a_failed_request_also_drains_the_run(env):
    connection, repo = env
    add_job(repo)
    fila = EnrichmentQueue(connection)
    fila.enqueue("ana", "run-1", [card()])
    fila.settle_all("run-1", atendidos=set())
    assert fila.is_drained("run-1") is True


def test_the_enrichment_process_sees_requests_from_any_run(env):
    connection, repo = env
    for job_id in ("li-1", "li-2"):
        add_job(repo, job_id=job_id)
    fila = EnrichmentQueue(connection)
    fila.enqueue("ana", "run-1", [card("li-1"), card("li-2")])
    assert [p.job_id for p in fila.next_batch()] == ["li-1", "li-2"]


def test_settled_requests_leave_the_batch(env):
    connection, repo = env
    for job_id in ("li-1", "li-2"):
        add_job(repo, job_id=job_id)
    fila = EnrichmentQueue(connection)
    fila.enqueue("ana", "run-1", [card("li-1"), card("li-2")])
    fila.settle_all("run-1", atendidos={"li-1"})
    assert fila.next_batch() == []
