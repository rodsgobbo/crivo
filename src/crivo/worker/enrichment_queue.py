"""Fila do enriquecimento: o ponto de suspensao entre os dois trechos do run.

Um run nao e uma execucao continua. O primeiro trecho vai do planejamento ate a
pontuacao provisoria e termina gravando aqui os pedidos de enriquecimento; o run
passa a aguardando e o processo de runs solta o worker. O processo de
enriquecimento consome no seu ritmo e, ao esgotar os pedidos de um run, devolve
o run a fila para o segundo trecho.

Sem esta separacao, a espera deliberada do governador -- que e o intervalo mais
longo de todo o run -- ocuparia um worker do inicio ao fim. E um processo que
morresse no meio deixaria um worker preso em vez de um run retomavel.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..store.repository import Repository

PENDENTE = "pendente"
ATENDIDO = "atendido"
FALHOU = "falhou"


@dataclass(frozen=True)
class EnrichmentRequest:
    """Um pedido de descricao, ligado ao run que o originou."""

    request_id: str
    run_id: str
    user_id: str
    job_id: str
    url: str
    estado: str


class EnrichmentQueue:
    """Grava pedidos, entrega-os ao enriquecedor e sinaliza o esgotamento."""

    def __init__(self, connection) -> None:
        self._connection = connection
        self._repository = Repository(connection)

    def enqueue(self, user_id: str, run_id: str, cards: list) -> list[str]:
        """Grava um pedido por vaga sobrevivente, na ordem recebida."""
        scope = self._repository.for_user(user_id)
        criados = []
        for card in cards:
            ja = scope.select(
                "enrichment_requests",
                where="run_id = ? AND job_id = ?",
                params=(run_id, card.job_id),
                columns="request_id",
            )
            if ja:
                continue
            request_id = str(uuid.uuid4())
            scope.insert(
                "enrichment_requests",
                {
                    "request_id": request_id,
                    "run_id": run_id,
                    "job_id": card.job_id,
                    "url": card.url,
                    "estado": PENDENTE,
                    "criado_em": _stamp(),
                },
            )
            criados.append(request_id)
        return criados

    def pending_for(self, run_id: str) -> list[EnrichmentRequest]:
        """Pedidos ainda nao atendidos de um run, na ordem em que entraram."""
        linhas = self._repository.execute(
            "SELECT * FROM enrichment_requests WHERE run_id = ? AND estado = ? "
            "ORDER BY rowid",
            (run_id, PENDENTE),
        ).fetchall()
        return [_to_request(linha) for linha in linhas]

    def next_batch(self, limite: int = 50) -> list[EnrichmentRequest]:
        """Proximos pedidos de qualquer run, para o processo de enriquecimento."""
        linhas = self._repository.execute(
            "SELECT * FROM enrichment_requests WHERE estado = ? ORDER BY rowid LIMIT ?",
            (PENDENTE, int(limite)),
        ).fetchall()
        return [_to_request(linha) for linha in linhas]

    def settle(self, request_id: str, atendido: bool = True) -> None:
        """Fecha um pedido, com ou sem sucesso."""
        self._repository.execute(
            "UPDATE enrichment_requests SET estado = ?, atendido_em = ? "
            "WHERE request_id = ?",
            (ATENDIDO if atendido else FALHOU, _stamp(), request_id),
        )

    def is_drained(self, run_id: str) -> bool:
        """O run pode voltar a fila para o segundo trecho?"""
        return not self.pending_for(run_id)

    def settle_all(self, run_id: str, atendidos: set[str]) -> None:
        """Fecha todos os pedidos de um run a partir do que foi atendido."""
        for pedido in self.pending_for(run_id):
            self.settle(pedido.request_id, atendido=pedido.job_id in atendidos)


def _to_request(linha) -> EnrichmentRequest:
    return EnrichmentRequest(
        request_id=linha["request_id"],
        run_id=linha["run_id"],
        user_id=linha["user_id"],
        job_id=linha["job_id"],
        url=linha["url"],
        estado=linha["estado"],
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
