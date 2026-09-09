"""Agendamento dos runs recorrentes e o portao de recuperacao.

Com capacidade de coleta serializada e escassa, a ordem de atendimento e a
diferenca entre um servico justo e um servico que atende sempre os mesmos. O
rodizio garante que ninguem seja atendido duas vezes antes que todos os ativos
tenham sido atendidos uma vez.

O adiamento apos bloqueio nao mora no agendador externo. Um agendador do sistema
operacional dispara no horario independentemente do que aconteceu antes, entao
guardar o adiamento nele seria guarda-lo onde ninguem consulta. O adiamento e um
portao na entrada do ciclo: consulta o ultimo bloqueio e encerra sem tocar a
rede se ainda estiver dentro do intervalo de recuperacao.

Um bloqueio suspende os proximos runs de todos, e nao apenas do usuario que o
provocou, porque o recurso bloqueado e comum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .pipeline.governor import RateGovernor
from .store.repository import Repository
from .worker.queue import INTERROMPIDO, RECUSADO, QueueError, RunQueue

logger = logging.getLogger(__name__)

INCREMENTAL = "incremental"
AMPLA = "ampla"


class SchedulerRefusal(Exception):
    """O ciclo ou a solicitacao nao pode prosseguir agora."""


@dataclass
class CycleResult:
    """O que um ciclo de agendamento fez."""

    enfileirados: list[str] = field(default_factory=list)
    recusados: dict[str, str] = field(default_factory=dict)
    interrompidos: list[str] = field(default_factory=list)
    adiado_por_bloqueio: bool = False
    motivo: str | None = None


class Scheduler:
    """Enfileira runs em rodizio, respeitando bloqueio e run em andamento."""

    def __init__(self, connection, config, governor: RateGovernor | None = None,
                 hoje=None) -> None:
        self._repository = Repository(connection)
        self._queue = RunQueue(connection)
        self._config = config
        self._governor = governor
        # Data em UTC, nao local: todo carimbo gravado no banco e UTC, e contar
        # "hoje" no fuso local fazia o contador zerar antes da virada do dia --
        # no horario de Brasilia, todo limite diario deixava de valer das 21h a
        # meia-noite.
        self._hoje = hoje or _hoje_utc

    # ------------------------------------------------------------- portao
    def recovery_gate(self) -> str | None:
        """Motivo do adiamento, ou `None` quando o ciclo pode seguir."""
        if self._governor is None:
            return None
        bloqueio = self._governor.active_block()
        if bloqueio is None:
            return None
        return (
            f"bloqueio de coleta em {bloqueio['ocorrido_em']} apos "
            f"{bloqueio['tentativas']} tentativas; aguardando o intervalo de "
            f"recuperacao de {self._config.collection.intervalo_recuperacao_s}s"
        )

    # ------------------------------------------------------------- ciclo
    def run_cycle(self) -> CycleResult:
        """Executa um ciclo: libera travados, confere o portao e enfileira."""
        resultado = CycleResult()

        resultado.interrompidos = self._queue.reap_stalled(
            self._config.run.duracao_maxima_s
        )

        motivo = self.recovery_gate()
        if motivo:
            resultado.adiado_por_bloqueio = True
            resultado.motivo = motivo
            logger.info("ciclo adiado: %s", motivo)
            return resultado

        for user_id in self.rotation():
            try:
                resultado.enfileirados.append(
                    self._queue.enqueue(user_id, janela=INCREMENTAL)
                )
            except QueueError as exc:
                resultado.recusados[user_id] = str(exc)
        return resultado

    def rotation(self) -> list[str]:
        """Ordem de atendimento: quem esperou mais vem primeiro.

        Usuario que nunca rodou vem antes de todos, porque a espera dele e a
        maior possivel.
        """
        linhas = self._repository.execute(
            """
            SELECT u.user_id, max(r.solicitado_em) AS ultimo
            FROM users u
            LEFT JOIN runs r ON r.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY (ultimo IS NOT NULL), ultimo, u.user_id
            """
        ).fetchall()
        return [linha["user_id"] for linha in linhas]

    # -------------------------------------------------------- run imediato
    def immediate_remaining(self, user_id: str) -> int:
        """Quantas buscas imediatas ainda cabem hoje.

        A interface precisa do saldo antes de oferecer o botao: um limite que
        so aparece na recusa faz o usuario gastar as tres tentativas sem saber
        que estava gastando.
        """
        restam = self._config.run.runs_imediatos_por_dia - self._immediate_today(
            user_id
        )
        return max(0, restam)

    #: Alcances que a interface oferece, em horas. A janela permanece `ampla`
    #: em todos: ela diz que o run foi pedido por alguem, e nao quanto ele
    #: alcanca -- e e por `ampla` que a cota diaria conta.
    ALCANCES = {"30d": 720, "7d": 168, "1d": 24}
    ALCANCE_PADRAO = "30d"

    def request_immediate(self, user_id: str, alcance: str | None = None) -> str:
        """Cria um run solicitado pelo usuario, com a janela ampla.

        `alcance` escolhe quantas horas de publicacao a busca cobre. Sem ele o
        comportamento e o de antes: os 30 dias da configuracao.
        """
        chave = alcance or self.ALCANCE_PADRAO
        if chave not in self.ALCANCES:
            raise SchedulerRefusal(
                f"alcance {chave!r} desconhecido; esperado um de "
                f"{sorted(self.ALCANCES)}"
            )
        horas = self.ALCANCES[chave]
        usados = self._immediate_today(user_id)
        limite = self._config.run.runs_imediatos_por_dia
        if usados >= limite:
            raise SchedulerRefusal(
                f"limite de {limite} buscas imediatas por dia atingido; "
                "novas solicitacoes serao aceitas a partir de amanha"
            )
        motivo = self.recovery_gate()
        if motivo:
            raise SchedulerRefusal(motivo)
        try:
            return self._queue.enqueue(user_id, janela=AMPLA, janela_horas=horas)
        except QueueError as exc:
            raise SchedulerRefusal(str(exc)) from exc

    def _immediate_today(self, user_id: str) -> int:
        """Buscas imediatas que o usuario de fato gastou hoje.

        A cota cobra trabalho feito, e nao tentativa registrada. Duas formas de
        run nao gastam nada e por isso nao contam:

        A recusa por run ativo, que nao chega a coletar. Cobra-la deixava o
        usuario sem cota por insistir num botao que o proprio sistema recusou --
        tres cliques seguidos bastavam para travar o dia.

        E a interrupcao antes da coleta produzir qualquer coisa. Um run morre
        assim quando o processo cai no meio, tipicamente porque alguem
        reiniciou o servidor; a falha e do sistema e nao uma escolha de quem
        clicou. Interrupcao depois de coletar continua contando, porque ali a
        capacidade de coleta foi de fato consumida.
        """
        hoje = self._hoje()
        linhas = self._repository.execute(
            "SELECT count(*) FROM runs WHERE user_id = ? AND janela = ? "
            "AND substr(solicitado_em, 1, 10) = ? AND estado != ? "
            "AND NOT (estado = ? AND coalesce(n_brutos, 0) = 0)",
            (user_id, AMPLA, hoje, RECUSADO, INTERROMPIDO),
        ).fetchone()
        return int(linhas[0] or 0)

    # ------------------------------------------------------------- janela
    def window_hours(self, janela: str, janela_horas: int | None = None) -> int:
        """Horas de publicacao que a busca deve alcancar.

        O que o run gravou vence a configuracao. Sem isso, reler um run antigo
        aplicaria o alcance de hoje a uma busca feita com outro -- e a escolha
        do usuario nao sobreviveria ao proprio run.
        """
        if janela_horas:
            return int(janela_horas)
        colecao = self._config.collection
        return (
            colecao.janela_ampla_horas if janela == AMPLA
            else colecao.janela_incremental_horas
        )


def _hoje_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
