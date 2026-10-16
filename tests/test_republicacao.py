"""Vaga republicada: o mesmo anuncio voltando com outro identificador."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from crivo.config import load_config
from crivo.pipeline.collector import Collector
from crivo.pipeline.enricher import Enricher
from crivo.pipeline.republicacao import SINAL, Republicacao, palavras
from crivo.pipeline.sources.guest import SearchOutcome
from crivo.scoring.ontology import load_ontology
from crivo.scoring.scorer import Scorer
from crivo.store.repository import Repository
from test_collector import AQUI, FakeSource, card, collector  # noqa: F401


def coletar(connection, *cards, republicacao=None):
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=list(cards))})
    return Collector(connection, fonte, republicacao=republicacao).collect(
        "ana", ["A"], AQUI, 24
    )


def envelhecer(connection, job_id, dias):
    """Faz de conta que a vaga apareceu `dias` atras."""
    quando = datetime.now(timezone.utc) - timedelta(days=dias)
    connection.execute(
        "UPDATE jobs SET primeira_vez_em = ? WHERE job_id = ?",
        (quando.isoformat(timespec="seconds"), job_id),
    )


def flags(connection, job_id):
    linha = connection.execute(
        "SELECT flags FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    return json.loads(linha[0])


# ------------------------------------------------------------------ titulo
def test_the_title_is_compared_as_a_set_of_words():
    """Espaco duplo, caixa e acento nao fazem de um anuncio outro anuncio."""
    assert palavras("Pessoa  Analista Infra Cloud - Sênior") == palavras(
        "pessoa analista infra cloud senior"
    )
    assert palavras("SRE Manager") != palavras("SRE Manager II")


# --------------------------------------------------------------- deteccao
def test_the_same_title_at_the_same_company_days_later_is_a_repost(collector):
    coletar(collector, card("li-1", "Cloud Engineer | Remota - 135368 INT"))
    envelhecer(collector, "li-1", 25)

    resultado = coletar(collector, card("li-2", "Cloud Engineer | Remota - 135368 INT"))

    assert SINAL in flags(collector, "li-2")
    # A passada provisoria pontua a partir da lista, e nao do banco.
    assert SINAL in resultado.cards[0].flags
    assert SINAL not in flags(collector, "li-1"), "a original nao e republicacao"


def test_the_same_title_on_the_same_day_is_one_opening_in_several_cities(collector):
    """123 grupos assim no banco real: e a mesma vaga, nao uma republicacao."""
    coletar(
        collector,
        card("li-1", "Analista de Infraestrutura", local="Barueri, SP"),
        card("li-2", "Analista de Infraestrutura", local="Sao Paulo, SP"),
    )
    assert SINAL not in flags(collector, "li-2")


def test_another_title_at_the_same_company_is_another_job(collector):
    coletar(collector, card("li-1", "SRE Manager"))
    envelhecer(collector, "li-1", 10)
    coletar(collector, card("li-2", "Platform Engineering Manager"))
    assert SINAL not in flags(collector, "li-2")


def test_the_same_title_at_another_company_is_another_job(collector):
    coletar(collector, card("li-1", "SRE Manager"))
    envelhecer(collector, "li-1", 10)
    coletar(collector, card("li-2", "SRE Manager", empresa="Outra"))
    assert SINAL not in flags(collector, "li-2")


def test_beyond_the_window_the_same_title_is_a_new_hiring(collector):
    coletar(collector, card("li-1", "SRE Manager"))
    envelhecer(collector, "li-1", 120)
    coletar(collector, card("li-2", "SRE Manager"), republicacao=Republicacao(90))
    assert SINAL not in flags(collector, "li-2")


def test_an_agency_reposting_is_its_business_and_not_a_stale_job(collector):
    coletar(collector, card("li-1", "Analista de Infraestrutura", empresa="Jobbol"))
    envelhecer(collector, "li-1", 10)
    coletar(
        collector,
        card("li-2", "Analista de Infraestrutura", empresa="JOBBOL"),
        republicacao=Republicacao(90, ["Jobbol"]),
    )
    assert SINAL not in flags(collector, "li-2")


def test_a_job_stored_before_the_rule_gets_the_mark_when_it_reappears(collector):
    """O historico ja tem 262 republicacoes sem marca; elas a ganham ao voltar."""
    coletar(collector, card("li-1", "SRE Manager"))
    coletar(collector, card("li-2", "SRE Manager"))  # mesmo dia: sem marca
    envelhecer(collector, "li-1", 30)
    envelhecer(collector, "li-2", 5)
    assert SINAL not in flags(collector, "li-2")

    resultado = coletar(collector, card("li-2", "SRE Manager"))

    assert SINAL in flags(collector, "li-2")
    assert SINAL in resultado.cards[0].flags
    assert resultado.novos == [], "reaparecer nao a torna nova"


def test_the_mark_keeps_the_signals_the_card_brought(collector):
    coletar(collector, card("li-1", "SRE Manager"))
    envelhecer(collector, "li-1", 10)
    coletar(collector, card("li-2", "SRE Manager", flags=("muitos_candidatos",)))
    assert set(flags(collector, "li-2")) == {"muitos_candidatos", SINAL}


# ------------------------------------------------------------ preservacao
def test_reading_the_description_does_not_erase_the_mark(collector):
    """A descricao substitui os sinais da pagina; a republicacao vem do historico."""
    coletar(collector, card("li-1", "SRE Manager"))
    envelhecer(collector, "li-1", 10)
    coletar(collector, card("li-2", "SRE Manager"))

    enricher = Enricher(collector, FakeSource(), governor=None)
    enricher._store_signals("ana", "li-2", ["early_applicant"])

    assert set(flags(collector, "li-2")) == {"early_applicant", SINAL}


# ------------------------------------------------------------------ efeito
def test_the_mark_costs_the_configured_penalty():
    config = load_config()
    scorer = Scorer(config, load_ontology())
    perfil = {"competencias": ["kubernetes"], "nivel_inferido": "senior"}
    vaga = {"titulo": "SRE Manager", "remoto": True}
    limpa = scorer.score(perfil, vaga).score
    marcada = scorer.score(perfil, vaga, sinais=[SINAL])
    assert marcada.ajustes[SINAL] == -config.scoring.penalidades[SINAL]
    assert marcada.score == limpa - config.scoring.penalidades[SINAL]


def test_the_default_configuration_ignores_the_measured_agencies():
    coleta = load_config().collection
    assert coleta.republicacao_janela_dias == 90
    assert "Jobbol" in coleta.republicacao_ignoradas
