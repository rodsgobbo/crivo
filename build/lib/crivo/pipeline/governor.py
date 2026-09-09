"""Governador de taxa: o unico ponto do sistema que emite requisicao sob sessao.

O spec de origem chamou esta contencao de obrigatoria e nao opcional, e a razao
e observada e nao teorica: no run manual, apos cerca de quinze aberturas de
pagina de detalhe, o endereco de descricao parou de responder por varios
minutos. Nao houve erro visivel -- houve esqueleto de carregamento infinito.

Por isso o bloqueio aqui e um estado gravado e nao uma sequencia de tempos
esgotados espalhada pelo log. Ao terceiro fracasso consecutivo, o governador
grava o bloqueio e passa a recusar toda chamada. Um operador que olhe o registro
do run ve "bloqueio de coleta", e nao tres linhas de tempo esgotado que alguem
precisa interpretar.

A espera entre chamadas e aleatoria dentro de uma faixa, e nao fixa: cadencia
exata e por si so um sinal de automacao.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..store.repository import Repository

logger = logging.getLogger(__name__)

#: Fracassos consecutivos que caracterizam bloqueio.
LIMITE_DE_FRACASSOS = 3

#: Multiplicador do recuo a cada tempo esgotado.
FATOR_DE_RECUO = 2.0


class CollectionBlocked(Exception):
    """Ha bloqueio de coleta registrado: nenhuma chamada nova e aceita."""


class AttemptTimeout(Exception):
    """A tentativa excedeu o tempo limite."""


@dataclass
class GovernorStats:
    """O que o governador viu, para o registro do run."""

    chamadas: int = 0
    esperas: list[float] = None
    fracassos_consecutivos: int = 0
    bloqueios: int = 0

    def __post_init__(self) -> None:
        if self.esperas is None:
            self.esperas = []


class RateGovernor:
    """Serializa, espaca e recua. Recusa tudo depois de bloquear."""

    def __init__(
        self,
        connection,
        config,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = Repository(connection)
        colecao = config.collection
        self._min_s = colecao.intervalo_enriquecimento_min_s
        self._max_s = colecao.intervalo_enriquecimento_max_s
        self._recuperacao_s = colecao.intervalo_recuperacao_s
        self._sleep = sleep
        self._jitter = jitter
        self._now = now or (lambda: datetime.now(timezone.utc))
        # Serializacao em todo o processo. O processo de enriquecimento e unico
        # na instalacao, entao esta trava cobre o sistema inteiro.
        self._lock = threading.Lock()
        self._ultima_chamada: float | None = None
        self.stats = GovernorStats()

    # ------------------------------------------------------------ bloqueio
    def active_block(self) -> dict | None:
        """Bloqueio ainda dentro do intervalo de recuperacao, se houver."""
        linhas = self._repository.select(
            "collection_blocks", order_by="rowid DESC", limit=1
        )
        if not linhas:
            return None
        bloco = linhas[0]
        if bloco["liberado_em"]:
            return None
        ocorrido = datetime.fromisoformat(bloco["ocorrido_em"])
        if self._now() >= ocorrido + timedelta(seconds=self._recuperacao_s):
            self._repository.execute(
                "UPDATE collection_blocks SET liberado_em = ? WHERE block_id = ?",
                (_stamp(self._now()), bloco["block_id"]),
            )
            return None
        return {
            "block_id": bloco["block_id"],
            "ocorrido_em": bloco["ocorrido_em"],
            "tentativas": bloco["tentativas"],
        }

    def record_block(self, tentativas: int) -> str:
        """Grava o bloqueio. A partir daqui toda chamada e recusada."""
        block_id = str(uuid.uuid4())
        self._repository.insert(
            "collection_blocks",
            {
                "block_id": block_id,
                "ocorrido_em": _stamp(self._now()),
                "tentativas": tentativas,
            },
        )
        self.stats.bloqueios += 1
        logger.warning(
            "bloqueio de coleta registrado apos %s fracassos consecutivos", tentativas
        )
        return block_id

    # ------------------------------------------------------------ execucao
    def run(self, operacao: Callable[[], Any], tentativas: int = 3) -> Any:
        """Executa uma requisicao sob contencao, com recuo crescente."""
        if self.active_block() is not None:
            raise CollectionBlocked(
                "ha bloqueio de coleta registrado; nenhuma requisicao nova sera "
                "emitida ate o intervalo de recuperacao passar"
            )
        with self._lock:
            self._aguardar_intervalo()
            return self._tentar(operacao, tentativas)

    def _aguardar_intervalo(self) -> None:
        if self._ultima_chamada is None:
            return
        espera = self._jitter(self._min_s, self._max_s)
        self.stats.esperas.append(espera)
        self._sleep(espera)

    def _tentar(self, operacao: Callable[[], Any], tentativas: int) -> Any:
        recuo = float(max(self._max_s, 1))
        ultimo_erro: Exception | None = None

        for tentativa in range(1, tentativas + 1):
            try:
                resultado = operacao()
            except AttemptTimeout as exc:
                ultimo_erro = exc
                self.stats.fracassos_consecutivos += 1
                logger.info(
                    "tentativa %s de %s esgotou o tempo; recuando %.0fs",
                    tentativa, tentativas, recuo,
                )
                if tentativa < tentativas:
                    self.stats.esperas.append(recuo)
                    self._sleep(recuo)
                    recuo *= FATOR_DE_RECUO
                continue
            self._ultima_chamada = time.monotonic()
            self.stats.chamadas += 1
            self.stats.fracassos_consecutivos = 0
            return resultado

        self.record_block(self.stats.fracassos_consecutivos)
        raise CollectionBlocked(
            f"{tentativas} tentativas consecutivas falharam; coleta interrompida"
        ) from ultimo_erro


def _stamp(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")
