"""Distribuicao da capacidade de coleta entre os usuarios.

Acrescentar usuario nao acrescenta capacidade: a coleta sob sessao vem de uma
conta operacional unica e serializada. A cota existe para que essa capacidade
fixa seja repartida de forma previsivel, em vez de ficar com quem chegar
primeiro.

O teto e movel e avaliado no momento do debito, e nao um saldo distribuido no
inicio do dia. A diferenca importa: usuarios entram ao longo do dia, e recalcular
um saldo ja distribuido deixaria quem consumiu com consumo acima do proprio teto
-- um estado impossivel de representar e origem de recusa retroativa. Com teto
movel, quem entra encolhe o teto de todos dali em diante, e quem ja gastou
apenas para de receber cota nova mais cedo.

A virada do dia zera o consumo sem transportar saldo. Acumular transformaria um
usuario inativo por uma semana num pico capaz de derrubar a conta operacional
para todo mundo, que e exatamente o risco que a cota existe para conter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..store.repository import Repository


@dataclass(frozen=True)
class QuotaStatus:
    """Situacao da cota de um usuario no dia corrente."""

    user_id: str
    dia: str
    consumida: int
    teto: int

    @property
    def disponivel(self) -> int:
        return max(0, self.teto - self.consumida)

    @property
    def esgotada(self) -> bool:
        return self.consumida >= self.teto


class QuotaAllocator:
    """Calcula o teto corrente, debita o consumo e registra o total."""

    def __init__(self, connection, config, hoje=None) -> None:
        self._repository = Repository(connection)
        self._orcamento = config.collection.orcamento_diario_coleta
        # Data em UTC, nao local: todo carimbo gravado no banco e UTC, e contar
        # "hoje" no fuso local fazia o contador zerar antes da virada do dia --
        # no horario de Brasilia, todo limite diario deixava de valer das 21h a
        # meia-noite.
        self._hoje = hoje or today

    # ------------------------------------------------------------ leitura
    def active_users(self, dia: str | None = None) -> int:
        """Usuarios com run solicitado no dia. Minimo de um, para nao dividir por zero."""
        dia = dia or self._hoje()
        linhas = self._repository.execute(
            "SELECT count(DISTINCT user_id) FROM runs "
            "WHERE substr(solicitado_em, 1, 10) = ?",
            (dia,),
        ).fetchone()
        return max(1, int(linhas[0] or 0))

    def ceiling(self, dia: str | None = None) -> int:
        """Teto corrente por usuario, recalculado a cada consulta."""
        return max(0, self._orcamento // self.active_users(dia))

    def status(self, user_id: str, dia: str | None = None) -> QuotaStatus:
        dia = dia or self._hoje()
        linhas = self._repository.for_user(user_id).select(
            "daily_quota", where="dia = ?", params=(dia,)
        )
        consumida = int(linhas[0]["consumida"]) if linhas else 0
        return QuotaStatus(
            user_id=user_id, dia=dia, consumida=consumida, teto=self.ceiling(dia)
        )

    def can_spend(self, user_id: str, dia: str | None = None) -> bool:
        return not self.status(user_id, dia).esgotada

    # ------------------------------------------------------------ escrita
    def spend(self, user_id: str, dia: str | None = None) -> QuotaStatus:
        """Debita um enriquecimento efetivamente pago."""
        dia = dia or self._hoje()
        scope = self._repository.for_user(user_id)
        existente = scope.select("daily_quota", where="dia = ?", params=(dia,))
        if existente:
            scope.update(
                "daily_quota",
                {"consumida": int(existente[0]["consumida"]) + 1},
                where="dia = ?",
                params=(dia,),
            )
        else:
            scope.insert("daily_quota", {"dia": dia, "consumida": 1})
        return self.status(user_id, dia)

    # ------------------------------------------------------------ relatorio
    def consumption(self, dia: str | None = None) -> dict[str, int]:
        """Consumo por usuario no dia, para o registro do run."""
        dia = dia or self._hoje()
        linhas = self._repository.execute(
            "SELECT user_id, consumida FROM daily_quota WHERE dia = ?", (dia,)
        ).fetchall()
        return {linha["user_id"]: int(linha["consumida"]) for linha in linhas}

    def total_consumption(self, dia: str | None = None) -> int:
        return sum(self.consumption(dia).values())


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
