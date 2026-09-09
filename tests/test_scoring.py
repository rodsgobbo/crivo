import pytest

from crivo.config import load_config
from crivo.scoring import gaps
from crivo.scoring.ontology import (
    OntologyError,
    load_ontology,
    normalize,
)
from crivo.scoring.scorer import FINAL, PROVISORIA, Scorer

DESCRICAO = (
    "Buscamos pessoa para liderar plataforma. Requisitos: Kubernetes, "
    "Terraform, Aurora PostgreSQL e Datadog. Desejavel FinOps."
)

PERFIL = {
    "competencias": ["k8s", "terragrunt", "postgres", "cloud cost"],
    "nivel_inferido": "manager",
    "setores": ["fintech"],
    "liderados": 9,
}


@pytest.fixture
def ontology():
    return load_ontology()


@pytest.fixture
def scorer(ontology):
    return Scorer(load_config(), ontology)


def vaga(**over):
    base = {
        "titulo": "Gerente de Plataforma",
        "remoto": True,
        "blocker": None,
        "setor": "fintech",
        "liderados": 8,
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ ontologia
def test_variants_collapse_to_the_same_canonical_term(ontology):
    assert ontology.canonical("Aurora PostgreSQL") == "postgresql"
    assert ontology.canonical("postgres") == "postgresql"
    assert ontology.canonical("k8s") == "kubernetes"


def test_an_unknown_term_keeps_its_normalized_form(ontology):
    assert ontology.canonical("COBOL") == "cobol"


def test_normalization_drops_accents(ontology):
    assert normalize("Observabilidade") == "observabilidade"
    assert ontology.canonical("otimizacao de custo") == "finops"


def test_extraction_recognizes_declared_terms(ontology):
    encontrados = ontology.extract(DESCRICAO)
    assert {"kubernetes", "terraform", "postgresql", "observabilidade"} <= encontrados


def test_extraction_prefers_the_longest_form(ontology):
    assert "sqlserver" in ontology.extract("Experiencia com SQL Server")


def test_extraction_does_not_match_inside_a_word(ontology):
    # "go" nao pode ser encontrado dentro de "algoritmo".
    assert "golang" not in ontology.extract("conhecimento de algoritmos")


def test_extraction_of_empty_text_finds_nothing(ontology):
    assert ontology.extract(None) == frozenset()
    assert ontology.extract("   ") == frozenset()


def test_a_missing_ontology_names_the_path(tmp_path):
    with pytest.raises(OntologyError) as err:
        load_ontology(tmp_path / "ausente.toml")
    assert "ausente.toml" in str(err.value)


def test_an_eliminatory_term_outside_the_ontology_is_refused(tmp_path):
    alvo = tmp_path / "ontology.toml"
    alvo.write_text(
        '[sinonimos]\naws = ["ec2"]\n\n[eliminatorios]\ntermos = ["telepatia"]\n',
        encoding="utf-8",
    )
    with pytest.raises(OntologyError) as err:
        load_ontology(alvo)
    assert "telepatia" in str(err.value)


# ------------------------------------------------------------------ score
def test_the_score_is_an_integer_between_zero_and_one_hundred(scorer):
    resultado = scorer.score(PERFIL, vaga(), descricao=DESCRICAO)
    assert isinstance(resultado.score, int)
    assert 0 <= resultado.score <= 100


def test_every_component_is_reported(scorer):
    componentes = scorer.score(PERFIL, vaga(), descricao=DESCRICAO).componentes
    assert set(componentes) == {
        "competencias", "nivel", "dominio", "escopo_gestao", "geografia"
    }


def test_the_same_input_always_yields_the_same_score(scorer):
    a = scorer.score(PERFIL, vaga(), descricao=DESCRICAO).score
    b = scorer.score(PERFIL, vaga(), descricao=DESCRICAO).score
    assert a == b


def test_synonyms_count_as_matches(scorer, ontology):
    com_sinonimo = scorer.score(PERFIL, vaga(), descricao=DESCRICAO)
    sem_sinonimo = scorer.score(
        {**PERFIL, "competencias": ["cobol", "delphi"]}, vaga(), descricao=DESCRICAO
    )
    assert com_sinonimo.componentes["competencias"] > sem_sinonimo.componentes["competencias"]


def test_a_remote_job_scores_the_top_geographic_component(scorer):
    remoto = scorer.score(PERFIL, vaga(remoto=True), descricao=DESCRICAO)
    assert remoto.componentes["geografia"] == 1.0


def test_an_unreachable_job_never_outranks_a_remote_one(scorer):
    """O caso que a simulacao real expos: 846 km em primeiro lugar.

    A vaga distante vencia porque a descricao dela era curta e o candidato
    cobria tudo que ela citava. O componente geografico sozinho impunha no
    maximo 8.5 pontos, insuficiente para afundar uma vaga inaceitavel.
    """
    distante = scorer.score(
        PERFIL,
        vaga(remoto=False, blocker="presencial em Porto Alegre, 846 km"),
        descricao="Coordenacao de cloud. AWS, Terraform e governanca.",
    )
    remota = scorer.score(
        PERFIL, vaga(remoto=True),
        descricao=DESCRICAO,
    )
    assert distante.score < remota.score
    assert distante.ajustes["fora_do_raio"] < 0


def test_a_remote_job_carries_no_distance_penalty(scorer):
    remota = scorer.score(PERFIL, vaga(remoto=True), descricao=DESCRICAO)
    assert "fora_do_raio" not in remota.ajustes


def test_more_evidence_at_the_same_ratio_is_worth_more(scorer):
    """Casar tudo de quatro requisitos vale mais que casar tudo de dois.

    Antes da suavizacao, as duas davam 1.0 e a vaga de descricao pobre
    empatava com a completa. Numa simulacao real isso pos em primeiro lugar
    uma vaga a 846 km cuja descricao citava tres coisas.
    """
    perfil = {**PERFIL, "competencias": ["k8s", "terragrunt", "postgres", "finops"]}
    pouca = scorer.score(perfil, vaga(), descricao="Kubernetes e Terraform.")
    muita = scorer.score(
        perfil, vaga(),
        descricao="Kubernetes, Terraform, Aurora PostgreSQL e FinOps.",
    )
    assert muita.componentes["competencias"] > pouca.componentes["competencias"]


def test_a_short_description_no_longer_reaches_a_perfect_component(scorer):
    """Tres requisitos casados nao sao evidencia suficiente para nota cheia."""
    perfil = {**PERFIL, "competencias": ["k8s", "terragrunt"]}
    curta = scorer.score(perfil, vaga(), descricao="Kubernetes e Terraform.")
    assert curta.componentes["competencias"] < 1.0


def test_a_blocked_job_scores_the_lowest_geographic_component(scorer):
    bloqueada = scorer.score(
        PERFIL, vaga(remoto=False, blocker="presencial em Recife"), descricao=DESCRICAO
    )
    assert bloqueada.componentes["geografia"] < 0.2


def test_the_same_level_scores_higher_than_a_distant_one(scorer):
    igual = scorer.score(PERFIL, vaga(titulo="Gerente de Plataforma"))
    distante = scorer.score(PERFIL, vaga(titulo="Analista de Suporte"))
    assert igual.componentes["nivel"] > distante.componentes["nivel"]


# --------------------------------------------------------- bonus e limite
def test_a_bonus_signal_raises_the_score(scorer):
    sem = scorer.score(PERFIL, vaga(), descricao=DESCRICAO)
    com = scorer.score(PERFIL, vaga(), descricao=DESCRICAO, sinais=["top applicant"])
    assert com.score > sem.score
    assert com.ajustes["top_applicant"] > 0


def test_a_penalty_signal_lowers_the_score(scorer):
    sem = scorer.score(PERFIL, vaga(), descricao=DESCRICAO)
    com = scorer.score(
        PERFIL, vaga(), descricao=DESCRICAO, sinais=["muitos candidatos"]
    )
    assert com.score < sem.score


def test_an_unknown_signal_changes_nothing(scorer):
    sem = scorer.score(PERFIL, vaga(), descricao=DESCRICAO)
    com = scorer.score(PERFIL, vaga(), descricao=DESCRICAO, sinais=["sinal inventado"])
    assert com.score == sem.score


def test_extreme_bonuses_never_push_past_one_hundred(scorer):
    resultado = scorer.score(
        PERFIL, vaga(), descricao=DESCRICAO,
        sinais=["top applicant", "early applicant", "conexoes na empresa",
                "actively reviewing"],
    )
    assert resultado.score <= 100


def test_a_repeated_signal_does_not_stack(scorer):
    """Um sinal esta presente ou ausente; repeti-lo nao vale mais."""
    uma = scorer.score(PERFIL, vaga(), descricao=DESCRICAO, sinais=["top applicant"])
    varias = scorer.score(
        PERFIL, vaga(), descricao=DESCRICAO, sinais=["top applicant"] * 8
    )
    assert uma.score == varias.score


def test_extreme_penalties_never_push_below_zero(scorer):
    fraco = {"competencias": [], "nivel_inferido": "pleno", "liderados": 0}
    resultado = scorer.score(
        fraco, vaga(titulo="CTO", remoto=False, blocker="longe"),
        descricao=DESCRICAO, sinais=["muitos candidatos", "repostada antiga"] * 10,
    )
    assert resultado.score >= 0


def test_the_raw_value_is_reported_apart_from_the_clamped_one(scorer):
    """O bruto e o ajuste ficam visiveis mesmo quando o final bate no teto."""
    perfeito = {**PERFIL, "competencias": list(PERFIL["competencias"]) + [
        "observabilidade", "sre", "aws", "azure", "gcp", "linux", "python",
    ]}
    resultado = scorer.score(
        perfeito, vaga(), descricao="Kubernetes.", sinais=["top applicant"]
    )
    assert resultado.bruto > 0
    assert resultado.ajustes["top_applicant"] == 10
    assert resultado.score == max(0, min(100, round(
        resultado.bruto + sum(resultado.ajustes.values())
    )))


def test_an_unmet_eliminatory_requirement_lowers_the_score(scorer):
    exige_ingles = DESCRICAO + " Obrigatorio ingles fluente."
    com_ingles = scorer.score(
        {**PERFIL, "competencias": PERFIL["competencias"] + ["english"]},
        vaga(), descricao=exige_ingles,
    )
    sem_ingles = scorer.score(PERFIL, vaga(), descricao=exige_ingles)
    assert sem_ingles.ajustes.get("requisito_eliminatorio", 0) < 0
    assert "requisito_eliminatorio" not in com_ingles.ajustes


# ------------------------------------------------------------ duas passadas
def test_without_a_description_the_score_says_so(scorer):
    resultado = scorer.score(PERFIL, vaga(), descricao=None, passada=PROVISORIA)
    assert resultado.descricao_disponivel is False
    assert resultado.passada == PROVISORIA
    assert 0 <= resultado.score <= 100


def test_the_final_pass_records_that_signals_were_available(scorer):
    resultado = scorer.score(
        PERFIL, vaga(), descricao=DESCRICAO, sinais=["top applicant"], passada=FINAL
    )
    assert resultado.passada == FINAL
    assert resultado.sinais_disponiveis is True


def test_the_two_passes_use_the_same_function(scorer):
    provisoria = scorer.score(PERFIL, vaga(), passada=PROVISORIA)
    final = scorer.score(PERFIL, vaga(), descricao=DESCRICAO, passada=FINAL)
    assert set(provisoria.componentes) == set(final.componentes)


# ------------------------------------------------------------ lacunas
def test_gaps_are_what_the_job_asks_and_the_profile_lacks(ontology):
    resultado = gaps.from_description(ontology, PERFIL["competencias"], DESCRICAO)
    assert "observabilidade" in resultado.lacunas
    assert resultado.tem_lacuna is True


def test_differentials_are_the_overlap(ontology):
    resultado = gaps.from_description(ontology, PERFIL["competencias"], DESCRICAO)
    assert "kubernetes" in resultado.diferenciais
    assert "postgresql" in resultado.diferenciais


def test_gaps_and_differentials_never_intersect(ontology):
    resultado = gaps.from_description(ontology, PERFIL["competencias"], DESCRICAO)
    assert set(resultado.lacunas) & set(resultado.diferenciais) == set()


def test_a_profile_covering_everything_has_no_gaps(ontology):
    tudo = list(ontology.extract(DESCRICAO))
    resultado = gaps.from_description(ontology, tudo, DESCRICAO)
    assert resultado.lacunas == ()


def test_frequency_ranks_the_most_asked_first(ontology):
    descricoes = [
        "Kubernetes e Terraform",
        "Kubernetes e Datadog",
        "Kubernetes",
    ]
    ranking = gaps.aggregate(ontology, descricoes)
    assert ranking[0] == ("kubernetes", 3)


def test_jobs_without_a_description_are_left_out_of_the_ranking(ontology):
    ranking = gaps.aggregate(ontology, ["Kubernetes", None, "   "])
    assert ranking == [("kubernetes", 1)]


def test_the_ranking_is_stable_between_runs(ontology):
    descricoes = ["Kubernetes e Terraform", "Terraform e Datadog"]
    assert gaps.aggregate(ontology, descricoes) == gaps.aggregate(ontology, descricoes)


def test_the_missing_ranking_excludes_what_the_profile_has(ontology):
    descricoes = ["Kubernetes e Datadog", "Kubernetes"]
    faltando = dict(gaps.missing_across(ontology, PERFIL["competencias"], descricoes))
    assert "kubernetes" not in faltando
    assert "observabilidade" in faltando


# ------------------------------------------- competencias vindas do historico
def test_skills_are_read_from_the_experience_text(ontology):
    """O sistema recomendava adicionar SRE a quem lidera um time de SRE."""
    from crivo.scoring.ontology import skills_from_profile

    campos = {
        "headline": "Gerente de Infraestrutura e Cloud",
        "competencias": ["Kubernetes"],
        "experiencias": [{
            "titulo": "Gerente de Infraestrutura",
            "descricao": "Lidero time de SRE com Terraform e observabilidade.",
        }],
    }
    encontradas = skills_from_profile(ontology, campos)
    assert {"kubernetes", "sre", "terraform", "observabilidade"} <= encontradas


def test_declared_skills_are_never_lost(ontology):
    from crivo.scoring.ontology import skills_from_profile

    campos = {"competencias": ["k8s", "postgres"], "experiencias": []}
    assert {"kubernetes", "postgresql"} <= skills_from_profile(ontology, campos)


def test_prose_alone_does_not_invent_a_skill(ontology):
    """A extracao continua conservadora: so reconhece termo da ontologia."""
    from crivo.scoring.ontology import skills_from_profile

    # "lideranca" seria extraida com razao: ela esta na ontologia. A prosa aqui
    # so tem termos que nao estao, para provar que nada e inventado.
    campos = {
        "competencias": [],
        "experiencias": [{"descricao": "Comunicacao, negociacao e organizacao."}],
    }
    assert skills_from_profile(ontology, campos) == frozenset()


# --------------------------------------------- descricao lida versus nao lida
ORCAMENTARIA = (
    "Responsavel pelo ciclo de planejamento orcamentario, consolidacao de "
    "forecast, analise de variacao e apresentacao ao comite executivo."
)


def test_a_description_with_no_technical_term_scores_below_an_unread_one(scorer):
    """Ler e nao achar nada nao e o mesmo que nao ter lido.

    Uma vaga de planejamento financeiro pontuava 78% sem uma lacuna sequer,
    porque a ausencia de competencia devolvia o valor neutro. Ficava acima de
    uma vaga de infraestrutura em que o perfil casava 4 de 10 requisitos: nao
    ter o que medir vencia ter medido.
    """
    alvo = vaga(titulo="Financial Planning Manager")
    lida = scorer.score(PERFIL, alvo, descricao=ORCAMENTARIA, passada=FINAL)
    nao_lida = scorer.score(PERFIL, alvo, descricao=None, passada=FINAL)
    assert lida.score < nao_lida.score


def test_an_unread_job_keeps_the_neutral_value(scorer):
    """Sem descricao, a ausencia nao informa nada e nao pode punir."""
    resultado = scorer.score(
        PERFIL, vaga(titulo="Gerente de TI"), descricao=None, passada=PROVISORIA
    )
    assert resultado.componentes["competencias"] == 0.5


def test_a_read_technical_job_outscores_a_read_non_technical_one(scorer):
    tecnica = scorer.score(
        PERFIL, vaga(titulo="Engineering Manager"),
        descricao="Kubernetes, Terraform, AWS e observabilidade em producao.",
        passada=FINAL,
    )
    outra = scorer.score(
        PERFIL, vaga(titulo="Financial Planning Manager"),
        descricao=ORCAMENTARIA, passada=FINAL,
    )
    assert tecnica.score > outra.score


# ------------------------------------------------------------------- tetos
def test_a_mandatory_requirement_the_candidate_lacks_caps_the_score(scorer):
    """Noventa por cento das palavras batendo nao vale noventa por cento.

    A penalidade sozinha deixava passar 85 numa vaga que o candidato nao pode
    aceitar. O teto e o que separa esta nota de uma contagem de palavras-chave.
    """
    descricao = (
        "Gestor de SRE. E imprescindivel experiencia com Azure. "
        "Trabalhamos com AWS, Kubernetes, Terraform e Python."
    )
    resultado = scorer.score(PERFIL, vaga(), descricao=descricao)
    assert resultado.tetos.get("requisito_eliminatorio") == 75
    assert resultado.score <= 75


def test_a_skill_merely_mentioned_does_not_cap(scorer):
    """Citar nao e exigir: so o marcador torna o requisito eliminatorio."""
    descricao = "Gestor de SRE com AWS, Azure, Kubernetes, Terraform e Python."
    assert not scorer.score(PERFIL, vaga(), descricao=descricao).tetos


def test_another_career_track_caps_lower_than_a_missing_requirement(scorer):
    """Descasamento de trilha nao e descasamento de senioridade.

    Os dois sao cargos de gestao, entao nenhum componente do score captura a
    diferenca -- ela vem do pre-filtro, que marcou o card.
    """
    fora = vaga(titulo="Software Engineering Manager", blocker="trilha: engenharia de software")
    resultado = scorer.score(PERFIL, fora, descricao=DESCRICAO)
    assert resultado.tetos.get("outra_trilha") == 65
    assert resultado.score <= 65


def test_a_track_blocker_is_not_a_distance_penalty(scorer):
    """O campo `blocker` guarda dois assuntos, e confundi-los custava 25 pontos.

    Uma vaga do lado de casa perdia a penalidade de distancia inteira so
    porque o titulo era de outra trilha.
    """
    fora = vaga(titulo="Software Engineering Manager", blocker="trilha: engenharia de software")
    assert "fora_do_raio" not in scorer.score(PERFIL, fora, descricao=DESCRICAO).ajustes


def test_a_blocker_without_prefix_is_still_geographic(scorer):
    """Runs anteriores ao prefixo gravaram avisos de distancia sem marca.

    Exigir a marca reescreveria em silencio o score de um historico que existe
    justamente para ser comparavel.
    """
    distante = vaga(remoto=False, blocker="presencial em Porto Alegre, 846 km")
    assert "fora_do_raio" in scorer.score(PERFIL, distante, descricao=DESCRICAO).ajustes


def test_without_a_description_there_is_no_ceiling(scorer):
    """Nao se afirma que um requisito e imprescindivel a partir do titulo."""
    assert not scorer.score(PERFIL, vaga()).tetos


# ------------------------------------------ dois avisos de trilha, dois casos
def test_the_no_technology_ceiling_ignores_the_software_escape(scorer):
    """O escape do aviso de software nao pode valer para o outro aviso.

    Ele existe porque um gestor vindo de backend nao esta fora da trilha de uma
    vaga de backend. Aplicado ao aviso de "titulo sem termo de tecnologia", ele
    desligaria a regra para todo candidato cuja headline cite software -- que e
    quase todo candidato de tecnologia.
    """
    from crivo.pipeline.prefilter import BLOCKER_SEM_TECNOLOGIA

    perfil = {**PERFIL, "headline": "Gerente de Engenharia de Software"}
    b = scorer.score(
        perfil, vaga(blocker=BLOCKER_SEM_TECNOLOGIA), "Buscamos alguem.", [], FINAL
    )
    assert "outra_trilha" in b.tetos
    assert b.score <= b.tetos["outra_trilha"]


def test_the_software_ceiling_still_yields_to_a_matching_history(scorer):
    from crivo.pipeline.prefilter import BLOCKER_DE_SOFTWARE

    de_software = {**PERFIL, "headline": "Gerente de Engenharia de Software"}
    de_infra = {**PERFIL, "headline": "Gerente de Infraestrutura"}

    assert "outra_trilha" not in scorer.score(
        de_software, vaga(blocker=BLOCKER_DE_SOFTWARE), "Buscamos.", [], FINAL
    ).tetos
    assert "outra_trilha" in scorer.score(
        de_infra, vaga(blocker=BLOCKER_DE_SOFTWARE), "Buscamos.", [], FINAL
    ).tetos


def test_the_no_technology_ceiling_does_not_wait_for_a_description(scorer):
    """A evidencia deste teto e o titulo, e o titulo o pre-filtro ja leu.

    Enquanto ele esperava a descricao junto com `requisito_eliminatorio`, uma
    vaga que nunca seria enriquecida ficava sem teto nenhum: no run e49b12fe,
    51 vagas empataram na nota mais alta da passada provisoria, entre elas
    "Supervisor(a) de Turbinas" e "Gerente financeiro".
    """
    from crivo.pipeline.prefilter import BLOCKER_SEM_TECNOLOGIA

    sem_descricao = scorer.score(PERFIL, vaga(blocker=BLOCKER_SEM_TECNOLOGIA))
    assert sem_descricao.tetos.get("outra_trilha") == 40
    assert sem_descricao.score <= 40


def test_a_requirement_ceiling_still_waits_for_the_description(scorer):
    """O outro teto continua exigindo o texto do anuncio.

    Nao se afirma que um requisito e imprescindivel a partir do titulo, e a
    mudanca no teto de trilha nao afrouxa esta parte.
    """
    assert "requisito_eliminatorio" not in scorer.score(PERFIL, vaga()).tetos


def test_being_outside_technology_costs_more_than_an_ambiguous_title(scorer):
    """Os dois avisos de trilha nao pesam igual, e o mais forte vence.

    Um diz que o titulo nao revela qual familia da tecnologia; o outro diz que
    ele esta fora dela. Um teto proximo do topo para o segundo so trocava o
    lugar da vaga na mesma pagina.
    """
    from crivo.pipeline.prefilter import BLOCKER_DE_SOFTWARE, BLOCKER_SEM_TECNOLOGIA

    duvida = scorer.score(PERFIL, vaga(blocker=BLOCKER_DE_SOFTWARE), DESCRICAO)
    fora = scorer.score(PERFIL, vaga(blocker=BLOCKER_SEM_TECNOLOGIA), DESCRICAO)
    juntos = scorer.score(
        PERFIL,
        vaga(blocker=f"{BLOCKER_DE_SOFTWARE}; {BLOCKER_SEM_TECNOLOGIA}"),
        DESCRICAO,
    )

    assert fora.tetos["outra_trilha"] < duvida.tetos["outra_trilha"]
    assert juntos.tetos["outra_trilha"] == fora.tetos["outra_trilha"]


def test_both_track_warnings_can_sit_on_the_same_card(scorer):
    """O campo `blocker` guarda os avisos concatenados por ponto e virgula."""
    from crivo.pipeline.prefilter import (
        BLOCKER_DE_SOFTWARE, BLOCKER_SEM_TECNOLOGIA,
    )

    juntos = f"{BLOCKER_DE_SOFTWARE}; {BLOCKER_SEM_TECNOLOGIA}"
    perfil = {**PERFIL, "headline": "Gerente de Engenharia de Software"}
    assert "outra_trilha" in scorer.score(
        perfil, vaga(blocker=juntos), "Buscamos.", [], FINAL
    ).tetos


# ------------------------------------ ausencia de evidencia nao e evidencia
def test_a_language_never_recorded_does_not_cap_the_score(scorer):
    """Metade do relatorio travava em 75 por um campo nunca preenchido.

    A extracao devolve `idiomas` vazio com frequencia, e o teto entao afirmava
    "este candidato nao fala ingles" a partir de dado que ninguem coletou.
    Medido no run 2a292735: 121 das 249 vagas.
    """
    sem_idiomas = {**PERFIL, "idiomas": None, "competencias": ["kubernetes"]}
    b = scorer.score(
        sem_idiomas, vaga(), "Buscamos alguem com ingles fluente.", [], FINAL
    )
    assert "requisito_eliminatorio" not in b.tetos


def test_a_language_recorded_and_missing_still_caps_the_score(scorer):
    """O teto continua valendo quando a resposta existe e e negativa."""
    so_portugues = {**PERFIL, "idiomas": ["portugues"], "competencias": ["kubernetes"]}
    b = scorer.score(
        so_portugues, vaga(), "Buscamos alguem com ingles fluente.", [], FINAL
    )
    assert "requisito_eliminatorio" in b.tetos


def test_a_declared_language_counts_as_a_matched_skill(scorer):
    """Idioma vive em campo proprio, e so `competencias` era lido."""
    fluente = {**PERFIL, "idiomas": ["ingles"], "competencias": ["kubernetes"]}
    b = scorer.score(
        fluente, vaga(), "Buscamos alguem com ingles fluente.", [], FINAL
    )
    assert "requisito_eliminatorio" not in b.tetos


def test_a_mandatory_skill_still_caps_regardless_of_the_field_rule(scorer):
    """A regra vale so para termo com campo declarado; o resto segue igual."""
    perfil = {**PERFIL, "idiomas": ["ingles"], "competencias": ["kubernetes"]}
    b = scorer.score(
        perfil, vaga(), "Experiencia com Terraform e imprescindivel.", [], FINAL
    )
    assert "requisito_eliminatorio" in b.tetos
