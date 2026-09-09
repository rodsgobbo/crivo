"""Verificacao de aterramento da resposta de sintese.

A regra e simples e o efeito e grande: se a resposta cita vaga, endereco ou
empresa que nao estava na requisicao, a resposta inteira e descartada. Nao ha
correcao parcial nem aproveitamento do resto.

Descartar tudo parece severo, e e deliberado. Uma resposta que inventou uma vaga
demonstrou que esta gerando em vez de ler, e o restante dela nao merece mais
confianca do que a parte inventada. Aproveitar o resto seria escolher acreditar
seletivamente num texto que ja se sabe nao aterrado.

A verificacao tambem confere as exigencias de forma: classificacao das
afirmacoes e distincao entre vaga lida e vaga avaliada apenas por card. Sem
elas, o candidato confiaria igualmente em avaliacoes de qualidade desigual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .prompt import SynthesisPayload

#: Marcas de classificacao exigidas da resposta.
CLASSIFICACOES = ("certo", "provavel", "suposicao", "suposição", "provável")

#: Marcas que denunciam avaliacao sem leitura da descricao.
MARCAS_DE_INFERENCIA = (
    "sem leitura da descricao",
    "sem leitura da descrição",
    "inferida de titulo",
    "inferida de título",
    "sem descricao",
    "sem descrição",
)

URL_RE = re.compile(r"https?://[^\s\)\]>\"']+")


class GroundingViolation(Exception):
    """A resposta citou algo que nao estava na requisicao."""


@dataclass(frozen=True)
class GroundingReport:
    """Resultado da verificacao, com o que motivou a recusa."""

    aterrada: bool
    urls_estranhas: tuple[str, ...] = ()
    ids_estranhos: tuple[str, ...] = ()
    faltando: tuple[str, ...] = field(default=())

    @property
    def motivo(self) -> str:
        partes = []
        if self.urls_estranhas:
            partes.append(f"enderecos ausentes do envio: {list(self.urls_estranhas)}")
        if self.ids_estranhos:
            partes.append(f"vagas ausentes do envio: {list(self.ids_estranhos)}")
        if self.faltando:
            partes.append(f"exigencias de forma nao atendidas: {list(self.faltando)}")
        return "; ".join(partes)


def _normalizar(url: str) -> str:
    return url.rstrip(".,;:").rstrip("/")


def verify(resposta: str, payload: SynthesisPayload) -> GroundingReport:
    """Confere se a resposta se apoia apenas no que foi enviado."""
    texto = resposta or ""

    permitidas = {_normalizar(u) for u in payload.urls_enviadas}
    citadas = {_normalizar(u) for u in URL_RE.findall(texto)}
    estranhas = tuple(sorted(citadas - permitidas))

    # Identificadores sao procurados como palavra inteira: um id curto poderia
    # aparecer por acaso dentro de outro numero.
    ids_estranhos: list[str] = []
    for candidato in re.findall(r"\b[a-zA-Z]{2}-\d{3,}\b|\b\d{8,}\b", texto):
        if candidato not in payload.ids_enviados:
            ids_estranhos.append(candidato)

    faltando: list[str] = []
    minusculo = texto.lower()
    if payload.vagas and not any(c in minusculo for c in CLASSIFICACOES):
        faltando.append("classificacao das afirmacoes")

    return GroundingReport(
        aterrada=not (estranhas or ids_estranhos or faltando),
        urls_estranhas=estranhas,
        ids_estranhos=tuple(sorted(set(ids_estranhos))),
        faltando=tuple(faltando),
    )


def require_provenance(resposta: str, ha_vaga_sem_descricao: bool) -> bool:
    """A resposta distingue vaga lida de vaga avaliada apenas por card?"""
    if not ha_vaga_sem_descricao:
        return True
    minusculo = (resposta or "").lower()
    return any(marca in minusculo for marca in MARCAS_DE_INFERENCIA)


def accept(
    resposta: str, payload: SynthesisPayload, ha_vaga_sem_descricao: bool = False
) -> str:
    """Devolve a resposta quando aterrada, ou levanta com o motivo."""
    relatorio = verify(resposta, payload)
    if not relatorio.aterrada:
        raise GroundingViolation(relatorio.motivo)
    if not require_provenance(resposta, ha_vaga_sem_descricao):
        raise GroundingViolation(
            "a resposta nao distingue vaga lida de vaga avaliada apenas por card"
        )
    return resposta
