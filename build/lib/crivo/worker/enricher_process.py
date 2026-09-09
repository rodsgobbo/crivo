"""Processo unico que detem a sessao da conta operacional.

Ser unico na instalacao e o que faz a serializacao exigida pela contencao ser
consequencia da topologia, e nao uma trava que alguem precisa lembrar de
adquirir antes de cada chamada. Subir dois destes anularia a contencao sem que
nenhum teste unitario percebesse, e por isso o laco adquire uma trava de
instancia no proprio banco antes de comecar.

O laco consome a fila de enriquecimento, atende os pedidos sob o governador e,
ao esgotar os pedidos de um run, devolve o run a fila para o segundo trecho.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..pipeline.enricher import Enricher
from ..pipeline.governor import RateGovernor
from ..pipeline.quota import QuotaAllocator
from ..pipeline.sources.guest import Card
from ..store.locks import CHAVE_DO_ENRIQUECEDOR, AlreadyRunning, InstanceLock
from ..store.repository import Repository
from .enrichment_queue import EnrichmentQueue
from .queue import RunQueue

logger = logging.getLogger(__name__)

#: Chave da trava de instancia unica, gravada em `schema_meta`. Definida na
#: camada de store porque quem adquire e quem consulta sao processos distintos.
CHAVE_DE_INSTANCIA = CHAVE_DO_ENRIQUECEDOR

#: Reexportada para nao quebrar quem ja a importava daqui. A definicao mora na
#: camada de store porque o processo de runs precisa da mesma excecao, e cada
#: um definir a sua faria `except AlreadyRunning` pegar so metade dos casos.
__all__ = ["AlreadyRunning", "CycleReport", "EnricherProcess"]


@dataclass
class CycleReport:
    """O que uma passada do laco fez."""

    atendidos: list[str] = field(default_factory=list)
    sem_descricao: list[str] = field(default_factory=list)
    runs_retomados: list[str] = field(default_factory=list)
    bloqueado: bool = False

    @property
    def houve_trabalho(self) -> bool:
        return bool(self.atendidos or self.sem_descricao or self.runs_retomados)


class EnricherProcess:
    """Laco do processo de enriquecimento."""

    def __init__(self, connection, config, source) -> None:
        self._connection = connection
        self._repository = Repository(connection)
        self._config = config
        self._fila = EnrichmentQueue(connection)
        self._runs = RunQueue(connection)
        self._governor = RateGovernor(connection, config)
        self._enricher = Enricher(
            connection, source, self._governor,
            QuotaAllocator(connection, config),
        )
        self._trava = InstanceLock(
            self._repository, CHAVE_DE_INSTANCIA, "enriquecimento", logger
        )

    # ------------------------------------------------------------ instancia
    def acquire(self) -> None:
        """Garante que so exista um processo de enriquecimento."""
        self._trava.acquire()

    def heartbeat(self) -> None:
        """Renova o sinal de vida da trava."""
        self._trava.heartbeat()

    def _dormir(self, segundos: float) -> None:
        """Espera em fatias, renovando a trava entre elas."""
        restante = float(segundos)
        while restante > 0:
            fatia = min(FATIA_DE_SONO_S, restante)
            time.sleep(fatia)
            restante -= fatia
            self.heartbeat()

    def release(self) -> None:
        self._trava.release()

    # ---------------------------------------------------------------- laco
    def run_cycle(self, limite: int = 25) -> CycleReport:
        """Atende os pedidos pendentes e devolve a fila os runs que esgotaram."""
        relatorio = CycleReport()
        pedidos = self._fila.next_batch(limite)
        if not pedidos:
            return relatorio

        por_usuario: dict[str, list] = {}
        for pedido in pedidos:
            por_usuario.setdefault(pedido.user_id, []).append(pedido)

        for user_id, do_usuario in por_usuario.items():
            cards = [
                Card(
                    job_id=p.job_id, titulo="", empresa=None, url=p.url,
                    local=None, modelo_trabalho=None, publicada_em=None,
                )
                for p in do_usuario
            ]
            resultado = self._enricher.enrich(user_id, cards)
            atendidos = set(resultado.enriquecidas + resultado.reusadas)
            relatorio.atendidos.extend(sorted(atendidos))
            relatorio.sem_descricao.extend(resultado.sem_descricao)
            relatorio.bloqueado = relatorio.bloqueado or resultado.bloqueado
            for pedido in do_usuario:
                self._fila.settle(
                    pedido.request_id, atendido=pedido.job_id in atendidos
                )

        for run_id in {p.run_id for p in pedidos}:
            if self._fila.is_drained(run_id):
                self._runs.resume(run_id)
                relatorio.runs_retomados.append(run_id)
                logger.info("run %s devolvido a fila para o segundo trecho", run_id)
        return relatorio

    def serve_forever(self, intervalo_ocioso_s: int = 30, ciclos: int | None = None):
        """Laco principal. `ciclos` limita a execucao, para teste."""
        self.acquire()
        try:
            executados = 0
            while ciclos is None or executados < ciclos:
                # Antes e depois do ciclo: um ciclo cheio pode levar minutos, e
                # a trava precisa continuar dizendo que ha alguem aqui durante
                # ele, e nao so nas bordas.
                self.heartbeat()
                relatorio = self.run_cycle()
                self.heartbeat()
                executados += 1
                if relatorio.bloqueado:
                    logger.warning(
                        "bloqueio de coleta: aguardando o intervalo de recuperacao"
                    )
                    self._dormir(self._config.collection.intervalo_recuperacao_s)
                elif not relatorio.houve_trabalho:
                    self._dormir(intervalo_ocioso_s)
        finally:
            self.release()


#: Tamanho da fatia de sono. Dormir a espera inteira de uma vez deixaria a
#: trava sem sinal de vida por uma hora, e outro processo a tomaria no meio.
FATIA_DE_SONO_S = 60
