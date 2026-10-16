"""Barreira de rede da suite inteira.

A configuracao padrao liga as fontes de vaga remota, e qualquer teste que monte o
estagio de coleta com ela sairia para o Jobicy e o We Work Remotely de verdade.
Isso aconteceu na primeira execucao depois que as fontes entraram: vagas reais
apareceram no banco de um teste de estagio, e a contagem dele quebrou.

O pior caso nao e esse, e sim o silencioso. A fonte remota captura qualquer erro
e segue -- e de proposito, porque ela e acessoria ao run --, entao um guarda que
so levantasse excecao seria engolido e o teste passaria tendo tentado a rede.
Por isso a tentativa e ANOTADA e o teste falha no fim, com o endereco no texto.

Duas camadas:
- o estagio de coleta nasce sem fontes remotas, a menos que o teste as injete;
- qualquer fonte que tente uma chamada real, sem o `http` falso dos testes,
  reprova o teste que a causou.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def sem_rede_nas_fontes_remotas(monkeypatch):
    from crivo.pipeline import stages
    from crivo.pipeline.sources import remotas

    tentativas: list[str] = []

    def _get(self, url, params):
        if self._http is not None:
            return self._http(url, params)
        tentativas.append(url)
        raise RuntimeError(f"teste tentou sair para a rede: {url}")

    monkeypatch.setattr(remotas.FonteRemota, "_get", _get)
    # A de verdade fica guardada ao lado, para o teste que valida a configuracao.
    monkeypatch.setattr(
        stages, "_fontes_remotas_de_verdade", stages._fontes_remotas, raising=False
    )
    monkeypatch.setattr(stages, "_fontes_remotas", lambda config: [])
    yield
    if tentativas:
        pytest.fail(
            "o teste tentou chamar uma fonte remota de verdade: "
            + ", ".join(tentativas)
        )
