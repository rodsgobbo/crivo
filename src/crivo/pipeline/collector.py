"""Execucao das buscas, normalizacao e deduplicacao dos cards.

Busca vazia e busca com falha sao registradas de forma diferente porque
significam coisas diferentes: a primeira e sinal de mercado e ajuda a calibrar o
planejador; a segunda e sinal de defeito e precisa aparecer no registro do run.
Somar as duas num contador so apagaria a distincao justamente quando ela importa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from ..store.repository import Repository
from .governor import CollectionBlocked
from .republicacao import SINAL as SINAL_REPUBLICADA
from .republicacao import Republicacao
from .sources.guest import Card, JobSource, SearchOutcome

logger = logging.getLogger(__name__)

NOVO = "novo"

#: Origem registrada para cards que vieram de recomendacao e nao de busca.
SEMENTE = "recomendacao"


def _registra_frentes(resultado) -> None:
    """Escreve no log quantos cards cada frente devolveu.

    A frente e um texto de localizacao que a origem resolve no catalogo dela, e
    endereco que nao resolve se parece com mercado vazio: zero cards, sem erro.
    Esta linha e o unico lugar onde os dois casos se distinguem -- uma frente em
    zero enquanto as outras trazem dezenas e um endereco invalido, e todas em
    zero e uma janela sem vaga nenhuma.

    Sai tambem no cancelamento, porque e justamente o run interrompido cujo
    motivo alguem vai procurar depois.
    """
    if not resultado.por_frente:
        return
    logger.info(
        "coleta por frente: %s",
        "; ".join(
            f"{rotulo} {cards} card(s)"
            for rotulo, cards in resultado.por_frente.items()
        ),
    )
    vazias = [rotulo for rotulo, cards in resultado.por_frente.items() if not cards]
    if vazias and len(vazias) < len(resultado.por_frente):
        # Avisa so quando ha com o que comparar. Todas em zero e outra coisa: e
        # a janela que nao tinha vaga, e nao o endereco que nao existe.
        logger.warning(
            "frente(s) sem nenhum card: %s; confira se a localizacao existe no "
            "catalogo da origem",
            ", ".join(vazias),
        )


def _termos_restantes(pedidos, indice: int) -> list[str]:
    """Termos que nao serao tentados, contados uma vez cada.

    O que sobrou e uma lista de pares (termo, frente), e o relatorio fala de
    termos: sem a deduplicacao, uma coleta barrada com duas frentes diria que
    "Head of Platform" nao foi tentada duas vezes.
    """
    vistos: dict[str, None] = {}
    for termo, _frente in pedidos[indice + 1:]:
        vistos.setdefault(termo, None)
    return list(vistos)


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
    #: Cards que cada frente devolveu, pelo rotulo dela, antes da deduplicacao.
    #:
    #: Sem esta conta nao ha como saber se uma frente esta funcionando. A
    #: localizacao e um texto que a origem resolve no catalogo dela: "European
    #: Union" ou "Canada" ou nao resolvem, e o sintoma de um endereco que nao
    #: existe e identico ao de um mercado vazio -- zero cards, sem erro. Antes
    #: da deduplicacao de proposito: a frente que achou a vaga depois de outra
    #: tambem a achou, e descontar isso faria a ultima frente parecer inutil.
    por_frente: dict[str, int] = field(default_factory=dict)

    @property
    def brutos(self) -> int:
        return len(self.cards) + self.repetidos

    @property
    def bloqueada(self) -> bool:
        return bool(self.bloqueadas)


class Collector:
    """Roda as buscas do run e grava os cards sob o usuario dono."""

    def __init__(
        self, connection, source: JobSource, governor=None, republicacao=None
    ) -> None:
        self._repository = Repository(connection)
        self._source = source
        self._republicacao = (
            republicacao if republicacao is not None else Republicacao()
        )
        # O governador chega opcional para nao obrigar todo teste de coleta a
        # montar um. Em producao ele nunca falta: a coleta era o unico caminho
        # do sistema que emitia requisicao a origem sem passar por contencao, e
        # era ela quem gastava as centenas de requisicoes em rajada.
        self._governor = governor

    def collect(
        self,
        user_id: str,
        buscas: list[str],
        frentes,
        janela_horas: int,
        quantidade: int = 25,
        progresso=None,
    ) -> CollectionResult:
        """Executa as buscas em cada frente e devolve os cards unicos do run.

        `frentes` diz onde procurar: cada uma e um par (localizacao, remoto), e
        cada termo e consultado em todas elas. Antes havia uma localizacao so --
        a do perfil --, e por isso vaga remota paga em moeda forte so aparecia
        por acidente.

        O card e unico no run inteiro, e nao por frente: a mesma vaga achada
        aqui e na frente internacional entra uma vez.

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
        # Um pedido por par (termo, frente). A ordem intercala as frentes em vez
        # de esgotar uma antes de comecar a outra: coleta barrada no meio ainda
        # deixa todas as frentes representadas, e esgotar a primeira faria o
        # bloqueio apagar as seguintes inteiras.
        pedidos = [(termo, frente) for termo in buscas for frente in frentes]
        # Toda frente declarada aparece na conta, inclusive a que nao for
        # alcancada: ausente e zero dizem coisas diferentes -- uma frente que
        # nunca rodou nao e uma frente que nao achou nada.
        resultado.por_frente = {frente.rotulo: 0 for frente in frentes}

        for indice, (termo, frente) in enumerate(pedidos):
            saida = self._buscar(
                termo, frente.local, janela_horas, quantidade, frente.remoto
            )
            resultado.por_frente[frente.rotulo] += len(saida.cards)
            self._absorve(saida, resultado, vistos)
            self._grava_novos(user_id, resultado)
            if saida.bloqueada:
                # Barrado uma vez, as buscas restantes so gastariam requisicao
                # contra um portao fechado -- e cada tentativa a mais alonga o
                # bloqueio. O que sobrou fica registrado como nao tentado, e nao
                # como mercado vazio.
                resultado.nao_tentadas.extend(_termos_restantes(pedidos, indice))
                logger.warning(
                    "coleta barrada em %r na frente %r; %s termo(s) restante(s) "
                    "nao serao tentados",
                    termo, frente.rotulo, len(resultado.nao_tentadas),
                )
                break
            if progresso is not None:
                try:
                    # O total e de requisicoes, e nao de termos: e ele que mede
                    # o tempo que a coleta ainda vai levar.
                    progresso(resultado, indice + 1, len(pedidos))
                except CollectionCancelled:
                    resultado.interrompida = True
                    resultado.nao_tentadas.extend(_termos_restantes(pedidos, indice))
                    logger.info("coleta cancelada apos %s requisicoes", indice + 1)
                    _registra_frentes(resultado)
                    raise
        _registra_frentes(resultado)
        return resultado

    def incorporar(self, user_id: str, saida: SearchOutcome, resultado: CollectionResult) -> None:
        """Absorve o resultado de uma fonte que nao passa pelas frentes.

        O mesmo caminho de uma busca: deduplicacao contra o que o run ja tem,
        gravacao sob o dono e contagem de novas. Uma vaga que o LinkedIn e uma
        fonte remota trouxerem ambos entra uma vez.
        """
        vistos = {card.job_id for card in resultado.cards}
        self._absorve(saida, resultado, vistos)
        self._grava_novos(user_id, resultado)

    # ------------------------------------------------------------ interno
    def _buscar(
        self, termo: str, local: str, janela_horas: int, quantidade: int,
        remoto: bool = False,
    ) -> SearchOutcome:
        """Uma busca, sob contencao quando ha governador."""
        def pedir():
            return self._source.search(termo, local, janela_horas, quantidade, remoto)

        if self._governor is None:
            return pedir()
        try:
            return self._governor.run(pedir)
        except CollectionBlocked as exc:
            return SearchOutcome(busca=termo, bloqueada=True, falha=str(exc))

    def _grava_novos(self, user_id: str, resultado: CollectionResult) -> None:
        """Persiste os cards deste resultado que ainda nao passaram pelo banco.

        Gravar por busca e nao so no fim: antes, uma coleta interrompida no
        meio -- por bloqueio, por cancelamento ou porque o processo caiu --
        jogava fora tudo o que ja tinha custado requisicao.
        """
        for indice in range(len(resultado.gravados), len(resultado.cards)):
            # O card marcado volta para a lista: a passada provisoria pontua a
            # partir dela, e nao do banco.
            card = self._marca_republicacao(user_id, resultado.cards[indice])
            resultado.cards[indice] = card
            if self._persist(user_id, card):
                resultado.novos.append(card.job_id)
            resultado.gravados.append(card.job_id)

    def _marca_republicacao(self, user_id: str, card: Card) -> Card:
        """Acrescenta o sinal de republicacao ao card, quando ele se aplica.

        Vale tambem para a vaga que reaparece: ela e comparada com o que existia
        quando surgiu, e assim as vagas gravadas antes desta regra ganham a marca
        na proxima vez que alguma busca as trouxer.
        """
        if SINAL_REPUBLICADA in card.flags:
            return card
        scope = self._repository.for_user(user_id)
        existente = scope.select(
            "jobs", where="job_id = ?", params=(card.job_id,),
            columns="primeira_vez_em, flags",
        )
        flags_gravadas = _from_json(existente[0]["flags"]) if existente else []
        if SINAL_REPUBLICADA in flags_gravadas:
            return replace(card, flags=(*card.flags, SINAL_REPUBLICADA))
        vista_em = existente[0]["primeira_vez_em"] if existente else _stamp()
        anterior = self._republicacao.anterior(
            scope, card.job_id, card.empresa, card.titulo, vista_em
        )
        if anterior is None:
            return card
        logger.debug(
            "card %s republica anuncio visto em %s", card.job_id, anterior[:10]
        )
        if existente:
            # O caminho de atualizacao de `_persist` nao reescreve sinais, entao
            # a marca da vaga ja gravada vai para o banco aqui.
            scope.update(
                "jobs",
                {"flags": _json(sorted({*flags_gravadas, SINAL_REPUBLICADA}))},
                where="job_id = ?", params=(card.job_id,),
            )
        return replace(card, flags=(*card.flags, SINAL_REPUBLICADA))

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
