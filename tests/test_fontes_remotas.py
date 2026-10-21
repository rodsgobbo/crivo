"""Fontes de vaga remota: Jobicy e We Work Remotely.

Nenhum teste aqui sai para a rede. As fontes recebem um `http` falso que devolve
cargas no formato real de cada provedor -- as amostras abaixo foram copiadas da
estrutura das respostas de 16/10/2026, reduzidas ao que o crivo le.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from crivo.pipeline.sources.guest import REMOTO, CollectionError, MultiPortalSource
from crivo.pipeline.sources.remotas import (
    CadenciaRemota,
    Jobicy,
    WeWorkRemotely,
    aceita_o_brasil,
)
from crivo.store.migrations import open_database

JOBICY = {
    "friendlyNotice": "Please ensure Jobicy is clearly credited...",
    "jobs": [
        {
            "id": 101, "jobTitle": "Site Reliability Engineer",
            "companyName": "Acme Cloud", "url": "https://jobicy.com/jobs/101-sre",
            "jobGeo": "Anywhere", "pubDate": "2026-10-15 09:00:00",
            "jobDescription": "<p>We run <b>Kubernetes</b> at scale.</p>",
            "annualSalaryMin": 90000, "annualSalaryMax": 120000,
            "salaryCurrency": "USD",
        },
        {
            "id": 102, "jobTitle": "Platform Engineering Manager",
            "companyName": "Beta", "url": "https://jobicy.com/jobs/102-pem",
            "jobGeo": "LATAM, Canada, USA", "pubDate": "2026-10-14 10:00:00",
            "jobDescription": "<p>Lead a platform team.</p>",
        },
        {
            "id": 103, "jobTitle": "DevOps Lead", "companyName": "Gamma",
            "url": "https://jobicy.com/jobs/103-devops", "jobGeo": "USA",
            "pubDate": "2026-10-13 10:00:00", "jobDescription": "<p>US only.</p>",
        },
    ],
}

WWR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>LaunchDarkly: Engineering Manager, Cloud Engineering</title>
  <region>Anywhere in the World</region>
  <pubDate>Wed, 15 Oct 2026 12:00:00 +0000</pubDate>
  <description>&lt;p&gt;Manage the cloud team.&lt;/p&gt;</description>
  <link>https://weworkremotely.com/remote-jobs/launchdarkly-em-cloud</link>
</item>
<item>
  <title>Contoso: Senior SRE</title>
  <region>USA Only</region>
  <pubDate>Tue, 14 Oct 2026 12:00:00 +0000</pubDate>
  <description>&lt;p&gt;US payroll.&lt;/p&gt;</description>
  <link>https://weworkremotely.com/remote-jobs/contoso-sre</link>
</item>
</channel></rss>"""


class Resposta:
    def __init__(self, corpo):
        self._corpo = corpo
        self.text = corpo if isinstance(corpo, str) else json.dumps(corpo)

    def json(self):
        return self._corpo if not isinstance(self._corpo, str) else json.loads(self._corpo)


def http_que_devolve(corpo):
    chamadas = []

    def http(url, params):
        chamadas.append((url, dict(params)))
        return Resposta(corpo)

    http.chamadas = chamadas
    return http


# ------------------------------------------------------------ elegibilidade
@pytest.mark.parametrize("onde", [
    "Anywhere", "Anywhere in the World", "Worldwide", "LATAM", "Latin America",
    "LATAM, Canada, USA", "Americas", "Brazil", "",
])
def test_these_hiring_regions_include_someone_living_in_brazil(onde):
    assert aceita_o_brasil(onde)


@pytest.mark.parametrize("onde", ["USA Only", "USA", "Europe Only", "UK", "Canada Only"])
def test_these_hiring_regions_exclude_someone_living_in_brazil(onde):
    """Vaga remota restrita a outro pais e o mesmo problema das frentes do LinkedIn."""
    assert not aceita_o_brasil(onde)


# ------------------------------------------------------------------ Jobicy
def test_jobicy_becomes_remote_cards_with_the_source_credited():
    coleta = Jobicy(http=http_que_devolve(JOBICY)).coletar()
    por_id = {c.job_id: c for c in coleta.cards}
    sre = por_id["jy-101"]
    assert sre.modelo_trabalho == REMOTO
    assert sre.url == "https://jobicy.com/jobs/101-sre", "o link e o da vaga original"
    # O credito que os termos pedem aparece no cartao como "achada por Jobicy".
    assert sre.busca == "Jobicy"
    assert sre.local == "Anywhere"
    assert sre.salario == "USD 90000-120000 por ano"
    assert sre.publicada_em == "2026-10-15"


def test_jobicy_drops_a_job_that_does_not_hire_from_brazil():
    coleta = Jobicy(http=http_que_devolve(JOBICY)).coletar()
    assert "jy-103" not in {c.job_id for c in coleta.cards}
    assert coleta.inelegiveis == 1


def test_jobicy_asks_the_server_for_latam_hiring():
    http = http_que_devolve(JOBICY)
    Jobicy(http=http).coletar()
    (_url, params), = http.chamadas
    assert params["geo"] == "latam"


def test_the_description_arrives_with_the_job_and_without_markup():
    """Estas vagas nao passam pela etapa lenta: a descricao vem na coleta."""
    coleta = Jobicy(http=http_que_devolve(JOBICY)).coletar()
    assert coleta.descricoes["jy-101"] == "We run Kubernetes at scale."


# -------------------------------------------------------- We Work Remotely
def test_wwr_splits_company_from_title_and_keeps_only_eligible_jobs():
    coleta = WeWorkRemotely(http=http_que_devolve(WWR)).coletar()
    assert len(coleta.cards) == 1
    card = coleta.cards[0]
    assert card.empresa == "LaunchDarkly"
    assert card.titulo == "Engineering Manager, Cloud Engineering"
    assert card.local == "Anywhere in the World"
    assert card.busca == "We Work Remotely"
    assert card.publicada_em == "2026-10-15"
    assert coleta.inelegiveis == 1, "a vaga USA Only fica de fora"


def test_wwr_identifier_is_stable_across_fetches():
    """Sem identificador no feed, o da vaga sai do endereco -- igual a cada leitura."""
    a = WeWorkRemotely(http=http_que_devolve(WWR)).coletar().cards[0].job_id
    b = WeWorkRemotely(http=http_que_devolve(WWR)).coletar().cards[0].job_id
    assert a == b and a.startswith("wwr-")


# ---------------------------------------------------------------- falhas
def test_a_failing_source_becomes_an_empty_contribution_and_not_a_crash():
    def quebra(url, params):
        raise TimeoutError("sem resposta")

    coleta = Jobicy(http=quebra).coletar()
    assert coleta.cards == []
    assert "TimeoutError" in coleta.falha
    assert coleta.como_busca().falha


# --------------------------------------------------------------- cadencia
@pytest.fixture
def conexao(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    yield connection
    connection.close()


def test_a_second_run_within_the_interval_reuses_the_answer(conexao):
    """Os provedores pedem pouca frequencia; a segunda busca do dia usa o guardado."""
    http = http_que_devolve(JOBICY)
    cadencia = CadenciaRemota(conexao, intervalo_horas=6)
    agora = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)

    primeira = cadencia.coletar(Jobicy(http=http), agora=agora)
    segunda = cadencia.coletar(Jobicy(http=http), agora=agora + timedelta(hours=2))

    assert len(http.chamadas) == 1, "uma chamada so dentro do intervalo"
    assert segunda.do_cache is True
    assert [c.job_id for c in segunda.cards] == [c.job_id for c in primeira.cards]
    assert segunda.descricoes == primeira.descricoes


def test_after_the_interval_the_source_is_called_again(conexao):
    http = http_que_devolve(JOBICY)
    cadencia = CadenciaRemota(conexao, intervalo_horas=6)
    agora = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    cadencia.coletar(Jobicy(http=http), agora=agora)
    cadencia.coletar(Jobicy(http=http), agora=agora + timedelta(hours=7))
    assert len(http.chamadas) == 2


def test_a_failure_is_not_cached(conexao):
    """Uma queda de minutos nao pode esconder a fonte por seis horas."""
    def quebra(url, params):
        raise TimeoutError("sem resposta")

    http = http_que_devolve(JOBICY)
    cadencia = CadenciaRemota(conexao, intervalo_horas=6)
    agora = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    cadencia.coletar(Jobicy(http=quebra), agora=agora)
    depois = cadencia.coletar(Jobicy(http=http), agora=agora + timedelta(minutes=5))
    assert depois.do_cache is False
    assert len(http.chamadas) == 1


# -------------------------------------------------- integracao com o run
def test_remote_jobs_reach_the_run_with_their_description_already_stored(
    monkeypatch, tmp_path
):
    """A vaga remota entra no run, e a descricao dela ja esta no registro.

    E isso que tira estas vagas da etapa lenta: o enriquecedor encontra a
    descricao gravada e nao pede nada a fonte do LinkedIn.
    """
    from crivo.config import load_config
    from crivo.pipeline import stages
    from crivo.store.repository import Repository
    from crivo.worker.queue import RunQueue
    from crivo.worker.runner import Runner
    from test_stages import USUARIO, FonteFalsa, consolidar, montar

    class FonteQueAnota(FonteFalsa):
        def __init__(self):
            super().__init__()
            self.descritas = []

        def describe(self, job_id, url):
            self.descritas.append(job_id)
            return super().describe(job_id, url)

    connection = open_database(tmp_path / "crivo.db")
    try:
        Repository(connection).insert(
            "users",
            {"user_id": USUARIO, "subject_google": "sub-ana",
             "criado_em": datetime.now(timezone.utc).isoformat()},
        )
        config = load_config()
        monkeypatch.setattr(
            stages, "_fontes_remotas",
            lambda cfg: [Jobicy(http=http_que_devolve(JOBICY))],
        )
        linkedin = FonteQueAnota()
        consolidar(connection, config)
        RunQueue(connection).enqueue(USUARIO)
        Runner(connection, config, montar(connection, config, linkedin)).run_once()

        remotas = {
            linha[0] for linha in connection.execute(
                "SELECT job_id FROM jobs WHERE job_id LIKE 'jy-%'"
            )
        }
        assert remotas == {"jy-101", "jy-102"}, "a vaga dos EUA nao entra"
        descritas = {
            linha[0] for linha in connection.execute(
                "SELECT job_id FROM job_descriptions WHERE job_id LIKE 'jy-%'"
            )
        }
        assert descritas == remotas
        assert not [j for j in linkedin.descritas if j.startswith("jy-")]
        # O resumo da coleta chega ao registro do run, com a fonte remota nele.
        coleta = json.loads(
            connection.execute("SELECT coleta FROM runs").fetchone()[0]
        )
        assert coleta["por_fonte"]["Jobicy"] == 2
        assert coleta["tentadas"] == coleta["previstas"] > 0
    finally:
        connection.close()


def test_an_unknown_source_name_stops_the_process_naming_it():
    """Fonte que some em silencio e o defeito das frentes remotas outra vez."""
    from dataclasses import replace

    from crivo.config import ConfigError, load_config
    from crivo.pipeline import stages

    config = load_config()
    errada = replace(
        config, collection=replace(config.collection, fontes_remotas=("linkedin2",))
    )
    with pytest.raises(ConfigError) as err:
        stages._fontes_remotas_de_verdade(errada)
    assert "linkedin2" in str(err.value)


def test_the_default_configuration_turns_on_both_sources():
    from crivo.config import load_config
    from crivo.pipeline import stages

    fontes = stages._fontes_remotas_de_verdade(load_config())
    assert [type(f) for f in fontes] == [Jobicy, WeWorkRemotely]


# ------------------------------------------------- rede de seguranca
def test_the_linkedin_source_refuses_to_describe_a_job_that_is_not_its_own():
    """"jy-123" viraria o anuncio 123 do LinkedIn, que e outra vaga ou nenhuma."""
    with pytest.raises(CollectionError) as err:
        MultiPortalSource(http=object()).describe("jy-123", "https://jobicy.com/x")
    assert "nao e do LinkedIn" in str(err.value)
