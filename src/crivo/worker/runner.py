"""Processo de runs: consome a fila e executa os estagios em ordem fixa.

O executor nao conhece a logica de nenhum estagio. Ele conhece a ordem, o
contrato de entrada e saida e a politica de interrupcao. E isso que permite que
os estagios sejam construidos nas fases seguintes sem tocar aqui.

Um run nao e uma execucao continua: um estagio pode pedir suspensao, e o
executor entao solta o worker em vez de esperar. O trecho seguinte roda quando o
run volta para a fila, o que mantem a espera deliberada do enriquecimento fora
de qualquer worker.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Config
from ..store.repository import Repository, ScopedRepository
from .queue import RunQueue

logger = logging.getLogger(__name__)


class Suspend(Exception):
    """Um estagio pede que o run espere trabalho de outro processo."""


class Cancelled(Exception):
    """O usuario pediu para parar, e o estagio parou num ponto barato.

    E excecao e nao valor de retorno porque a parada precisa atravessar o
    estagio inteiro sem que cada trecho intermediario tenha de lembrar de
    propaga-la. Um estagio que a deixe subir esta correto; um que a engula
    prenderia o usuario ate o fim de um trabalho que ele ja mandou parar.
    """


@dataclass
class RunContext:
    """Estado que atravessa os estagios de um run."""

    run_id: str
    user_id: str
    janela: str
    config: Config
    scope: ScopedRepository
    #: Horas de publicacao gravadas com o run. Nulo quer dizer "use a
    #: configuracao", que e como todo run anterior a versao 2 foi criado.
    janela_horas: int | None = None
    dados: dict[str, Any] = field(default_factory=dict)
    contadores: dict[str, Any] = field(default_factory=dict)
    #: Estagios ja executados neste run, na ordem em que rodaram.
    executados: list[str] = field(default_factory=list)
    #: Sinal de vida do processo executor, para o estagio que demora mais do
    #: que a trava de instancia tolera. O padrao nao faz nada, de modo que um
    #: estagio possa chama-lo sem saber se ha trava alguma.
    keepalive: Any = field(default=lambda: None)


class Stage(Protocol):
    """Unidade de trabalho do pipeline."""

    name: str

    def run(self, context: RunContext) -> None: ...


class Runner:
    """Reserva um run da fila e o leva pelos estagios ate o fim ou a suspensao."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        config: Config,
        stages: Sequence[Stage],
        keepalive: Any = None,
    ) -> None:
        self._connection = connection
        self._config = config
        self._stages = tuple(stages)
        # Sinal de vida do processo, distinto do sinal de vida do run. Um run
        # com coleta longa passa dos quinze minutos que a trava de instancia
        # tolera, e sem isto a partida seguinte concluiria que este processo
        # morreu e tomaria o lugar dele -- com ele ainda executando.
        self._keepalive = keepalive or (lambda: None)
        self._queue = RunQueue(connection)
        self._repository = Repository(connection)
        nomes = [stage.name for stage in self._stages]
        duplicados = {n for n in nomes if nomes.count(n) > 1}
        if duplicados:
            raise ValueError(
                f"estagios com nome repetido {sorted(duplicados)}; "
                "o nome identifica o ponto de retomada e precisa ser unico"
            )

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(stage.name for stage in self._stages)

    def run_once(self) -> RunContext | None:
        """Executa um run da fila. Devolve `None` quando a fila esta vazia."""
        reserved = self._queue.reserve_next()
        if reserved is None:
            return None

        context = RunContext(
            run_id=reserved["run_id"],
            user_id=reserved["user_id"],
            janela=reserved["janela"],
            config=self._config,
            scope=self._repository.for_user(reserved["user_id"]),
            janela_horas=reserved["janela_horas"],
            keepalive=self._keepalive,
        )
        # Estagios ja executados num trecho anterior nao rodam de novo: a
        # retomada continua de onde parou, em vez de repetir requisicao paga.
        ja_feitos = set(self._completed_stages(context.run_id))

        for stage in self._stages:
            if stage.name in ja_feitos:
                context.executados.append(stage.name)
                continue
            # A borda entre dois estagios e o outro ponto barato de parada,
            # alem do interior da coleta: nada foi comecado, nada fica pela
            # metade. Sem esta checagem, cancelar durante a sintese ainda
            # esperaria a sintese inteira.
            if self._queue.cancel_requested(context.run_id):
                return self._cancelar(context, stage.name)
            try:
                stage.run(context)
            except Cancelled:
                return self._cancelar(context, stage.name)
            except Suspend:
                # Os contadores do trecho vao para o banco antes de soltar o
                # worker. `RunContext` nao sobrevive a suspensao, e sem esta
                # gravacao tudo o que o primeiro trecho contou -- quantas vagas
                # vieram, quantas passaram o filtro, o que foi buscado -- se
                # perdia, e o relatorio final abria com zeros.
                if context.contadores:
                    self._queue.record_counters(
                        context.run_id, **context.contadores
                    )
                self._queue.suspend(context.run_id)
                logger.info(
                    "run %s suspenso no estagio %s", context.run_id, stage.name
                )
                return context
            except Exception as exc:
                # Marcado e encerrado, nunca relancado. Relancar levava a
                # excecao ate o laco do processo: o executor morria, o run
                # continuava `em_andamento`, a partida seguinte o devolvia a
                # fila e o matava de novo. Um run com defeito parava os runs de
                # todo mundo.
                logger.exception(
                    "run %s falhou no estagio %s", context.run_id, stage.name
                )
                self._queue.fail(
                    context.run_id,
                    f"falha no estagio {stage.name}: "
                    f"{type(exc).__name__}: {exc}",
                )
                return context
            context.executados.append(stage.name)
            self._mark(context.run_id, stage.name)
            self._queue.heartbeat(context.run_id)

        self._queue.finish(context.run_id, **context.contadores)
        logger.info("run %s concluido", context.run_id)
        return context

    # ------------------------------------------------------------ interno
    def _cancelar(self, context: RunContext, estagio: str) -> RunContext:
        """Encerra o run a pedido, guardando o que ele ja tinha apurado.

        Os contadores vao para o banco antes do encerramento pelo mesmo motivo
        da suspensao: o que a coleta ja trouxe custou requisicao a origem, e
        descarta-lo faria o cancelamento sair mais caro do que deixar terminar.
        """
        self._queue.cancel(context.run_id, **context.contadores)
        logger.info(
            "run %s cancelado pelo usuario no estagio %s", context.run_id, estagio
        )
        return context

    def _mark(self, run_id: str, stage_name: str) -> None:
        """Grava no banco que este estagio terminou."""
        feitos = self._completed_stages(run_id)
        if stage_name in feitos:
            return
        feitos.append(stage_name)
        self._connection.execute(
            "UPDATE runs SET estagios_concluidos = ? WHERE run_id = ?",
            (json.dumps(feitos, ensure_ascii=False), run_id),
        )

    def _completed_stages(self, run_id: str) -> list[str]:
        row = self._connection.execute(
            "SELECT estagios_concluidos FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or not row[0]:
            return []
        return list(json.loads(row[0]))
