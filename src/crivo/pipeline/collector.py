"""Execucao das buscas, normalizacao e deduplicacao dos cards.

Busca vazia e busca com falha sao registradas de forma diferente porque
significam coisas diferentes: a primeira e sinal de mercado e ajuda a calibrar o
planejador; a segunda e sinal de defeito e precisa aparecer no registro do run.
Somar as duas num contador so apagaria a distincao justamente quando ela importa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..store.repository import Repository
from .governor import CollectionBlocked
from .sources.guest import Card, JobSource, SearchOutcome

logger = logging.getLogger(__name__)

NOVO = "novo"

#: Origem registrada para cards que vieram de recomendacao e nao de busca.
SEMENTE = "recomendacao"


class CollectionCancelled(Exception):
    """O usuario pediu para parar esta coleta."""


@dataclass
class CollectionResult:
    """O que um run coletou, com a distincao entre vazio e defeito."""

    cards: list[Card] = field(default_factory=list)
    novos: list[str] = field(default_factory=list)
    improdutivas: list[str] = field(default_factory=list)
    falhas: dict[str, str] = field(default_factory=dict)
    repetidos: int = 0
    #: Buscas que a origem recusou por excesso, e as que nem chegaram a ser
    #: tentadas depois disso. Contadas a parte de `improdutivas` porque uma diz
    #: "nao ha vaga" e a outra diz "pare de pedir".
    bloqueadas: list[str] = field(default_factory=list)
    nao_tentadas: list[str] = field(default_factory=list)
    interrompida: bool = False
    #: Cards ja gravados, para que a gravacao por busca nao reescreva o que a
    #: busca anterior ja passou pelo banco.
    gravados: list[str] = field(default_factory=list)

    @property
    def brutos(self) -> int:
        return len(self.cards) + self.repetidos

    @property
    def bloqueada(self) -> bool:
        return bool(self.bloqueadas)


class Collector:
    """Roda as buscas do run e grava os cards sob o usuario dono."""

    def __init__(self, connection, source: JobSource, governor=None) -> None:
        self._repository = Repository(connection)
        self._source = source
        # O governador chega opcional para nao obrigar todo teste de coleta a
        # montar um. Em producao ele nunca falta: a coleta era o unico caminho
        # do sistema que emitia requisicao a origem sem passar por contencao, e
        # era ela quem gastava as centenas de requisicoes em rajada.
        self._governor = governor

    def collect(
        self,
        user_id: str,
        buscas: list[str],
        local: str,
        janela_horas: int,
        quantidade: int = 25,
        progresso=None,
    ) -> CollectionResult:
        """Executa as buscas e devolve os cards unicos do run.

        `progresso` e chamado ao fim de cada busca com o resultado parcial. E
        por ele que o run renova o sinal de vida e publica o quanto ja andou:
        sem isso a coleta inteira era um unico salto de vinte minutos em que
        nada no banco mudava, e a leitura obvia de fora era que havia travado.
        Ele tambem e o ponto onde o pedido de cancelamento e atendido -- entre
        duas buscas, e nao no meio de uma, para nao deixar requisicao pela
        metade.
        """
        resultado = CollectionResult()
        vistos: set[str] = set()
        pendentes = list(buscas)

        for indice, termo in enumerate(pendentes):
            saida = self._buscar(termo, local, janela_horas, quantidade)
            self._absorve(saida, resultado, vistos)
            self._grava_novos(user_id, resultado)
            if saida.bloqueada:
                # Barrado uma vez, as buscas restantes so gastariam requisicao
                # contra um portao fechado -- e cada tentativa a mais alonga o
                # bloqueio. O que sobrou fica registrado como nao tentado, e nao
                # como mercado vazio.
                resultado.nao_tentadas.extend(pendentes[indice + 1:])
                logger.warning(
                    "coleta barrada em %r; %s buscas restantes nao serao tentadas",
                    termo, len(resultado.nao_tentadas),
                )
                break
            if progresso is not None:
                try:
                    progresso(resultado, indice + 1, len(pendentes))
                except CollectionCancelled:
                    resultado.interrompida = True
                    resultado.nao_tentadas.extend(pendentes[indice + 1:])
                    logger.info("coleta cancelada apos %s buscas", indice + 1)
                    raise
        return resultado

    # ------------------------------------------------------------ interno
    def _buscar(
        self, termo: str, local: str, janela_horas: int, quantidade: int
    ) -> SearchOutcome:
        """Uma busca, sob contencao quando ha governador."""
        if self._governor is None:
            return self._source.search(termo, local, janela_horas, quantidade)
        try:
            return self._governor.run(
                lambda: self._source.search(termo, local, janela_horas, quantidade)
            )
        except CollectionBlocked as exc:
            return SearchOutcome(busca=termo, bloqueada=True, falha=str(exc))

    def _grava_novos(self, user_id: str, resultado: CollectionResult) -> None:
        """Persiste os cards deste resultado que ainda nao passaram pelo banco.

        Gravar por busca e nao so no fim: antes, uma coleta interrompida no
        meio -- por bloqueio, por cancelamento ou porque o processo caiu --
        jogava fora tudo o que ja tinha custado requisicao.
        """
        for card in resultado.cards[len(resultado.gravados):]:
            if self._persist(user_id, card):
                resultado.novos.append(card.job_id)
            resultado.gravados.append(card.job_id)

    def _absorve(
        self, saida: SearchOutcome, resultado: CollectionResult, vistos: set[str]
    ) -> None:
        if saida.bloqueada:
            resultado.bloqueadas.append(saida.busca)
            if saida.falha:
                resultado.falhas[saida.busca] = saida.falha
            return
        if saida.falha:
            resultado.falhas[saida.busca] = saida.falha
            return
        if saida.improdutiva:
            resultado.improdutivas.append(saida.busca)
            return
        for card in saida.cards:
            if card.job_id in vistos:
                resultado.repetidos += 1
                continue
            vistos.add(card.job_id)
            resultado.cards.append(card)

    def _persist(self, user_id: str, card: Card, origem: str = "busca") -> bool:
        """Grava o card. Devolve `True` quando a vaga e nova para este usuario."""
        scope = self._repository.for_user(user_id)
        agora = _stamp()
        existentes = scope.select(
            "jobs", where="job_id = ?", params=(card.job_id,), columns="job_id"
        )
        if existentes:
            # Reaparecimento: a primeira vez nao se mexe, e o contador de
            # ausencias elegiveis zera porque a vaga voltou a ser vista.
            #
            # A data de publicacao, ao contrario, se atualiza quando a origem
            # traz uma. Anuncio republicado ganha data nova e mantem o mesmo
            # identificador, e sem esta linha ele guardava para sempre a data da
            # primeira vez que o vimos. Numa busca de 24 horas isso fazia dez
            # vagas parecerem violacao da janela quando eram republicacoes com
            # data nossa vencida -- e a conclusao errada foi tirada.
            #
            # Card sem data nao apaga a que existe: ausencia aqui e a origem
            # calando, e nao afirmando que a vaga nao tem data.
            mudancas = {"ultima_vez_em": agora, "ausencias_elegiveis": 0}
            if card.publicada_em:
                mudancas["publicada_em"] = card.publicada_em
            scope.update(
                "jobs",
                mudancas,
                where="job_id = ?",
                params=(card.job_id,),
            )
            return False
        scope.insert(
            "jobs",
            {
                "job_id": card.job_id,
                "titulo": card.titulo,
                "empresa": card.empresa,
                "url": card.url,
                "local": card.local,
                "modelo_trabalho": card.modelo_trabalho,
                "publicada_em": card.publicada_em,
                "flags": _json(list(card.flags)),
                "estado": NOVO,
                "primeira_vez_em": agora,
                "ultima_vez_em": agora,
                # Gravada so na primeira vez, junto de `primeira_vez_em`. A
                # pergunta que ela responde e de origem -- "por que esta vaga
                # apareceu?" -- e reaparecimento nao muda de onde algo veio.
                # No caminho de atualizacao acima ela nao e tocada.
                "busca": card.busca or origem,
            },
        )
        logger.debug("card novo %s via %s", card.job_id, origem)
        return True

    def record_signals(self, user_id: str, job_id: str, flags: list[str]) -> None:
        """Grava os sinais que so a sessao operacional entrega."""
        self._repository.for_user(user_id).update(
            "jobs", {"flags": _json(sorted(set(flags)))},
            where="job_id = ?", params=(job_id,),
        )

    def signals_for(self, user_id: str, job_id: str) -> list[str]:
        rows = self._repository.for_user(user_id).select(
            "jobs", where="job_id = ?", params=(job_id,), columns="flags"
        )
        return _from_json(rows[0]["flags"]) if rows else []


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(valor) -> str:
    import json

    return json.dumps(valor, ensure_ascii=False)


def _from_json(valor) -> list:
    import json

    return json.loads(valor or "[]")
