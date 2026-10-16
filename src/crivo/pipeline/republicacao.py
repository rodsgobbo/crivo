"""Deteccao de vaga republicada: o mesmo anuncio voltando com outro numero.

O LinkedIn da identificador novo a cada publicacao, e a empresa que nao fecha a
vaga costuma republica-la. Para o crivo isso chega como vaga nova, e a pergunta
que importa a quem le -- "esta vaga esta aberta ha quanto tempo?" -- fica sem
resposta.

Medido em 16/10/2026, no banco real: de 5076 vagas, 262 eram o mesmo cargo na
mesma empresa ja visto num dia anterior, as vezes com o codigo de requisicao no
titulo ("Cloud Engineer | Remota - 135368", em 24/08 e de novo em 18/09).

O criterio, emprestado do career-ops e medido aqui:

- mesma empresa e o mesmo conjunto de palavras no titulo -- conjunto, e nao
  texto, porque "Pessoa  Analista" com espaco duplo e o mesmo anuncio;
- a anterior vista ao menos UM DIA antes. Duas vagas iguais no mesmo dia sao a
  mesma posicao aberta em varias cidades (123 grupos assim no banco), e isso nao
  e republicacao;
- dentro de uma janela, por padrao 90 dias: o mesmo titulo um ano depois e
  outra contratacao;
- fora as empresas declaradas como agencia, para quem publicar o mesmo cargo
  toda semana e o negocio, e nao sinal de vaga parada.

A marca vira o sinal `repostada_antiga`, que a penalidade de mesmo nome em
`[scoring.penalidades]` ja descontava -- so que nada a emitia.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

SINAL = "repostada_antiga"

#: Distancia minima ate a aparicao anterior. Um dia, e nao "outra data no
#: calendario": um run que atravessa a meia-noite traria a vaga de varias
#: cidades em duas datas, e ela seria chamada de republicada por si mesma.
DISTANCIA_MINIMA = timedelta(days=1)


def _normaliza(texto: str | None) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    return sem_acento.encode("ascii", "ignore").decode().lower()


def palavras(titulo: str | None) -> frozenset[str]:
    """O titulo como conjunto de palavras, sem acento, caixa ou pontuacao."""
    return frozenset(
        p for p in re.findall(r"[a-z0-9+#]+", _normaliza(titulo)) if len(p) > 1
    )


def _instante(carimbo: str) -> datetime:
    return datetime.fromisoformat(carimbo)


class Republicacao:
    """Decide se uma vaga e um anuncio ja visto antes com outro identificador."""

    def __init__(self, janela_dias: int = 90, empresas_ignoradas=()) -> None:
        self._janela = timedelta(days=janela_dias)
        self._ignoradas = {
            " ".join(sorted(palavras(e))) for e in empresas_ignoradas
        }

    def anterior(
        self, scope, job_id: str, empresa: str | None, titulo: str | None,
        vista_em: str,
    ) -> str | None:
        """O carimbo da aparicao anterior do mesmo anuncio, ou `None`.

        `vista_em` e quando ESTA vaga apareceu pela primeira vez: para uma vaga
        que reaparece, a comparacao e com o que existia quando ela surgiu, e nao
        com hoje.
        """
        if not (empresa or "").strip():
            return None
        if " ".join(sorted(palavras(empresa))) in self._ignoradas:
            return None
        alvo = palavras(titulo)
        if not alvo:
            return None
        momento = _instante(vista_em)
        linhas = scope.select(
            "jobs",
            where=(
                "empresa = ? COLLATE NOCASE AND job_id <> ? "
                "AND primeira_vez_em <= ? AND primeira_vez_em >= ?"
            ),
            params=(
                empresa.strip(), job_id,
                (momento - DISTANCIA_MINIMA).isoformat(timespec="seconds"),
                (momento - self._janela).isoformat(timespec="seconds"),
            ),
            columns="titulo, primeira_vez_em",
        )
        iguais = [l["primeira_vez_em"] for l in linhas if palavras(l["titulo"]) == alvo]
        return min(iguais) if iguais else None
