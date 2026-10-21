"""O relatorio diz de onde vieram as vagas e onde a origem recusou."""

from __future__ import annotations

import json

from crivo.config import load_config
from crivo.pipeline.collector import Collector
from crivo.pipeline.sources.guest import SearchOutcome
from crivo.pipeline.stages import resumo_da_coleta
from crivo.report.renderer import ReportRenderer, resumo_para_a_pagina
from test_collector import AQUI, FakeSource, card, collector  # noqa: F401
from test_report import add_vaga, env  # noqa: F401


def test_a_barred_collection_says_where_it_stopped_and_what_was_left(collector):
    fonte = FakeSource({
        "A": SearchOutcome(busca="A", cards=[card("li-1")]),
        "B": SearchOutcome(busca="B", bloqueada=True, falha="status code 429"),
    })
    resultado = Collector(collector, fonte).collect("ana", ["A", "B", "C", "D"], AQUI, 24)

    resumo = resumo_da_coleta(resultado)

    assert resumo["previstas"] == 4
    assert resumo["tentadas"] == 2
    assert resumo["barrada_em"] == "B"
    assert resumo["nao_tentadas"] == ["C", "D"]
    assert resumo["por_fonte"] == {"aqui": 1}


def test_a_full_collection_has_no_stopping_point(collector):
    fonte = FakeSource({"A": SearchOutcome(busca="A", cards=[card("li-1")])})
    resumo = resumo_da_coleta(
        Collector(collector, fonte).collect("ana", ["A", "B"], AQUI, 24)
    )
    assert resumo["tentadas"] == resumo["previstas"] == 2
    assert resumo["barrada_em"] is None


def test_the_page_names_the_sources_for_a_reader():
    """"aqui" e o rotulo da configuracao; quem le precisa ler "LinkedIn"."""
    resumo = resumo_para_a_pagina(json.dumps({
        "previstas": 25, "tentadas": 25, "barrada_em": None, "nao_tentadas": [],
        "falhas": {}, "por_fonte": {"aqui": 59, "Jobicy": 40, "We Work Remotely": 21},
    }))
    assert resumo["fontes"] == [
        {"nome": "LinkedIn", "vagas": 59},
        {"nome": "Jobicy", "vagas": 40},
        {"nome": "We Work Remotely", "vagas": 21},
    ]


def test_a_run_without_the_record_shows_nothing_instead_of_zeros():
    assert resumo_para_a_pagina(None) is None
    assert resumo_para_a_pagina("nao e json") is None


def test_the_report_explains_a_barred_collection(env):
    connection, repo = env
    add_vaga(repo)
    connection.execute(
        "UPDATE runs SET coleta = ? WHERE run_id = 'run-1'",
        (json.dumps({
            "previstas": 25, "tentadas": 6, "barrada_em": "Cloud Director",
            "nao_tentadas": ["Database Director", "DevOps Director"],
            "falhas": {}, "por_fonte": {"aqui": 59, "Jobicy": 40},
        }),),
    )
    html = ReportRenderer(connection, load_config()).render_run("ana", "run-1")
    assert "6 de 25 buscas no LinkedIn rodaram" in html
    assert "Cloud Director" in html
    assert "Database Director, DevOps Director" in html
    assert "LinkedIn 59" in html and "Jobicy 40" in html


def test_the_report_of_an_old_run_has_no_collection_line(env):
    connection, repo = env
    add_vaga(repo)
    html = ReportRenderer(connection, load_config()).render_run("ana", "run-1")
    assert "De onde vieram" not in html
