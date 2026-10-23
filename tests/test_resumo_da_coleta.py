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


# ------------------------------------------------------- revezamento
from crivo.pipeline.stages import coleta_anterior, priorizar_pendentes  # noqa: E402


def test_terms_left_out_by_a_barred_collection_go_first_next_time():
    anterior = {"barrada_em": "C", "nao_tentadas": ["D", "E"]}
    assert priorizar_pendentes(["A", "B", "C", "D", "E"], anterior) == [
        "C", "D", "E", "A", "B",
    ]


def test_nothing_moves_when_the_last_collection_was_not_barred():
    buscas = ["A", "B", "C"]
    assert priorizar_pendentes(buscas, {"barrada_em": None}) == buscas
    assert priorizar_pendentes(buscas, None) == buscas


def test_a_pending_term_no_longer_planned_is_not_invented():
    """Reordenar, nunca acrescentar: o planejamento decide o que se busca."""
    anterior = {"barrada_em": "Velho", "nao_tentadas": ["B"]}
    assert priorizar_pendentes(["A", "B"], anterior) == ["B", "A"]


def test_a_barred_run_hands_its_pending_terms_to_the_next_one(tmp_path):
    from crivo.store.migrations import open_database
    from crivo.store.repository import Repository
    from crivo.worker.queue import RunQueue
    from crivo.worker.runner import Runner
    from test_stages import (
        USUARIO, FonteFalsa, atender_enriquecimento, consolidar, montar,
    )

    class BarraNaSegunda(FonteFalsa):
        """Recusa a segunda busca do primeiro run, como o LinkedIn em 21/10."""

        def __init__(self):
            super().__init__()
            self.barrar = True

        def search(self, termo, local, janela_horas, quantidade, remoto=False):
            if self.barrar and len(self.buscas) == 1:
                self.buscas.append(termo)
                return SearchOutcome(busca=termo, bloqueada=True, falha="429")
            return super().search(termo, local, janela_horas, quantidade, remoto)

    connection = open_database(tmp_path / "crivo.db")
    try:
        Repository(connection).insert(
            "users",
            {"user_id": USUARIO, "subject_google": "sub-ana",
             "criado_em": "2026-10-21T00:00:00+00:00"},
        )
        config = load_config()
        consolidar(connection, config)
        fonte = BarraNaSegunda()
        fila = RunQueue(connection)

        run_1 = fila.enqueue(USUARIO)
        runner = Runner(connection, config, montar(connection, config, fonte))
        runner.run_once()
        atender_enriquecimento(connection, run_1)
        connection.execute(
            "UPDATE runs SET estado = 'enfileirado' WHERE run_id = ?", (run_1,)
        )
        runner.run_once()
        primeiro = coleta_anterior(connection, USUARIO, "nenhum")
        assert primeiro["barrada_em"] == fonte.buscas[1]
        pendentes = [primeiro["barrada_em"], *primeiro["nao_tentadas"]]

        # A recusa registrou um bloqueio no governador; o run seguinte so sai
        # depois da hora de recuperacao. Aqui ela passa de uma vez.
        assert connection.execute("SELECT COUNT(*) FROM collection_blocks").fetchone()[0] == 1
        connection.execute("UPDATE collection_blocks SET liberado_em = ocorrido_em")
        fonte.barrar = False
        fonte.buscas.clear()
        fila.enqueue(USUARIO)
        Runner(connection, config, montar(connection, config, fonte)).run_once()

        assert fonte.buscas[: len(pendentes)] == pendentes
    finally:
        connection.close()
