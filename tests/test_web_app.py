"""Superficie HTTP: o que o usuario de fato alcanca.

Os testes por servico provam que a logica funciona. Estes provam que ela e
alcancavel, que a sessao guarda o acesso e que um usuario nao chega ao dado de
outro atravessando a camada web -- e nao apenas o repositorio.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crivo.config import load_config
from crivo.providers.client import Completion
from crivo.providers.vault import EnvelopeCipher, generate_master_key
from crivo.store.migrations import ThreadLocalDatabase
from crivo.web import app as web_app
from crivo.web.app import create_app
from crivo.web.auth import SESSION_COOKIE, Identity
from crivo.web.linkedin import LinkedInIdentity

TEXTO_LONGO = (
    "Gerente de Infraestrutura e Cloud com vinte anos de experiencia. "
    "Liderou times de SRE em fintech com Kubernetes e Terraform. "
) * 4


class FakeIdentity:
    name = "google"

    def __init__(self):
        self.subject = "sub-ana"

    def authorization_url(self, state, code_challenge, redirect_uri):
        return f"https://provedor/auth?state={state}"

    def exchange(self, code, code_verifier, redirect_uri):
        return Identity(subject=self.subject, email=f"{self.subject}@exemplo.br")


class FakeLinkedIn:
    name = "linkedin"

    def authorization_url(self, state, code_challenge, redirect_uri):
        return f"https://linkedin/auth?state={state}"

    def exchange(self, code, code_verifier, redirect_uri):
        return (
            LinkedInIdentity(
                subject="li-ana", campos={"nome": "Ana"},
                escopos=("email", "openid", "profile"),
            ),
            "testemunho",
            3600,
        )

    def revoke(self, token):
        pass


class FakeDrive:
    scope = "https://www.googleapis.com/auth/drive.file"

    def export_text(self, file_id, access_token):
        return TEXTO_LONGO


class FakeRouter:
    def complete(self, destinations, system, user):
        return Completion(
            texto=json.dumps({
                "nome": "Ana Souza", "headline": "Gerente de Infraestrutura",
                "localizacao": "Osasco, SP",
                "experiencias": [{"titulo": "Gerente de Infraestrutura",
                                  "empresa": "Fintech", "inicio": "2020-01",
                                  "fim": None, "descricao": "SRE"}],
                "competencias": ["Kubernetes"], "formacao": None, "idiomas": None,
                "trechos_origem": {"nome": "Ana Souza"},
            }, ensure_ascii=False),
            provedor=destinations[0].provider_id, modelo=destinations[0].modelo,
        )


@pytest.fixture
def cliente(tmp_path):
    banco = ThreadLocalDatabase(tmp_path / "crivo.db")
    identidade = FakeIdentity()
    app = create_app(
        load_config(), banco,
        identity_provider=identidade,
        linkedin_provider=FakeLinkedIn(),
        drive_client=FakeDrive(),
        cipher=EnvelopeCipher(generate_master_key()),
        router_factory=FakeRouter,
    )
    # O cookie de sessao e Secure: um cliente em http simplesmente nao o envia.
    # Falar https com o servidor de teste e o que torna a protecao exercitavel
    # em vez de contornada.
    with TestClient(app, base_url="https://testserver") as c:
        yield c, identidade, banco
    banco.close()


def entrar(cliente, subject="sub-ana"):
    c, identidade, _b = cliente
    identidade.subject = subject
    inicio = c.get("/auth/login", follow_redirects=False)
    state = inicio.headers["location"].split("state=")[1]
    # O retorno do provedor devolve o usuario ao produto: o 303 e seguido ate a
    # pagina inicial. Quem chega aqui esta num navegador, no meio de uma tarefa.
    retorno = c.get(f"/auth/callback?state={state}&code=codigo")
    assert retorno.status_code == 200
    assert retorno.url.path == "/"
    return c.get("/auth/me").json()["user_id"]


# ------------------------------------------------------------------ saude
def test_health_reports_the_pending_queue(cliente):
    c, _i, _b = cliente
    assert c.get("/health").json() == {"estado": "ok", "runs_enfileirados": 0}


def test_the_web_process_does_not_import_the_stage_runner():
    fonte = inspect.getsource(web_app)
    assert "worker.runner" not in fonte
    assert "Runner" not in fonte


# ------------------------------------------------------------ 5.63 guarda
@pytest.mark.parametrize(
    "metodo,rota",
    [
        ("get", "/auth/me"),
        ("get", "/profile"),
        ("get", "/runs"),
        ("post", "/runs"),
        ("get", "/reports/qualquer"),
        ("get", "/credentials"),
        ("get", "/resume/pending"),
        ("get", "/linkedin"),
    ],
)
def test_a_protected_route_refuses_without_a_valid_session(cliente, metodo, rota):
    c, _i, _b = cliente
    resposta = getattr(c, metodo)(rota)
    assert resposta.status_code == 401
    assert "sessao" in resposta.json()["detail"]


def test_an_invented_token_does_not_open_a_protected_route(cliente):
    c, _i, _b = cliente
    c.cookies.set(SESSION_COOKIE, "inventado")
    assert c.get("/profile").status_code == 401


# ------------------------------------------------------ 5.64 entrada e saida
def test_the_login_flow_works_over_http(cliente):
    c, _i, _b = cliente
    user_id = entrar(cliente)
    assert c.get("/auth/me").json() == {"user_id": user_id}


def test_the_session_cookie_carries_its_protections(cliente):
    c, _i, _b = cliente
    inicio = c.get("/auth/login", follow_redirects=False)
    state = inicio.headers["location"].split("state=")[1]
    resposta = c.get(
        f"/auth/callback?state={state}&code=codigo", follow_redirects=False
    )
    bruto = resposta.headers["set-cookie"].lower()
    assert "httponly" in bruto
    assert "secure" in bruto
    assert "samesite=lax" in bruto


def test_a_cancelled_authorization_returns_the_cause(cliente):
    """O motivo volta na pagina inicial, onde ha contexto para entende-lo."""
    c, _i, _b = cliente
    inicio = c.get("/auth/login", follow_redirects=False)
    state = inicio.headers["location"].split("state=")[1]
    resposta = c.get(f"/auth/callback?state={state}&error=access_denied")
    assert resposta.status_code == 200
    assert resposta.url.path == "/"
    assert "access_denied" in resposta.text
    assert "A autorização não foi concluída" in resposta.text


def test_a_hostile_message_from_the_provider_is_escaped(cliente):
    """O recado vem de terceiro e chega pela URL; o autoescape e o que protege."""
    c, _i, _b = cliente
    resposta = c.get("/?erro=<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in resposta.text
    assert "alert(1)" in resposta.text


def test_logging_out_closes_the_session_but_keeps_the_connection(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    c.get("/linkedin/connect", follow_redirects=False)
    inicio = c.get("/linkedin/connect", follow_redirects=False)
    state = inicio.headers["location"].split("state=")[1]
    c.get(f"/linkedin/callback?state={state}&code=codigo")
    assert c.get("/linkedin").json()["conectada"] is True

    assert c.post("/auth/logout").status_code == 200
    assert c.get("/auth/me").status_code == 401

    entrar(cliente)
    assert c.get("/linkedin").json()["conectada"] is True


# --------------------------------------------------- 5.65 isolamento por HTTP
def test_a_session_never_reaches_another_users_report(cliente):
    c, _i, banco = cliente
    ana = entrar(cliente, subject="sub-ana")
    from crivo.worker.queue import RunQueue

    run_da_ana = RunQueue(banco()).enqueue(ana)

    c.post("/auth/logout")
    entrar(cliente, subject="sub-bruno")
    assert c.get(f"/reports/{run_da_ana}").status_code == 404


def test_a_session_never_lists_another_users_runs(cliente):
    c, _i, banco = cliente
    ana = entrar(cliente, subject="sub-ana")
    from crivo.worker.queue import RunQueue

    RunQueue(banco()).enqueue(ana)
    assert len(c.get("/runs").json()["runs"]) == 1

    c.post("/auth/logout")
    entrar(cliente, subject="sub-bruno")
    assert c.get("/runs").json()["runs"] == []


def test_marking_another_users_job_is_refused(cliente):
    c, _i, banco = cliente
    ana = entrar(cliente, subject="sub-ana")
    from crivo.store.repository import Repository

    Repository(banco()).for_user(ana).insert(
        "jobs",
        {"job_id": "li-1", "titulo": "SRE", "url": "https://x", "estado": "novo",
         "primeira_vez_em": "2026-08-21", "ultima_vez_em": "2026-08-21"},
    )
    c.post("/auth/logout")
    entrar(cliente, subject="sub-bruno")
    resposta = c.patch("/runs/jobs/li-1", json={"estado": "aplicado"})
    assert resposta.status_code == 400


# ------------------------------------------------------------- credenciais
def test_the_credential_endpoints_never_return_the_value(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    criada = c.post("/credentials", json={"provedor": "groq", "chave": "gsk-segredo-1234"})
    assert criada.status_code == 200
    assert "gsk-segredo-1234" not in criada.text
    assert criada.json()["sufixo"] == "1234"

    listagem = c.get("/credentials")
    assert "gsk-segredo-1234" not in listagem.text


def test_the_offer_shows_limit_and_destination(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    corpo = c.get("/credentials/providers").json()
    assert any(p["destino_dos_dados"] for p in corpo["provedores"])


def test_the_disclosure_is_reachable_before_deciding(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    aviso = c.get("/credentials/providers/ollama-local/disclosure").json()["aviso"]
    assert "nao sai da maquina" in aviso


def test_an_unknown_provider_has_no_disclosure(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    assert c.get("/credentials/providers/inventado/disclosure").status_code == 404


# ------------------------------------------------------------- curriculo
def test_a_resume_can_be_imported_and_confirmed_over_http(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    c.post("/credentials", json={"provedor": "groq", "chave": "gsk-1234"})

    importado = c.post("/resume/drive", json={"file_id": "f", "access_token": "t"})
    assert importado.status_code == 200
    resume_id = importado.json()["resume_id"]

    extraida = c.post(f"/resume/{resume_id}/extract").json()
    assert extraida["confirmada"] is False
    assert c.get("/resume/confirmed").status_code == 404

    confirmada = c.post(f"/resume/extractions/{extraida['extraction_id']}/confirm")
    assert confirmada.json()["confirmada"] is True
    assert c.get("/resume/confirmed").status_code == 200


def test_a_correction_is_visible_without_erasing_the_extracted_value(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    c.post("/credentials", json={"provedor": "groq", "chave": "gsk-1234"})
    resume_id = c.post(
        "/resume/drive", json={"file_id": "f", "access_token": "t"}
    ).json()["resume_id"]
    extraida = c.post(f"/resume/{resume_id}/extract").json()

    corrigida = c.patch(
        f"/resume/extractions/{extraida['extraction_id']}",
        json={"campo": "headline", "valor": "Head de SRE"},
    ).json()
    assert corrigida["campos"]["headline"] == "Gerente de Infraestrutura"
    assert corrigida["efetivo"]["headline"] == "Head de SRE"


def test_without_a_credential_the_manual_path_is_offered(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    resume_id = c.post(
        "/resume/drive", json={"file_id": "f", "access_token": "t"}
    ).json()["resume_id"]
    corpo = c.post(f"/resume/{resume_id}/extract").json()
    assert corpo["preenchimento_manual"] is True


def test_an_unaccepted_upload_is_refused_with_the_accepted_formats(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    resposta = c.post(
        "/resume/upload", files={"arquivo": ("cv.odt", b"x" * 1000, "text/plain")}
    )
    assert resposta.status_code == 400
    assert "docx" in resposta.json()["erro"]


# ------------------------------------------------------------------ linkedin
def test_submitting_credential_material_is_refused_over_http(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    resposta = c.post("/linkedin/credentials", json={"li_at": "AQEDAT..."})
    assert resposta.status_code == 400
    assert "nao aceita senha" in resposta.json()["erro"]


def test_the_connection_reports_fields_unavailable_by_scope(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    inicio = c.get("/linkedin/connect", follow_redirects=False)
    state = inicio.headers["location"].split("state=")[1]
    # A ressalva chega na pagina, e nao num JSON que o usuario nao ia ler:
    # conectar o LinkedIn e nao receber o historico e justamente a surpresa
    # que precisa ser dita na cara.
    pagina = c.get(f"/linkedin/callback?state={state}&code=codigo")
    assert pagina.url.path == "/"
    assert "experiencias" in pagina.text
    assert "Conectado, com ressalva" in pagina.text
    # E o estado continua consultavel por quem quer o dado estruturado.
    assert "experiencias" in c.get("/linkedin").json()["campos_indisponiveis"]


# --------------------------------------------------------------- runs
def test_a_run_without_a_profile_is_refused_with_the_reason(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    resposta = c.post("/runs")
    assert resposta.status_code == 409
    assert "curriculo" in resposta.json()["erro"]


def _com_perfil(cliente):
    """Consolida um perfil com historico, que e o que o portao do run exige."""
    from crivo.profile.merger import ProfileMerger

    _c, _i, banco = cliente
    user_id = entrar(cliente)
    ProfileMerger(banco(), load_config()).consolidate(
        user_id,
        resume_fields={
            "nome": "Ana",
            "experiencias": [
                {"titulo": "Gerente", "empresa": "Acme",
                 "inicio": "2019-03", "fim": None, "descricao": "x"}
            ],
        },
    )
    return user_id


def _preferencia(cliente, user_id):
    from crivo.profile.merger import ProfileMerger

    _c, _i, banco = cliente
    return ProfileMerger(banco(), load_config()).current(user_id).campos[
        "dias_escritorio_max"
    ]


def test_the_office_preference_is_saved_kept_and_clearable(cliente):
    """O botao de reconsolidar nao carrega o seletor, e nao pode apagar a escolha."""
    c, _i, _banco = cliente
    user_id = _com_perfil(cliente)

    c.post("/profile/consolidate", data={"dias_escritorio_max": "1"})
    assert _preferencia(cliente, user_id) == 1

    c.post("/profile/consolidate")
    assert _preferencia(cliente, user_id) == 1

    # Zero e resposta -- "so remoto" --, e nao ausencia de resposta.
    c.post("/profile/consolidate", data={"dias_escritorio_max": "0"})
    assert _preferencia(cliente, user_id) == 0

    # Vazio e a unica forma de voltar a "sem preferencia".
    c.post("/profile/consolidate", data={"dias_escritorio_max": ""})
    assert _preferencia(cliente, user_id) is None


@pytest.mark.parametrize("alcance,horas", [("30d", 720), ("7d", 168), ("1d", 24)])
def test_the_reach_chosen_in_the_form_reaches_the_run(cliente, alcance, horas):
    """O radio da pagina precisa chegar ate a linha do run.

    O alcance estava fixo em 30 dias e nada na tela dizia isso. Um seletor que
    nao atravessa a rota seria pior do que nao ter seletor: a pagina passaria a
    afirmar uma escolha que o run ignora.
    """
    c, _i, banco = cliente
    _com_perfil(cliente)
    resposta = c.post(
        "/runs", data={"alcance": alcance}, headers={"Accept": "text/html"}
    )
    assert resposta.status_code in (200, 303)
    linha = banco().execute(
        "SELECT janela, janela_horas FROM runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert linha["janela_horas"] == horas
    # A janela continua `ampla`: e por ela que a cota diaria conta.
    assert linha["janela"] == "ampla"


def test_an_api_client_without_a_reach_keeps_the_previous_default(cliente):
    """Nenhum contrato de API muda por causa do seletor da tela."""
    c, _i, banco = cliente
    _com_perfil(cliente)
    assert c.post("/runs").status_code == 200
    linha = banco().execute(
        "SELECT janela_horas FROM runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert linha["janela_horas"] == load_config().collection.janela_ampla_horas


# ------------------------------------------------------------ 5.66 processos
def test_each_process_mode_is_resolvable():
    from crivo.__main__ import MODOS, StartupError, prepare

    ambiente = {
        "CRIVO_MASTER_KEY": "chave-mestra", "CRIVO_DATABASE_URL": "db.sqlite",
        "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "segredo",
    }
    for modo in MODOS:
        config, cofre = prepare(modo, "config/default.toml", ambiente)
        assert config is not None and cofre is not None


def test_an_unknown_mode_names_the_accepted_ones():
    from crivo.__main__ import MODOS, StartupError, prepare

    with pytest.raises(StartupError) as err:
        prepare("inventado", "config/default.toml", {})
    for modo in MODOS:
        assert modo in str(err.value)


def test_a_missing_secret_prevents_startup():
    from crivo.__main__ import StartupError, prepare

    with pytest.raises(StartupError) as err:
        prepare("web", "config/default.toml", {"CRIVO_DATABASE_URL": "db"})
    assert "GOOGLE_CLIENT_SECRET" in str(err.value)


def test_an_invalid_configuration_prevents_startup(tmp_path):
    from crivo.__main__ import StartupError, prepare

    # Configuracao completa com um unico valor fora da faixa: e a chave invalida
    # que precisa aparecer na mensagem, e nao a primeira secao que faltasse.
    base = Path("config/default.toml").read_text(encoding="utf-8")
    ruim = tmp_path / "config.toml"
    ruim.write_text(
        base.replace("limiar_destaque = 70", "limiar_destaque = 500"),
        encoding="utf-8",
    )
    with pytest.raises(StartupError) as err:
        prepare("schedule", str(ruim), {"CRIVO_DATABASE_URL": "db"})
    assert "limiar_destaque" in str(err.value)


# ----------------------------------------------------------------- insights
def _semear_vaga(banco, user_id, job_id="li-1"):
    """Vaga coletada e descrita, como estaria depois de um run."""
    from crivo.store.repository import Repository

    repo = Repository(banco())
    repo.for_user(user_id).insert(
        "jobs",
        {
            "job_id": job_id, "titulo": "SRE Manager",
            "url": f"https://linkedin.com/jobs/view/{job_id}", "estado": "novo",
            "flags": json.dumps(["early_applicant"]),
            "publicada_em": "2026-08-21", "primeira_vez_em": "2026-08-21",
            "ultima_vez_em": "2026-08-21",
        },
    )
    repo.insert(
        "job_descriptions",
        {"job_id": job_id, "texto": "Sobre a vaga", "coletada_em": "2026-08-21"},
    )


# ------------------------------------------------- filtro de vagas novas na URL
def _pontuar(banco, user_id, run_id, job_id, titulo="SRE Manager"):
    """Vaga pontuada num run, como o relatorio a encontraria."""
    from crivo.store.repository import Repository

    repo = Repository(banco())
    escopo = repo.for_user(user_id)
    if not escopo.select("jobs", where="job_id = ?", params=(job_id,)):
        escopo.insert(
            "jobs",
            {
                "job_id": job_id, "titulo": titulo,
                "url": f"https://linkedin.com/jobs/view/{job_id}",
                "estado": "novo", "flags": "[]", "publicada_em": "2026-08-21",
                "primeira_vez_em": "2026-08-21", "ultima_vez_em": "2026-08-21",
            },
        )
    escopo.insert(
        "scores",
        {
            "job_id": job_id, "run_id": run_id, "passada": "final", "score": 80,
            "componentes": json.dumps({"componentes": {}}), "lacunas": "[]",
            "diferenciais": "[]", "descricao_disponivel": 1,
            "sinais_sessao_disponiveis": 1, "criado_em": "2026-08-21",
        },
    )


def test_the_report_address_can_ask_for_new_jobs_only(cliente):
    """`?novas=1` precisa chegar ao filtro, e o nome do parametro e o acoplamento.

    O formulario manda `novas`, a rota le `novas` e o filtro entende `so_novas`.
    Sao tres nomes em tres arquivos, e uma divergencia entre eles nao quebra
    nada visivelmente: a pagina volta inteira, como se ninguem tivesse filtrado.
    """
    c, _i, banco = cliente
    user_id = entrar(cliente)
    from crivo.store.repository import Repository

    anterior = _enfileirar(banco, user_id)
    # Dois runs ativos nao existem para o mesmo usuario, e o de ontem ja acabou.
    Repository(banco()).for_user(user_id).update(
        "runs", {"estado": "concluido"}, where="run_id = ?", params=(anterior,)
    )
    atual = _enfileirar(banco, user_id)
    _pontuar(banco, user_id, anterior, "repetida", titulo="Analista SRE")
    _pontuar(banco, user_id, atual, "repetida", titulo="Analista SRE")
    _pontuar(banco, user_id, atual, "recem-chegada", titulo="Head de Plataforma")

    inteiro = c.get(f"/reports/{atual}").text
    assert "Analista SRE" in inteiro
    assert "Head de Plataforma" in inteiro

    so_novas = c.get(f"/reports/{atual}?novas=1").text
    assert "Head de Plataforma" in so_novas
    assert "Analista SRE" not in so_novas


def test_insights_from_the_browser_are_recorded(cliente):
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id)

    resposta = c.post(
        "/insights/li-1",
        json={"candidatos": 214, "senioridade": {"senior": 30, "gerencia": 8},
              "sinais": ["top_applicant"]},
    )
    assert resposta.status_code == 200
    gravado = resposta.json()["gravado"]
    assert gravado["candidatos"] == 214
    assert gravado["senioridade"] == {"senior": 30, "gerencia": 8}

    linha = banco().execute(
        "SELECT candidatos, distribuicao_senioridade FROM job_descriptions"
    ).fetchone()
    assert linha[0] == 214
    assert json.loads(linha[1]) == {"senior": 30, "gerencia": 8}


def test_signals_from_the_browser_join_the_ones_from_collection(cliente):
    """O envio acrescenta ao que a coleta ja sabia, em vez de substituir."""
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id)

    c.post("/insights/li-1", json={"sinais": ["top_applicant"]})
    flags = banco().execute(
        "SELECT flags FROM jobs WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    assert json.loads(flags) == ["early_applicant", "top_applicant"]


def test_an_unknown_signal_is_dropped_instead_of_scored(cliente):
    """Rotulo desconhecido nao pode virar categoria nova dentro do scorer."""
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id)

    resposta = c.post(
        "/insights/li-1",
        json={"sinais": ["top_applicant", "voce_e_perfeito_para_a_vaga"]},
    )
    assert resposta.json()["gravado"]["sinais"] == ["top_applicant"]


def test_an_envelope_with_nothing_recognizable_is_refused(cliente):
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id)

    resposta = c.post("/insights/li-1", json={"candidatos": "muitos"})
    assert resposta.status_code == 400
    assert "reconhecivel" in resposta.json()["erro"]


def test_insights_for_a_job_of_another_user_are_refused(cliente):
    c, _i, banco = cliente
    ana = entrar(cliente, "sub-ana")
    _semear_vaga(banco, ana, "li-da-ana")
    c.get("/auth/logout")
    entrar(cliente, "sub-bruno")

    resposta = c.post("/insights/li-da-ana", json={"candidatos": 10})
    assert resposta.status_code == 400
    assert banco().execute(
        "SELECT candidatos FROM job_descriptions"
    ).fetchone()[0] is None


def test_insights_require_a_session(cliente):
    c, _i, _b = cliente
    assert c.post("/insights/li-1", json={"candidatos": 10}).status_code == 401


# ------------------------------------------------- varredura da lista em lote
def test_a_sweep_records_every_card_at_once(cliente):
    """O aviso "You'd be a top applicant" mora no card, e um run traz ~100.

    Uma vaga por vez significaria abrir uma aba por card que ja estava na tela.
    """
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id, "li-1")
    _semear_vaga(banco, user_id, "li-2")

    resposta = c.post(
        "/insights/lote",
        json={"vagas": [
            {"job_id": "li-1", "sinais": ["top_applicant"]},
            {"job_id": "li-2", "sinais": ["muitos_candidatos"], "candidatos": 214},
        ]},
    )

    assert resposta.status_code == 200
    assert resposta.json()["gravadas"]["li-1"]["sinais"] == ["top_applicant"]
    assert resposta.json()["gravadas"]["li-2"]["candidatos"] == 214
    flags = dict(banco().execute(
        "SELECT job_id, flags FROM jobs WHERE user_id = ?", (user_id,)
    ).fetchall())
    assert "top_applicant" in json.loads(flags["li-1"])


def test_an_uncollected_card_is_skipped_instead_of_failing_the_sweep(cliente):
    """A varredura passa por tudo o que o LinkedIn mostrar.

    Recusar o lote inteiro porque um card nunca foi coletado jogaria fora a
    leitura de todos os outros, que e o unico momento em que o dado existe.
    """
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id, "li-1")

    resposta = c.post(
        "/insights/lote",
        json={"vagas": [
            {"job_id": "li-1", "sinais": ["top_applicant"]},
            {"job_id": "li-nunca-coletada", "sinais": ["top_applicant"]},
        ]},
    )

    corpo = resposta.json()
    assert resposta.status_code == 200
    assert list(corpo["gravadas"]) == ["li-1"]
    assert corpo["ignoradas"][0]["job_id"] == "li-nunca-coletada"


def test_a_sweep_collects_the_job_the_crivo_never_found(cliente):
    """A vaga que voce esta olhando quase nunca e uma que o crivo achou.

    Medido no banco real: das doze empresas visiveis numa varredura, UMA tinha
    vaga coletada -- as buscas do planejador sao focadas no Brasil e a navegacao
    nao e. Recusar a desconhecida fazia a varredura gravar nada, sem erro.
    """
    c, _i, banco = cliente
    user_id = entrar(cliente)

    corpo = c.post("/insights/lote", json={"vagas": [{
        "job_id": "li-9001",
        "sinais": ["top_applicant"],
        "vaga": {
            "titulo": "Engineering Manager, Infrastructure",
            "empresa": "Kikoff",
            "url": "https://www.linkedin.com/jobs/view/9001/",
        },
    }]}).json()

    assert corpo["gravadas"]["li-9001"]["sinais"] == ["top_applicant"]
    linha = banco().execute(
        "SELECT titulo, empresa, url, estado, busca, flags FROM jobs "
        "WHERE job_id = ?", ("li-9001",)
    ).fetchone()
    assert linha["titulo"] == "Engineering Manager, Infrastructure"
    assert linha["empresa"] == "Kikoff"
    assert linha["estado"] == "novo"
    # A origem fica gravada: quem le o relatorio precisa saber que esta veio da
    # navegacao e nao de uma busca do planejador.
    assert linha["busca"] == "extensao"
    assert json.loads(linha["flags"]) == ["top_applicant"]


def test_the_applicant_count_of_a_brand_new_job_is_not_lost(cliente):
    """Vaga criada pela navegacao nao tem linha de descricao ainda.

    O UPDATE acertava zero linhas e a rota respondia `{"candidatos": 100}`
    assim mesmo -- afirmando uma gravacao que nao aconteceu.
    """
    c, _i, banco = cliente
    entrar(cliente)

    corpo = c.post("/insights/lote", json={"vagas": [{
        "job_id": "li-9010", "candidatos": 214, "sinais": ["muitos_candidatos"],
        "senioridade": {"senior": 30},
        "vaga": {"titulo": "SRE Manager", "url": "https://exemplo/9010"},
    }]}).json()

    assert corpo["gravadas"]["li-9010"]["candidatos"] == 214
    linha = banco().execute(
        "SELECT candidatos, distribuicao_senioridade, texto FROM job_descriptions "
        "WHERE job_id = ?", ("li-9010",)
    ).fetchone()
    assert linha["candidatos"] == 214
    assert json.loads(linha["distribuicao_senioridade"]) == {"senior": 30}
    # Texto vazio marca que a linha existe pela concorrencia, e nao pela
    # descricao -- o enriquecimento a preenche depois sem apagar os numeros.
    assert linha["texto"] == ""


def test_a_card_without_title_or_url_is_still_refused(cliente):
    """Sem titulo nao ha o que pontuar; sem endereco nao ha como voltar a vaga."""
    c, _i, banco = cliente
    entrar(cliente)

    corpo = c.post("/insights/lote", json={"vagas": [
        {"job_id": "li-9002", "sinais": ["top_applicant"]},
        {"job_id": "li-9003", "sinais": ["top_applicant"],
         "vaga": {"titulo": "Sem endereco"}},
    ]}).json()

    assert corpo["gravadas"] == {}
    assert len(corpo["ignoradas"]) == 2
    assert banco().execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_a_known_job_is_not_recreated_by_a_sweep(cliente):
    """O envio traz os metadados sempre; eles so valem quando a vaga falta."""
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id, "li-1")

    c.post("/insights/lote", json={"vagas": [{
        "job_id": "li-1", "sinais": ["top_applicant"],
        "vaga": {"titulo": "Titulo do card", "url": "https://exemplo/1"},
    }]})

    linha = banco().execute(
        "SELECT titulo, busca FROM jobs WHERE job_id = ?", ("li-1",)
    ).fetchone()
    assert linha["titulo"] == "SRE Manager"      # o que a coleta gravou
    assert linha["busca"] != "extensao"


def test_a_sweep_of_another_users_jobs_records_nothing(cliente):
    c, _i, banco = cliente
    ana = entrar(cliente, "sub-ana")
    _semear_vaga(banco, ana, "li-da-ana")
    c.get("/auth/logout")
    entrar(cliente, "sub-bruno")

    corpo = c.post(
        "/insights/lote",
        json={"vagas": [{"job_id": "li-da-ana", "sinais": ["top_applicant"]}]},
    ).json()

    assert corpo["gravadas"] == {}
    assert json.loads(banco().execute(
        "SELECT flags FROM jobs WHERE job_id = ?", ("li-da-ana",)
    ).fetchone()[0]) == ["early_applicant"]


def test_an_empty_or_oversized_sweep_is_refused(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    assert c.post("/insights/lote", json={"vagas": []}).status_code == 400
    demais = [{"job_id": f"li-{n}"} for n in range(web_app.MAXIMO_DO_LOTE + 1)]
    assert c.post("/insights/lote", json={"vagas": demais}).status_code == 400


def test_a_sweep_requires_a_session(cliente):
    c, _i, _b = cliente
    resposta = c.post(
        "/insights/lote", json={"vagas": [{"job_id": "li-1", "sinais": ["top_applicant"]}]}
    )
    assert resposta.status_code == 401


# ------------------------------- token do extrator: a credencial que atravessa
def _token_do_extrator(banco, user_id):
    from crivo.store.extractor_tokens import ExtractorTokens

    con = banco()
    token = ExtractorTokens(con).issue(user_id)
    con.commit()
    return token


def test_a_sweep_authenticates_by_header_without_any_cookie(cliente):
    """O cookie de sessao nao chega aqui, e e por isso que o token existe.

    `samesite=lax` faz o navegador reter o cookie num POST cross-site, e a
    varredura roda dentro da pagina do LinkedIn. A rota tinha CORS e origem
    liberada e ainda assim era inalcancavel: 401 em toda tentativa.
    """
    from crivo.store.extractor_tokens import CABECALHO_DO_EXTRATOR

    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id, "li-1")
    token = _token_do_extrator(banco, user_id)
    c.get("/auth/logout")
    c.cookies.clear()

    resposta = c.post(
        "/insights/lote",
        json={"vagas": [{"job_id": "li-1", "sinais": ["top_applicant"]}]},
        headers={CABECALHO_DO_EXTRATOR: token},
    )

    assert resposta.status_code == 200
    assert resposta.json()["gravadas"]["li-1"]["sinais"] == ["top_applicant"]
    assert "top_applicant" in json.loads(banco().execute(
        "SELECT flags FROM jobs WHERE job_id = ?", ("li-1",)
    ).fetchone()[0])


def test_a_wrong_token_is_refused(cliente):
    from crivo.store.extractor_tokens import CABECALHO_DO_EXTRATOR

    c, _i, _b = cliente
    resposta = c.post(
        "/insights/lote",
        json={"vagas": [{"job_id": "li-1"}]},
        headers={CABECALHO_DO_EXTRATOR: "nao-e-o-token"},
    )
    assert resposta.status_code == 401


def test_the_extractor_token_opens_nothing_but_the_insight_routes(cliente):
    """O escopo e metade do desenho. Vazado, ele mente sobre candidatos --
    nao le relatorio, perfil nem credencial de provedor.
    """
    from crivo.store.extractor_tokens import CABECALHO_DO_EXTRATOR

    c, _i, banco = cliente
    user_id = entrar(cliente)
    token = _token_do_extrator(banco, user_id)
    c.get("/auth/logout")
    c.cookies.clear()

    cabecalho = {CABECALHO_DO_EXTRATOR: token}
    for rota in ("/runs", "/auth/me", "/profile"):
        assert c.get(rota, headers=cabecalho).status_code in (401, 302, 303), rota


def test_the_preflight_announces_the_token_header(cliente):
    """Cabecalho fora da lista segura so e enviado se o preflight o nomear."""
    from crivo.store.extractor_tokens import CABECALHO_DO_EXTRATOR

    c, _i, _b = cliente
    permitidos = c.options("/insights/lote").headers["access-control-allow-headers"]
    assert CABECALHO_DO_EXTRATOR in permitidos.lower()


def test_issuing_a_token_invalidates_the_previous_one(cliente):
    """Um token por usuario torna "gerar de novo" um ato com consequencia."""
    from crivo.store.extractor_tokens import CABECALHO_DO_EXTRATOR

    c, _i, banco = cliente
    user_id = entrar(cliente)
    _semear_vaga(banco, user_id, "li-1")
    velho = _token_do_extrator(banco, user_id)
    novo = _token_do_extrator(banco, user_id)

    c.get("/auth/logout")
    c.cookies.clear()
    corpo = {"vagas": [{"job_id": "li-1", "sinais": ["top_applicant"]}]}
    assert c.post("/insights/lote", json=corpo,
                  headers={CABECALHO_DO_EXTRATOR: velho}).status_code == 401
    assert c.post("/insights/lote", json=corpo,
                  headers={CABECALHO_DO_EXTRATOR: novo}).status_code == 200


def test_the_extension_page_issues_a_token_only_by_post(cliente):
    """GET que muda estado seria disparado por pre-carregamento do navegador --
    derrubando a extensao de quem so abriu a pagina para conferir.
    """
    c, _i, banco = cliente
    user_id = entrar(cliente)

    pagina = c.get("/extensao")
    assert pagina.status_code == 200
    assert "Nenhum token emitido" in pagina.text
    assert banco().execute("SELECT COUNT(*) FROM extractor_tokens").fetchone()[0] == 0

    emitida = c.post("/extensao/token")
    assert emitida.status_code == 200
    assert banco().execute("SELECT COUNT(*) FROM extractor_tokens").fetchone()[0] == 1


def test_the_extension_page_requires_a_session(cliente):
    c, _i, _b = cliente
    assert c.get("/extensao", follow_redirects=False).status_code in (401, 302, 303)


def test_the_sweep_route_carries_the_same_origin_allowance(cliente):
    """Ela e chamada do mesmo lugar que a de uma vaga: a pagina do LinkedIn."""
    c, _i, _b = cliente
    resposta = c.options("/insights/lote")
    assert resposta.status_code == 204
    assert (
        resposta.headers["access-control-allow-origin"] == web_app.ORIGEM_DO_EXTRATOR
    )


def test_only_the_extractor_origin_may_reach_the_insights_route(cliente):
    """A liberacao e nominal: outra pagina nao fala com esta aplicacao."""
    c, _i, _b = cliente
    resposta = c.options("/insights/li-1")
    assert resposta.status_code == 204
    assert (
        resposta.headers["access-control-allow-origin"]
        == web_app.ORIGEM_DO_EXTRATOR
    )


def test_the_broad_origin_is_never_allowed(cliente):
    c, _i, _b = cliente
    liberado = c.options("/insights/li-1").headers["access-control-allow-origin"]
    assert liberado != "*"


def test_other_routes_do_not_carry_the_origin_allowance(cliente):
    """Liberar a aplicacao inteira daria a origem externa acesso a relatorio."""
    c, _i, _b = cliente
    entrar(cliente)
    assert "access-control-allow-origin" not in c.get("/runs").headers


def test_the_web_process_does_not_import_the_worker_processes():
    """A face web enfileira trabalho; ela nao importa quem o executa.

    A fila e uma estrutura no banco e a face web escreve nela por desenho. O
    que ela nao pode e alcancar os processos que consomem essa fila -- inclusive
    para ler uma constante deles. A chave da trava do enriquecedor mora na
    camada de store, que e onde a tabela mora.
    """
    fonte = inspect.getsource(web_app)
    assert "enricher_process" not in fonte
    assert "EnricherProcess" not in fonte


# ------------------------------------------------------------ porta de entrada
def test_the_root_address_answers_instead_of_404(cliente):
    """Houve um periodo em que `/` devolvia 404 com todo o resto de pe.

    Servico satisfeito, produto inalcancavel: quem abria o endereco no
    navegador via `{"detail":"Not Found"}`.
    """
    c, _i, _b = cliente
    resposta = c.get("/")
    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]


def test_the_entry_page_does_not_require_a_session(cliente):
    """Quem ainda nao entrou precisa de um lugar de onde entrar."""
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "Google — sua sessão" in pagina
    assert "pendente" in pagina


def test_the_entry_page_names_the_logged_user(cliente):
    c, _i, _b = cliente
    user_id = entrar(cliente)
    assert user_id in c.get("/").text


def test_the_entry_page_warns_that_example_credentials_will_fail(cliente, monkeypatch):
    """Dizer isso antes vale mais que deixar o usuario descobrir no callback."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "placeholder-sem-app-registrado")
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "um valor de exemplo" in pagina
    assert "Acesso bloqueado" in pagina


def test_the_entry_page_is_quiet_when_credentials_are_real(cliente, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "8341.apps.googleusercontent.com")
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "vai falhar" not in pagina
    assert "/auth/login" in pagina


def test_the_entry_page_says_the_enricher_is_down(cliente):
    """Sem esse processo os runs param antes de buscar descricao."""
    c, _i, _b = cliente
    assert "não está de pé" in c.get("/").text


def _travar_enriquecedor(banco, carimbo: str) -> None:
    from crivo.store.locks import CHAVE_DO_ENRIQUECEDOR
    from crivo.store.repository import Repository

    Repository(banco()).insert(
        "schema_meta",
        {"chave": CHAVE_DO_ENRIQUECEDOR, "valor": "1", "aplicada_em": carimbo},
    )


def test_the_entry_page_sees_the_enricher_when_it_holds_the_lock(cliente):
    from datetime import datetime, timezone

    c, _i, banco = cliente
    _travar_enriquecedor(
        banco, datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    assert "não está de pé" not in c.get("/").text


def test_a_lock_left_behind_by_a_dead_enricher_does_not_count_as_up(cliente):
    """Trava existente nao e o mesmo que processo vivo.

    Um enriquecedor derrubado a forca deixa a linha para tras. Enquanto a
    pagina lia so a existencia dela, ela afirmava que a leitura de vagas estava
    de pe -- e o usuario esperava por um processo que ninguem ia subir, porque
    a propria tela dizia que ele ja estava rodando.
    """
    c, _i, banco = cliente
    _travar_enriquecedor(banco, "2026-08-21T00:00:00+00:00")
    assert "não está de pé" in c.get("/").text


def test_hostile_text_never_reaches_the_entry_page_unescaped(cliente, monkeypatch):
    """A pagina renderiza estado do banco; autoescape vale aqui como no relatorio."""
    c, _i, _b = cliente
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in c.get("/").text


def test_the_entry_page_shows_both_authorizations(cliente):
    """Sao duas autorizacoes distintas, e a segunda depende da primeira."""
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "Google — sua sessão" in pagina
    assert "LinkedIn — origem de perfil" in pagina


def test_both_buttons_are_on_the_page_before_anything_is_configured(cliente):
    """Esconder o botao que vai falhar tirava da pagina a acao principal dela.

    Uma pagina de entrada sem botao de entrar le como quebrada. O problema real
    nao e a pessoa clicar: e ela nao saber por que falhou. Isso o aviso resolve,
    e o botao ausente nao resolvia.
    """
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "Entrar com Google" in pagina
    assert "/auth/login" in pagina
    assert "Conectar LinkedIn" in pagina


def test_the_linkedin_button_has_no_destination_without_a_session(cliente):
    """Visivel para que se saiba que existe; sem destino para nao dar 401."""
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "Conectar LinkedIn — entre primeiro" in pagina
    assert "/linkedin/connect" not in pagina


def test_the_linkedin_step_is_gated_on_the_session(cliente):
    """A pagina diz que a segunda autorizacao depende da primeira.

    A afirmacao vinha na forma "exige o passo 1", presa a uma numeracao que a
    pagina nao tem mais: a sequencia 1, 2, 3, cartao sem numero, 4 prometia uma
    linearidade que um painel de estado nao cumpre. O que importa e o gate
    continuar declarado, e nao a redacao dele.
    """
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "exige o passo anterior" in pagina
    assert "Conectar LinkedIn — entre primeiro" in pagina


def test_the_linkedin_step_offers_to_connect_once_there_is_a_session(
    cliente, monkeypatch
):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "77xk.linkedin.real")
    c, _i, _b = cliente
    entrar(cliente)
    assert "/linkedin/connect" in c.get("/").text


def test_the_page_says_linkedin_does_not_bring_work_history(cliente):
    """Sem isto o usuario conecta esperando o historico dele e nao recebe nada.

    Os escopos abertos sem parceria comercial devolvem identidade e e-mail. O
    que alimenta a busca e o curriculo, e a pagina precisa dizer isso antes da
    conexao e nao depois.
    """
    c, _i, _b = cliente
    pagina = c.get("/").text
    assert "origem de identidade" in pagina
    assert "não" in pagina and "competências" in pagina


def test_a_broken_linkedin_configuration_does_not_break_the_entry_page(cliente):
    """A pagina inicial e onde o usuario descobre que algo esta mal configurado.

    Ela nao pode ser a coisa que quebra por causa disso.
    """
    c, _i, _b = cliente
    entrar(cliente)

    def explode(*_a, **_k):
        raise RuntimeError("cofre indisponivel")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(web_app.WebContext, "linkedin_connector", explode)
    try:
        assert c.get("/").status_code == 200
    finally:
        monkey.undo()


# ----------------------------------------------------------- entrada local
def test_the_local_login_route_does_not_exist_by_default(cliente):
    """Nao fica desabilitada: nao e registrada.

    Uma rota de autenticacao registrada e depois recusada ficaria publicada no
    `/docs`, o que e um convite a tentativa.
    """
    c, _i, _b = cliente
    assert c.get("/auth/local").status_code == 404
    assert "/auth/local" not in c.get("/openapi.json").text


def test_the_local_login_button_is_absent_by_default(cliente):
    c, _i, _b = cliente
    assert "Entrar sem provedor" not in c.get("/").text


def test_the_environment_variable_decides(monkeypatch):
    from crivo.web.auth import VARIAVEL_DE_ENTRADA_LOCAL, entrada_local_ligada

    assert entrada_local_ligada({}) is False
    assert entrada_local_ligada({VARIAVEL_DE_ENTRADA_LOCAL: "0"}) is False
    assert entrada_local_ligada({VARIAVEL_DE_ENTRADA_LOCAL: ""}) is False
    assert entrada_local_ligada({VARIAVEL_DE_ENTRADA_LOCAL: "1"}) is True
    assert entrada_local_ligada({VARIAVEL_DE_ENTRADA_LOCAL: "TRUE"}) is True


@pytest.fixture
def cliente_local(tmp_path, monkeypatch):
    monkeypatch.setenv("CRIVO_DEV_LOGIN", "1")
    banco = ThreadLocalDatabase(tmp_path / "crivo.db")
    app = create_app(
        load_config(), banco,
        identity_provider=FakeIdentity(),
        linkedin_provider=FakeLinkedIn(),
        drive_client=FakeDrive(),
        cipher=EnvelopeCipher(generate_master_key()),
        router_factory=FakeRouter,
    )
    with TestClient(app, base_url="https://testserver") as c:
        yield c, banco
    banco.close()


def test_the_local_login_opens_a_real_session(cliente_local):
    """Uma sessao que fosse caso especial exercitaria codigo que nao e o de
    producao, e derrotaria o proposito de usa-la para testar o produto."""
    c, _b = cliente_local
    resposta = c.get("/auth/local")
    assert resposta.status_code == 200
    assert resposta.json()["user_id"]
    # A prova: uma rota protegida passa a responder.
    assert c.get("/runs").status_code == 200


def test_the_local_session_says_what_it_is(cliente_local):
    c, _b = cliente_local
    assert "desenvolvimento" in c.get("/auth/local").json()["aviso"]


def test_the_local_login_button_appears_when_enabled(cliente_local):
    c, _b = cliente_local
    assert "Entrar sem provedor" in c.get("/").text
    assert "Desligue antes de expor" in c.get("/").text


def test_the_local_user_is_marked_in_the_database(cliente_local):
    """Um banco que passou por aqui sempre pode ser distinguido."""
    from crivo.web.auth import SUBJECT_LOCAL

    c, banco = cliente_local
    c.get("/auth/local")
    assunto = banco().execute(
        "SELECT subject_google FROM users"
    ).fetchone()[0]
    assert assunto == SUBJECT_LOCAL


def test_logging_in_twice_locally_reuses_the_same_user(cliente_local):
    c, banco = cliente_local
    primeiro = c.get("/auth/local").json()["user_id"]
    segundo = c.get("/auth/local").json()["user_id"]
    assert primeiro == segundo
    assert banco().execute("SELECT count(*) FROM users").fetchone()[0] == 1


# ------------------------------------------ origem declarada e nao ligada
@pytest.fixture
def cliente_sem_linkedin(tmp_path):
    """O processo real subia assim: a rota viva e nenhum provedor atras dela."""
    banco = ThreadLocalDatabase(tmp_path / "crivo.db")
    app = create_app(
        load_config(), banco,
        identity_provider=FakeIdentity(),
        linkedin_provider=None,
        drive_client=FakeDrive(),
        cipher=EnvelopeCipher(generate_master_key()),
        router_factory=FakeRouter,
    )
    with TestClient(app, base_url="https://testserver") as c:
        yield c, banco
    banco.close()


def _entrar_direto(c):
    inicio = c.get("/auth/login", follow_redirects=False)
    state = inicio.headers["location"].split("state=")[1]
    c.get(f"/auth/callback?state={state}&code=codigo")


def test_connecting_without_a_provider_explains_instead_of_crashing(
    cliente_sem_linkedin,
):
    """Era um 500 com AttributeError: 'NoneType' object has no attribute 'name'.

    Os testes nao pegavam porque todos injetam um provedor falso. O processo
    real montava a aplicacao sem nenhum, entao a rota existia e nao tinha nada
    atras dela.
    """
    c, _b = cliente_sem_linkedin
    _entrar_direto(c)
    resposta = c.get("/linkedin/connect")
    assert resposta.status_code == 200
    assert resposta.url.path == "/"
    assert "nao configurada" in resposta.text
    assert "LINKEDIN_CLIENT_ID" in resposta.text
    assert "A autorização não foi concluída" in resposta.text


def test_the_entry_page_still_works_without_a_linkedin_provider(
    cliente_sem_linkedin,
):
    c, _b = cliente_sem_linkedin
    _entrar_direto(c)
    assert c.get("/").status_code == 200


def test_the_real_process_wires_every_port_it_declares():
    """A aplicacao de producao precisa passar as origens que as rotas usam.

    O defeito nao estava na rota nem no conector: estava na montagem. Nenhum
    teste de servico podia pega-lo, porque nenhum monta a aplicacao do jeito
    que o processo real monta.
    """
    import inspect

    from crivo import __main__ as processo

    fonte = inspect.getsource(processo)
    assert "linkedin_provider=" in fonte
    assert "identity_provider=" in fonte
    assert "cipher=" in fonte


# ------------------------------------------------------------ sair da sessao
def test_the_page_offers_a_way_out_when_there_is_a_session(cliente):
    """A rota de saida existia e nao era alcancavel: e POST, e link nao faz POST.

    Nao havia forma de encerrar sessao pela interface.
    """
    c, _i, _b = cliente
    entrar(cliente)
    pagina = c.get("/").text
    assert 'action="/auth/logout"' in pagina
    assert "Sair" in pagina


def test_there_is_nothing_to_leave_without_a_session(cliente):
    c, _i, _b = cliente
    assert 'action="/auth/logout"' not in c.get("/").text


def test_leaving_returns_to_the_page_without_a_session(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    resposta = c.post("/auth/logout")
    assert resposta.status_code == 200
    assert resposta.url.path == "/"
    assert c.get("/auth/me").status_code == 401


def test_a_local_session_can_still_be_traded_for_a_real_one(cliente_local):
    """Entrar localmente nao pode ser um beco: o cartao do Google sumia."""
    c, _b = cliente_local
    c.get("/auth/local")
    pagina = c.get("/").text
    assert "sessão local de desenvolvimento" in pagina
    assert "/auth/login" in pagina
    assert 'action="/auth/logout"' in pagina


def test_a_real_session_is_not_announced_as_local(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    assert "sessão local de desenvolvimento" not in c.get("/").text


# ------------------------------------------------------- cancelar a busca
def _enfileirar(banco, user_id):
    from crivo.worker.queue import RunQueue

    return RunQueue(banco()).enqueue(user_id, janela="ampla")


def test_the_cancel_button_appears_while_a_run_is_in_flight(cliente):
    c, _i, banco = cliente
    user_id = entrar(cliente)
    _enfileirar(banco, user_id)
    assert "Cancelar esta busca" in c.get("/").text


def test_there_is_no_cancel_button_when_nothing_is_running(cliente):
    c, _i, _b = cliente
    entrar(cliente)
    assert "Cancelar esta busca" not in c.get("/").text


def test_cancelling_records_the_request_without_ending_the_run(cliente):
    """A face web nao encerra o run: ela pede, e quem executa atende.

    Encerra-lo daqui deixaria requisicao pela metade no processo que esta
    coletando, e ele voltaria em seguida gravando sobre um run que esta pagina
    ja considera morto.
    """
    from crivo.worker.queue import RunQueue

    c, _i, banco = cliente
    user_id = entrar(cliente)
    run_id = _enfileirar(banco, user_id)

    # `Accept: text/html` e o que um navegador manda ao enviar o formulario, e
    # e por ele que a rota escolhe redirecionar em vez de responder JSON.
    resposta = c.post(
        f"/runs/{run_id}/cancelar",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert resposta.status_code == 303
    fila = RunQueue(banco())
    assert fila.cancel_requested(run_id) is True
    assert fila.get(run_id)["estado"] == "enfileirado"


def test_after_asking_to_stop_the_page_says_so_instead_of_offering_again(cliente):
    """Clicar de novo achando que o primeiro clique se perdeu e o defeito."""
    c, _i, banco = cliente
    user_id = entrar(cliente)
    run_id = _enfileirar(banco, user_id)
    c.post(f"/runs/{run_id}/cancelar", follow_redirects=False)

    pagina = c.get("/").text
    assert "Parada pedida" in pagina
    assert "Cancelar esta busca" not in pagina


def test_cancelling_a_run_that_already_ended_is_refused(cliente):
    from crivo.worker.queue import RunQueue

    c, _i, banco = cliente
    user_id = entrar(cliente)
    run_id = _enfileirar(banco, user_id)
    RunQueue(banco()).finish(run_id)

    resposta = c.post(
        f"/runs/{run_id}/cancelar",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert resposta.status_code == 409


def test_one_user_cannot_cancel_another_users_run(cliente):
    """Sem o dono na condicao, adivinhar o identificador bastaria."""
    from crivo.worker.queue import RunQueue

    c, _i, banco = cliente
    outro = entrar(cliente, subject="sub-bruno")
    run_id = _enfileirar(banco, outro)
    c.post("/auth/logout")
    entrar(cliente, subject="sub-ana")

    resposta = c.post(
        f"/runs/{run_id}/cancelar",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert resposta.status_code == 409
    assert RunQueue(banco()).cancel_requested(run_id) is False


# ------------------------------------------------------ encerrar com Ctrl+C
def test_an_interrupt_exits_quietly_naming_the_process(capsys, monkeypatch):
    """Ctrl+C nao e defeito, e nao pode se parecer com um.

    Parar o servidor imprimia um traceback de `KeyboardInterrupt` -- no modo
    `tudo`, tres deles entrelacados na mesma janela, porque os processos
    escrevem juntos. O encerramento ja estava correto; o que estava errado era
    o que a tela dizia, e neste projeto traceback treinou o usuario a procurar
    defeito.
    """
    from crivo import __main__ as cli

    def interromper(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_executar", interromper)
    codigo = cli.main(["enricher", "--config", "config/default.toml"])

    assert codigo == cli.SAIDA_POR_INTERRUPCAO
    saida = capsys.readouterr().err
    assert "[enricher] encerrado." in saida
    assert "Traceback" not in saida
    assert "KeyboardInterrupt" not in saida


def test_the_interrupt_exit_code_is_the_conventional_one():
    """128 mais o numero do sinal. Script que chame o crivo conta com isso."""
    from crivo.__main__ import SAIDA_POR_INTERRUPCAO

    assert SAIDA_POR_INTERRUPCAO == 130


def test_an_interrupted_enricher_releases_its_lock(tmp_path):
    """A trava sobrevive ao processo se o `finally` nao rodar.

    Ela e o que impede dois enriquecedores, e um Ctrl+C que a deixasse para
    tras faria a partida seguinte recusar-se a subir citando um concorrente que
    nao existe -- e a saida seria apagar a linha na mao.
    """
    from crivo.pipeline.governor import RateGovernor
    from crivo.store.locks import CHAVE_DO_ENRIQUECEDOR, enricher_ativo
    from crivo.store.migrations import open_database
    from crivo.store.repository import Repository
    from crivo.worker.enricher_process import EnricherProcess

    connection = open_database(tmp_path / "crivo.db")
    processo = EnricherProcess(connection, load_config(), object())
    processo._governor = RateGovernor(connection, load_config(), sleep=lambda _s: None)

    def parar(_s):
        raise KeyboardInterrupt

    processo._dormir = parar
    with pytest.raises(KeyboardInterrupt):
        processo.serve_forever(ciclos=1)

    assert not enricher_ativo(Repository(connection))
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_meta WHERE chave = ?", (CHAVE_DO_ENRIQUECEDOR,)
    ).fetchone()[0] == 0
    connection.close()


# ------------------------------------------ derrubar sem atropelar a limpeza
class ProcessoFalso:
    """Imita `Popen` o bastante para exercitar a ordem do encerramento."""

    def __init__(self, saidas_ate_encerrar=1):
        self._restantes = saidas_ate_encerrar
        self.terminado = False
        self.morto = False

    def poll(self):
        return None if self._restantes > 0 else 0

    def wait(self, timeout=None):
        if self._restantes > 0:
            self._restantes -= 1
        if self._restantes > 0:
            raise TimeoutError("ainda vivo")
        return 0

    def terminate(self):
        self.terminado = True
        self._restantes = 0

    def kill(self):
        self.morto = True
        self._restantes = 0


def test_a_companion_that_exits_on_its_own_is_never_terminated():
    """`terminate` no Windows e `TerminateProcess`: nao roda `finally` nenhum.

    O Ctrl+C do console ja alcancou os tres processos, e cada um esta soltando a
    propria trava de instancia. Terminar antes de esperar corria contra isso, e
    quando ganhava a corrida o enriquecedor morria segurando a trava -- que so
    e considerada abandonada apos quinze minutos.
    """
    from crivo.__main__ import _derrubar

    limpo = ProcessoFalso(saidas_ate_encerrar=1)
    _derrubar([limpo], prazo=0.1)

    assert limpo.terminado is False
    assert limpo.morto is False


def test_a_companion_that_hangs_is_still_terminated():
    """Nao correr contra a saida limpa nao e esperar para sempre."""
    from crivo.__main__ import _derrubar

    travado = ProcessoFalso(saidas_ate_encerrar=99)
    _derrubar([travado], prazo=0.1)

    assert travado.terminado is True


def test_a_companion_already_gone_is_left_alone():
    from crivo.__main__ import _derrubar

    morto = ProcessoFalso(saidas_ate_encerrar=0)
    _derrubar([morto], prazo=0.1)

    assert morto.terminado is False
    assert morto.morto is False


def _semear_historico_ruim(conexao, user_id, termo, quantas=8):
    """Grava vagas de um termo, todas marcadas como de outra trilha."""
    from crivo.store.repository import Repository

    escopo = Repository(conexao).for_user(user_id)
    conexao.execute(
        "INSERT INTO runs (run_id, user_id, estado, janela, solicitado_em) "
        "VALUES ('velho', ?, 'concluido', 'ampla', '2026-08-01')", (user_id,)
    )
    for i in range(quantas):
        escopo.insert("jobs", {
            "job_id": f"ruim-{i}", "titulo": "Coordenador Financeiro",
            "url": "u", "estado": "novo", "busca": termo,
            "primeira_vez_em": "2026-08-01", "ultima_vez_em": "2026-08-01",
        })
        escopo.insert("scores", {
            "job_id": f"ruim-{i}", "run_id": "velho", "passada": "final",
            "score": 20, "criado_em": "2026-08-01",
            "componentes": json.dumps({"tetos": {"outra_trilha": 65}}),
        })


def test_the_page_lists_the_same_searches_the_run_will_execute(cliente, tmp_path):
    """A pagina responde "por que este cargo nao apareceu?".

    Ela responderia errado se listasse busca que o run nao chega a executar, ou
    escondesse uma que ele executa. As duas montagens sao consultas separadas --
    a face web nao importa o pipeline -- e e este teste que as mantem de acordo.
    """
    from crivo.pipeline.planner import load_filters, plan
    from crivo.pipeline.stages import PlanejamentoStage
    from crivo.profile.merger import ProfileMerger
    from crivo.store.repository import Repository
    from crivo.web.app import _buscas_previstas, _rendimento_historico
    from crivo.worker.runner import RunContext

    c, _i, banco = cliente
    user_id = entrar(cliente)
    conexao = banco()
    ProfileMerger(conexao, load_config()).consolidate(
        user_id,
        resume_fields={
            "nome": "Ana", "headline": "Gerente de SRE e Infraestrutura",
            "localizacao": "Osasco, SP",
            "experiencias": [{"titulo": "Gerente de SRE", "empresa": "X",
                              "inicio": "2015-01", "fim": None, "descricao": "AWS"}],
            "competencias": ["Kubernetes", "AWS", "FinOps"],
            "formacao": ["CC"], "idiomas": ["Portugues"],
        },
    )
    repositorio = Repository(conexao)
    config = load_config()

    # Historico que de fato rebaixa algum termo: sem ele as duas montagens
    # coincidem por vacuidade, e o teste passaria mesmo com o estagio ignorando
    # o rendimento.
    versao = ProfileMerger(conexao, config).current(user_id)
    reprovado = [
        q.texto for q in plan(
            {**versao.campos, "nivel_inferido": versao.nivel_inferido},
            versao.nivel_inferido, load_filters(),
            limite=config.collection.max_buscas_por_run,
        )
    ][-1]
    _semear_historico_ruim(conexao, user_id, reprovado)

    # O estagio de verdade, e nao uma reimplementacao dele. Comparar a pagina
    # com uma copia da logica do run deixaria as duas concordarem enquanto
    # ambas divergem do que de fato roda.
    contexto_do_run = RunContext(
        run_id="novo", user_id=user_id, janela="ampla", config=config,
        scope=repositorio.for_user(user_id),
    )
    contexto_do_run.dados["perfil"] = {
        **versao.campos, "nivel_inferido": versao.nivel_inferido
    }
    PlanejamentoStage(conexao, config=config).run(contexto_do_run)
    do_run = [q.texto for q in contexto_do_run.dados["buscas"]]

    from crivo.web.app import WebContext

    contexto = WebContext(config, banco)
    da_pagina = _buscas_previstas(contexto, repositorio, user_id)["todas"]
    assert da_pagina == do_run
    # E o historico precisa ter mudado alguma coisa: sem esta afirmacao o teste
    # voltaria a passar com o estagio ignorando o rendimento.
    assert reprovado not in do_run


def test_both_yield_queries_agree_on_the_same_database(cliente):
    """A consulta da pagina e a do estagio sao separadas de proposito.

    A face web nao importa o pipeline, e uma consulta de leitura e mais barata
    de repetir do que essa fronteira e de furar. O que nao pode e as duas
    divergirem em silencio.
    """
    from crivo.pipeline.stages import PlanejamentoStage
    from crivo.store.repository import Repository
    from crivo.web.app import _rendimento_historico
    from crivo.worker.runner import RunContext

    c, _i, banco = cliente
    user_id = entrar(cliente)
    conexao = banco()
    repositorio = Repository(conexao)

    estagio = PlanejamentoStage(conexao, config=load_config())
    contexto = RunContext(
        run_id="r", user_id=user_id, janela="ampla", config=load_config(),
        scope=repositorio.for_user(user_id),
    )
    assert estagio._rendimento(contexto) == _rendimento_historico(
        repositorio, user_id
    )
