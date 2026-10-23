"""Vaga que nao aceita mais candidaturas sai do relatorio."""

from __future__ import annotations

import pytest

from crivo.config import load_config
from crivo.pipeline.sources.guest import parse_job_posting, vaga_encerrada
from crivo.report.renderer import ReportRenderer
from test_enricher import FakeSource, card, enricher, env  # noqa: F401
from test_report import add_vaga
from test_report import env as env_relatorio  # noqa: F401

#: O topo da pagina publica de uma vaga fechada de verdade: "SRE Tech Lead
#: Manager, Product - USDS", li-4474369358, lida em 21/10/2026. O LinkedIn
#: mostrava "No longer accepting applications" a quem estava logado; a pagina
#: publica, em portugues, traz o mesmo bloco `closed-job`.
FECHADA = """
<section class="top-card-layout">
  <h1 class="top-card-layout__title">SRE Tech Lead Manager, Product - USDS</h1>
  <figure class="closed-job closed-job__flavor topcard__flavor-row">
    <span class="closed-job__icon closed-job__icon--error-pebble lazy-load"></span>
    <figcaption class="closed-job__flavor--closed">Não aceita mais candidaturas</figcaption>
  </figure>
</section>
<div class="show-more-less-html__markup">Sobre a vaga: Kubernetes</div>
"""

ABERTA = """
<section class="top-card-layout">
  <h1 class="top-card-layout__title">SRE Manager</h1>
  <span class="num-applicants__caption">36 candidaturas</span>
  <button data-modal="job-details-topcard-apply-modal">Candidatar-se</button>
</section>
<div class="show-more-less-html__markup">Sobre a vaga: Kubernetes</div>
"""


# ------------------------------------------------------------ deteccao
@pytest.mark.parametrize("pagina", [
    FECHADA,
    "<p>No longer accepting applications</p>",
    "<p>Não aceita mais candidaturas</p>",
    "<p>Nao esta aceitando mais candidaturas</p>",
])
def test_these_pages_say_the_job_is_closed(pagina):
    assert vaga_encerrada(pagina)


def test_an_open_page_is_not_closed():
    assert not vaga_encerrada(ABERTA)


def test_a_closed_page_keeps_its_description_and_says_it_is_closed():
    dados = parse_job_posting(FECHADA, "li-1")
    assert dados["encerrada"] is True
    assert "Kubernetes" in dados["texto"]
    assert parse_job_posting(ABERTA, "li-1")["encerrada"] is False


def test_a_closed_page_without_a_body_is_an_answer_and_not_an_error():
    """Sem corpo e sem a marca, a pagina mudou de forma; com a marca, fechou."""
    dados = parse_job_posting('<figure class="closed-job"></figure>', "li-1")
    assert dados["encerrada"] is True
    assert dados["texto"] == ""


# ---------------------------------------------------------- enriquecedor
class FonteDeVagaFechada(FakeSource):
    def describe(self, job_id, url):
        return {**super().describe(job_id, url), "encerrada": True}


def test_a_closed_job_expires_for_everyone_who_has_it(env):
    connection, _c, _g = env
    enricher(env, FonteDeVagaFechada()).enrich("ana", [card()])
    estados = dict(connection.execute("SELECT user_id, estado FROM jobs"))
    assert estados == {"ana": "expirado", "bruno": "expirado"}


def test_a_job_someone_applied_to_keeps_its_state(env):
    """A candidatura existe mesmo com a vaga fechada."""
    connection, _c, _g = env
    connection.execute("UPDATE jobs SET estado = 'aplicado' WHERE user_id = 'bruno'")
    enricher(env, FonteDeVagaFechada()).enrich("ana", [card()])
    estados = dict(connection.execute("SELECT user_id, estado FROM jobs"))
    assert estados == {"ana": "expirado", "bruno": "aplicado"}


def test_an_open_job_is_left_alone(env):
    connection, _c, _g = env
    enricher(env, FakeSource()).enrich("ana", [card()])
    assert {e for (e,) in connection.execute("SELECT estado FROM jobs")} == {"novo"}


# ------------------------------------------------------------- relatorio
def test_the_report_drops_closed_jobs_and_says_how_many(env_relatorio):
    connection, repo = env_relatorio
    add_vaga(repo, job_id="li-1", titulo="Vaga Aberta")
    add_vaga(repo, job_id="li-2", titulo="Vaga Fechada", estado="expirado")
    html = ReportRenderer(connection, load_config()).render_run("ana", "run-1")
    assert "Vaga Aberta" in html
    assert "Vaga Fechada" not in html
    texto = " ".join(html.split())
    assert "1 vaga deste run não aceita mais candidaturas e saiu da lista" in texto


# --------------------------------------------------- reconferencia do topo
import json  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from crivo.pipeline.governor import CollectionBlocked, RateGovernor  # noqa: E402
from crivo.pipeline.stages import ReconferenciaStage  # noqa: E402
from crivo.store.migrations import open_database  # noqa: E402
from crivo.store.repository import Repository  # noqa: E402
from crivo.worker.runner import RunContext  # noqa: E402

AGORA = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


class FonteQueConfere:
    def __init__(self, fechadas=(), bloqueia=False):
        self.fechadas = set(fechadas)
        self.bloqueia = bloqueia
        self.conferidas = []

    def describe(self, job_id, url):
        if self.bloqueia:
            raise CollectionBlocked("barrada")
        self.conferidas.append(job_id)
        return {"texto": "x", "encerrada": job_id in self.fechadas}


@pytest.fixture
def run_pontuado(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    repo.insert(
        "users", {"user_id": "ana", "subject_google": "s", "criado_em": "2026-01-01"}
    )
    repo.insert(
        "runs",
        {"run_id": "r1", "user_id": "ana", "estado": "em_andamento",
         "janela": "ampla", "solicitado_em": AGORA.isoformat()},
    )

    def vaga(job_id, score, descrita_ha_horas, estado="novo"):
        repo.for_user("ana").insert("jobs", {
            "job_id": job_id, "titulo": "SRE", "url": f"https://x/{job_id}",
            "estado": estado, "primeira_vez_em": "2026-10-01",
            "ultima_vez_em": "2026-10-21",
        })
        repo.insert("job_descriptions", {
            "job_id": job_id, "texto": "x",
            "coletada_em": (AGORA - timedelta(hours=descrita_ha_horas)).isoformat(),
        })
        repo.for_user("ana").insert("scores", {
            "job_id": job_id, "run_id": "r1", "passada": "final", "score": score,
            "componentes": json.dumps({}), "criado_em": AGORA.isoformat(),
        })

    yield connection, vaga
    connection.close()


def reconferir(connection, fonte):
    config = load_config()
    estagio = ReconferenciaStage(
        connection, fonte, RateGovernor(connection, config, sleep=lambda s: None),
        agora=lambda: AGORA,
    )
    estagio.run(RunContext(
        run_id="r1", user_id="ana", janela="ampla", config=config,
        scope=Repository(connection).for_user("ana"),
    ))


def estado(connection, job_id):
    return connection.execute(
        "SELECT estado FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()[0]


def test_a_top_job_closed_after_its_description_was_read_expires(run_pontuado):
    """O caso de 21/10/2026: lida aberta, fechada depois, ainda no topo."""
    connection, vaga = run_pontuado
    vaga("li-fechou", 90, descrita_ha_horas=48)
    vaga("li-aberta", 80, descrita_ha_horas=48)
    reconferir(connection, FonteQueConfere(fechadas={"li-fechou"}))
    assert estado(connection, "li-fechou") == "expirado"
    assert estado(connection, "li-aberta") == "novo"


def test_a_description_read_in_this_run_is_not_fetched_again(run_pontuado):
    """A lida agora ja passou pela verificacao; reconferir seria requisicao a toa."""
    connection, vaga = run_pontuado
    vaga("li-recente", 90, descrita_ha_horas=2)
    fonte = FonteQueConfere()
    reconferir(connection, fonte)
    assert fonte.conferidas == []


def test_below_the_threshold_only_the_top_is_checked(run_pontuado):
    """Reconferir as ~200 vagas antigas de um run custaria horas de governador."""
    connection, vaga = run_pontuado
    for i in range(ReconferenciaStage.LIMITE + 5):
        vaga(f"li-{i:03d}", 60 - i, descrita_ha_horas=48)
    fonte = FonteQueConfere()
    reconferir(connection, fonte)
    assert len(fonte.conferidas) == ReconferenciaStage.LIMITE
    assert fonte.conferidas[0] == "li-000"


def test_remote_sources_and_applied_jobs_are_not_checked(run_pontuado):
    """Vaga remota nao tem pagina no LinkedIn; a aplicada nao muda de estado."""
    connection, vaga = run_pontuado
    vaga("jy-1", 95, descrita_ha_horas=48)
    vaga("li-aplicada", 90, descrita_ha_horas=48, estado="aplicado")
    fonte = FonteQueConfere()
    reconferir(connection, fonte)
    assert fonte.conferidas == []


def test_a_block_stops_the_check_without_breaking_the_run(run_pontuado):
    connection, vaga = run_pontuado
    vaga("li-1", 90, descrita_ha_horas=48)
    reconferir(connection, FonteQueConfere(bloqueia=True))
    assert estado(connection, "li-1") == "novo"


def test_a_highlight_below_the_top_twenty_is_also_checked(run_pontuado):
    """A vaga de 23/10/2026 veio da busca e fechou: a busca nao prova vaga aberta."""
    connection, vaga = run_pontuado
    for i in range(ReconferenciaStage.LIMITE):
        vaga(f"li-{i:03d}", 99, descrita_ha_horas=2)  # topo, lidas agora
    vaga("li-destaque", 75, descrita_ha_horas=48)
    vaga("li-fraca", 40, descrita_ha_horas=48)
    fonte = FonteQueConfere(fechadas={"li-destaque"})
    reconferir(connection, fonte)
    assert fonte.conferidas == ["li-destaque"]
    assert estado(connection, "li-destaque") == "expirado"


def test_a_job_from_the_extension_is_checked_wherever_it_ranks(run_pontuado):
    connection, vaga = run_pontuado
    for i in range(ReconferenciaStage.LIMITE):
        vaga(f"li-{i:03d}", 99, descrita_ha_horas=2)
    vaga("li-extensao", 30, descrita_ha_horas=48)
    connection.execute("UPDATE jobs SET busca = 'extensao' WHERE job_id = 'li-extensao'")
    fonte = FonteQueConfere()
    reconferir(connection, fonte)
    assert fonte.conferidas == ["li-extensao"]


def test_the_whole_check_has_a_ceiling(run_pontuado):
    connection, vaga = run_pontuado
    for i in range(ReconferenciaStage.TETO + 15):
        vaga(f"li-{i:03d}", 90, descrita_ha_horas=48)
    fonte = FonteQueConfere()
    reconferir(connection, fonte)
    assert len(fonte.conferidas) == ReconferenciaStage.TETO
