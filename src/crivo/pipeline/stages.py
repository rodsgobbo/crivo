"""Os estagios do run, na ordem em que acontecem.

Este modulo e montagem, nao logica. Cada estagio ja tem o seu modulo -- o
planejador escolhe as buscas, o coletor executa, o pre-filtro descarta, o
pontuador ordena, o sintetizador escreve -- e o que faltava era o encaixe entre
eles. O executor conhece a ordem e o contrato; aqui moram as pecas que ele
executa.

A separacao em dois trechos existe por causa do enriquecimento. Ler a descricao
de cada vaga e a parte lenta do run, e a espera entre requisicoes e deliberada:
ela e o que mantem a coleta abaixo do que a origem tolera. Segurar um worker
durante essa espera desperdicaria o recurso mais escasso, entao o primeiro
trecho grava os pedidos, pede suspensao e solta o worker. O segundo trecho roda
quando o enriquecedor esgota a fila daquele run.

A pontuacao acontece duas vezes pelo mesmo motivo. A provisoria ordena a fila do
enriquecimento usando so o que a busca devolveu; a final refaz a conta com as
descricoes que chegaram. Inverter a ordem seria circular: escolher o alvo do
orcamento pelo score que depende do que o orcamento pagaria.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

from ..profile.merger import ProfileMerger
from ..scheduler import Scheduler
from ..scoring import gaps
from ..scoring.ontology import Ontology, load_ontology
from ..scoring.scorer import FINAL, PROVISORIA, Scorer
from ..store.insights import InsightStore
from ..store.repository import Repository
from ..worker.enrichment_queue import EnrichmentQueue
from ..worker.queue import RunQueue
from ..worker.runner import Cancelled, RunContext, Suspend
from .collector import CollectionCancelled, Collector
from .republicacao import Republicacao
from .governor import RateGovernor
from .planner import Filters, load_filters, plan, teto_por_frente
from .prefilter import Prefilter
from .sources.guest import REMOTO, Card

logger = logging.getLogger(__name__)


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PerfilStage:
    """Carrega o perfil-alvo, ou recusa o run nomeando o que falta."""

    name = "perfil"

    def __init__(self, connection, config) -> None:
        self._merger = ProfileMerger(connection, config)

    def run(self, context: RunContext) -> None:
        # `require_for_run` levanta quando nao ha historico. Deixar subir e
        # deliberado: um run sem perfil nao tem o que buscar, e mascarar isso
        # produziria um relatorio vazio sem causa visivel.
        versao = self._merger.require_for_run(context.user_id)
        context.dados["perfil"] = {
            **versao.campos,
            "nivel_inferido": versao.nivel_inferido,
        }
        context.dados["problemas_higiene"] = versao.problemas_higiene


class PlanejamentoStage:
    """Escolhe as buscas do run a partir do perfil e do que o usuario pediu."""

    name = "planejamento"

    def __init__(
        self, connection, filters: Filters | None = None, config=None
    ) -> None:
        self._connection = connection
        self._filters = filters if filters is not None else load_filters()
        self._config = config

    def _extras(self, context: RunContext) -> tuple[str, ...]:
        linhas = context.scope.select(
            "buscas_do_usuario", columns="texto", order_by="criado_em"
        )
        return tuple(linha["texto"] for linha in linhas)

    def _rendimento(self, context: RunContext) -> dict[str, tuple[int, int]]:
        """Quanto cada termo rendeu, somando os runs anteriores deste usuario.

        Le o teto de trilha gravado em `componentes`, que e o sinal de que a
        origem casou por palavra solta e trouxe outra carreira. Um termo cujo
        historico inteiro e disso nao merece uma das vinte e cinco vagas do
        plano -- ele gasta requisicao contra o limite de taxa e nao devolve nada.

        Sem historico o mapa sai vazio e nada e rebaixado, que e o estado de
        toda instalacao nova.
        """
        linhas = self._connection.execute(
            """
            SELECT j.busca,
                   COUNT(*) AS total,
                   SUM(CASE WHEN s.componentes LIKE '%outra_trilha%'
                            THEN 1 ELSE 0 END) AS fora
            FROM scores s
            JOIN jobs j ON j.job_id = s.job_id AND j.user_id = s.user_id
            WHERE s.user_id = ? AND s.passada = ? AND j.busca IS NOT NULL
            GROUP BY j.busca
            """,
            (context.user_id, FINAL),
        ).fetchall()
        return {
            linha["busca"]: (int(linha["total"]), int(linha["fora"] or 0))
            for linha in linhas
        }

    def run(self, context: RunContext) -> None:
        perfil = carregar_perfil(self._connection, self._config, context)
        config = self._config or context.config
        buscas = plan(
            perfil,
            perfil.get("nivel_inferido") or "pleno",
            self._filters,
            extras=self._extras(context),
            # O teto conta requisicao, e nao termo: cada termo e consultado em
            # todas as frentes. Dividir aqui e o que impede uma frente nova de
            # dobrar a exposicao a origem sem ninguem ter decidido isso.
            limite=teto_por_frente(
                getattr(config.collection, "max_buscas_por_run", None),
                len(frentes_de_busca(config, perfil.get("localizacao") or "")),
            ),
            rendimento=self._rendimento(context),
        )
        context.dados["buscas"] = buscas
        # O contador guarda o texto das buscas, e nao a quantidade: quando o
        # run traz pouca coisa, a primeira pergunta e o que ele procurou.
        context.contadores["buscas"] = [q.texto for q in buscas]
        # Gravado agora, e nao so na suspensao ou no fim. Um run devolvido a
        # fila retoma com a memoria vazia, e sem os termos no banco a coleta
        # nao tem o que executar -- era a `KeyError: 'buscas'` que derrubava o
        # processo executor.
        #
        # So quando ha run para gravar: a pagina inicial roda este mesmo
        # estagio, com um identificador que nao existe, para mostrar as buscas
        # que o proximo run executaria. Exigir a linha aqui transformaria uma
        # previa em erro.
        fila = RunQueue(self._connection)
        if fila.get(context.run_id) is not None:
            fila.record_counters(
                context.run_id, buscas=context.contadores["buscas"]
            )


class ColetaStage:
    """Executa as buscas e grava os cards sob o usuario dono."""

    name = "coleta"

    def __init__(self, connection, source, config, governor) -> None:
        # O governador e obrigatorio e vem de fora. A coleta era o unico caminho
        # do sistema que emitia requisicao a origem sem passar por contencao --
        # noventa buscas em rajada gastavam mais requisicao do que um dia
        # inteiro de enriquecimento, justamente sob o limite que o
        # enriquecimento respeita e ela nao.
        #
        # Sem padrao, e nao com `None` significando "sem contencao": o defeito
        # que este parametro corrige e exatamente o de alguem montar a coleta
        # sem pensar em taxa, e um padrao silencioso deixaria esse caminho
        # aberto de novo.
        self._collector = Collector(
            connection, source, governor,
            Republicacao(
                config.collection.republicacao_janela_dias,
                config.collection.republicacao_ignoradas,
            ),
        )
        self._connection = connection
        self._config = config
        self._fila = RunQueue(connection)
        self._remotas = _fontes_remotas(config)

    def run(self, context: RunContext) -> None:
        buscas = carregar_buscas(self._connection, context)
        if not buscas:
            raise RunSemBuscas(
                f"run {context.run_id} retomado sem buscas gravadas; "
                "peca a busca de novo para que o planejamento rode"
            )
        buscas = priorizar_pendentes(
            buscas, coleta_anterior(self._connection, context.user_id, context.run_id)
        )
        perfil = carregar_perfil(self._connection, self._config, context)
        # A escolha gravada com o run vence a configuracao: foi o usuario quem
        # decidiu o alcance, e um run releito meses depois precisa continuar
        # dizendo o que ele de fato cobriu.
        janela = Scheduler(self._connection, self._config).window_hours(
            context.janela, context.janela_horas
        )
        try:
            resultado = self._collector.collect(
                context.user_id,
                buscas,
                frentes=frentes_de_busca(
                    self._config, perfil.get("localizacao") or ""
                ),
                janela_horas=janela,
                progresso=self._passo(context),
            )
        except CollectionCancelled as exc:
            raise Cancelled("coleta cancelada pelo usuario") from exc
        self._coleta_remota(context, resultado)
        self._registra(context, resultado)

    # ------------------------------------------------------------ interno
    def _coleta_remota(self, context: RunContext, resultado) -> None:
        """Consulta as fontes de vaga remota e junta o que veio ao run.

        Depois da busca no LinkedIn, e nao no meio dela: sao outros servidores,
        fora da contencao do governador, e uma chamada por fonte -- nao uma por
        termo. A descricao ja vem junto, e e gravada aqui, no registro
        compartilhado: o enriquecedor a encontra la e nao faz requisicao.

        Fonte que falha vira contribuicao vazia e uma linha no log. Ela e
        acessoria ao run, e nunca o derruba.
        """
        if not self._remotas:
            return
        from .sources.remotas import CadenciaRemota

        cadencia = CadenciaRemota(
            self._connection, self._config.collection.intervalo_fontes_remotas_h
        )
        for fonte in self._remotas:
            coleta = cadencia.coletar(fonte)
            self._grava_descricoes(coleta.descricoes)
            self._collector.incorporar(context.user_id, coleta.como_busca(), resultado)
            resultado.por_frente[coleta.fonte] = len(coleta.cards)

    def _grava_descricoes(self, descricoes: dict[str, str]) -> None:
        """Grava no registro compartilhado a descricao que veio com a vaga.

        `INSERT OR IGNORE`: descricao ja guardada nao e substituida, pelo mesmo
        motivo que o enriquecedor nao a substitui -- o registro e de todos.
        """
        agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for job_id, texto in descricoes.items():
            if texto:
                self._connection.execute(
                    "INSERT OR IGNORE INTO job_descriptions (job_id, texto, coletada_em) "
                    "VALUES (?, ?, ?)",
                    (job_id, texto, agora),
                )

    def _passo(self, context: RunContext):
        """Fecha sobre o run para publicar avanco e atender o cancelamento.

        A coleta e o trecho mais longo antes do enriquecimento, e ate aqui ela
        nao escrevia nada no banco antes de acabar: o sinal de vida ficava
        parado por vinte minutos e a tela mostrava zero o tempo todo, o que de
        fora e indistinguivel de um processo morto.
        """
        def passo(resultado, feitas: int, total: int) -> None:
            self._registra(context, resultado)
            self._fila.record_counters(context.run_id, **context.contadores)
            self._fila.heartbeat(context.run_id)
            # Dois sinais de vida distintos: um diz que este run anda, o outro
            # que o processo que o executa esta de pe. A coleta e o unico
            # estagio longo o bastante para a trava de instancia vencer no meio.
            context.keepalive()
            logger.info(
                "coleta do run %s: busca %s de %s, %s vagas ate aqui",
                context.run_id, feitas, total, len(resultado.cards),
            )
            if self._fila.cancel_requested(context.run_id):
                raise CollectionCancelled(context.run_id)

        return passo

    def _da_navegacao(self, context: RunContext, ja_vistos: set) -> list:
        """Vagas que entraram pela extensao e ainda nao foram pontuadas.

        Sem isto, a varredura do navegador seria um beco sem saida: ela grava a
        vaga e o sinal do Premium, e nenhum run olha para aquela linha -- o
        relatorio e por run, e o que nunca entrou num run nao tem nota e nao
        aparece em lugar nenhum.

        Elas entram como card da coleta e seguem o mesmo caminho: pre-filtro,
        enriquecimento, score. O que as distingue e a origem gravada em `busca`,
        e ela nao se perde -- o relatorio de rendimento das buscas mostra
        `extensao` como uma origem ao lado dos termos do planejador.

        `ausencias_elegiveis` nao entra no criterio: a vaga vem da navegacao e
        nao de uma busca que possa deixar de traze-la.
        """
        linhas = self._connection.execute(
            "SELECT job_id, titulo, empresa, url, local, modelo_trabalho, "
            "publicada_em, busca FROM jobs WHERE user_id = ? AND busca = ? "
            "AND job_id NOT IN (SELECT job_id FROM scores WHERE user_id = ?)",
            (context.user_id, InsightStore.ORIGEM, context.user_id),
        ).fetchall()
        return [
            Card(
                job_id=linha["job_id"], titulo=linha["titulo"] or "",
                empresa=linha["empresa"], url=linha["url"] or "",
                local=linha["local"], modelo_trabalho=linha["modelo_trabalho"],
                publicada_em=linha["publicada_em"], busca=linha["busca"] or "",
            )
            for linha in linhas
            if linha["job_id"] not in ja_vistos
        ]

    def _registra(self, context: RunContext, resultado) -> None:
        cards = list(resultado.cards)
        cards.extend(
            self._da_navegacao(context, {c.job_id for c in cards})
        )
        context.dados["cards"] = cards
        context.contadores["n_brutos"] = len(cards)
        context.contadores["n_novos"] = len(resultado.novos)
        # Bloqueio contado e bloqueio visivel. A biblioteca de coleta engole o
        # 429 e devolve lista vazia, entao sem este contador o relatorio dizia
        # que o mercado estava vazio quando o que houve foi um portao fechado.
        context.contadores["bloqueios"] = len(resultado.bloqueadas)
        context.contadores["coleta"] = resumo_da_coleta(resultado)


class PreFiltroStage:
    """Descarta o que nao merece uma requisicao de descricao."""

    name = "prefiltro"

    def __init__(self, connection, config, filters: Filters | None = None) -> None:
        self._connection = connection
        self._prefilter = Prefilter(
            filters if filters is not None else load_filters(), config
        )

    def run(self, context: RunContext) -> None:
        perfil = context.dados["perfil"]
        resultado = self._prefilter.evaluate(
            context.dados["cards"], perfil.get("localizacao")
        )
        context.dados["sobreviventes"] = resultado.mantidos
        # Sobreviver ao pre-filtro e merecer uma requisicao de descricao sao
        # duas coisas. Ver `PrefilterResult.a_enriquecer`.
        context.dados["a_enriquecer"] = resultado.a_enriquecer
        context.contadores["n_filtrados"] = len(resultado.mantidos)

        # O descarte e gravado com o motivo. Um relatorio que so mostrasse os
        # sobreviventes deixaria o usuario sem como discordar do filtro.
        for descarte in resultado.descartados:
            context.scope.insert(
                "discards",
                {
                    "run_id": context.run_id,
                    "job_id": descarte.job_id,
                    "titulo": descarte.titulo,
                    "empresa": descarte.empresa,
                    "motivo": descarte.motivo,
                },
            )
        # O blocker geografico fica na vaga, e nao no descarte: ele penaliza o
        # score de quem sobreviveu, e nao so explica quem caiu.
        for job_id, blocker in resultado.blockers.items():
            self._connection.execute(
                "UPDATE jobs SET blocker = ? WHERE job_id = ? AND user_id = ?",
                (blocker, job_id, context.user_id),
            )


class RunSemBuscas(RuntimeError):
    """Retomada sem termo de busca em lugar nenhum."""


def coleta_anterior(connection, user_id: str, run_id: str) -> dict | None:
    """O resumo da coleta do run anterior deste usuario, se houver."""
    linha = connection.execute(
        "SELECT coleta FROM runs WHERE user_id = ? AND run_id <> ? "
        "AND coleta IS NOT NULL ORDER BY solicitado_em DESC LIMIT 1",
        (user_id, run_id),
    ).fetchone()
    if linha is None:
        return None
    try:
        return json.loads(linha[0])
    except ValueError:
        return None


def priorizar_pendentes(buscas: list[str], anterior: dict | None) -> list[str]:
    """Poe na frente os termos que a coleta anterior nao conseguiu fazer.

    Quando o LinkedIn barra a coleta, ela para ali para nao prolongar o
    bloqueio, e os termos restantes ficam sem resposta. Em 21/10/2026 isso
    acontecia no quarto minuto, e os termos do fim da lista nunca rodavam --
    sempre os mesmos, porque o planejamento os ordena igual a cada run.

    Retomar dentro do mesmo run nao resolve: o bloqueio ainda valia dezessete
    minutos depois. O que resolve e o revezamento. O termo barrado e os que nao
    foram tentados vao para o comeco da busca seguinte, na ordem que ja tinham;
    os demais seguem atras. Nenhum termo entra ou sai, so a ordem muda.
    """
    if not anterior or not anterior.get("barrada_em"):
        return list(buscas)
    pendentes = {anterior["barrada_em"], *(anterior.get("nao_tentadas") or [])}
    primeiro = [b for b in buscas if b in pendentes]
    if primeiro:
        logger.info(
            "coleta anterior barrada: %s termo(s) pendente(s) vao na frente",
            len(primeiro),
        )
    return primeiro + [b for b in buscas if b not in pendentes]


def resumo_da_coleta(resultado) -> dict:
    """O que a coleta fez, no formato que o run grava e o relatorio le.

    `por_fonte` junta as frentes do LinkedIn e as fontes remotas, porque para
    quem le o relatorio as duas respondem a mesma pergunta: de onde veio cada
    vaga.
    """
    bloqueadas = list(getattr(resultado, "bloqueadas", None) or [])
    return {
        "previstas": getattr(resultado, "previstas", 0),
        "tentadas": getattr(resultado, "tentadas", 0),
        "barrada_em": bloqueadas[0] if bloqueadas else None,
        "nao_tentadas": list(getattr(resultado, "nao_tentadas", None) or []),
        "falhas": dict(getattr(resultado, "falhas", None) or {}),
        "por_fonte": dict(getattr(resultado, "por_frente", None) or {}),
    }


def _fontes_remotas(config) -> list:
    """As fontes remotas da configuracao, prontas -- ou a recusa nomeando a errada.

    Nome desconhecido impede a partida do processo, e nao vira fonte ignorada:
    uma fonte que some em silencio e o mesmo defeito das frentes remotas, que
    pareceram funcionar por quatro dias sem funcionar.
    """
    from ..config import ConfigError
    from .sources.remotas import FONTES

    nomes = getattr(getattr(config, "collection", None), "fontes_remotas", ()) or ()
    desconhecidas = [n for n in nomes if n not in FONTES]
    if desconhecidas:
        raise ConfigError(
            f"collection.remotas.fontes: fonte desconhecida {desconhecidas}; "
            f"esperado um de {sorted(FONTES)}"
        )
    return [FONTES[nome]() for nome in nomes]


def frentes_de_busca(config, localizacao_do_perfil: str):
    """As frentes do run, com a localizacao do perfil ja resolvida.

    Frente com `local` vazio significa "onde a pessoa mora", e e aqui que isso
    deixa de ser um buraco na configuracao e passa a ser um endereco. Guardar a
    cidade no arquivo de configuracao seria pior: ela pertence a cada usuario, e
    o arquivo e do operador.

    Configuracao sem nenhuma frente declarada devolve a frente unica de antes --
    a do perfil, sem exigir remoto. E o comportamento anterior a este campo, e
    um arquivo escrito antes dele continua valendo.
    """
    from ..config import Frente

    declaradas = getattr(config.collection, "frentes", ()) or ()
    remotas = [f.rotulo for f in declaradas if f.remoto]
    if remotas:
        # A busca sem login do LinkedIn descarta o filtro de remoto. Tres
        # frentes foram declaradas assim e trouxeram vaga presencial no
        # exterior por quatro dias, sem nenhum sinal -- gastando a maior parte
        # das buscas e das descricoes do run. O aviso existe para que a mesma
        # configuracao nao volte a parecer que funciona.
        logger.warning(
            "frente(s) %s pedem vaga remota, mas a busca sem login do LinkedIn "
            "ignora esse filtro: elas vao trazer vaga presencial naquela "
            "localizacao", ", ".join(remotas),
        )
    if not declaradas:
        return (Frente(rotulo="aqui", local=localizacao_do_perfil, remoto=False),)
    return tuple(
        Frente(
            rotulo=f.rotulo,
            local=f.local or localizacao_do_perfil,
            remoto=f.remoto,
        )
        for f in declaradas
    )


def carregar_buscas(connection, context: RunContext) -> list[str]:
    """Os termos deste run: da memoria se houver, do banco quando nao houver.

    Mesma razao de `carregar_perfil`, e o mesmo defeito que ele ja tinha
    corrigido para o perfil. `RunContext.dados` nao atravessa nem a suspensao
    do enriquecimento nem a retomada de um run devolvido a fila por morte do
    processo -- e `context.dados["buscas"]` levantava `KeyError` ali. A excecao
    subia ate o laco do executor e o matava.

    Os termos vem da propria linha do run, gravada pelo planejamento, porque e
    o que aquele run de fato procurou. Replanejar aqui daria a lista de hoje
    para um run de ontem, e o relatorio passaria a citar busca que nunca
    aconteceu.
    """
    do_contexto = context.dados.get("buscas")
    if do_contexto:
        return [q.texto for q in do_contexto]
    linha = RunQueue(connection).get(context.run_id)
    gravadas = json.loads(linha["buscas"] or "[]") if linha else []
    return [str(termo) for termo in gravadas if str(termo).strip()]


def carregar_perfil(connection, config, context: RunContext) -> dict:
    """O perfil do run, da memoria se houver e do banco quando nao houver.

    `RunContext.dados` vive so enquanto o worker segura o run. Depois de uma
    suspensao o trecho seguinte roda com o contexto vazio, e os estagios ja
    concluidos nao rodam de novo para repovoa-lo. Quem precisa de dado do
    primeiro trecho le do banco, que e o unico estado que atravessa a espera.
    """
    if context.dados.get("perfil"):
        return context.dados["perfil"]
    versao = ProfileMerger(connection, config).current(context.user_id)
    if versao is None:
        raise RuntimeError(
            f"run {context.run_id} retomado sem perfil consolidado para "
            f"{context.user_id}"
        )
    perfil = {**versao.campos, "nivel_inferido": versao.nivel_inferido}
    context.dados["perfil"] = perfil
    return perfil


class _PontuacaoStage:
    """Base das duas passadas: elas diferem no que leem, nao em como pontuam."""

    passada = PROVISORIA

    def __init__(self, connection, config, ontology: Ontology | None = None) -> None:
        self._connection = connection
        self._config = config
        self._ontology = ontology if ontology is not None else load_ontology()
        self._scorer = Scorer(config, self._ontology)

    def _alvos(self, context: RunContext) -> list[dict]:
        """As vagas a pontuar, no formato que o scorer entende."""
        if self.passada == PROVISORIA:
            return [
                {
                    "job_id": card.job_id, "titulo": card.titulo,
                    "empresa": card.empresa, "remoto": card.remoto,
                    "flags": list(card.flags or ()), "descricao": None,
                    "blocker": None, "local": card.local,
                }
                for card in context.dados.get("sobreviventes") or []
            ]
        # Na passada final os sobreviventes sao justamente quem recebeu score
        # provisorio: a lista sobrevive a suspensao porque esta gravada.
        linhas = self._connection.execute(
            """
            SELECT j.job_id, j.titulo, j.empresa, j.modelo_trabalho, j.flags,
                   j.blocker, j.local, d.texto AS descricao
            FROM scores s
            JOIN jobs j ON j.job_id = s.job_id AND j.user_id = s.user_id
            LEFT JOIN job_descriptions d ON d.job_id = s.job_id
            WHERE s.user_id = ? AND s.run_id = ? AND s.passada = ?
            ORDER BY s.score DESC, j.job_id
            """,
            (context.user_id, context.run_id, PROVISORIA),
        ).fetchall()
        return [
            {
                "job_id": linha["job_id"], "titulo": linha["titulo"],
                "empresa": linha["empresa"],
                # A constante, e nao a palavra escrita a mao. Aqui estava
                # `== "remoto"`, em portugues, enquanto a origem grava `remote`
                # -- a comparacao era sempre falsa e toda vaga remota entrava na
                # passada final como presencial. Uma vaga remota longe levava a
                # penalidade de distancia que existe justamente para quem
                # precisa se deslocar, e a passada provisoria, que usa
                # `card.remoto`, discordava da final sobre a mesma vaga.
                "remoto": linha["modelo_trabalho"] == REMOTO,
                "flags": json.loads(linha["flags"] or "[]"),
                "descricao": linha["descricao"], "blocker": linha["blocker"],
                "local": linha["local"],
            }
            for linha in linhas
        ]

    def _corrigir_presenca(self, context: RunContext, perfil: dict, alvo: dict) -> None:
        """Vaga anunciada como remota que a descricao revela hibrida.

        A origem responde remoto ou presencial, e nada entre os dois. O
        pre-filtro decide geografia com essa resposta, antes de existir
        descricao, e vaga remota nunca e avaliada geograficamente -- entao o
        deslocamento de uma vaga que pede tres dias no escritorio sumia do
        sistema inteiro.

        Aqui e o primeiro momento em que da para saber: a descricao ja chegou e
        o candidato ja disse quantos dias aceita. Quando a vaga exige mais que
        isso, ela deixa de contar como remota e passa pelo mesmo calculo de
        distancia de qualquer presencial.
        """
        if _sem_presenca_avaliada(perfil) or not alvo["remoto"]:
            return

        from . import presenca as presenca_module
        from .prefilter import blocker_geografico, find_city

        exigencia = presenca_module.ler(alvo["descricao"])
        limite = int(perfil["dias_escritorio_max"])
        # Sem numero na descricao nao ha o que comparar: "hibrido" sozinho nao
        # diz se sao cinco dias ou um, e supor seria inventar a evidencia.
        if exigencia.dias is None or exigencia.dias <= limite:
            return

        cidades = _tabela_de_cidades()
        encontrada = find_city(perfil.get("localizacao"), cidades)
        avisos = [
            parte.strip()
            for parte in str(alvo["blocker"] or "").split(";")
            if parte.strip()
        ]
        avisos.append(
            f"{presenca_module.PREFIXO_PRESENCA} {exigencia.dias} dias de "
            f"escritorio por semana, acima dos {limite} que voce aceita"
        )
        geografico = blocker_geografico(
            alvo.get("local"),
            encontrada[1] if encontrada else None,
            cidades,
            self._config.profile.raio_deslocamento_km,
        )
        if geografico:
            avisos.append(geografico)

        alvo["remoto"] = False
        alvo["blocker"] = "; ".join(avisos)
        # Gravado na vaga, e nao apenas no score: o relatorio le o blocker de
        # `jobs`, e quem abrir o run seguinte precisa ver o mesmo aviso.
        context.scope.update(
            "jobs",
            {"blocker": alvo["blocker"]},
            where="job_id = ?",
            params=(alvo["job_id"],),
        )

    def run(self, context: RunContext) -> None:
        perfil = carregar_perfil(self._connection, self._config, context)
        for alvo in self._alvos(context):
            descricao = alvo["descricao"]
            if self.passada == FINAL:
                self._corrigir_presenca(context, perfil, alvo)
            resultado = self._scorer.score(
                perfil,
                {
                    "titulo": alvo["titulo"], "empresa": alvo["empresa"],
                    "setor": None, "liderados": None,
                    "remoto": alvo["remoto"], "blocker": alvo["blocker"],
                },
                descricao=descricao,
                sinais=alvo["flags"],
                passada=self.passada,
            )
            # Declaradas mais as que o historico evidencia. So a lista declarada
            # fazia a lacuna mandar acrescentar ao perfil o que o curriculo ja
            # afirma -- o defeito que `skills_from_profile` corrigia e que
            # nenhum estagio chamava.
            from ..scoring.ontology import skills_from_profile

            diferenca = gaps.from_description(
                self._ontology, skills_from_profile(self._ontology, perfil),
                descricao or alvo["titulo"],
            )
            context.scope.insert(
                "scores",
                {
                    "job_id": alvo["job_id"],
                    "run_id": context.run_id,
                    "passada": self.passada,
                    "score": resultado.score,
                    "componentes": json.dumps(
                        {
                            **resultado.componentes,
                            "ajustes": resultado.ajustes,
                            # Os tetos viajam com o score porque o relatorio
                            # precisa dizer por que a nota nao e mais alta, e
                            # recalcula-los exigiria a descricao de volta.
                            "tetos": resultado.tetos,
                            "teto_aplicado": resultado.teto_aplicado,
                        },
                        ensure_ascii=False,
                    ),
                    "lacunas": json.dumps(list(diferenca.lacunas), ensure_ascii=False),
                    "diferenciais": json.dumps(
                        list(diferenca.diferenciais), ensure_ascii=False
                    ),
                    "descricao_disponivel": int(resultado.descricao_disponivel),
                    "sinais_sessao_disponiveis": int(resultado.sinais_disponiveis),
                    "criado_em": _agora(),
                },
            )


def _sem_presenca_avaliada(perfil: dict) -> bool:
    """O candidato nao declarou quantos dias de escritorio aceita.

    Campo vazio desliga a regra inteira. Nao saber quantos dias alguem aceita
    nao autoriza supor que aceita zero -- e a mesma linha de §1.3, onde um campo
    nunca preenchido estava derrubando metade do relatorio.
    """
    return perfil.get("dias_escritorio_max") is None


#: Tabela de cidades do processo. Sao dezenas de vagas por run, e reler o
#: arquivo em cada uma seria trabalho repetido para um dado que nao muda.
_CIDADES: dict | None = None


def _tabela_de_cidades() -> dict:
    global _CIDADES
    if _CIDADES is None:
        from .prefilter import load_cities

        _CIDADES = load_cities()
    return _CIDADES


class PontuacaoProvisoriaStage(_PontuacaoStage):
    """Ordena a fila do enriquecimento com o que a busca ja entregou."""

    name = "pontuacao_provisoria"
    passada = PROVISORIA


class PontuacaoFinalStage(_PontuacaoStage):
    """Refaz a conta com as descricoes que o enriquecedor trouxe."""

    name = "pontuacao_final"
    passada = FINAL


#: Sinal que adianta uma vaga na fila do enriquecimento. So o LinkedIn logado
#: calcula este, e ele e o unico que fala do candidato em vez da vaga.
SINAL_PRIORITARIO = "top_applicant"


class EnriquecimentoStage:
    """Grava os pedidos de descricao e solta o worker ate serem atendidos."""

    name = "enriquecimento"

    def __init__(self, connection, config=None) -> None:
        self._connection = connection
        self._queue = EnrichmentQueue(connection)
        self._fracao = getattr(
            getattr(config, "collection", None), "fracao_enriquecida", 1.0
        ) or 1.0

    def run(self, context: RunContext) -> None:
        # `a_enriquecer` e um subconjunto de `sobreviventes`: o que fica de
        # fora segue no relatorio, com o blocker e o teto que o pre-filtro lhe
        # deu, mas sem gastar o trecho mais caro do run. O `or` cobre o
        # contexto de um run gravado antes desta chave existir.
        alvos = context.dados.get("a_enriquecer")
        if alvos is None:
            alvos = context.dados.get("sobreviventes") or []
        alvos = self._por_prioridade(context.user_id, alvos)
        alvos = self._dentro_da_fracao(context.user_id, context.run_id, alvos)
        self._queue.enqueue(context.user_id, context.run_id, alvos)
        if not self._queue.is_drained(context.run_id):
            # Suspensao, e nao espera. O intervalo entre requisicoes de
            # descricao e o trecho mais longo do run inteiro.
            raise Suspend(
                f"run {context.run_id} aguarda o enriquecimento de "
                f"{len(alvos)} vagas"
            )

    def _por_prioridade(self, user_id: str, alvos: list) -> list:
        """Quem o LinkedIn ja apontou como melhor candidato vai na frente.

        A ordem da fila nao seria decisao nenhuma se todo pedido fosse
        atendido. Ela decide porque o orcamento diario acaba: as vagas que
        sobrarem depois do teto ficam sem descricao, e sem descricao nao ha
        teto de requisito, nao ha competencia extraida e o score cai para o que
        o titulo sozinho diz.

        Ordenacao estavel: dentro de cada grupo a ordem da coleta permanece, e
        um run sem nenhum sinal gravado sai exatamente como entrava antes.
        """
        if len(alvos) < 2:
            return alvos
        marcadas = self._marcadas(user_id)
        if not marcadas:
            return alvos
        return sorted(alvos, key=lambda card: card.job_id not in marcadas)

    def _marcadas(self, user_id: str) -> set[str]:
        """Vagas deste usuario que o LinkedIn apontou como melhor candidato."""
        return {
            linha["job_id"]
            for linha in self._connection.execute(
                "SELECT job_id FROM jobs WHERE user_id = ? AND flags LIKE ?",
                (user_id, f'%"{SINAL_PRIORITARIO}"%'),
            ).fetchall()
        }

    def _dentro_da_fracao(self, user_id: str, run_id: str, alvos: list) -> list:
        """Corta a fila de descricao pela ordem da nota provisoria.

        A descricao e o trecho mais longo do run: uma requisicao a cada 8 a 20
        segundos de governador. Num run de 1 dia com quatro frentes, foram 380
        pedidos enfileirados -- cerca de 76 minutos so de espera deliberada,
        quase todo o tempo do run.

        O corte e pela ORDEM provisoria, e nao por um limiar de nota. A nota
        provisoria e comprimida -- num run real, 341 de 448 sobreviventes
        ficaram acima de 60 --, entao um limiar nao separa nada. A ordem
        separa: no historico de 3201 vagas com descricao lida, os 70% do topo
        da ordem provisoria de cada run continham 95,6% das que terminaram em
        destaque; os 60% do topo, 92%.

        Tres coisas passam por cima do corte:
        - os marcados pelo sinal Premium, que `_por_prioridade` ja pos na
          frente e que portanto caem dentro da fatia;
        - a vaga cuja descricao ja esta no registro compartilhado, porque
          ela nao custa requisicao -- o enriquecedor a reaproveita;
        - a fracao 1.0, que e o comportamento anterior a esta regra.

        O que fica de fora nao some: segue no relatorio com a nota provisoria
        e a marca de "avaliada sem leitura da descricao", e pode ser descrita
        num run seguinte em que suba na ordem.
        """
        if self._fracao >= 1.0 or len(alvos) < 2:
            return alvos

        notas = {
            linha["job_id"]: linha["score"]
            for linha in self._connection.execute(
                "SELECT job_id, score FROM scores WHERE run_id = ? AND passada = ?",
                (run_id, PROVISORIA),
            )
        }
        ja_descritas = self._ja_descritas([c.job_id for c in alvos])
        # Lidas uma vez, e nao dentro da chave de ordenacao: consultar o banco
        # por vaga dentro do `sorted` seria uma lentidao nova escondida na
        # correcao de lentidao.
        marcadas = self._marcadas(user_id)
        indice = {card.job_id: i for i, card in enumerate(alvos)}
        pagas = sorted(
            (c for c in alvos if c.job_id not in ja_descritas),
            # Sinal Premium na frente, depois a nota provisoria, e a ordem de
            # coleta como desempate -- para o corte ser o mesmo a cada leitura.
            key=lambda c: (c.job_id not in marcadas,
                           -notas.get(c.job_id, 0), indice[c.job_id]),
        )
        cabem = max(1, math.ceil(len(pagas) * self._fracao))
        escolhidas = {c.job_id for c in pagas[:cabem]}
        cortadas = len(pagas) - cabem
        if cortadas:
            logger.info(
                "run %s: %s descricao(oes) fora do corte de %.0f%% pela ordem "
                "provisoria; %s reaproveitada(s) do registro",
                run_id[:8], cortadas, 100 * self._fracao, len(ja_descritas),
            )
        return [
            c for c in alvos
            if c.job_id in escolhidas or c.job_id in ja_descritas
        ]

    def _ja_descritas(self, ids: list[str]) -> set[str]:
        """Vagas cuja descricao ja esta no registro compartilhado."""
        achadas: set[str] = set()
        # Em lotes: o SQLite antigo limita a 999 parametros por consulta, e um
        # run de quatro frentes ja passou de 400 alvos.
        for inicio in range(0, len(ids), 500):
            lote = ids[inicio:inicio + 500]
            achadas.update(
                linha[0] for linha in self._connection.execute(
                    "SELECT job_id FROM job_descriptions WHERE job_id IN "
                    f"({', '.join('?' * len(lote))})",
                    lote,
                )
            )
        return achadas


class JulgamentoStage:
    """Segunda leitura do topo por modelo de linguagem.

    Roda depois da pontuacao final e antes da sintese: a sintese comenta a
    lista, entao precisa comentar a lista ja reordenada.

    Nao substitui o score. As notas do modelo ficam em colunas proprias, e a
    ordem deterministica continua gravada e reprodutivel -- e ela que responde
    quando nao ha credencial, e e ela que permite comparar dois runs.
    """

    name = "julgamento"

    def __init__(self, connection, config, model_client_factory) -> None:
        self._connection = connection
        self._config = config
        self._factory = model_client_factory

    def run(self, context: RunContext) -> None:
        from ..report.renderer import ReportRenderer
        from ..scoring.judge import Judge

        cliente = self._factory(context.user_id) if self._factory else None
        # As vagas vem do relatorio, e nao de consulta propria: e exatamente a
        # lista que o usuario vai ler, na ordem em que ele vai le-la.
        contexto = ReportRenderer(self._connection, self._config).build_context(
            context.user_id, context.run_id
        )
        resultado = Judge(cliente, self._config).judge(
            context.user_id,
            carregar_perfil(self._connection, self._config, context),
            contexto.vagas,
        )
        if resultado.falha:
            logger.info(
                "run %s sem releitura do modelo: %s", context.run_id, resultado.falha
            )
            return
        for job_id, nota in resultado.notas.items():
            context.scope.update(
                "scores",
                {
                    "nota_do_modelo": nota,
                    "motivo_do_modelo": resultado.motivos.get(job_id),
                },
                where="job_id = ? AND run_id = ? AND passada = ?",
                params=(job_id, context.run_id, FINAL),
            )
        logger.info(
            "run %s: modelo releu %s vagas (%s)",
            context.run_id, len(resultado.notas), resultado.modelo,
        )


class SinteseStage:
    """Escreve a leitura do run, quando ha modelo disponivel."""

    name = "sintese"

    def __init__(self, connection, config, model_client_factory) -> None:
        self._connection = connection
        self._config = config
        self._factory = model_client_factory

    def run(self, context: RunContext) -> None:
        from ..report.renderer import ReportRenderer
        from ..synthesis.synthesizer import Synthesizer

        cliente = self._factory(context.user_id) if self._factory else None
        # As vagas ja pontuadas sao montadas pelo relatorio, e nao por uma
        # consulta paralela aqui. Duas montagens divergiriam com o tempo, e a
        # sintese passaria a comentar um conjunto diferente do que o usuario le.
        contexto = ReportRenderer(self._connection, self._config).build_context(
            context.user_id, context.run_id
        )
        try:
            resultado = Synthesizer(
                self._connection, cliente, self._config
            ).synthesize(
                context.user_id,
                context.run_id,
                perfil=carregar_perfil(self._connection, self._config, context),
                vagas=contexto.vagas,
                descartadas=contexto.descartadas,
                contexto={"contagens": contexto.contagens},
            )
        except Exception as exc:  # a sintese e opcional; o run nao morre por ela
            logger.warning("sintese falhou no run %s: %s", context.run_id, exc)
            context.contadores["falha_sintese"] = f"{type(exc).__name__}: {exc}"
            return
        if resultado is not None and getattr(resultado, "falha", None):
            context.contadores["falha_sintese"] = str(resultado.falha)


class ReconferenciaStage:
    """Confere de novo se as vagas do topo ainda aceitam candidaturas.

    O enriquecedor ve a marca de vaga fechada quando le a descricao -- mas a
    descricao e lida uma vez so e reaproveitada. Vaga lida aberta e fechada
    depois seguia no relatorio: em 21/10/2026, "SRE Tech Lead Manager" ficou
    entre as melhores com o LinkedIn dizendo "No longer accepting
    applications".

    Confere so o topo, e so a vaga cuja descricao tem mais de um dia: a lida
    neste run ja passou pela verificacao. Tipicamente sao poucas requisicoes,
    sob o mesmo governador da coleta. Bloqueio ou erro numa vaga nao derruba o
    run: a vaga so fica sem reconferir.
    """

    name = "reconferencia"

    #: Quantas vagas do topo conferir, e a idade minima da descricao para isso.
    LIMITE = 20
    IDADE_MINIMA = timedelta(days=1)

    def __init__(self, connection, source, governor, agora=None) -> None:
        self._connection = connection
        self._source = source
        self._governor = governor
        self._agora = agora or (lambda: datetime.now(timezone.utc))

    def run(self, context: RunContext) -> None:
        from .enricher import expirar_encerrada
        from .governor import CollectionBlocked
        from .sources.guest import CollectionError

        limite = (self._agora() - self.IDADE_MINIMA).isoformat(timespec="seconds")
        topo = self._connection.execute(
            """
            SELECT j.job_id, j.url, d.coletada_em
            FROM scores s
            JOIN jobs j ON j.job_id = s.job_id AND j.user_id = s.user_id
            LEFT JOIN job_descriptions d ON d.job_id = s.job_id
            WHERE s.user_id = ? AND s.run_id = ? AND s.passada = 'final'
              AND j.estado IN ('novo', 'visto') AND j.job_id LIKE 'li-%'
            ORDER BY s.score DESC, j.job_id
            LIMIT ?
            """,
            (context.user_id, context.run_id, self.LIMITE),
        ).fetchall()
        conferidas = encerradas = 0
        for job_id, url, coletada_em in topo:
            if coletada_em and coletada_em >= limite:
                continue
            try:
                dados = self._governor.run(
                    lambda j=job_id, u=url: self._source.describe(j, u)
                )
            except CollectionBlocked:
                logger.warning("reconferencia interrompida por bloqueio de coleta")
                break
            except CollectionError as exc:
                logger.info("vaga %s sem reconferir: %s", job_id, exc)
                continue
            conferidas += 1
            if dados.get("encerrada") and expirar_encerrada(
                Repository(self._connection), job_id
            ):
                encerradas += 1
        if conferidas:
            logger.info(
                "reconferencia: %s vaga(s) do topo conferida(s), %s encerrada(s)",
                conferidas, encerradas,
            )


def build_stages(
    connection, config, source, model_client_factory=None, governor=None
) -> list:
    """A ordem do run. Um lugar so, para que ela seja legivel de uma vez.

    `governor` e injetavel para que o teste possa espacar sem esperar de
    verdade: com o intervalo real, uma dezena de buscas simuladas levaria
    minutos de relogio de parede e a suite deixaria de ser executavel.
    """
    filters = load_filters()
    ontology = load_ontology()
    # Um governador so para a coleta e a reconferencia: as duas falam com a
    # mesma origem, e o espacamento vale para o conjunto.
    governor = governor if governor is not None else RateGovernor(connection, config)
    return [
        PerfilStage(connection, config),
        PlanejamentoStage(connection, filters, config),
        ColetaStage(connection, source, config, governor),
        PreFiltroStage(connection, config, filters),
        PontuacaoProvisoriaStage(connection, config, ontology),
        EnriquecimentoStage(connection, config),
        PontuacaoFinalStage(connection, config, ontology),
        ReconferenciaStage(connection, source, governor),
        JulgamentoStage(connection, config, model_client_factory),
        SinteseStage(connection, config, model_client_factory),
    ]
