"""Rascunho de pedido de indicacao, sob demanda e conferido."""

from __future__ import annotations

import json

from crivo.providers.client import Completion, DeterministicFallback, build_system
from crivo.report.indicacao import (
    TAREFA,
    montar_pedido,
    numeros_sem_origem,
    rascunhar,
)
from test_web_app import cliente  # noqa: F401 -- o ambiente web dos testes

PERFIL = {
    "nome": "Ana",
    "headline": "Tech Manager SRE",
    "experiencias": [{
        "titulo": "Tech Manager", "empresa": "PicPay", "inicio": "2024-05",
        "fim": None, "descricao": "Time de 9 pessoas; economia de US$ 257 mil/ano em FinOps",
    }],
    "competencias": ["Kubernetes", "FinOps"],
    "idiomas": ["Ingles avancado"],
}
VAGA = {"titulo": "Head of SRE", "empresa": "Acme", "descricao": "Lidere 3 squads de plataforma."}


class ClienteFalso:
    def __init__(self, texto="", erro=None):
        self.texto, self.erro, self.pedidos = texto, erro, []

    def assert_owner(self, user_id):
        assert user_id == "ana"

    def complete(self, task, payload, untrusted=True):
        self.pedidos.append((task, payload))
        if self.erro:
            raise self.erro
        return Completion(texto=self.texto, provedor="groq", modelo="m")


# ---------------------------------------------------------------- pedido
def test_the_request_carries_the_confirmed_profile_and_the_job():
    pedido = montar_pedido(PERFIL, VAGA)
    assert "Tech Manager na PicPay (2024-05 a atual)" in pedido
    assert "US$ 257 mil/ano" in pedido
    assert "Head of SRE" in pedido and "Acme" in pedido


def test_there_is_a_fixed_instruction_that_forbids_inventing():
    gabarito = build_system(TAREFA)
    assert "Nunca invente numero" in gabarito
    assert "[Nome]" in gabarito
    assert "150 palavras" in gabarito


# --------------------------------------------------------------- conferencia
def test_a_number_that_is_neither_in_the_profile_nor_the_job_is_pointed_out():
    """Modelo que inventa "reduzi custos em 40%" nao passa calado."""
    fonte = montar_pedido(PERFIL, VAGA)
    texto = "Liderei 9 pessoas, economizei US$ 257 mil e reduzi custos em 40%."
    assert numeros_sem_origem(texto, fonte) == ["40"]


def test_number_punctuation_does_not_matter():
    assert numeros_sem_origem("custo de 1196 reais", "R$ 1.196,00") == []


def test_the_draft_reports_what_it_could_not_ground():
    cliente = ClienteFalso(texto="Ola [Nome], liderei 12 pessoas na PicPay.")
    rascunho = rascunhar(cliente, "ana", PERFIL, VAGA)
    assert rascunho.numeros_sem_origem == ["12"]
    assert rascunho.provedor == "groq"
    assert cliente.pedidos[0][0] == TAREFA


def test_without_a_model_the_page_says_so_instead_of_failing():
    assert rascunhar(None, "ana", PERFIL, VAGA).falha
    caido = rascunhar(
        ClienteFalso(erro=DeterministicFallback("sobrecarga")), "ana", PERFIL, VAGA
    )
    assert caido.falha == "sobrecarga"


def test_a_long_draft_is_flagged_and_not_cut():
    rascunho = rascunhar(ClienteFalso(texto="palavra " * 180), "ana", PERFIL, VAGA)
    assert rascunho.longo_demais
    assert rascunho.palavras == 180


# ----------------------------------------------------------------- na web
def _vaga_no_run(banco, user_id, run_id):
    from crivo.store.repository import Repository

    repo = Repository(banco())
    repo.for_user(user_id).insert("jobs", {
        "job_id": "li-1", "titulo": "Head of SRE", "empresa": "Acme",
        "url": "https://linkedin.com/jobs/view/1", "estado": "novo", "flags": "[]",
        "primeira_vez_em": "2026-10-23", "ultima_vez_em": "2026-10-23",
    })
    repo.for_user(user_id).insert("scores", {
        "job_id": "li-1", "run_id": run_id, "passada": "final", "score": 60,
        "componentes": json.dumps({}), "criado_em": "2026-10-23",
    })


def _perfil(banco, user_id):
    from crivo.config import load_config
    from crivo.profile.merger import ProfileMerger

    ProfileMerger(banco(), load_config()).consolidate(user_id, resume_fields={
        **PERFIL, "localizacao": "Sao Paulo, SP", "formacao": ["CC"],
    })


def test_the_route_writes_a_draft_for_a_job_of_this_report(cliente, monkeypatch):
    from crivo.report import indicacao
    from test_web_app import _enfileirar, entrar

    c, _i, banco = cliente
    user_id = entrar(cliente)
    run_id = _enfileirar(banco, user_id)
    _vaga_no_run(banco, user_id, run_id)
    _perfil(banco, user_id)
    recebido = {}

    def falso(client, uid, perfil, vaga):
        recebido.update(uid=uid, vaga=vaga, perfil=perfil)
        return indicacao.Rascunho(
            texto="Ola [Nome] <script>x</script>", provedor="groq", palavras=3,
            numeros_sem_origem=["40"],
        )

    monkeypatch.setattr(indicacao, "rascunhar", falso)
    pagina = c.post(f"/reports/{run_id}/indicacao/li-1")
    assert pagina.status_code == 200
    assert recebido["uid"] == user_id and recebido["vaga"]["empresa"] == "Acme"
    assert "Ola [Nome]" in pagina.text
    assert "<script>x</script>" not in pagina.text, "o texto do modelo e escapado"
    assert "Confira estes números" in pagina.text and "40" in pagina.text


def test_a_job_outside_the_report_is_refused(cliente):
    from test_web_app import _enfileirar, entrar

    c, _i, banco = cliente
    user_id = entrar(cliente)
    run_id = _enfileirar(banco, user_id)
    assert c.post(f"/reports/{run_id}/indicacao/li-inexistente").status_code == 404


def test_a_get_does_not_generate(cliente):
    """Pre-carregamento do navegador nao pode gastar o modelo do usuario."""
    from test_web_app import _enfileirar, entrar

    c, _i, banco = cliente
    user_id = entrar(cliente)
    run_id = _enfileirar(banco, user_id)
    assert c.get(f"/reports/{run_id}/indicacao/li-1").status_code == 405


def test_the_report_offers_the_draft_only_where_it_recommends_a_referral():
    from test_report import _vaga

    from crivo.report.renderer import ReportContext, build_environment

    vagas = [
        _vaga(job_id="ind", acao="pedir indicação antes", acao_porque="fila longa"),
        _vaga(job_id="apl", acao="aplicar agora", acao_porque="nota alta"),
    ]
    html = build_environment().get_template("report.html.j2").render(ctx=ReportContext(
        run_id="r1", user_id="ana", gerado_em="2026-10-23T12:00:00+00:00",
        vagas=vagas,
    ))
    assert html.count("Rascunhar pedido de indicação") == 1
    assert 'action="/reports/r1/indicacao/ind"' in html
