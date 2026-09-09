import pytest

from crivo.config import load_config
from crivo.profile import hygiene
from crivo.profile.merger import HistoryRequired, ProfileMerger
from crivo.profile.seniority import (
    EXECUTIVE,
    LEAD,
    MANAGER,
    PLENO,
    SENIOR,
    infer,
    infer_from_experiences,
    most_recent,
)
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

EXPERIENCIAS = [
    {
        "titulo": "Gerente de Infraestrutura",
        "empresa": "Fintech",
        "inicio": "2020-01",
        "fim": None,
        "descricao": "Lidero time de SRE",
    },
    {
        "titulo": "Especialista de Cloud",
        "empresa": "Banco",
        "inicio": "2016-03",
        "fim": "2019-12",
        "descricao": "Plataforma e observabilidade",
    },
]


@pytest.fixture
def merger(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    Repository(connection).insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana", "criado_em": "2026-01-01"},
    )
    yield ProfileMerger(connection, load_config())
    connection.close()


# ------------------------------------------------------------------ nivel
@pytest.mark.parametrize(
    "titulo,esperado",
    [
        ("CTO", EXECUTIVE),
        ("Head de Infraestrutura", EXECUTIVE),
        ("Diretora de Engenharia", EXECUTIVE),
        ("Gerente de Plataforma", MANAGER),
        ("Coordenador de Cloud", MANAGER),
        ("Tech Lead", LEAD),
        ("Especialista de Cloud", LEAD),
        ("Engenheiro Senior", SENIOR),
        ("Analista de Sistemas", PLENO),
    ],
)
def test_the_title_decides_the_level(titulo, esperado):
    assert infer(titulo).nivel == esperado


def test_an_executive_title_containing_a_lower_word_stays_executive():
    assert infer("Head de Engenharia Senior").nivel == EXECUTIVE


def test_the_evidence_is_the_matched_text():
    resultado = infer("Gerente de Infraestrutura")
    assert resultado.evidencia.lower() == "gerente"
    assert resultado.origem == "titulo"


def test_leading_a_team_raises_a_lower_title():
    resultado = infer("Especialista de Cloud", "Lidero time de oito pessoas")
    assert resultado.nivel == MANAGER
    assert resultado.origem == "descricao"


def test_leading_a_team_does_not_lower_an_executive():
    assert infer("Diretor de Tecnologia", "Lidero time").nivel == EXECUTIVE


def test_an_absent_title_falls_back_to_the_default():
    assert infer(None).nivel == PLENO


def test_the_current_role_wins_over_an_older_start():
    recente = most_recent(EXPERIENCIAS)
    assert recente["titulo"] == "Gerente de Infraestrutura"


def test_without_a_current_role_the_latest_start_wins():
    passadas = [
        {"titulo": "A", "inicio": "2015-01", "fim": "2016-01"},
        {"titulo": "B", "inicio": "2018-01", "fim": "2020-01"},
    ]
    assert most_recent(passadas)["titulo"] == "B"


def test_inference_over_experiences_uses_the_most_recent():
    assert infer_from_experiences(EXPERIENCIAS).nivel == MANAGER


def test_inference_without_experiences_is_the_default():
    assert infer_from_experiences([]).nivel == PLENO


# ------------------------------------------------------------------ higiene
def test_no_problems_yields_an_empty_list_not_a_silence():
    assert hygiene.diagnose(EXPERIENCIAS) == []


def test_two_current_roles_are_flagged():
    duplicadas = [
        {"titulo": "A", "empresa": "X", "inicio": "2020-01", "fim": None},
        {"titulo": "B", "empresa": "Y", "inicio": "2021-01", "fim": None},
    ]
    problemas = hygiene.diagnose(duplicadas)
    assert [p.tipo for p in problemas] == [hygiene.CARGOS_ATUAIS_SIMULTANEOS]
    assert "A — X" in problemas[0].trecho and "B — Y" in problemas[0].trecho


def test_a_long_gap_is_flagged():
    comLacuna = [
        {"titulo": "A", "empresa": "X", "inicio": "2015-01", "fim": "2016-01"},
        {"titulo": "B", "empresa": "Y", "inicio": "2018-01", "fim": None},
    ]
    problemas = hygiene.diagnose(comLacuna)
    assert [p.tipo for p in problemas] == [hygiene.LACUNA_TEMPORAL]
    assert "24 meses" in problemas[0].detalhe


def test_a_short_gap_is_not_flagged():
    curta = [
        {"titulo": "A", "empresa": "X", "inicio": "2015-01", "fim": "2016-01"},
        {"titulo": "B", "empresa": "Y", "inicio": "2016-04", "fim": None},
    ]
    assert hygiene.diagnose(curta) == []


def test_an_overlap_is_flagged():
    sobrepostas = [
        {"titulo": "A", "empresa": "X", "inicio": "2015-01", "fim": "2018-01"},
        {"titulo": "B", "empresa": "Y", "inicio": "2017-01", "fim": "2019-01"},
    ]
    problemas = hygiene.diagnose(sobrepostas)
    assert [p.tipo for p in problemas] == [hygiene.SOBREPOSICAO]


def test_an_unreadable_period_is_flagged_with_the_offending_value():
    ruim = [{"titulo": "A", "empresa": "X", "inicio": "outono de 2015", "fim": None}]
    problemas = hygiene.diagnose(ruim)
    assert problemas[0].tipo == hygiene.PERIODO_ILEGIVEL
    assert "outono de 2015" in problemas[0].detalhe


def test_every_problem_carries_field_and_snippet():
    duplicadas = [
        {"titulo": "A", "empresa": "X", "inicio": "2020-01", "fim": None},
        {"titulo": "B", "empresa": "Y", "inicio": "2021-01", "fim": None},
    ]
    for problema in hygiene.diagnose(duplicadas):
        assert problema.campo and problema.trecho and problema.detalhe


def test_no_experiences_yields_no_problems():
    assert hygiene.diagnose(None) == []


# ------------------------------------------------------------------ merge
def test_the_resume_beats_the_linkedin_connection(merger):
    versao = merger.consolidate(
        "ana",
        resume_fields={"headline": "Do curriculo"},
        linkedin_fields={"headline": "Do linkedin", "nome": "Ana"},
    )
    assert versao.campos["headline"] == "Do curriculo"
    assert versao.origem_por_campo["headline"] == "resume"
    assert versao.origem_por_campo["nome"] == "linkedin"


def test_a_manual_edit_beats_the_resume(merger):
    versao = merger.consolidate(
        "ana",
        manual_fields={"headline": "Corrigido a mao"},
        resume_fields={"headline": "Do curriculo"},
    )
    assert versao.campos["headline"] == "Corrigido a mao"
    assert versao.origem_por_campo["headline"] == "manual"


def test_an_absent_field_has_no_origin(merger):
    versao = merger.consolidate("ana", resume_fields={"nome": "Ana"})
    assert versao.campos["idiomas"] is None
    assert "idiomas" not in versao.origem_por_campo


def test_each_consolidation_creates_a_new_immutable_version(merger):
    merger.consolidate("ana", resume_fields={"headline": "A"})
    merger.consolidate("ana", resume_fields={"headline": "B"})
    historico = merger.history("ana")
    assert [v.campos["headline"] for v in historico] == ["A", "B"]
    assert merger.current("ana").campos["headline"] == "B"


def test_the_level_is_inferred_into_the_version(merger):
    versao = merger.consolidate("ana", resume_fields={"experiencias": EXPERIENCIAS})
    assert versao.nivel_inferido == MANAGER


def test_hygiene_problems_land_in_the_version(merger):
    duplicadas = [
        {"titulo": "A", "empresa": "X", "inicio": "2020-01", "fim": None},
        {"titulo": "B", "empresa": "Y", "inicio": "2021-01", "fim": None},
    ]
    versao = merger.consolidate("ana", resume_fields={"experiencias": duplicadas})
    assert versao.problemas_higiene[0]["tipo"] == hygiene.CARGOS_ATUAIS_SIMULTANEOS


def test_the_current_version_is_reused_while_no_source_changed(merger):
    primeira = merger.consolidate("ana", resume_fields={"headline": "A"})
    reusada = merger.current_or_consolidate(
        "ana", ultima_alteracao="2020-01-01T00:00:00+00:00",
        resume_fields={"headline": "B"},
    )
    assert reusada.version_id == primeira.version_id


def test_a_newer_source_change_forces_a_new_version(merger):
    primeira = merger.consolidate("ana", resume_fields={"headline": "A"})
    nova = merger.current_or_consolidate(
        "ana", ultima_alteracao="2099-01-01T00:00:00+00:00",
        resume_fields={"headline": "B"},
    )
    assert nova.version_id != primeira.version_id
    assert nova.campos["headline"] == "B"


# ------------------------------------------------------------------ portao
def test_a_run_without_any_profile_is_refused(merger):
    with pytest.raises(HistoryRequired) as err:
        merger.require_for_run("ana")
    assert "importe um curriculo" in str(err.value)


def test_a_profile_without_history_is_refused(merger):
    merger.consolidate("ana", linkedin_fields={"nome": "Ana", "headline": "SRE"})
    with pytest.raises(HistoryRequired) as err:
        merger.require_for_run("ana")
    assert "historico profissional" in str(err.value)


def test_a_profile_with_history_passes_the_gate(merger):
    merger.consolidate("ana", resume_fields={"experiencias": EXPERIENCIAS})
    assert merger.require_for_run("ana").tem_historico is True


# --------------------------------------------------- leitura de periodo
def test_a_month_written_in_words_is_read_as_a_date():
    """Curriculo nao vem em ISO, e o extrator devolve o que estava escrito."""
    from crivo.profile import periodo

    assert periodo.meses_absolutos("Ago/2025") == periodo.meses_absolutos("2025-08")
    assert periodo.meses_absolutos("ago 2025") == periodo.meses_absolutos("2025-08")
    assert periodo.meses_absolutos("08/2025") == periodo.meses_absolutos("2025-08")
    assert periodo.meses_absolutos("2025") == periodo.meses_absolutos("2025-01")


def test_an_unreadable_period_stays_unreadable():
    """A tolerancia e sobre formato, nunca sobre inventar data."""
    from crivo.profile import periodo

    assert periodo.meses_absolutos("semestre passado") is None
    assert periodo.meses_absolutos("") is None
    assert periodo.meses_absolutos("13/2025") is None


def test_an_open_period_is_not_an_unreadable_one():
    from crivo.profile import periodo

    assert periodo.em_curso("atual") is True
    assert periodo.em_curso("Present") is True
    assert periodo.em_curso("Ago/2025") is False


def test_the_most_recent_role_is_chosen_by_date_and_not_alphabetically():
    """Ordenar `inicio` como texto elegia o cargo errado.

    Num curriculo real, "Out/2022" vencia "Ago/2025" porque O vem depois de A.
    O eleito foi uma associacao voluntaria ainda em curso, e o nivel do
    candidato caiu de gestor para pleno -- o que enviesou toda a busca para
    vagas abaixo do nivel dele.
    """
    from crivo.profile.seniority import MANAGER, infer_from_experiences, most_recent

    experiencias = [
        {"titulo": "Tech Manager - SRE", "inicio": "Ago/2025", "fim": None},
        {"titulo": "Community Member", "inicio": "Fev/2022", "fim": None},
        {"titulo": "Site Reliability Engineer", "inicio": "Out/2022", "fim": "Mai/2024"},
    ]
    assert most_recent(experiencias)["titulo"] == "Tech Manager - SRE"
    assert infer_from_experiences(experiencias).nivel == MANAGER


def test_a_role_ended_with_the_word_current_counts_as_ongoing():
    from crivo.profile.seniority import most_recent

    experiencias = [
        {"titulo": "Gerente", "inicio": "Jan/2024", "fim": "atual"},
        {"titulo": "Analista", "inicio": "Jan/2020", "fim": "Dez/2023"},
    ]
    assert most_recent(experiencias)["titulo"] == "Gerente"


def test_a_month_in_words_is_no_longer_reported_as_unreadable():
    from crivo.profile import hygiene

    problemas = hygiene.diagnose(
        [{"titulo": "Tech Manager", "inicio": "Ago/2025", "fim": None}]
    )
    assert not [p for p in problemas if p.tipo == hygiene.PERIODO_ILEGIVEL]
