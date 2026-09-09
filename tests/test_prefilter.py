import pytest

from crivo.config import load_config
from crivo.pipeline.planner import load_filters
from crivo.pipeline.prefilter import (
    Prefilter,
    PrefilterError,
    find_city,
    haversine_km,
    load_cities,
    normalize,
)
from crivo.pipeline.sources.guest import PRESENCIAL, REMOTO, Card


def card(titulo="SRE Manager", local="Sao Paulo, SP", modelo=PRESENCIAL, job_id=None):
    return Card(
        job_id=job_id or titulo.lower().replace(" ", "-"),
        titulo=titulo,
        empresa="Fintech",
        url="https://exemplo.br/vaga",
        local=local,
        modelo_trabalho=modelo,
        publicada_em="2026-08-01",
    )


@pytest.fixture
def prefilter():
    return Prefilter(load_filters(), load_config(), load_cities())


# ------------------------------------------------------------------ apoio
def test_normalization_drops_accents_and_case():
    assert normalize("São José dos Campos") == "sao jose dos campos"
    assert normalize(None) == ""


def test_distance_between_known_cities_is_plausible():
    cidades = load_cities()
    d = haversine_km(cidades["osasco"], cidades["campinas"])
    assert 70 < d < 110


def test_the_longest_city_name_wins_the_match():
    cidades = load_cities()
    nome, _ = find_city("Sao Bernardo do Campo, SP", cidades)
    assert nome == "sao bernardo do campo"


def test_an_unknown_city_matches_nothing():
    assert find_city("Ilhabela, SP", load_cities()) is None


def test_a_missing_city_table_names_the_path(tmp_path):
    with pytest.raises(PrefilterError) as err:
        load_cities(tmp_path / "ausente.toml")
    assert "ausente.toml" in str(err.value)


# ------------------------------------------------------------------ titulo
@pytest.mark.parametrize(
    "titulo",
    [
        "Analista de QA",
        "Product Manager",
        "Agile Coach",
        "Executivo de Vendas",
        "Engenheiro de Estruturas",
        "Suporte N1",
        "Estagio em TI",
        "Desenvolvedor Junior",
        "Tech Recruiter",
        # Manutencao de campo. O texto dessas vagas cita rede, seguranca e
        # confiabilidade, entao o score nao as separa -- a distincao esta no
        # cargo, e por isso precisa acontecer aqui.
        "Supervisor de Manutencao de Rede - Barra Funda",
        "Coordenador de Manutencao",
        "Tecnico de Manutencao de Campo",
        "Gerente de Manutencao Industrial",
    ],
)
def test_out_of_scope_titles_are_discarded(prefilter, titulo):
    resultado = prefilter.evaluate([card(titulo=titulo)], "Osasco, SP")
    assert resultado.mantidos == []
    assert resultado.descartados[0].motivo.startswith("titulo:")


def test_the_discard_records_title_company_and_reason(prefilter):
    resultado = prefilter.evaluate([card(titulo="Product Manager")], "Osasco, SP")
    descarte = resultado.descartados[0]
    assert descarte.titulo == "Product Manager"
    assert descarte.empresa == "Fintech"
    assert descarte.motivo


def test_generic_software_engineering_is_flagged_not_discarded(prefilter):
    """Uma coleta real mostrou que descartar aqui perde vaga adequada.

    "Software Engineering Manager" numa empresa de plataforma nao traz termo de
    infraestrutura no titulo, e era eliminada antes de chegar ao score. O sinal
    que decidiria esta na descricao, que o pre-filtro ainda nao tem.
    """
    resultado = prefilter.evaluate([card(titulo="Engenheiro de Software")], "Osasco, SP")
    assert len(resultado.mantidos) == 1
    assert resultado.descartados == []
    assert "engenharia de software" in list(resultado.blockers.values())[0]


def test_a_management_software_role_survives_the_prefilter(prefilter):
    resultado = prefilter.evaluate(
        [card(titulo="Software Engineering Manager", job_id="sem")], "Osasco, SP"
    )
    assert len(resultado.mantidos) == 1


def test_software_engineering_with_an_infra_term_survives(prefilter):
    resultado = prefilter.evaluate(
        [card(titulo="Software Engineer, Platform")], "Osasco, SP"
    )
    assert len(resultado.mantidos) == 1


def test_an_in_scope_title_survives(prefilter):
    resultado = prefilter.evaluate([card(titulo="SRE Manager")], "Osasco, SP")
    assert len(resultado.mantidos) == 1
    assert resultado.descartados == []


# ------------------------------------------------------------------ geografia
def test_a_remote_job_is_never_evaluated_geographically(prefilter):
    resultado = prefilter.evaluate(
        [card(local="Recife, PE", modelo=REMOTO)], "Osasco, SP"
    )
    assert len(resultado.mantidos) == 1
    assert resultado.blockers == {}


def test_an_onsite_job_within_the_radius_has_no_blocker(prefilter):
    resultado = prefilter.evaluate([card(local="Sao Paulo, SP")], "Osasco, SP")
    assert resultado.blockers == {}


def test_an_onsite_job_outside_the_radius_is_kept_with_a_blocker(prefilter):
    resultado = prefilter.evaluate([card(local="Porto Alegre, RS")], "Osasco, SP")
    assert len(resultado.mantidos) == 1
    blocker = list(resultado.blockers.values())[0]
    assert "Porto Alegre" in blocker
    assert "km" in blocker


def test_the_blocker_names_the_configured_radius(prefilter):
    resultado = prefilter.evaluate([card(local="Campinas, SP")], "Osasco, SP")
    blocker = list(resultado.blockers.values())[0]
    assert "raio de 60 km" in blocker


def test_an_unknown_city_becomes_an_explicit_unknown_distance(prefilter):
    resultado = prefilter.evaluate([card(local="Ilhabela, SP")], "Osasco, SP")
    assert len(resultado.mantidos) == 1
    assert "desconhecida" in list(resultado.blockers.values())[0]


def test_a_job_without_a_location_has_no_blocker(prefilter):
    resultado = prefilter.evaluate([card(local=None)], "Osasco, SP")
    assert resultado.blockers == {}


def test_without_a_profile_city_nothing_is_blocked(prefilter):
    resultado = prefilter.evaluate([card(local="Porto Alegre, RS")], None)
    assert resultado.blockers == {}


def test_a_blocked_job_is_kept_not_discarded(prefilter):
    resultado = prefilter.evaluate([card(local="Salvador, BA")], "Osasco, SP")
    assert resultado.descartados == []
    assert len(resultado.mantidos) == 1


# ------------------------------------------------------------------ contagens
def test_the_counts_before_and_after_are_reported(prefilter):
    cards = [
        card(titulo="SRE Manager", job_id="a"),
        card(titulo="Product Manager", job_id="b"),
        card(titulo="Gerente de Infraestrutura", job_id="c"),
        card(titulo="Analista de QA", job_id="d"),
    ]
    resultado = prefilter.evaluate(cards, "Osasco, SP")
    assert resultado.antes == 4
    assert resultado.depois == 2


def test_an_empty_input_yields_empty_counts(prefilter):
    resultado = prefilter.evaluate([], "Osasco, SP")
    assert (resultado.antes, resultado.depois) == (0, 0)


@pytest.mark.parametrize(
    "titulo",
    [
        "SRE Manager",
        "Engineering Manager, Infrastructure",
        "Gerente de Infraestrutura e Cloud",
        "Database Engineering Manager",
        "Gerente de Redes e Seguranca",
    ],
)
def test_the_maintenance_rule_does_not_reach_infrastructure_roles(prefilter, titulo):
    """A regra e por cargo de manutencao, nao pela palavra rede ou manutencao.

    Um padrao largo demais custaria mais caro que o falso positivo que ele
    corrige: descartar em silencio a vaga certa nao aparece em lugar nenhum.
    """
    resultado = prefilter.evaluate([card(titulo=titulo)], "Osasco, SP")
    assert resultado.descartados == []


# -------------------------------------------- vocabulario compartilhado
@pytest.mark.parametrize(
    "titulo",
    [
        # Financas corporativas atraidas pelo eixo FinOps. Observado numa
        # coleta real de 371 vagas: 31 destas passavam o filtro.
        "Analista de FP&A Pleno",
        "Especialista em Planejamento Financeiro (FP&A)",
        "Analista Financeiro (Planejamento e Performance)",
        "Analista Senior de Controladoria",
        # Carreira de dados e aprendizado de maquina. A descricao delas cita
        # AWS, pipeline e observabilidade igual a de infraestrutura, entao a
        # separacao precisa acontecer no titulo.
        "Engenheiro de Dados",
        "Data Engineer (Snowflake)",
        "Analista de Dados Pleno",
        "Cientista de Dados",
        "Machine Learning Engineer",
    ],
)
def test_adjacent_careers_that_share_vocabulary_are_discarded(prefilter, titulo):
    resultado = prefilter.evaluate([card(titulo=titulo)], "Osasco, SP")
    assert resultado.mantidos == []
    assert resultado.descartados[0].motivo.startswith("titulo:")


@pytest.mark.parametrize(
    "titulo",
    [
        # DBA e adjacente a SRE e frequentemente a mesma pessoa. Descartar por
        # conter "dados" custaria as vagas mais aderentes de quem acumula as
        # duas funcoes.
        "Administrador de Banco de Dados PostgreSQL",
        "Tech Lead de Banco de Dados",
        "Database Reliability Engineer",
        "DBA Oracle Senior",
        # O eixo FinOps continua valendo: o cargo existe e e o do candidato.
        "Gerentes e Especialistas em FinOps",
        "Cloud FinOps Analyst",
    ],
)
def test_the_candidates_own_specialities_survive_the_new_rules(prefilter, titulo):
    """As regras novas nao podem custar o que o candidato de fato faz."""
    resultado = prefilter.evaluate([card(titulo=titulo)], "Osasco, SP")
    assert [c.titulo for c in resultado.mantidos] == [titulo]


# -------------------------------------------- titulo sem termo de tecnologia
def _blocker(prefilter, titulo, local="Sao Paulo, SP", modelo=REMOTO):
    c = card(titulo=titulo, local=local, modelo=modelo)
    resultado = prefilter.evaluate([c], "Osasco, SP")
    if not resultado.mantidos:
        return "DESCARTADA"
    return resultado.blockers.get(c.job_id, "")


def test_a_title_with_no_technology_term_is_marked(prefilter):
    """Casos reais que pontuaram acima de 70% num run.

    A origem casa por palavra solta: a busca de nivel manda "Coordenador" e
    volta coordenador de qualquer coisa. Nenhuma lista de negacao alcanca essa
    variedade -- ela tinha "(gerente|supervisor|coordenador|tecnico) de
    manutencao" e ainda assim deixou passar "LIDER DE MANUTENCAO ELETRICA".
    """
    for titulo in (
        "LIDER DE MANUTENCAO ELETRICA/REFRIGERACAO",
        "Coordenador de Redacao (Letras ou MKT)",
        "Coordenador(a) de Customer Success",
        "Encarregado Operacional - Osasco",
        "Gerente de Planejamento e Controle",
        "Coordenador Financeiro de Projetos (Real Estate)",
    ):
        assert "sem nenhum termo de tecnologia" in _blocker(prefilter, titulo), titulo


def test_a_title_on_an_axis_is_not_marked(prefilter):
    for titulo in (
        "SRE Manager",
        "Coordenador em infraestrutura",
        "Especialista em Arquitetura de Plataforma Cloud",
        "Analista DBA Senior",
        "Staff Platform Engineer",
    ):
        assert "sem nenhum termo de tecnologia" not in _blocker(prefilter, titulo), titulo


def test_a_generic_technology_title_is_not_marked(prefilter):
    """Cargo certo que nao cita eixo nenhum. Marcar isto seria o erro oposto."""
    for titulo in (
        "Diretor de Tecnologia",
        "Chief Technology Officer (CTO) - SP",
        "Supervisor de TI",
        "Arquiteto(a) de Sistemas",
        "Gerente de Engenharia",
    ):
        assert "sem nenhum termo de tecnologia" not in _blocker(prefilter, titulo), titulo


def test_a_short_acronym_only_matches_a_whole_word(prefilter):
    """Sigla so vale como palavra inteira.

    "ti" como pedaco esta dentro de "otimizacao" e "marketing"; "it" dentro de
    "auditoria". Casando por pedaco, a regra chamaria de tecnologia justamente
    as carreiras que ela existe para separar.
    """
    for titulo in (
        "Analista de Otimizacao de Processos",
        "Coordenador de Marketing",
        "Gerente de Auditoria Interna",
    ):
        assert "sem nenhum termo de tecnologia" in _blocker(prefilter, titulo), titulo


def test_occupational_safety_does_not_pass_as_security(prefilter):
    """"seguranca" solto e uma forma do eixo Seguranca, e colide.

    Observado em coleta real: "Coordenador Seguranca do Trabalho" atravessou a
    regra de trilha por essa palavra e pontuou 66%. E outra profissao: NR, CIPA.
    """
    assert _blocker(prefilter, "Coordenador Seguranca do Trabalho") == "DESCARTADA"
    assert "sem nenhum termo" not in _blocker(
        prefilter, "Coordenador de Seguranca da Informacao"
    )


def test_without_the_vocabulary_the_rule_says_nothing(prefilter):
    """Arquivo de filtros anterior a secao [trilha] continua valendo.

    Sem vocabulario, toda vaga pareceria fora da trilha -- pior do que nao ter
    a regra.
    """
    import dataclasses

    from crivo.pipeline.prefilter import Prefilter

    vazio = dataclasses.replace(
        load_filters(), trilha_genericos=(), trilha_siglas=(), eixos={}, eixos_en={}
    )
    sem_regra = Prefilter(vazio, load_config(), load_cities())
    assert _blocker(sem_regra, "Coordenador de Redacao") == ""


# --------------------------------- desenvolvimento nao e infraestrutura
def test_development_titles_are_marked_as_another_track(prefilter):
    """Como o mercado de fato nomeia essa familia, e nao so "engenharia de software".

    Observado no run a6d5a060: sete das trinta melhores eram desenvolvimento
    puro e nenhuma casava o padrao antigo. Elas ocupavam o topo de um perfil de
    SRE e Infraestrutura sem nenhum aviso.
    """
    for titulo in (
        "Coordenador backend (Java/.NET) | Digital Equities",
        "Arquiteto(a) de Software | Java/Node.js + Microservicos",
        "Tech Lead Fullstack",
        "Coordenador de Desenvolvimento de Software",
    ):
        assert "engenharia de software" in _blocker(prefilter, titulo), titulo


def test_a_title_naming_both_tracks_is_left_alone(prefilter):
    """A excecao protege quem cita infraestrutura junto.

    "Backend Platform Engineer" e plataforma, e marca-lo empurraria para baixo
    exatamente o cargo procurado.
    """
    for titulo in (
        "Backend Platform Engineer",
        "Tech Lead | Cloud Architecture & AI",
        "Java Developer - Cloud Infrastructure",
        "Database Administrator Team Lead",
    ):
        assert "engenharia de software" not in _blocker(prefilter, titulo), titulo


def test_a_development_title_is_marked_and_not_discarded(prefilter):
    """Um Tech Lead Backend numa empresa de plataforma pode ser a vaga certa.

    Só a descrição diria, e o pré-filtro não a tem. O teto tira do topo sem
    esconder -- descartar aqui seria decidir por quem procura.
    """
    assert _blocker(prefilter, "Tech Lead Fullstack") != "DESCARTADA"


# ----------------------------------- sobreviver e merecer a etapa cara
def test_a_title_outside_technology_survives_without_costing_a_request(prefilter):
    """Manter no relatorio custa uma linha; buscar a descricao custa o run.

    Medido no run e49b12fe: 60 das 154 requisicoes de descricao foram gastas em
    titulos sem nenhuma marca de tecnologia -- "Supervisor(a) de Turbinas",
    "Coordenador Operacoes de Sinistro" -- cerca de treze minutos de
    governador. A vaga continua no relatorio, com o aviso e o teto; o que ela
    perde e a requisicao, que nenhuma descricao ia salvar.
    """
    fora = card(titulo="Supervisor(a) de Planejamento", job_id="fora")
    dentro = card(titulo="SRE Manager", job_id="dentro")
    resultado = prefilter.evaluate([fora, dentro], "Sao Paulo")

    assert {c.job_id for c in resultado.mantidos} == {"fora", "dentro"}
    assert [c.job_id for c in resultado.a_enriquecer] == ["dentro"]


def test_the_software_warning_still_earns_its_description(prefilter):
    """O corte do enriquecimento nao vale para o aviso de software.

    Ele existe justamente porque a descricao e quem decide entre a vaga de
    plataforma e o desenvolvimento puro. Economizar a requisicao dele seria
    apagar a pergunta em vez de responde-la.
    """
    duvida = card(titulo="Software Engineering Manager", job_id="duvida")
    resultado = prefilter.evaluate([duvida], "Sao Paulo")

    assert "engenharia de software" in resultado.blockers["duvida"]
    assert [c.job_id for c in resultado.a_enriquecer] == ["duvida"]


def test_industrial_and_financial_titles_are_discarded_outright(prefilter):
    """Familias sem leitura de tecnologia possivel caem no titulo.

    Nao sao titulos ambiguos que a descricao esclareceria. O que trouxe cada
    uma foi a palavra de nivel da busca, e todas ficaram empatadas no topo da
    passada provisoria do run e49b12fe.
    """
    for titulo in (
        "Supervisor(a) de Turbinas",
        "Diretor de Contratos - Termeletricas",
        "SUPERVISOR DE FAIXA DE DOMINIO - SP",
        "Gerente financeiro",
        "Accounting Senior Manager, Brazil",
        "Manager, Costing FTW",
        "Regional Sales Director, Sao Paulo Brazil",
        "Gerente - Mining Operations",
        "Coordenador Operacoes de Sinistro",
        "GPO Director, Procurement Processes",
    ):
        assert _blocker(prefilter, titulo) == "DESCARTADA", titulo


def test_the_new_rejections_do_not_reach_a_technology_title(prefilter):
    """Cada padrao novo virou cargo, e nao palavra solta.

    "Sales" sozinho descartava "Especialista em Arquitetura Cloud [...] I Tech
    Sales I RJ e SP" -- arquitetura de nuvem alocada numa area de vendas, que e
    a trilha certa. "Procurement" sozinho descartava a implementacao de um ERP.
    """
    for titulo in (
        "Especialista em Arquitetura Cloud - Com foco em Google Cloud I Tech Sales I RJ e SP",
        "Gerente de Implementacao de Oracle Procurement Cloud| Sao Paulo",
        "Analista Senior Salesforce Operations/ADM",
        "Especialista em Mineracao de Dados",
    ):
        assert _blocker(prefilter, titulo) != "DESCARTADA", titulo


def test_the_track_vocabulary_recognizes_the_measured_false_positives(prefilter):
    """Os quatro titulos de tecnologia que a regra invertida marcava por engano.

    Medidos no run e49b12fe. O erro era barato quando a marcacao so mexia no
    teto; passou a custar a descricao inteira quando ela tirou a vaga do
    enriquecimento.
    """
    for titulo in (
        "SysOps Analyst",
        "Especialista I - Technical Lead",
        "Gerente DFIR e Gestao de Vulnerabilidades",
        "Gerente Senior - Investigacao Cibernetica e Resposta Forense",
    ):
        assert "sem nenhum termo" not in _blocker(prefilter, titulo), titulo
