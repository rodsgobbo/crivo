"""Vaga que nao aceita mais candidaturas sai do relatorio."""

from __future__ import annotations

import pytest

from crivo.config import load_config
from crivo.pipeline.sources.guest import parse_job_posting, vaga_encerrada
from crivo.report.renderer import ReportRenderer
from test_enricher import FakeSource, card, enricher, env  # noqa: F401
from test_report import add_vaga
from test_report import env as env_relatorio  # noqa: F401

#: O topo da pagina publica de vaga fechada, no formato conhecido do LinkedIn.
FECHADA = """
<section class="top-card-layout">
  <h1 class="top-card-layout__title">SRE Manager</h1>
  <figure class="closed-job">
    <figcaption class="closed-job__flavor--closed">
      No longer accepting applications
    </figcaption>
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
