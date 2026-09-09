"""Executa um run completo com dados realistas e grava o relatorio em disco.

Este roteiro usa os servicos de verdade -- consolidacao de perfil, planejador,
pre-filtro, governador, cota, scorer, lacunas e renderizador. O que ele substitui
por uma dublê e a fonte de vagas, para que a demonstracao nao dependa de rede nem
gaste requisicao na origem.

O modo deterministico fica ligado: e o caminho de quem ainda nao cadastrou
credencial de modelo, e mostra que o produto entrega valor sem nenhuma chamada a
modelo de linguagem.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from crivo.config import load_config  # noqa: E402
from crivo.pipeline.collector import Collector  # noqa: E402
from crivo.pipeline.enricher import Enricher  # noqa: E402
from crivo.pipeline.governor import RateGovernor  # noqa: E402
from crivo.pipeline.planner import load_filters, plan  # noqa: E402
from crivo.pipeline.prefilter import Prefilter, load_cities  # noqa: E402
from crivo.pipeline.quota import QuotaAllocator  # noqa: E402
from crivo.pipeline.sources.guest import PRESENCIAL, REMOTO, Card, SearchOutcome  # noqa: E402
from crivo.profile.merger import ProfileMerger  # noqa: E402
from crivo.report.renderer import ReportRenderer  # noqa: E402
from crivo.scoring import gaps  # noqa: E402
from crivo.scoring.ontology import load_ontology, skills_from_profile  # noqa: E402
from crivo.scoring.scorer import FINAL, PROVISORIA, Scorer  # noqa: E402
from crivo.store.migrations import open_database  # noqa: E402
from crivo.store.repository import Repository  # noqa: E402
from crivo.store.scores import ScoreStore  # noqa: E402
from crivo.worker.queue import RunQueue  # noqa: E402

USUARIO = "demo-user"

PERFIL = {
    "nome": "Ana Ribeiro",
    "headline": "Gerente de Infraestrutura e Cloud",
    "localizacao": "Campinas, SP",
    "competencias": [
        "Kubernetes", "Terraform", "AWS", "postgres", "Datadog",
        "FinOps", "Linux", "GitLab CI",
    ],
    "experiencias": [
        {
            "titulo": "Gerente de Infraestrutura e Cloud", "empresa": "Fintech BR",
            "inicio": "2020-02", "fim": None,
            "descricao": "Lidero time de nove pessoas entre SRE e plataforma. "
                         "Reducao de 93% no tempo de triagem e 75% no tempo de deploy.",
        },
        {
            "titulo": "Coordenador de Infraestrutura", "empresa": "Banco Digital",
            "inicio": "2016-03", "fim": "2020-01",
            "descricao": "Migracao para nuvem, observabilidade e reducao de custo.",
        },
        # Lacuna proposital entre 2014 e 2016, para o diagnostico de higiene achar.
        {
            "titulo": "Analista de Redes Senior", "empresa": "Integradora",
            "inicio": "2011-01", "fim": "2014-06",
            "descricao": "Redes, firewall e datacenter.",
        },
    ],
    "formacao": "Tecnologia em Redes de Computadores",
    "idiomas": ["portugues", "ingles intermediario"],
}

VAGAS = [
    ("li-101", "Engineering Manager, Platform", "VTEX", True, "Sao Paulo, SP",
     "Buscamos Engineering Manager para plataforma. Kubernetes, Terraform, AWS, "
     "observabilidade com Datadog e cultura de SRE. Gestao de time de 8 pessoas. "
     "Contato: talentos@vtex.com"),
    ("li-102", "Gerente de Infraestrutura e Cloud", "Magalu Cloud", True, "Remoto",
     "Lideranca de infraestrutura: AWS, Kubernetes, Terraform, FinOps e "
     "confiabilidade. Desejavel Datadog e GitLab CI."),
    ("li-103", "Site Reliability Engineering Manager", "Kraken", True, "Remoto",
     "SRE Manager. Kubernetes, observability, error budget e SLO. "
     "Obrigatorio ingles fluente para o time distribuido."),
    ("li-104", "Head de Infraestrutura", "Agibank", False, "Campinas, SP",
     "Head de infraestrutura para operacao presencial. Kubernetes, Oracle, "
     "seguranca e compliance Bacen."),
    ("li-105", "Coordenador de Cloud", "Sicredi", False, "Porto Alegre, RS",
     "Coordenacao de cloud. AWS, Terraform e governanca."),
    ("li-106", "Product Manager Sr", "Startup X", True, "Sao Paulo, SP", None),
    ("li-107", "Analista de QA Pleno", "Consultoria Y", True, "Remoto", None),
    ("li-108", "Engenheiro de Software", "Fintech Z", True, "Sao Paulo, SP", None),
    ("li-109", "Software Engineer, Platform", "Nubank", True, "Sao Paulo, SP",
     "Plataforma interna. Kubernetes, Go, observabilidade e Terraform."),
    ("li-110", "Estagio em TI", "Empresa W", False, "Sao Paulo, SP", None),
]


def url_simulada(titulo: str, empresa: str) -> str:
    """URL que de fato abre alguma coisa, para uma vaga que nao existe.

    A primeira versao desta demonstracao inventava enderecos no formato de vaga
    real (`/jobs/view/li-102`). Eles eram bem formados e mortos: clicar dava 404.
    Uma demonstracao que parece quebrada ensina a coisa errada sobre o produto.

    Aqui o endereco aponta para a busca real daquele titulo e empresa. Clicar
    leva a algo verdadeiro e util, e nenhum endereco falso e apresentado como se
    fosse uma vaga.
    """
    from urllib.parse import urlencode

    termo = f"{titulo} {empresa}".strip()
    return "https://www.linkedin.com/jobs/search/?" + urlencode({"keywords": termo})


class FonteDeDemonstracao:
    """Devolve um conjunto fixo de vagas, sem tocar a rede."""

    def __init__(self):
        self.cards = [
            Card(
                job_id=jid, titulo=titulo, empresa=empresa,
                url=url_simulada(titulo, empresa),
                local=local,
                modelo_trabalho=REMOTO if remoto else PRESENCIAL,
                publicada_em="2026-08-20",
            )
            for jid, titulo, empresa, remoto, local, _desc in VAGAS
        ]
        self.descricoes = {jid: d for jid, _t, _e, _r, _l, d in VAGAS if d}
        self.buscas = 0
        self.descricoes_pedidas = []

    def search(self, termo, local, janela_horas, quantidade):
        self.buscas += 1
        if self.buscas > 1:
            return SearchOutcome(busca=termo, improdutiva=True)
        return SearchOutcome(busca=termo, cards=list(self.cards))

    def describe(self, job_id, url):
        self.descricoes_pedidas.append(job_id)
        texto = self.descricoes.get(job_id)
        if texto is None:
            from crivo.pipeline.sources.guest import CollectionError

            raise CollectionError(f"descricao indisponivel para {job_id}")
        import re

        emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", texto)
        return {"texto": texto, "emails": emails}


def main() -> int:
    destino = RAIZ / "demo" / "saida"
    destino.mkdir(parents=True, exist_ok=True)
    banco = destino / "demo.db"
    if banco.exists():
        banco.unlink()

    config = load_config(RAIZ / "config" / "default.toml")
    object.__setattr__(config.synthesis, "modo_deterministico", True)
    conexao = open_database(banco)
    repo = Repository(conexao)
    repo.insert(
        "users",
        {"user_id": USUARIO, "subject_google": "sub-demo",
         "email": "demo@exemplo.br", "criado_em": "2026-08-21T09:00:00+00:00"},
    )

    print("1. PERFIL")
    merger = ProfileMerger(conexao, config)
    perfil = merger.consolidate(USUARIO, resume_fields=PERFIL)
    print(f"   nivel inferido: {perfil.nivel_inferido}")
    print(f"   problemas de higiene: {len(perfil.problemas_higiene)}")
    for problema in perfil.problemas_higiene:
        print(f"     - {problema['tipo']}: {problema['detalhe'][:70]}")

    print("\n2. BUSCAS")
    filtros = load_filters(RAIZ / "config" / "filters.toml")
    buscas = plan(perfil.campos, perfil.nivel_inferido, filtros)
    print(f"   {len(buscas)} buscas derivadas do perfil, sem chamada de modelo")
    for b in buscas[:6]:
        print(f"     - [{b.idioma}] {b.texto}")
    print(f"     ... e mais {max(0, len(buscas) - 6)}")

    fila = RunQueue(conexao)
    run_id = fila.enqueue(USUARIO, janela="ampla")
    fila.reserve_next()

    print("\n3. COLETA")
    fonte = FonteDeDemonstracao()
    coletor = Collector(conexao, fonte)
    coleta = coletor.collect(USUARIO, [b.texto for b in buscas], "Brasil", 720)
    print(f"   {coleta.brutos} cards brutos, {len(coleta.cards)} unicos, "
          f"{len(coleta.novos)} novos")
    print(f"   {len(coleta.improdutivas)} buscas improdutivas, "
          f"{len(coleta.falhas)} com falha")

    print("\n4. PRE-FILTRO")
    pre = Prefilter(filtros, config, load_cities(RAIZ / "config" / "cidades.toml"))
    filtrado = pre.evaluate(coleta.cards, perfil.campos.get("localizacao"))
    print(f"   {filtrado.antes} -> {filtrado.depois} "
          f"({len(filtrado.descartados)} descartadas)")
    for d in filtrado.descartados:
        print(f"     x {d.titulo:34} {d.motivo}")
    for jid, blocker in filtrado.blockers.items():
        print(f"     ! {jid}: {blocker}")
    for descarte in filtrado.descartados:
        repo.for_user(USUARIO).insert(
            "discards",
            {"run_id": run_id, "job_id": descarte.job_id, "titulo": descarte.titulo,
             "empresa": descarte.empresa, "motivo": descarte.motivo},
        )
    for jid, blocker in filtrado.blockers.items():
        repo.for_user(USUARIO).update(
            "jobs", {"blocker": blocker}, where="job_id = ?", params=(jid,)
        )

    ontologia = load_ontology(RAIZ / "config" / "ontology.toml")
    scorer = Scorer(config, ontologia)
    declaradas = ontologia.canonical_set(perfil.campos["competencias"])
    do_historico = skills_from_profile(ontologia, perfil.campos) - declaradas
    if do_historico:
        print("\n   competencias lidas do historico: "
              + ", ".join(sorted(do_historico)))
    store = ScoreStore(conexao)
    # As competencias vem do campo declarado E do texto das experiencias: quem
    # lidera um time de SRE ha seis anos nao precisa listar "SRE" a mao.
    competencias = sorted(skills_from_profile(ontologia, perfil.campos))
    perfil_score = {
        "competencias": competencias,
        "nivel_inferido": perfil.nivel_inferido,
        "setores": ["fintech"],
        "liderados": 9,
    }

    print("\n5. SCORE PROVISORIO (ordena a fila de enriquecimento)")
    for c in filtrado.mantidos:
        b = scorer.score(
            perfil_score,
            {"titulo": c.titulo, "remoto": c.remoto,
             "blocker": filtrado.blockers.get(c.job_id), "setor": "fintech",
             "liderados": 8},
            passada=PROVISORIA,
        )
        store.record(USUARIO, run_id, c.job_id, b)
    ordem = store.ranking(USUARIO, run_id, PROVISORIA)
    print(f"   fila: {' > '.join(ordem)}")

    print("\n6. ENRIQUECIMENTO (so os sobreviventes, um por vez)")
    cota = QuotaAllocator(conexao, config)
    governador = RateGovernor(conexao, config, sleep=lambda s: None)
    enricher = Enricher(conexao, fonte, governador, session=None, quota=cota)
    por_id = {c.job_id: c for c in filtrado.mantidos}
    resultado = enricher.enrich(USUARIO, [por_id[j] for j in ordem])
    print(f"   descricoes pedidas: {len(fonte.descricoes_pedidas)} "
          f"(de {filtrado.antes} coletadas)")
    print(f"   obtidas: {len(resultado.enriquecidas)} | "
          f"sem descricao: {len(resultado.sem_descricao)}")
    print(f"   cota consumida hoje: {cota.status(USUARIO).consumida} "
          f"de teto {cota.status(USUARIO).teto}")

    print("\n7. SCORE FINAL E LACUNAS")
    descricoes = []
    for c in filtrado.mantidos:
        guardada = enricher.stored_description(c.job_id)
        texto = guardada["texto"] if guardada else None
        descricoes.append(texto)
        b = scorer.score(
            perfil_score,
            {"titulo": c.titulo, "remoto": c.remoto,
             "blocker": filtrado.blockers.get(c.job_id), "setor": "fintech",
             "liderados": 8},
            descricao=texto, sinais=[], passada=FINAL,
        )
        lac = gaps.from_description(ontologia, perfil_score["competencias"], texto)
        store.record(USUARIO, run_id, c.job_id, b,
                     lacunas=lac.lacunas, diferenciais=lac.diferenciais)
    for s in store.for_run(USUARIO, run_id, FINAL):
        marca = "" if s.descricao_disponivel else "  (sem leitura da descricao)"
        print(f"   {s.score:3d}%  {por_id[s.job_id].titulo:36} "
              f"{por_id[s.job_id].empresa}{marca}")
        if s.lacunas:
            print(f"          falta: {', '.join(s.lacunas)}")

    ranking = gaps.aggregate(ontologia, descricoes)
    print(f"\n8. COMPETENCIAS MAIS PEDIDAS")
    for termo, quantas in ranking[:8]:
        tem = "voce tem" if termo in ontologia.canonical_set(
            perfil_score["competencias"]) else "FALTA"
        print(f"   {quantas}x  {termo:20} {tem}")

    fila.finish(
        run_id, n_brutos=coleta.brutos, n_filtrados=filtrado.depois,
        n_novos=len(coleta.novos), buscas=[b.texto for b in buscas],
        cota_esgotada=len(resultado.sem_cota),
    )

    print("\n9. RELATORIO")
    renderer = ReportRenderer(conexao, config)
    pagina = renderer.render_run(
        USUARIO, run_id,
        ranking_competencias=ranking,
        problemas_higiene=perfil.problemas_higiene,
    )
    arquivo = destino / "relatorio.html"
    arquivo.write_text(pagina, encoding="utf-8")
    print(f"   {len(pagina)} bytes -> {arquivo}")
    print(f"   media de aderencia do run: {store.average(USUARIO, run_id, FINAL)}%")
    conexao.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
