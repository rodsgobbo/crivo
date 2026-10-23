"""A leitura da pagina de vaga, contra paginas reais do LinkedIn.

Os demais testes usam marcacao escrita a mao, e marcacao escrita a mao passa a
refletir o que o codigo espera, e nao o que a origem manda. Foi assim duas
vezes: o padrao de candidatos lia o `figure` em vez da legenda, e o dublê
trazia o numero direto no `figure` -- marcacao que a origem nunca produziu. E,
ao trocar o dublê por estas paginas em 23/10/2026, apareceu a terceira: a
contagem exata ("36 candidaturas") vem num `span`, e so o `figcaption` era
lido. 89% das descricoes do banco estavam sem contagem por isso.

As paginas ficam em `tests/fixtures/linkedin/`, capturadas sem login, com o
unico token anonimo da pagina trocado. Quando o LinkedIn mudar a marcacao, o
caminho e capturar uma pagina nova, acrescenta-la aqui e ver qual destes
testes quebra -- e nao ajustar o padrao ate o dublê passar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crivo.pipeline.sources.guest import parse_job_posting, vaga_encerrada

PASTA = Path(__file__).parent / "fixtures" / "linkedin"


def pagina(nome: str) -> str:
    return (PASTA / nome).read_text(encoding="utf-8")


# ------------------------------------------------------------ cada pagina
def test_an_open_job_with_an_exact_applicant_count():
    dados = parse_job_posting(pagina("vaga_aberta_contagem_exata.html"), "li-4449874526")
    assert dados["candidatos"] == 36
    assert dados["encerrada"] is False
    assert dados["publicada_ha"] == "Há 2 dias"
    assert len(dados["texto"]) > 1000
    assert dados["sinais"] == [], "36 nao e muitos nem e dos primeiros"


def test_an_open_job_with_a_floor_of_applicants():
    dados = parse_job_posting(pagina("vaga_aberta_mais_de_200.html"), "li-4469988221")
    assert dados["candidatos"] == 200
    assert dados["sinais"] == ["muitos_candidatos"]
    assert dados["encerrada"] is False


def test_a_closed_job():
    dados = parse_job_posting(pagina("vaga_encerrada.html"), "li-4474369358")
    assert dados["encerrada"] is True
    # "Seja um dos 25 primeiros" e sinal, e nao contagem: ler o 25 como numero
    # de candidatos inverteria o sentido.
    assert dados["candidatos"] is None
    assert dados["sinais"] == ["early_applicant"]
    assert len(dados["texto"]) > 1000, "vaga fechada ainda traz a descricao"


# ---------------------------------------------------------------- conjunto
@pytest.mark.parametrize("nome", sorted(p.name for p in PASTA.glob("*.html")))
def test_every_real_page_yields_a_description(nome):
    """O canario: pagina real sem corpo extraido e marcacao que mudou."""
    dados = parse_job_posting(pagina(nome), nome)
    assert dados["texto"].strip()
    # Nada de marcacao vazando para o texto que vai para a nota e o modelo.
    assert "<" not in dados["texto"] and "class=" not in dados["texto"]


@pytest.mark.parametrize("nome, fechada", [
    ("vaga_aberta_contagem_exata.html", False),
    ("vaga_aberta_mais_de_200.html", False),
    ("vaga_encerrada.html", True),
])
def test_only_the_closed_page_is_called_closed(nome, fechada):
    assert vaga_encerrada(pagina(nome)) is fechada


def test_the_fixtures_carry_no_session_token():
    for arquivo in PASTA.glob("*.html"):
        texto = arquivo.read_text(encoding="utf-8")
        assert 'loginCsrfParam" value="REMOVIDO"' in texto, arquivo.name
