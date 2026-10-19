"""Expurgo das vagas que nenhuma busca traz ha mais de N dias (padrao 30).

O criterio e a ULTIMA vez que uma busca trouxe a vaga, e nao a data de
publicacao. Vaga publicada ha dois meses que o LinkedIn continua mostrando ainda
esta no ar, e apaga-la so a faria voltar como nova na busca seguinte. A que
nenhuma busca traz ha trinta dias saiu do ar ou deixou de ser anunciada -- e ela
que ocupa o banco sem servir a ninguem. Medido em 17/10/2026: 1924 de 5493.

O que sai junto com a vaga, porque so existe por causa dela:
- as notas dela em todos os runs (`scores`);
- os pedidos de descricao ja encerrados (`enrichment_requests`);
- a descricao, que e compartilhada -- sai so quando nenhum usuario tem mais a
  vaga.

O que nunca sai:
- vaga em que o usuario se candidatou. E o registro dele, e nao acervo de busca;
- vaga com pedido de descricao pendente: o enriquecedor ainda vai procura-la.

O preco, aceito de proposito: relatorio antigo perde as vagas expurgadas, e a
deteccao de republicacao so enxerga o que sobrou. Uma vaga ainda vista na
ultima semana fica, entao a comparacao contra o anuncio de dois meses atras so se
perde quando a original sumiu ha mais de trinta dias.

Roda uma vez por dia, pelo processo de runs, entre dois runs: e o unico
processo que escreve vaga, entao nao ha coleta gravando enquanto o expurgo
apaga. O dia do ultimo expurgo fica em `schema_meta`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .lifecycle import APLICADO

logger = logging.getLogger(__name__)

#: Chave em `schema_meta` com o dia (UTC) do ultimo expurgo.
CHAVE = "expurgo_de_vagas"


@dataclass(frozen=True)
class Expurgo:
    """O que um expurgo apagou."""

    vagas: int
    descricoes: int
    notas: int


class ExpurgoDeVagas:
    def __init__(self, connection, dias: int = 30) -> None:
        self._connection = connection
        self._dias = dias

    def talvez_executar(self, agora: datetime | None = None) -> Expurgo | None:
        """Expurga se ainda nao expurgou hoje. Devolve `None` quando nao rodou."""
        if self._dias <= 0:
            return None
        agora = agora or datetime.now(timezone.utc)
        hoje = agora.date().isoformat()
        linha = self._connection.execute(
            "SELECT valor FROM schema_meta WHERE chave = ?", (CHAVE,)
        ).fetchone()
        if linha is not None and linha[0] == hoje:
            return None
        resultado = self.executar(agora)
        self._connection.execute(
            "INSERT INTO schema_meta (chave, valor, aplicada_em) VALUES (?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, "
            "aplicada_em = excluded.aplicada_em",
            (CHAVE, hoje, agora.isoformat(timespec="seconds")),
        )
        self._connection.commit()
        return resultado

    def candidatas(self, agora: datetime | None = None) -> list[tuple[str, str]]:
        """Pares (usuario, vaga) que o expurgo apagaria agora."""
        limite = self._limite(agora)
        return [
            (linha[0], linha[1])
            for linha in self._connection.execute(
                """
                SELECT j.user_id, j.job_id FROM jobs j
                WHERE j.ultima_vez_em < ? AND j.estado <> ?
                  AND NOT EXISTS (
                      SELECT 1 FROM enrichment_requests r
                      WHERE r.user_id = j.user_id AND r.job_id = j.job_id
                        AND r.estado = 'pendente'
                  )
                """,
                (limite, APLICADO),
            )
        ]

    def executar(self, agora: datetime | None = None) -> Expurgo:
        """Apaga as candidatas e o que so existe por causa delas."""
        alvos = self.candidatas(agora)
        notas = 0
        descricoes = 0
        with self._connection:
            for user_id, job_id in alvos:
                notas += self._connection.execute(
                    "DELETE FROM scores WHERE user_id = ? AND job_id = ?",
                    (user_id, job_id),
                ).rowcount
                self._connection.execute(
                    "DELETE FROM enrichment_requests WHERE user_id = ? AND job_id = ?",
                    (user_id, job_id),
                )
                self._connection.execute(
                    "DELETE FROM jobs WHERE user_id = ? AND job_id = ?",
                    (user_id, job_id),
                )
            # Descricao compartilhada: sai so a das vagas apagadas aqui, e so
            # quando nenhum outro usuario ainda tem a vaga.
            for job_id in {job_id for _user, job_id in alvos}:
                descricoes += self._connection.execute(
                    "DELETE FROM job_descriptions WHERE job_id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM jobs WHERE job_id = ?)",
                    (job_id, job_id),
                ).rowcount
        resultado = Expurgo(vagas=len(alvos), descricoes=descricoes, notas=notas)
        if alvos:
            logger.info(
                "expurgo: %s vaga(s) sem aparecer ha %s dias, %s descricao(oes) "
                "e %s nota(s) apagadas",
                resultado.vagas, self._dias, resultado.descricoes, resultado.notas,
            )
        return resultado

    def _limite(self, agora: datetime | None) -> str:
        agora = agora or datetime.now(timezone.utc)
        return (agora - timedelta(days=self._dias)).isoformat(timespec="seconds")
