"""A leitura da pagina de busca, contra uma pagina real do LinkedIn.

E o par de `test_paginas_reais.py`, para a outra metade da coleta. A pagina de
vaga e lida por codigo nosso; a de busca e lida pela biblioteca de coleta, e por
isso ficou sem canario ate 11/11/2026: nao havia padrao nosso para testar. Mas
o defeito mais caro desta origem nasceu exatamente ali -- em agosto, uma classe
de marcacao que a biblioteca procurava (`job-search-card__listdate`) deixou de
casar com a que a origem manda em anuncio recente
(`job-search-card__listdate--new`), e 299 de 299 vagas de uma coleta de 24 horas
chegaram sem data. Nada avisou.

Este teste passa a pagina real pelo caminho inteiro -- a biblioteca, a tabela
dela, `to_card` -- trocando so a rede. O que ele protege e a juncao: uma versao
nova da biblioteca que volte a perder a data, ou a origem mudando a marcacao do
card, quebra aqui e nao num relatorio.

A pagina em `tests/fixtures/linkedin/busca/` e a resposta crua do endereco de
busca sem login, capturada em 11/11/2026 com uma requisicao so, sob o
governador, janela de 24 horas. Os identificadores de rastreio foram trocados
por `REMOVIDO`. Ela traz vagas dos EUA porque o local pedido na captura foi
"Brasil", que a origem nao resolveu -- as buscas do crivo mandam a cidade do
perfil. Para o que se testa aqui, que e marcacao, nao muda nada.

Quando este teste quebrar: capture uma pagina nova, ponha ao lado desta e veja
o que mudou. Nao ajuste a pagina ate o teste passar.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from crivo.pipeline.sources.guest import MultiPortalSource

PASTA = Path(__file__).parent / "fixtures" / "linkedin" / "busca"
PAGINA = PASTA / "busca_24h.html"

#: Resposta da origem quando a busca nao tem mais resultado.
PAGINA_VAZIA = "<!DOCTYPE html>\n\n<!---->"


class _Resposta:
    def __init__(self, texto: str, url: str) -> None:
        self.status_code = 200
        self.text = texto
        self.url = url


class _SessaoQueDevolve:
    """No lugar da sessao HTTP da biblioteca: a pagina real, e nenhuma rede."""

    def __init__(self, texto: str) -> None:
        self.headers: dict = {}
        self._texto = texto
        self.pedidos: list[dict] = []

    def get(self, url, params=None, **_):
        self.pedidos.append({"url": url, **(params or {})})
        primeira = (params or {}).get("start", 0) == 0
        return _Resposta(self._texto if primeira else PAGINA_VAZIA, url)


@pytest.fixture
def busca(monkeypatch):
    """Uma busca de verdade pela biblioteca, com a pagina guardada como resposta."""
    import jobspy.linkedin

    sessao = _SessaoQueDevolve(PAGINA.read_text(encoding="utf-8"))
    monkeypatch.setattr(jobspy.linkedin, "create_session", lambda **_: sessao)
    resultado = MultiPortalSource(portais=("linkedin",)).search(
        "Gerente de Infraestrutura", "Brasil", janela_horas=24, quantidade=10
    )
    return resultado, sessao


def test_the_real_page_is_read_without_touching_the_network(busca):
    resultado, sessao = busca
    assert len(sessao.pedidos) == 1
    assert sessao.pedidos[0]["url"].endswith("/seeMoreJobPostings/search")
    # A janela pedida chega a origem em segundos.
    assert sessao.pedidos[0]["f_TPR"] == "r86400"


def test_every_card_of_the_real_page_becomes_a_job(busca):
    resultado, _sessao = busca
    assert resultado.falha is None
    assert not resultado.improdutiva and not resultado.bloqueada
    assert len(resultado.cards) == 10

    for card in resultado.cards:
        assert re.fullmatch(r"li-\d+", card.job_id), card.job_id
        assert card.url.endswith("/jobs/view/" + card.job_id.removeprefix("li-"))
        assert card.titulo and card.empresa and card.local
        assert card.busca == "Gerente de Infraestrutura"
        # Nada de marcacao nem de rastreio vazando para o que vai ao banco.
        for campo in (card.titulo, card.empresa, card.local, card.url):
            assert "<" not in campo and "REMOVIDO" not in campo

    # Por identificador, e nao por posicao: a biblioteca reordena por data.
    por_id = {card.job_id: card for card in resultado.cards}
    assert len(por_id) == 10
    conhecido = por_id["li-4481245909"]
    assert conhecido.titulo == "Network and Data Center Manager"
    assert conhecido.empresa == "Analysis Group"
    # A pagina diz "Boston, MA"; a biblioteca completa o pais por conta dela.
    assert conhecido.local.startswith("Boston, MA")
    assert conhecido.publicada_em == "2026-11-10"


def test_a_recent_job_keeps_its_date(busca):
    """O defeito de agosto: a data do anuncio recente vem sob outra classe.

    Todos os dez anuncios desta pagina sao de menos de 24 horas e todos trazem
    a classe `--new`. Se a leitura voltar a depender do nome antigo, as dez
    datas somem de uma vez -- que e exatamente o que aconteceu.
    """
    marcacao = PAGINA.read_text(encoding="utf-8")
    assert marcacao.count('class="job-search-card__listdate--new"') == 10
    assert 'class="job-search-card__listdate"' not in marcacao

    resultado, _sessao = busca
    datas = [card.publicada_em for card in resultado.cards]
    assert all(datas), datas
    assert sorted(set(datas)) == ["2026-11-10", "2026-11-11"]
    assert datas.count("2026-11-11") == 7


def test_the_search_fixture_carries_no_tracking_identifier():
    marcacao = PAGINA.read_text(encoding="utf-8")
    for nome in ("refId", "trackingId"):
        valores = set(re.findall(nome + r"=([^&\"']+)", marcacao))
        assert valores == {"REMOVIDO"}, (nome, valores)
    for atributo in ("data-reference-id", "data-tracking-id"):
        valores = set(re.findall(atributo + r'="([^"]*)"', marcacao))
        assert valores == {"REMOVIDO"}, (atributo, valores)
