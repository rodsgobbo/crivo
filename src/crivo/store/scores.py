"""Persistencia das pontuacoes, com as duas passadas e o historico.

Gravar as duas passadas e o que permite explicar a fila de enriquecimento depois:
a provisoria mostra por que aquela vaga entrou no topo antes de a descricao
existir, e a final mostra o que mudou quando os sinais chegaram.

O historico nunca e sobrescrito. Ele e o unico indicador de que mexer no perfil
adiantou: se a aderencia media sobe depois que o candidato corrige o curriculo,
isso apareceu aqui e em nenhum outro lugar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..scoring.scorer import FINAL, PROVISORIA, ScoreBreakdown
from .repository import Repository


@dataclass(frozen=True)
class StoredScore:
    """Uma pontuacao gravada, de uma passada de um run."""

    job_id: str
    run_id: str
    passada: str
    score: int
    componentes: dict
    lacunas: tuple[str, ...]
    diferenciais: tuple[str, ...]
    descricao_disponivel: bool
    sinais_disponiveis: bool
    criado_em: str


class ScoreStore:
    """Grava e le pontuacoes sem nunca sobrescrever as anteriores."""

    def __init__(self, connection) -> None:
        self._repository = Repository(connection)

    def record(
        self,
        user_id: str,
        run_id: str,
        job_id: str,
        breakdown: ScoreBreakdown,
        lacunas=(),
        diferenciais=(),
    ) -> StoredScore:
        """Grava uma passada. Regravar a mesma passada do mesmo run substitui."""
        scope = self._repository.for_user(user_id)
        criado_em = _stamp()
        valores = {
            "job_id": job_id,
            "run_id": run_id,
            "passada": breakdown.passada,
            "score": breakdown.score,
            "componentes": json.dumps(
                {"componentes": breakdown.componentes, "ajustes": breakdown.ajustes,
                 "bruto": breakdown.bruto},
                ensure_ascii=False,
            ),
            "lacunas": json.dumps(list(lacunas), ensure_ascii=False),
            "diferenciais": json.dumps(list(diferenciais), ensure_ascii=False),
            "descricao_disponivel": int(breakdown.descricao_disponivel),
            "sinais_sessao_disponiveis": int(breakdown.sinais_disponiveis),
            "criado_em": criado_em,
        }
        scope.delete(
            "scores",
            where="job_id = ? AND run_id = ? AND passada = ?",
            params=(job_id, run_id, breakdown.passada),
        )
        scope.insert("scores", valores)
        return _to_stored(valores)

    def for_run(
        self, user_id: str, run_id: str, passada: str = FINAL
    ) -> list[StoredScore]:
        """Pontuacoes de um run numa passada, da maior para a menor."""
        return [
            _row_to_stored(linha)
            for linha in self._repository.for_user(user_id).select(
                "scores",
                where="run_id = ? AND passada = ?",
                params=(run_id, passada),
                order_by="score DESC, job_id",
            )
        ]

    def ranking(self, user_id: str, run_id: str, passada: str = PROVISORIA) -> list[str]:
        """Ordem de prioridade das vagas, para gastar a cota onde rende."""
        return [s.job_id for s in self.for_run(user_id, run_id, passada)]

    def history(self, user_id: str, job_id: str) -> list[StoredScore]:
        """Todas as pontuacoes ja dadas a esta vaga, da mais antiga a mais nova."""
        return [
            _row_to_stored(linha)
            for linha in self._repository.for_user(user_id).select(
                "scores", where="job_id = ?", params=(job_id,), order_by="rowid"
            )
        ]

    def average(self, user_id: str, run_id: str, passada: str = FINAL) -> float | None:
        """Aderencia media de um run. E este numero que se acompanha no tempo."""
        pontuacoes = [s.score for s in self.for_run(user_id, run_id, passada)]
        if not pontuacoes:
            return None
        return round(sum(pontuacoes) / len(pontuacoes), 2)


def _to_stored(valores: dict) -> StoredScore:
    detalhe = json.loads(valores["componentes"])
    return StoredScore(
        job_id=valores["job_id"],
        run_id=valores["run_id"],
        passada=valores["passada"],
        score=valores["score"],
        componentes=detalhe.get("componentes", {}),
        lacunas=tuple(json.loads(valores["lacunas"])),
        diferenciais=tuple(json.loads(valores["diferenciais"])),
        descricao_disponivel=bool(valores["descricao_disponivel"]),
        sinais_disponiveis=bool(valores["sinais_sessao_disponiveis"]),
        criado_em=valores["criado_em"],
    )


def _row_to_stored(linha) -> StoredScore:
    detalhe = json.loads(linha["componentes"])
    return StoredScore(
        job_id=linha["job_id"],
        run_id=linha["run_id"],
        passada=linha["passada"],
        score=int(linha["score"]),
        componentes=detalhe.get("componentes", {}),
        lacunas=tuple(json.loads(linha["lacunas"] or "[]")),
        diferenciais=tuple(json.loads(linha["diferenciais"] or "[]")),
        descricao_disponivel=bool(linha["descricao_disponivel"]),
        sinais_disponiveis=bool(linha["sinais_sessao_disponiveis"]),
        criado_em=linha["criado_em"],
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
