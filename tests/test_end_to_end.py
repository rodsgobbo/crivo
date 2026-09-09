"""Run completo com origens e provedores falsos, do curriculo ao relatorio.

Os testes por componente cobrem cada peca isolada. O que nenhum deles cobre e a
composicao: se os estagios se encaixam, se o que um grava o seguinte le, e se um
run atravessa os dois trechos separados pelo ponto de suspensao.

Nada aqui toca a rede. As fontes e o provedor de modelo sao falsos, e ha um
teste que troca `socket.socket` por uma funcao que levanta erro para provar isso.
"""

from __future__ import annotations

import json

import pytest

from crivo.config import load_config
from crivo.pipeline.collector import Collector
from crivo.pipeline.enricher import Enricher
from crivo.pipeline.governor import RateGovernor
from crivo.pipeline.planner import load_filters, plan
from crivo.pipeline.prefilter import Prefilter, load_cities
from crivo.pipeline.quota import QuotaAllocator
from crivo.pipeline.sources.guest import PRESENCIAL, REMOTO, Card, SearchOutcome
from crivo.profile.merger import ProfileMerger
from crivo.providers.client import Completion, ModelClient
from crivo.providers.registry import ProviderRegistry, load_providers
from crivo.providers.vault import CredentialVault, EnvelopeCipher, generate_master_key
from crivo.report.renderer import ReportRenderer
from crivo.scoring import gaps
from crivo.scoring.ontology import load_ontology
from crivo.scoring.scorer import FINAL, PROVISORIA, Scorer
from crivo.store.migrations import open_database
from crivo.store.repository import Repository
from crivo.store.scores import ScoreStore
from crivo.synthesis.synthesizer import Synthesizer
from crivo.worker.enrichment_queue import EnrichmentQueue
from crivo.worker.queue import AGUARDANDO_ENRIQUECIMENTO, CONCLUIDO, RunQueue

DIA = "2026-08-21"

EXPERIENCIAS = [
    {
        "titulo": "Gerente de Infraestrutura", "empresa": "Fintech",
        "inicio": "2020-01", "fim": None,
        "descricao": "Lidero time de SRE com Kubernetes e observabilidade",
    }
]

PERFIL_CURRICULO = {
    "nome": "Ana Souza",
    "headline": "Gerente de Infraestrutura e Cloud",
    "localizacao": "Osasco, SP",
    "experiencias": EXPERIENCIAS,
    "competencias": ["Kubernetes", "Terraform", "postgres"],
}

DESCRICOES = {
    "li-1": "Sobre a vaga: Kubernetes, Terraform, Datadog e Aurora PostgreSQL. "
            "Contato: rh@fintech.br",
    "li-2": "Sobre a vaga: lideranca de plataforma, Kubernetes e FinOps.",
}

# A resposta precisa trazer a marca de proveniencia: ha vaga sem descricao neste
# run, e o verificador de aterramento recusa resposta que nao distinga vaga lida
# de vaga avaliada apenas por card.
SINTESE = """Bloco 1 — Verdades desconfortaveis
[certo] O mercado para gerente de infraestrutura e raso no Brasil.

Bloco 2 — Vagas
SRE Manager — Fintech
https://exemplo.br/li-1

Head de Infraestrutura — Fintech
Avaliacao inferida de titulo e empresa, sem leitura da descricao.
"""


def card(job_id, titulo, remoto=True, local="Sao Paulo, SP", publicada=DIA):
    return Card(
        job_id=job_id, titulo=titulo, empresa="Fintech",
        url=f"https://exemplo.br/{job_id}", local=local,
        modelo_trabalho=REMOTO if remoto else PRESENCIAL, publicada_em=publicada,
    )


CARDS = [
    card("li-1", "SRE Manager"),
    card("li-2", "Gerente de Plataforma"),
    card("li-3", "Product Manager"),
    card("li-4", "Head de Infraestrutura", remoto=False, local="Porto Alegre, RS"),
]


class FakeSource:
    """Devolve os cards programados e as descricoes correspondentes."""

    def __init__(self, cards=None, descricoes=None):
        self.cards = CARDS if cards is None else cards
        self.descricoes = DESCRICOES if descricoes is None else descricoes
        self.buscas = []
        self.descricoes_pedidas = []

    def search(self, termo, local, janela_horas, quantidade):
        self.buscas.append(termo)
        # So a primeira busca traz resultado, para o run nao multiplicar cards.
        if len(self.buscas) > 1:
            return SearchOutcome(busca=termo, improdutiva=True)
        return SearchOutcome(busca=termo, cards=list(self.cards))

    def describe(self, job_id, url):
        self.descricoes_pedidas.append(job_id)
        texto = self.descricoes.get(job_id)
        if texto is None:
            from crivo.pipeline.sources.guest import CollectionError

            raise CollectionError(f"sem descricao para {job_id}")
        emails = ["rh@fintech.br"] if "rh@fintech.br" in texto else []
        return {"texto": texto, "emails": emails}


class FakeRouter:
    def __init__(self, texto=SINTESE):
        self.texto = texto
        self.chamadas = 0

    def complete(self, destinations, system, user):
        self.chamadas += 1
        return Completion(
            texto=self.texto, provedor=destinations[0].provider_id,
            modelo=destinations[0].modelo, tokens_entrada=1500, tokens_saida=900,
        )


def aceita(provider, secret):
    return True


@pytest.fixture
def sistema(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    repo.insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana",
         "criado_em": "2026-01-01T00:00:00+00:00"},
    )
    yield connection, load_config(), repo
    connection.close()


def executar_run(
    connection, config, repo, fonte=None, router=None, com_credencial=True,
    deterministico=False,
):
    """Roda o pipeline inteiro e devolve o que cada etapa produziu."""
    fonte = fonte or FakeSource()
    ontologia = load_ontology()
    filtros = load_filters()

    merger = ProfileMerger(connection, config)
    perfil = merger.consolidate("ana", resume_fields=PERFIL_CURRICULO)

    fila = RunQueue(connection)
    run_id = fila.enqueue("ana", config_efetiva={"limiar": config.report.limiar_destaque})
    fila.reserve_next()

    buscas = plan(perfil.campos, perfil.nivel_inferido, filtros)
    coletor = Collector(connection, fonte)
    coleta = coletor.collect("ana", [b.texto for b in buscas], "Brasil", 24)

    pre = Prefilter(filtros, config, load_cities())
    filtrado = pre.evaluate(coleta.cards, perfil.campos.get("localizacao"))
    for descarte in filtrado.descartados:
        repo.for_user("ana").insert(
            "discards",
            {"run_id": run_id, "job_id": descarte.job_id, "titulo": descarte.titulo,
             "empresa": descarte.empresa, "motivo": descarte.motivo},
        )
    for job_id, blocker in filtrado.blockers.items():
        repo.for_user("ana").update(
            "jobs", {"blocker": blocker}, where="job_id = ?", params=(job_id,)
        )

    scorer = Scorer(config, ontologia)
    store = ScoreStore(connection)
    perfil_score = {
        "competencias": perfil.campos.get("competencias") or [],
        "nivel_inferido": perfil.nivel_inferido,
        "liderados": 9,
    }
    for c in filtrado.mantidos:
        provisorio = scorer.score(
            perfil_score,
            {"titulo": c.titulo, "remoto": c.remoto,
             "blocker": filtrado.blockers.get(c.job_id)},
            passada=PROVISORIA,
        )
        store.record("ana", run_id, c.job_id, provisorio)

    # Ponto de suspensao: pedidos gravados, worker liberado.
    enfileirados = EnrichmentQueue(connection)
    ordem = store.ranking("ana", run_id, PROVISORIA)
    por_id = {c.job_id: c for c in filtrado.mantidos}
    enfileirados.enqueue("ana", run_id, [por_id[j] for j in ordem])
    fila.suspend(run_id)

    # Segundo trecho, apos o enriquecimento.
    cota = QuotaAllocator(connection, config, hoje=lambda: DIA)
    governor = RateGovernor(connection, config, sleep=lambda s: None)
    enricher = Enricher(connection, fonte, governor, quota=cota)
    resultado = enricher.enrich("ana", [por_id[j] for j in ordem])
    enfileirados.settle_all(run_id, set(resultado.enriquecidas + resultado.reusadas))
    fila.resume(run_id)
    fila.reserve_next()

    descricoes = []
    for c in filtrado.mantidos:
        guardada = enricher.stored_description(c.job_id)
        texto = guardada["texto"] if guardada else None
        descricoes.append(texto)
        final = scorer.score(
            perfil_score,
            {"titulo": c.titulo, "remoto": c.remoto,
             "blocker": filtrado.blockers.get(c.job_id)},
            descricao=texto, sinais=[], passada=FINAL,
        )
        lacunas = gaps.from_description(
            ontologia, perfil_score["competencias"], texto
        )
        store.record(
            "ana", run_id, c.job_id, final,
            lacunas=lacunas.lacunas, diferenciais=lacunas.diferenciais,
        )

    registry = ProviderRegistry(load_providers())
    vault = CredentialVault(connection, EnvelopeCipher(generate_master_key()), registry)
    cliente = None
    if com_credencial:
        vault.store("ana", "groq", "chave", validate=aceita)
        cliente = ModelClient(
            "ana", vault, registry, router or FakeRouter(),
            config.synthesis.limite_caracteres_texto_externo,
        )
    if deterministico:
        object.__setattr__(config.synthesis, "modo_deterministico", True)

    vagas_para_sintese = [
        {
            "id": s.job_id, "titulo": por_id[s.job_id].titulo,
            "empresa": por_id[s.job_id].empresa, "url": por_id[s.job_id].url,
            "local": por_id[s.job_id].local, "modelo": por_id[s.job_id].modelo_trabalho,
            "publicada_em": por_id[s.job_id].publicada_em, "score": s.score,
            "componentes": s.componentes, "sinais": [], "lacunas": list(s.lacunas),
            "diferenciais": list(s.diferenciais),
            "blocker": filtrado.blockers.get(s.job_id),
            "descricao": enricher.stored_description(s.job_id)["texto"]
            if enricher.stored_description(s.job_id) else None,
        }
        for s in store.for_run("ana", run_id, FINAL)
    ]
    sintetizador = Synthesizer(connection, cliente, config)
    sintese = sintetizador.synthesize(
        "ana", run_id, perfil.campos, vagas_para_sintese, [], {"n_brutos": coleta.brutos}
    )

    fila.finish(
        run_id, n_brutos=coleta.brutos, n_filtrados=filtrado.depois,
        n_novos=len(coleta.novos), buscas=[b.texto for b in buscas],
    )

    renderer = ReportRenderer(connection, config)
    pagina = renderer.render_run(
        "ana", run_id, sintese=sintese.texto,
        ranking_competencias=gaps.aggregate(ontologia, descricoes),
        problemas_higiene=perfil.problemas_higiene,
    )
    return {
        "run_id": run_id, "perfil": perfil, "coleta": coleta, "filtrado": filtrado,
        "enriquecimento": resultado, "sintese": sintese, "pagina": pagina,
        "fonte": fonte, "store": store, "fila": fila,
    }


# ------------------------------------------------------------------ completo
def test_a_full_run_produces_a_report(sistema):
    saida = executar_run(*sistema)
    assert "Triagem de vagas" in saida["pagina"]
    assert saida["fila"].get(saida["run_id"])["estado"] == CONCLUIDO


def test_the_prefilter_runs_before_any_description_is_fetched(sistema):
    saida = executar_run(*sistema)
    # Product Manager foi descartada e nunca teve descricao pedida.
    assert "li-3" not in saida["fonte"].descricoes_pedidas
    assert any(d.job_id == "li-3" for d in saida["filtrado"].descartados)


def test_only_survivors_cost_a_description_request(sistema):
    saida = executar_run(*sistema)
    pedidas = set(saida["fonte"].descricoes_pedidas)
    sobreviventes = {c.job_id for c in saida["filtrado"].mantidos}
    assert pedidas <= sobreviventes


def test_a_job_outside_the_radius_survives_with_a_blocker(sistema):
    saida = executar_run(*sistema)
    assert "li-4" in saida["filtrado"].blockers
    assert "Porto Alegre" in saida["pagina"]


def test_the_report_ranks_by_final_score(sistema):
    saida = executar_run(*sistema)
    finais = saida["store"].for_run("ana", saida["run_id"], FINAL)
    scores = [s.score for s in finais]
    assert scores == sorted(scores, reverse=True)


def test_both_scoring_passes_are_recorded(sistema):
    saida = executar_run(*sistema)
    historico = saida["store"].history("ana", "li-1")
    assert {s.passada for s in historico} == {PROVISORIA, FINAL}


def test_the_run_passes_through_the_suspension_state(sistema):
    connection, config, repo = sistema
    saida = executar_run(connection, config, repo)
    # O run foi suspenso e retomado: o registro guarda os estagios do caminho.
    assert saida["fila"].get(saida["run_id"])["estado"] == CONCLUIDO
    pendentes = EnrichmentQueue(connection).pending_for(saida["run_id"])
    assert pendentes == []


def test_gaps_reach_the_report(sistema):
    saida = executar_run(*sistema)
    assert "observabilidade" in saida["pagina"]


def test_contacts_from_the_description_reach_the_report(sistema):
    saida = executar_run(*sistema)
    assert "rh@fintech.br" in saida["pagina"]


def test_a_job_without_a_description_is_marked_as_inferred(sistema):
    saida = executar_run(*sistema)
    # li-4 nao tem descricao programada na fonte falsa.
    assert "li-4" in saida["enriquecimento"].sem_descricao
    assert "sem leitura da descri" in saida["pagina"]


def test_the_synthesis_text_appears_in_the_report(sistema):
    saida = executar_run(*sistema)
    assert "Verdades desconfortaveis" in saida["pagina"]


def test_quota_is_spent_only_on_paid_enrichments(sistema):
    connection, config, repo = sistema
    saida = executar_run(connection, config, repo)
    cota = QuotaAllocator(connection, config, hoje=lambda: DIA)
    assert cota.status("ana", DIA).consumida == len(
        saida["enriquecimento"].enriquecidas
    )


# ------------------------------------------------------- modo deterministico
def test_a_deterministic_run_still_produces_a_report(sistema):
    saida = executar_run(*sistema, deterministico=True)
    assert "Modo determin" in saida["pagina"]
    assert saida["sintese"].disponivel is False
    assert "Vagas" in saida["pagina"]


def test_a_user_without_a_credential_falls_back_to_deterministic(sistema):
    saida = executar_run(*sistema, com_credencial=False)
    assert saida["sintese"].disponivel is False
    assert "Triagem de vagas" in saida["pagina"]


def test_the_deterministic_run_never_calls_the_model(sistema):
    router = FakeRouter()
    executar_run(*sistema, router=router, deterministico=True)
    assert router.chamadas == 0


# ------------------------------------------------------------------ rede
def test_the_whole_run_touches_no_network(sistema):
    import socket

    original = socket.socket

    def proibido(*args, **kwargs):
        raise AssertionError("o run com fontes falsas nao pode tocar a rede")

    socket.socket = proibido
    try:
        executar_run(*sistema)
    finally:
        socket.socket = original


# ------------------------------------------------------------- isolamento
def test_a_second_user_never_sees_the_first_ones_data(sistema):
    connection, config, repo = sistema
    repo.insert(
        "users",
        {"user_id": "bruno", "subject_google": "sub-bruno", "criado_em": DIA},
    )
    executar_run(connection, config, repo)
    do_bruno = repo.for_user("bruno").select("jobs")
    assert do_bruno == []


def test_the_shared_description_is_reused_by_a_second_user(sistema):
    connection, config, repo = sistema
    saida = executar_run(connection, config, repo)
    repo.insert(
        "users",
        {"user_id": "bruno", "subject_google": "sub-bruno", "criado_em": DIA},
    )
    fonte = saida["fonte"]
    pedidas_antes = len(fonte.descricoes_pedidas)

    governor = RateGovernor(connection, config, sleep=lambda s: None)
    enricher = Enricher(connection, fonte, governor)
    resultado = enricher.enrich("bruno", [card("li-1", "SRE Manager")])
    assert resultado.reusadas == ["li-1"]
    assert len(fonte.descricoes_pedidas) == pedidas_antes
