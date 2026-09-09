"""Cobertura do encaixe entre os estagios do run.

Os modulos que compoem o pipeline ja tem teste proprio. O que se verifica aqui e
a montagem: que a saida de um estagio chega ao seguinte no formato esperado, que
a suspensao acontece no ponto certo e -- sobretudo -- que o segundo trecho
sobrevive a perda do contexto em memoria. Foi exatamente ali que a primeira
versao quebrou, e um teste de peca isolada nunca teria mostrado isso.
"""

from datetime import datetime, timezone

import pytest

from crivo.config import load_config
from crivo.pipeline.governor import RateGovernor
from crivo.pipeline.sources.guest import PRESENCIAL, REMOTO, Card, SearchOutcome
from crivo.pipeline.stages import (
    ColetaStage,
    EnriquecimentoStage,
    PerfilStage,
    PlanejamentoStage,
    PontuacaoFinalStage,
    PontuacaoProvisoriaStage,
    PreFiltroStage,
    build_stages,
)
from crivo.profile.merger import HistoryRequired, ProfileMerger
from crivo.store.migrations import open_database
from crivo.store.repository import Repository
from crivo.worker.enrichment_queue import EnrichmentQueue
from crivo.worker.queue import CONCLUIDO, RunQueue
from crivo.worker.runner import RunContext, Runner

USUARIO = "ana"


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FonteFalsa:
    """Fonte de vagas que nao toca a rede.

    Devolve um titulo aderente, um ruido semantico e um terceiro aderente, para
    que o pre-filtro tenha o que descartar e o pontuador tenha o que ordenar.
    """

    def __init__(self) -> None:
        self.buscas: list[str] = []

    def search(self, termo, local, janela_horas, quantidade):
        self.buscas.append(termo)
        indice = len(self.buscas)
        titulos = [
            "Engenheiro de Software Senior",
            "Tecnico de Manutencao Predial",
            "Site Reliability Engineer",
        ]
        return SearchOutcome(
            busca=termo,
            cards=[
                Card(
                    job_id=f"vaga-{indice}-{i}",
                    titulo=titulo,
                    empresa=f"Empresa {i}",
                    url=f"https://exemplo.test/{indice}/{i}",
                    local="Sao Paulo, SP",
                    # As constantes, e nao as palavras a mao. Escritas em
                    # portugues, elas nunca casavam o que a origem grava, e o
                    # dublê validava um contrato que a producao nao cumpre.
                    #
                    # A remota e a que sobrevive ao pre-filtro. Antes era o
                    # ruido semantico, entao nenhuma vaga remota chegava a
                    # passada final e o defeito de comparacao ficava invisivel.
                    modelo_trabalho=(
                        REMOTO if titulo.startswith("Site Reliability")
                        else PRESENCIAL
                    ),
                    publicada_em="2026-08-20",
                    flags=("top_applicant",) if i == 0 else (),
                    busca=termo,
                )
                for i, titulo in enumerate(titulos)
            ],
        )

    def describe(self, job_id, url):
        return {"texto": "Buscamos Python e AWS.", "candidatos": 7}


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    Repository(connection).insert(
        "users",
        {"user_id": USUARIO, "subject_google": "sub-ana", "criado_em": _agora()},
    )
    yield connection, RunQueue(connection), load_config()
    connection.close()


def consolidar(connection, config, **sobrescreve):
    campos = {
        "nome": "Ana",
        "headline": "Engenheira de Software Senior",
        "localizacao": "Sao Paulo, SP",
        "experiencias": [
            {
                "titulo": "Engenheira de Software",
                "empresa": "Acme",
                "inicio": "2015-01",
                "fim": None,
                "descricao": "Python",
            }
        ],
        "competencias": ["Python", "AWS"],
        "formacao": ["CC"],
        "idiomas": ["Portugues"],
    }
    campos.update(sobrescreve)
    return ProfileMerger(connection, config).consolidate(
        USUARIO, resume_fields=campos
    )


def montar(connection, config, fonte=None):
    # Governador com espera fingida: a contencao entre buscas e de 8 a 20s em
    # producao, e esperar de verdade faria esta suite levar minutos por teste.
    # O que se verifica aqui e a ordem dos estagios, nao a cadencia -- essa tem
    # os proprios testes em test_governor.py.
    governador = RateGovernor(connection, config, sleep=lambda _s: None)
    return build_stages(
        connection, config, fonte or FonteFalsa(), None, governor=governador
    )


# ------------------------------------------------------------------- ordem
def test_the_declared_order_matches_what_a_run_needs(env):
    connection, _fila, config = env
    nomes = Runner(connection, config, montar(connection, config)).stage_names
    assert nomes == (
        "perfil",
        "planejamento",
        "coleta",
        "prefiltro",
        "pontuacao_provisoria",
        "enriquecimento",
        "pontuacao_final",
        "julgamento",
        "sintese",
    )


def test_a_run_without_a_profile_is_refused_by_name(env):
    connection, fila, config = env
    fila.enqueue(USUARIO)
    with pytest.raises(HistoryRequired) as erro:
        Runner(connection, config, montar(connection, config)).run_once()
    assert "curriculo" in str(erro.value)


# ------------------------------------------------------- primeiro trecho
def test_the_first_half_stops_at_enrichment(env):
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)

    contexto = Runner(connection, config, montar(connection, config)).run_once()

    assert contexto.executados == [
        "perfil", "planejamento", "coleta", "prefiltro", "pontuacao_provisoria"
    ]
    assert fila.get(run_id)["estado"] != CONCLUIDO
    assert EnrichmentQueue(connection).pending_for(run_id)


def test_searches_come_from_the_profile_and_are_recorded(env):
    connection, fila, config = env
    consolidar(connection, config)
    fila.enqueue(USUARIO)
    fonte = FonteFalsa()

    contexto = Runner(
        connection, config, montar(connection, config, fonte)
    ).run_once()

    assert fonte.buscas, "o coletor precisa receber as buscas do planejador"
    # O contador guarda o texto e nao a contagem: quando o run traz pouca
    # coisa, a primeira pergunta e o que ele procurou.
    assert contexto.contadores["buscas"] == fonte.buscas


def test_semantic_noise_is_discarded_with_a_reason(env):
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)

    Runner(connection, config, montar(connection, config)).run_once()

    descartes = Repository(connection).for_user(USUARIO).select(
        "discards", where="run_id = ?", params=(run_id,)
    )
    assert descartes, "o ruido semantico precisa cair no pre-filtro"
    assert all(d["motivo"] for d in descartes)
    assert any("Manutencao" in d["titulo"] for d in descartes)


def test_only_survivors_are_scored_and_queued_for_description(env):
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)

    contexto = Runner(connection, config, montar(connection, config)).run_once()

    provisorios = connection.execute(
        "SELECT COUNT(*) FROM scores WHERE run_id = ? AND passada = 'provisoria'",
        (run_id,),
    ).fetchone()[0]
    assert provisorios == contexto.contadores["n_filtrados"]
    assert len(EnrichmentQueue(connection).pending_for(run_id)) == provisorios
    assert contexto.contadores["n_brutos"] > contexto.contadores["n_filtrados"]


# --------------------------------------------------------- segundo trecho
def atender_enriquecimento(connection, run_id, texto="Buscamos Python e Azure."):
    """Faz o papel do processo de enriquecimento: grava e fecha os pedidos."""
    fila = EnrichmentQueue(connection)
    for pedido in fila.pending_for(run_id):
        connection.execute(
            "INSERT OR REPLACE INTO job_descriptions "
            "(job_id, texto, emails_contato, candidatos, coletada_em) "
            "VALUES (?, ?, '[]', 5, ?)",
            (pedido.job_id, texto, _agora()),
        )
        fila.settle(pedido.request_id, atendido=True)
    connection.commit()


def test_the_second_half_survives_the_loss_of_in_memory_context(env):
    """A retomada roda com `RunContext.dados` vazio.

    Os estagios ja concluidos nao rodam de novo para repovoa-lo, entao quem
    precisa de dado do primeiro trecho tem de le-lo do banco. Este teste existe
    porque a primeira versao levantava `KeyError: 'perfil'` exatamente aqui.
    """
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    Runner(connection, config, montar(connection, config)).run_once()

    atender_enriquecimento(connection, run_id)
    connection.execute(
        "UPDATE runs SET estado = 'enfileirado' WHERE run_id = ?", (run_id,)
    )
    connection.commit()

    # Executor novo, contexto novo: nada da primeira metade sobrevive em memoria.
    contexto = Runner(connection, config, montar(connection, config)).run_once()

    assert contexto.executados[-3:] == [
        "pontuacao_final", "julgamento", "sintese"
    ]
    assert fila.get(run_id)["estado"] == CONCLUIDO


def test_the_final_pass_uses_the_collected_description(env):
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    Runner(connection, config, montar(connection, config)).run_once()
    atender_enriquecimento(connection, run_id)
    connection.execute(
        "UPDATE runs SET estado = 'enfileirado' WHERE run_id = ?", (run_id,)
    )
    connection.commit()

    Runner(connection, config, montar(connection, config)).run_once()

    linhas = connection.execute(
        "SELECT descricao_disponivel, lacunas FROM scores "
        "WHERE run_id = ? AND passada = 'final'",
        (run_id,),
    ).fetchall()
    assert linhas, "a passada final precisa gravar score para os sobreviventes"
    assert all(linha["descricao_disponivel"] == 1 for linha in linhas)
    # "azure" esta na descricao e nao no perfil: e lacuna acionavel.
    assert any("azure" in linha["lacunas"] for linha in linhas)


def test_without_a_model_the_run_finishes_in_deterministic_mode(env):
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    Runner(connection, config, montar(connection, config)).run_once()
    atender_enriquecimento(connection, run_id)
    connection.execute(
        "UPDATE runs SET estado = 'enfileirado' WHERE run_id = ?", (run_id,)
    )
    connection.commit()

    Runner(connection, config, montar(connection, config)).run_once()

    linha = fila.get(run_id)
    assert linha["estado"] == CONCLUIDO
    # Ausencia de modelo e estado degradado visivel, e nao falha do run: o
    # relatorio ainda sai com score, lacunas e ranking.
    assert linha["falha_sintese"]


# ------------------------------------------------------- estagios isolados
def test_the_enrichment_stage_does_not_suspend_when_nothing_survived(env):
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    reservado = RunQueue(connection).reserve_next()
    from crivo.worker.runner import RunContext

    contexto = RunContext(
        run_id=reservado["run_id"],
        user_id=USUARIO,
        janela=reservado["janela"],
        config=config,
        scope=Repository(connection).for_user(USUARIO),
    )
    contexto.dados["sobreviventes"] = []

    # Sem sobrevivente nao ha o que enriquecer, e suspender aqui deixaria o run
    # parado para sempre esperando uma fila que ninguem vai preencher.
    EnriquecimentoStage(connection).run(contexto)
    assert not EnrichmentQueue(connection).pending_for(run_id)


def test_a_job_brought_by_browsing_joins_the_next_run(env):
    """Sem isto a varredura seria um beco sem saida.

    Ela grava a vaga e o sinal do Premium, e nenhum run olharia para aquela
    linha -- o relatorio e por run, e o que nunca entrou num run nao tem nota e
    nao aparece em lugar nenhum.
    """
    import json

    from crivo.store.insights import InsightStore
    from crivo.worker.runner import RunContext

    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    reservado = RunQueue(connection).reserve_next()
    escopo = Repository(connection).for_user(USUARIO)

    InsightStore(connection).record(USUARIO, "li-navegada", {
        "sinais": ["top_applicant"],
        "vaga": {"titulo": "Head of Platform Engineering", "empresa": "Rootly",
                 "url": "https://www.linkedin.com/jobs/view/navegada/"},
    })

    contexto = RunContext(
        run_id=reservado["run_id"], user_id=USUARIO, janela=reservado["janela"],
        config=config, scope=escopo,
    )

    class ColetaVazia:
        cards, novos, bloqueadas = [], [], []

    estagio = ColetaStage.__new__(ColetaStage)
    estagio._connection = connection
    estagio._registra(contexto, ColetaVazia())

    trazidas = [c.job_id for c in contexto.dados["cards"]]
    assert trazidas == ["li-navegada"]
    assert contexto.contadores["n_brutos"] == 1
    # A origem sobrevive ao caminho: o rendimento das buscas mostra `extensao`
    # ao lado dos termos do planejador em vez de fingir que houve uma busca.
    assert contexto.dados["cards"][0].busca == "extensao"


def test_a_browsed_job_already_scored_does_not_come_back(env):
    """Ela entra uma vez. Voltar a cada run a faria ser pontuada para sempre."""
    from crivo.store.insights import InsightStore
    from crivo.worker.runner import RunContext

    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    reservado = RunQueue(connection).reserve_next()
    escopo = Repository(connection).for_user(USUARIO)

    InsightStore(connection).record(USUARIO, "li-navegada", {
        "sinais": ["top_applicant"],
        "vaga": {"titulo": "Head of Platform", "url": "https://exemplo/n"},
    })
    # O run precisa existir: `scores.run_id` tem chave estrangeira, e um id
    # inventado falharia por integridade em vez de exercitar o que se testa.
    escopo.insert("scores", {
        "job_id": "li-navegada", "run_id": run_id, "passada": "final",
        "score": 70, "componentes": "{}", "lacunas": "[]", "diferenciais": "[]",
        "descricao_disponivel": 0, "sinais_sessao_disponiveis": 0,
        "criado_em": "2026-09-01T00:00:00+00:00",
    })

    contexto = RunContext(
        run_id=reservado["run_id"], user_id=USUARIO, janela=reservado["janela"],
        config=config, scope=escopo,
    )

    class ColetaVazia:
        cards, novos, bloqueadas = [], [], []

    estagio = ColetaStage.__new__(ColetaStage)
    estagio._connection = connection
    estagio._registra(contexto, ColetaVazia())

    assert contexto.dados["cards"] == []


def test_a_top_applicant_goes_first_in_the_enrichment_queue(env):
    """A ordem da fila decide quem fica sem descricao quando o orcamento acaba.

    Sem descricao nao ha teto de requisito nem competencia extraida, e o score
    cai para o que o titulo sozinho diz. Gastar a ultima requisicao do dia numa
    vaga qualquer, tendo uma em que o LinkedIn ja apontou o usuario como melhor
    candidato, e a troca errada.
    """
    import json

    from crivo.pipeline.sources.guest import Card
    from crivo.worker.runner import RunContext

    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    reservado = RunQueue(connection).reserve_next()
    escopo = Repository(connection).for_user(USUARIO)

    def semear(job_id, flags):
        escopo.insert("jobs", {
            "job_id": job_id, "titulo": "SRE Manager", "estado": "novo",
            "url": f"https://linkedin.com/jobs/view/{job_id}",
            "flags": json.dumps(flags), "publicada_em": "2026-09-01",
            "primeira_vez_em": "2026-09-01", "ultima_vez_em": "2026-09-01",
        })
        return Card(job_id, "SRE Manager", "Acme",
                    f"https://linkedin.com/jobs/view/{job_id}", "SP", "on-site", None)

    cards = [
        semear("li-a", []),
        semear("li-b", ["early_applicant"]),
        semear("li-c", ["top_applicant"]),
        semear("li-d", []),
    ]

    contexto = RunContext(
        run_id=reservado["run_id"], user_id=USUARIO, janela=reservado["janela"],
        config=config, scope=escopo,
    )
    contexto.dados["a_enriquecer"] = cards

    from crivo.worker.runner import Suspend

    with pytest.raises(Suspend):
        # Esperada: a fila ficou com pedidos pendentes, e o estagio solta o
        # worker em vez de segurar um durante o intervalo do governador.
        EnriquecimentoStage(connection).run(contexto)

    ordem = [p.job_id for p in EnrichmentQueue(connection).pending_for(run_id)]
    assert ordem[0] == "li-c"
    # Estavel: sem sinal, a ordem da coleta permanece.
    assert ordem[1:] == ["li-a", "li-b", "li-d"]


# ------------------------------------------------- cancelamento e progresso
def test_collection_publishes_progress_before_it_finishes(env):
    """A coleta era um salto em que nada no banco mudava por minutos.

    O sinal de vida ficava parado e a tela mostrava zero o tempo todo, o que de
    fora e indistinguivel de um processo morto -- e foi assim que ela foi lida.
    """
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)

    vistos = []
    original = RunQueue.record_counters

    def espiar(self, rid, **contadores):
        if rid == run_id and "n_brutos" in contadores:
            vistos.append(contadores["n_brutos"])
        return original(self, rid, **contadores)

    RunQueue.record_counters = espiar
    try:
        Runner(connection, config, montar(connection, config)).run_once()
    finally:
        RunQueue.record_counters = original

    assert vistos, "a coleta precisa gravar avanco antes de terminar"
    assert vistos == sorted(vistos), "o contador nao pode andar para tras"


def test_a_cancelled_run_stops_and_keeps_what_it_already_collected(env):
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    fila.request_cancel(run_id, USUARIO)

    contexto = Runner(connection, config, montar(connection, config)).run_once()

    linha = fila.get(run_id)
    assert linha["estado"] == "interrompido"
    assert "cancelado" in linha["motivo_recusa"]
    # Parou antes da coleta, entao nao ha o que guardar -- mas o run precisa ter
    # terminado, e nao ficado ativo prendendo a vez do usuario.
    assert contexto is not None


def test_cancelling_during_collection_keeps_the_cards_already_stored(env):
    """Cancelar nao pode sair mais caro do que deixar terminar."""
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)

    original = RunQueue.cancel_requested
    chamadas = []

    def pedir_na_segunda(self, rid):
        chamadas.append(rid)
        # As tres primeiras consultas sao as bordas de perfil, planejamento e
        # coleta; da quarta em diante e o passo de progresso, ja dentro da
        # coleta e depois da primeira busca ter acontecido. E ali que a parada
        # precisa ser atendida para que haja o que preservar.
        return len(chamadas) > 3

    RunQueue.cancel_requested = pedir_na_segunda
    try:
        Runner(connection, config, montar(connection, config)).run_once()
    finally:
        RunQueue.cancel_requested = original

    assert fila.get(run_id)["estado"] == "interrompido"
    vagas = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert vagas > 0, "o que a coleta ja trouxe custou requisicao e fica"


# ------------------------------------------------ remoto nas duas passadas
def test_the_final_pass_recognises_a_remote_job(env):
    """A passada final comparava com "remoto", em portugues; a origem grava
    "remote". A comparacao era sempre falsa, entao toda vaga remota entrava na
    conta final como presencial -- e uma vaga remota longe levava a penalidade
    de distancia que existe justamente para quem precisa se deslocar.

    Pior que o desconto: as duas passadas discordavam sobre a mesma vaga, e a
    que o relatorio mostra era a errada. Nenhum teste pegou porque o dublê
    tambem escrevia "remoto" -- ele validava um contrato inexistente.
    """
    connection, fila, config = env
    consolidar(connection, config)
    run_id = fila.enqueue(USUARIO)
    Runner(connection, config, montar(connection, config)).run_once()

    estagio = PontuacaoFinalStage(connection, config)
    contexto = RunContext(
        run_id=run_id, user_id=USUARIO, janela="incremental", config=config,
        scope=Repository(connection).for_user(USUARIO),
    )
    alvos = estagio._alvos(contexto)

    modelo = dict(
        connection.execute(
            "SELECT job_id, modelo_trabalho FROM jobs WHERE user_id = ?", (USUARIO,)
        ).fetchall()
    )
    assert alvos, "a passada final precisa ter o que pontuar"
    for alvo in alvos:
        assert alvo["remoto"] is (modelo[alvo["job_id"]] == REMOTO), alvo["titulo"]
    assert any(a["remoto"] for a in alvos), "o dublê precisa ter vaga remota"
