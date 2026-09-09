"""Processo web: atende o navegador e nunca executa um estagio de run.

A separacao entre este processo e o de runs nao e organizacional, e de tempo de
vida. Um run dura minutos ou horas porque cada enriquecimento e separado do
seguinte por uma espera deliberada; prende-lo ao ciclo de uma requisicao HTTP
transformaria toda a contencao de taxa em tempo esgotado de navegador.

Por isso este modulo nao importa o executor de estagios. A unica coisa que ele
faz em relacao a runs e enfileirar e ler resultado ja gravado.

O contexto e uma fabrica e nao um conjunto de objetos prontos. Uma conexao do
SQLite pertence a thread que a criou, e o servidor atende rotas sincronas num
conjunto de threads; guardar servicos ja construidos amarraria todos eles a
thread que subiu a aplicacao.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    HTMLResponse, JSONResponse, RedirectResponse, Response,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config
from ..profile import merger as profile_merger_module
from ..profile.merger import HistoryRequired, ProfileMerger
from ..store.extractor_tokens import CABECALHO_DO_EXTRATOR, ExtractorTokens
from ..store.insights import InsightError, InsightStore
from ..store.locks import enricher_ativo
from ..providers.registry import ProviderRegistry, load_providers
from ..providers.vault import CredentialVault, EnvelopeCipher
from ..report.renderer import ReportRenderer
from ..resume.importer import ResumeImporter
from ..resume.parser import ResumeParser
from ..scheduler import Scheduler, SchedulerRefusal
from ..store.lifecycle import JobLifecycle, LifecycleError
from ..store.privacy import PrivacyError, PrivacyService
from ..store.repository import Repository
from ..worker.queue import RunQueue
from . import auth as auth_module
from . import credentials as credentials_module
from . import linkedin as linkedin_module
from . import resume as resume_module
from . import tempo


def _sempre_aceita(provider, secret) -> bool:
    """Validacao padrao de credencial, usada quando nenhuma e injetada.

    Uma verificacao real faria uma requisicao ao provedor. Ela e injetavel para
    que o processo web suba sem rede em ambiente de teste.
    """
    return bool(secret) or not provider.exige_credencial


@dataclass(frozen=True)
class WebContext:
    """Fabricas das dependencias das rotas."""

    config: Config
    connect: Callable[[], sqlite3.Connection]
    identity_provider: object | None = None
    linkedin_provider: object | None = None
    drive_client: object | None = None
    cipher: EnvelopeCipher | None = None
    router_factory: Callable[[], object] | None = None
    validate_credential: Callable[..., bool] = _sempre_aceita

    # ------------------------------------------------------------ basicos
    def repository(self) -> Repository:
        return Repository(self.connect())

    def queue(self) -> RunQueue:
        return RunQueue(self.connect())

    def insight_store(self) -> InsightStore:
        return InsightStore(self.connect())

    def extractor_tokens(self) -> ExtractorTokens:
        return ExtractorTokens(self.connect())

    def guard(self) -> auth_module.SessionGuard:
        return auth_module.SessionGuard(self.auth_service())

    # ------------------------------------------------------------ servicos
    def auth_service(self) -> auth_module.AuthService:
        return auth_module.AuthService(
            self.connect(), self.identity_provider, self.config.session.duracao_maxima_s
        )

    def linkedin_connector(self):
        return linkedin_module.LinkedInConnector(
            self.connect(), self.linkedin_provider, self.cipher
        )

    def provider_registry(self) -> ProviderRegistry:
        return ProviderRegistry(load_providers())

    def credential_vault(self) -> CredentialVault:
        return CredentialVault(self.connect(), self.cipher, self.provider_registry())

    def resume_importer(self) -> ResumeImporter:
        return ResumeImporter(self.connect(), self.config, self.drive_client)

    def model_client(self, user_id: str):
        from ..providers.client import ModelClient

        if self.router_factory is None:
            return None
        return ModelClient(
            user_id, self.credential_vault(), self.provider_registry(),
            self.router_factory(), self.config.synthesis.limite_caracteres_texto_externo,
        )

    def resume_parser(self, user_id: str) -> ResumeParser:
        return ResumeParser(self.connect(), self.model_client(user_id))

    def profile_merger(self) -> ProfileMerger:
        return ProfileMerger(self.connect(), self.config)

    def privacy_service(self) -> PrivacyService:
        return PrivacyService(self.connect(), self.config)

    def report_renderer(self) -> ReportRenderer:
        return ReportRenderer(self.connect(), self.config)

    def lifecycle(self) -> JobLifecycle:
        return JobLifecycle(self.connect())

    def scheduler(self) -> Scheduler:
        return Scheduler(self.connect(), self.config)


# ---------------------------------------------------------------- rotas
def build_profile_router(context: WebContext) -> APIRouter:
    router = APIRouter(prefix="/profile", tags=["perfil-alvo"])

    @router.get("")
    def perfil(request: Request):
        user_id = context.guard().require(request)
        versao = context.profile_merger().current(user_id)
        if versao is None:
            if auth_module.de_navegador(request):
                return RedirectResponse(
                    "/?erro=" + quote(
                        "nenhum perfil consolidado; confirme a extracao do "
                        "curriculo e consolide antes de ver o perfil-alvo"
                    ),
                    status_code=303,
                )
            return JSONResponse(
                {"erro": "nenhum perfil consolidado; importe um curriculo"},
                status_code=404,
            )
        if auth_module.de_navegador(request):
            return HTMLResponse(
                _ambiente().get_template("perfil.html.j2").render(
                    ctx={
                        "campos": profile_merger_module.CAMPOS,
                        "versao": versao,
                        "historico": len(context.profile_merger().history(user_id)),
                        "erro": request.query_params.get("erro"),
                        "aviso": request.query_params.get("aviso"),
                    }
                )
            )
        return {
            "version_id": versao.version_id,
            "campos": versao.campos,
            "origem_por_campo": versao.origem_por_campo,
            "nivel_inferido": versao.nivel_inferido,
            "problemas_higiene": versao.problemas_higiene,
        }

    @router.post("/consolidate")
    def consolidar(request: Request):
        user_id = context.guard().require(request)
        confirmada = context.resume_parser(user_id).confirmed(user_id)
        conexao = context.linkedin_connector().connection_for(user_id)

        linkedin_fields = (
            json.loads(conexao["campos_identidade"]) if conexao else {}
        )
        versao = context.profile_merger().consolidate(
            user_id,
            resume_fields=confirmada.efetivo() if confirmada else None,
            linkedin_fields=linkedin_fields,
        )
        if auth_module.de_navegador(request):
            return RedirectResponse(
                "/profile?aviso=" + quote(
                    f"perfil consolidado; senioridade inferida: "
                    f"{versao.nivel_inferido}"
                ),
                status_code=303,
            )
        return {"version_id": versao.version_id, "nivel": versao.nivel_inferido}

    @router.post("/buscas")
    async def acrescentar_busca(request: Request):
        """Acrescenta uma busca escrita pelo usuario ao proximo run."""
        user_id = context.guard().require(request)
        formulario = await request.form()
        texto = " ".join(str(formulario.get("texto") or "").split())
        if len(texto.split()) < 2:
            return RedirectResponse(
                "/?erro=" + quote(
                    "uma busca precisa de ao menos duas palavras; um termo "
                    "solto devolve o mercado inteiro"
                ),
                status_code=303,
            )
        # Repetir uma busca ja cadastrada nao e erro do usuario: e ele
        # esquecendo que ja pediu. A chave primaria impede a duplicata, e o
        # `OR IGNORE` transforma a colisao em silencio em vez de tela vermelha.
        context.repository().execute(
            "INSERT OR IGNORE INTO buscas_do_usuario (user_id, texto, criado_em) "
            "VALUES (?, ?, ?)",
            (user_id, texto, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        return RedirectResponse(
            "/?aviso=" + quote(f"busca {texto!r} acrescentada"), status_code=303
        )

    @router.post("/buscas/remover")
    async def remover_busca(request: Request):
        user_id = context.guard().require(request)
        formulario = await request.form()
        context.repository().execute(
            "DELETE FROM buscas_do_usuario WHERE user_id = ? AND texto = ?",
            (user_id, str(formulario.get("texto") or "")),
        )
        return RedirectResponse("/?aviso=" + quote("busca removida"), status_code=303)

    @router.get("/export")
    def exportar(request: Request):
        user_id = context.guard().require(request)
        try:
            return context.privacy_service().export_account(user_id)
        except PrivacyError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=404)

    @router.delete("/account")
    def excluir(request: Request):
        user_id = context.guard().require(request)
        try:
            operacao = context.privacy_service().delete_account(user_id)
        except PrivacyError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=404)
        resposta = JSONResponse({"excluida_em": operacao.instante})
        auth_module.clear_session_cookie(resposta)
        return resposta

    return router


def build_runs_router(context: WebContext) -> APIRouter:
    router = APIRouter(prefix="/runs", tags=["runs"])

    @router.get("")
    def listar(request: Request):
        user_id = context.guard().require(request)
        linhas = context.repository().for_user(user_id).select(
            "runs", order_by="rowid DESC", limit=50
        )
        return {
            "runs": [
                {
                    "run_id": linha["run_id"], "estado": linha["estado"],
                    "janela": linha["janela"], "solicitado_em": linha["solicitado_em"],
                    "n_brutos": linha["n_brutos"], "n_filtrados": linha["n_filtrados"],
                    "falha_sintese": linha["falha_sintese"],
                }
                for linha in linhas
            ]
        }

    @router.post("")
    async def solicitar(request: Request):
        user_id = context.guard().require(request)
        # O alcance vem do formulario quando ha um; clientes de API que nao o
        # informam continuam recebendo o padrao de antes, os 30 dias.
        alcance = None
        if auth_module.de_navegador(request):
            formulario = await request.form()
            alcance = str(formulario.get("alcance") or "") or None
        try:
            context.profile_merger().require_for_run(user_id)
            run_id = context.scheduler().request_immediate(user_id, alcance)
        except HistoryRequired as exc:
            if auth_module.de_navegador(request):
                return RedirectResponse(f"/?erro={quote(str(exc))}", status_code=303)
            return JSONResponse({"erro": str(exc)}, status_code=409)
        except SchedulerRefusal as exc:
            if auth_module.de_navegador(request):
                return RedirectResponse(f"/?erro={quote(str(exc))}", status_code=303)
            return JSONResponse({"erro": str(exc)}, status_code=429)
        if auth_module.de_navegador(request):
            return RedirectResponse(
                "/?aviso=" + quote(
                    f"busca {run_id[:8]} enfileirada; ela avanca no processo de "
                    "runs, e o relatorio aparece nesta pagina quando concluir"
                ),
                status_code=303,
            )
        return {"run_id": run_id}

    @router.post("/{run_id}/cancelar")
    def cancelar(request: Request, run_id: str):
        """Pede a parada de um run em curso.

        O pedido e gravado e atendido pelo processo de runs no ponto onde
        parar e barato -- entre duas buscas. A face web nao encerra o run
        sozinha: ela nao esta executando nada, e mudar o estado por baixo de
        quem esta deixaria requisicao pela metade.

        Por isso a resposta diz "pedido registrado" e nao "cancelado". A
        diferenca importa: quem clica precisa saber que a parada leva alguns
        segundos, senao clica de novo achando que nao funcionou.
        """
        user_id = context.guard().require(request)
        pedido = context.queue().request_cancel(run_id, user_id)
        if auth_module.de_navegador(request):
            if not pedido:
                return RedirectResponse(
                    "/?erro=" + quote(
                        "esta busca ja tinha terminado ou ja estava sendo "
                        "cancelada; nada foi alterado"
                    ),
                    status_code=303,
                )
            return RedirectResponse(
                "/?aviso=" + quote(
                    "parada pedida; a busca encerra ao terminar a consulta em "
                    "curso, e o que ela ja trouxe fica guardado"
                ),
                status_code=303,
            )
        if not pedido:
            return JSONResponse(
                {"erro": f"run {run_id} nao esta ativo para este usuario"},
                status_code=409,
            )
        return {"run_id": run_id, "cancelamento": "pedido"}

    @router.patch("/jobs/{job_id}")
    def marcar(request: Request, job_id: str, corpo: dict):
        user_id = context.guard().require(request)
        try:
            context.lifecycle().mark(user_id, job_id, str(corpo.get("estado") or ""))
        except LifecycleError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)
        return {"job_id": job_id, "estado": corpo.get("estado")}

    return router


#: Marca de credencial nao preenchida. O `.env.example` traz o campo vazio, e
#: quem preenche "para destravar a partida" costuma usar um texto assim. Vale
#: dizer isso na cara do usuario em vez de deixa-lo descobrir no callback.
MARCAS_DE_EXEMPLO = ("placeholder", "exemplo", "changeme", "your-", "seu-")


def _e_de_exemplo(valor: str | None) -> bool:
    baixo = (valor or "").strip().lower()
    return not baixo or any(marca in baixo for marca in MARCAS_DE_EXEMPLO)


def build_home_router(context: WebContext) -> APIRouter:
    """Porta de entrada da aplicacao.

    Existiu um periodo em que `/` devolvia 404: todos os servicos de pe e
    alcancaveis por HTTP, e quem abria o endereco via `{"detail":"Not Found"}`.
    Servico satisfeito, produto inalcancavel -- a mesma lacuna que ja tinha
    aparecido quando o sistema nao tinha endereco HTTP nenhum.
    """
    router = APIRouter(tags=["inicio"])

    @router.get("/", response_class=HTMLResponse)
    def inicio(request: Request):
        return HTMLResponse(
            _ambiente().get_template("home.html.j2").render(
                ctx=_estado_do_sistema(context, request)
            )
        )

    return router


@lru_cache(maxsize=1)
def _ambiente() -> Environment:
    ambiente = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
        autoescape=select_autoescape(default_for_string=True, default=True),
    )
    # O banco guarda UTC; a tela mostra o fuso de quem le. Como filtro, a
    # conversao fica onde o valor e exibido, em vez de exigir que cada rota
    # lembre de converter antes de passar o contexto.
    ambiente.filters["local"] = tempo.local
    ambiente.filters["local_completo"] = tempo.local_completo
    return ambiente


def _estado_do_linkedin(context: WebContext, user_id: str) -> dict:
    """Estado da conexao LinkedIn, ou o motivo de nao dar para saber.

    A pagina inicial nao pode quebrar porque uma origem conectada esta mal
    configurada: ela e justamente o lugar onde o usuario descobre isso.
    """
    try:
        conector = context.linkedin_connector()
        linha = conector.connection_for(user_id)
        if linha is None:
            return {"conectada": False}
        return {
            "conectada": True,
            "precisa_reconectar": conector.needs_reconnection(user_id),
            "indisponiveis": json.loads(linha["campos_indisponiveis"] or "[]"),
        }
    except Exception as exc:  # pragma: no cover - depende de config incompleta
        return {"conectada": False, "indisponivel": type(exc).__name__}


def _estado_do_curriculo(context: WebContext, repositorio, user_id: str) -> dict:
    """Em que ponto da importacao o curriculo esta.

    A pagina precisa oferecer a acao seguinte, e nao apenas contar arquivos.
    Importar, extrair e conferir sao tres estados distintos, e so o terceiro
    libera um run -- confundi-los faria a home dizer "pronto" cedo demais.
    """
    escopo = repositorio.for_user(user_id)
    importados = escopo.select(
        "resumes", columns="resume_id, importado_em", order_by="importado_em DESC"
    )
    if not importados:
        return {"resume_atual": None, "extracao": None}

    parser = context.resume_parser(user_id)
    confirmada = parser.confirmed(user_id)
    if confirmada is not None:
        estagio = "confirmada"
        extraction_id = confirmada.extraction_id
    else:
        pendentes = parser.pending(user_id)
        estagio = "pendente" if pendentes else "sem_extracao"
        extraction_id = pendentes[0].extraction_id if pendentes else None
    return {
        "resume_atual": importados[0]["resume_id"],
        "extracao": {"estagio": estagio, "extraction_id": extraction_id},
    }


def _estado_do_sistema(context: WebContext, request: Request) -> dict:
    """Le o estado real do banco. Nada aqui e derivado de configuracao."""
    repositorio = context.repository()

    def conta(tabela: str, user_id: str | None = None) -> int:
        escopo = repositorio.for_user(user_id) if user_id else repositorio
        return len(escopo.select(tabela, columns="rowid"))

    user_id = context.guard().resolve(request)
    estado: dict = {
        "user_id": user_id,
        # Recados que os retornos de autorizacao trazem na URL. Sao texto de
        # terceiro; o autoescape do template e o que impede que virem marcacao.
        "erro": request.query_params.get("erro"),
        "aviso": request.query_params.get("aviso"),
        "na_fila": context.queue().pending_count(),
        "enricher_ativo": enricher_ativo(repositorio),
        "google_de_exemplo": _e_de_exemplo(os.environ.get("GOOGLE_CLIENT_ID")),
        "linkedin_de_exemplo": _e_de_exemplo(os.environ.get("LINKEDIN_CLIENT_ID")),
        "linkedin": None,
        "entrada_local": auth_module.entrada_local_ligada(),
        "resumes": 0, "tem_perfil": False, "credenciais": 0, "vagas": 0, "runs": [],
    }
    if user_id is None:
        estado["proximo"] = _proximo_passo(estado)
        return estado

    estado["linkedin"] = _estado_do_linkedin(context, user_id)
    linhas = repositorio.select(
        "users", where="user_id = ?", params=(user_id,), columns="subject_google"
    )
    estado["sessao_local"] = bool(linhas) and (
        linhas[0]["subject_google"] == auth_module.SUBJECT_LOCAL
    )

    estado["resumes"] = conta("resumes", user_id)
    estado.update(_estado_do_curriculo(context, repositorio, user_id))
    estado["buscas_restantes"] = context.scheduler().immediate_remaining(user_id)
    # A oferta traz destino dos dados junto: escolher provedor de modelo e
    # decidir para onde o curriculo vai, e essa decisao nao pode exigir uma
    # consulta separada a outro endpoint.
    # O que a busca vai procurar, antes de ela gastar uma cota. A pergunta
    # "de quantos dias foi a pesquisa?" veio depois de um run inteiro, e a
    # seguinte -- "por que nao procurou Head SRE?" -- so podia ser respondida
    # lendo o codigo do planejador.
    estado["buscas_previstas"] = _buscas_previstas(context, repositorio, user_id)
    estado["provedores"] = [
        {
            "id": oferta.id,
            "rotulo": oferta.rotulo,
            "exige_credencial": oferta.exige_credencial,
            "limite_declarado": oferta.limite_declarado,
            "destino_dos_dados": oferta.destino_dos_dados,
        }
        for oferta in context.provider_registry().offers()
    ]
    estado["credenciais"] = conta("provider_credentials", user_id)
    estado["vagas"] = conta("jobs", user_id)
    estado["tem_perfil"] = bool(
        repositorio.for_user(user_id).select("profile_versions", columns="rowid")
    )
    linhas = repositorio.for_user(user_id).select(
        "runs", order_by="rowid DESC", limit=8
    )
    estado["runs"] = [
        {
            "run_id": linha["run_id"], "estado": linha["estado"],
            "solicitado_em": linha["solicitado_em"] or "",
            "motivo_recusa": linha["motivo_recusa"],
            "falha_sintese": linha["falha_sintese"],
        }
        for linha in linhas
    ]
    # O proximo passo e calculado depois de todo o estado estar lido: ele
    # depende de curriculo, extracao e perfil ao mesmo tempo.
    estado["proximo"] = _proximo_passo(estado)
    estado["run_ativo"] = _run_ativo(linhas)
    if estado["run_ativo"] and estado["run_ativo"]["estagio"] == "enriquecimento":
        estado["run_ativo"]["enriquecimento"] = _progresso_do_enriquecimento(
            repositorio, estado["run_ativo"]["run_id"]
        )
    return estado


#: Estados em que um run ainda vai produzir algo. Enquanto um deles esta em
#: curso, um novo pedido e recusado -- e a pagina precisa dizer isso antes do
#: clique, e nao depois.
ESTADOS_EM_CURSO = ("enfileirado", "em_andamento", "aguardando_enriquecimento")

#: Rotulo legivel de cada estagio, para que a espera diga o que esta havendo.
ROTULO_DO_ESTAGIO = {
    "perfil": "lendo o seu perfil",
    "planejamento": "montando as buscas",
    "coleta": "buscando vagas nos portais",
    "prefiltro": "descartando o que não serve",
    "pontuacao_provisoria": "pontuando por título",
    "enriquecimento": "lendo as descrições das vagas",
    "pontuacao_final": "refazendo a pontuação com as descrições",
    "sintese": "escrevendo a leitura da busca",
}

#: Ordem em que os estagios acontecem, para medir o quanto ja andou.
ORDEM_DOS_ESTAGIOS = tuple(ROTULO_DO_ESTAGIO)


def _progresso_do_enriquecimento(repositorio, run_id: str) -> dict | None:
    """Quantas descricoes ja vieram e quantas faltam, ou `None` fora da etapa.

    A contagem de etapas nao serve aqui: o enriquecimento e uma etapa so, e ela
    sozinha dura mais que todas as outras juntas. Sem um numero que ande, a
    barra fica parada por uma hora e a leitura obvia e que travou.
    """
    linhas = repositorio.execute(
        "SELECT estado, COUNT(*) AS n FROM enrichment_requests "
        "WHERE run_id = ? GROUP BY estado",
        (run_id,),
    ).fetchall()
    por_estado = {linha["estado"]: linha["n"] for linha in linhas}
    total = sum(por_estado.values())
    if not total:
        return None
    pendentes = por_estado.get("pendente", 0)
    return {
        "total": total,
        "pendentes": pendentes,
        "prontas": total - pendentes,
        "percentual": (total - pendentes) * 100 // total,
    }


#: O caminho ate poder buscar, em ordem. Cada passo declara como se reconhece
#: concluido, o que dizer enquanto nao esta, e a acao que o resolve.
#:
#: A sequencia mora aqui, e nao espalhada em condicionais do gabarito, porque a
#: pagina precisa responder uma pergunta so -- "o que eu faco agora?" -- e ela
#: nao consegue responder isso se a resposta estiver distribuida por sete
#: blocos `{% if %}` em quatro cartoes distintos.
PASSOS = (
    {
        "chave": "sessao",
        "pronto": lambda e: bool(e["user_id"]),
        "rotulo": "Google — sua sessão",
        "porque": "Tudo além desta página exige sessão.",
        "acao": "/auth/login",
        "metodo": "get",
        "botao": "Entrar com Google",
    },
    {
        "chave": "curriculo",
        "pronto": lambda e: bool(e["resumes"]),
        "rotulo": "Importe seu currículo",
        "porque": (
            "É dele que saem experiências e competências: o LinkedIn não as "
            "entrega nos escopos abertos."
        ),
        "acao": None,  # o formulario de envio vive no cartao de configuracao
        "metodo": None,
        "botao": None,
    },
    {
        "chave": "extracao",
        "pronto": lambda e: (e.get("extracao") or {}).get("estagio") != "sem_extracao",
        "rotulo": "Extraia os campos do currículo",
        "porque": "Importar guarda o texto; extrair transforma em campos.",
        "acao": "/resume/{resume_atual}/extract",
        "metodo": "post",
        "botao": "Extrair campos",
    },
    {
        "chave": "conferencia",
        "pronto": lambda e: (e.get("extracao") or {}).get("estagio") == "confirmada",
        "rotulo": "Confira o que foi extraído",
        "porque": (
            "Uma extração errada contamina a busca inteira, e o erro só "
            "apareceria no relatório final."
        ),
        "acao": "/resume/conferencia",
        "metodo": "get",
        "botao": "Conferir extração",
    },
    {
        "chave": "perfil",
        "pronto": lambda e: bool(e["tem_perfil"]),
        "rotulo": "Monte o seu perfil",
        "porque": (
            "Junta a identidade do LinkedIn com o histórico do currículo. "
            "É o perfil vigente que decide o que a busca procura."
        ),
        "acao": "/profile/consolidate",
        "metodo": "post",
        "botao": "Montar meu perfil",
    },
)


def _rendimento_historico(repositorio, user_id: str) -> dict[str, tuple[int, int]]:
    """Quanto cada termo rendeu nos runs anteriores deste usuario.

    Duplica a consulta do estagio de planejamento de proposito: a face web nao
    importa o pipeline, e uma consulta de leitura e mais barata de repetir do
    que essa fronteira e de furar. As duas precisam concordar, e o teste que
    compara as duas listas e o que garante isso.
    """
    linhas = repositorio.execute(
        """
        SELECT j.busca,
               COUNT(*) AS total,
               SUM(CASE WHEN s.componentes LIKE '%outra_trilha%'
                        THEN 1 ELSE 0 END) AS fora
        FROM scores s
        JOIN jobs j ON j.job_id = s.job_id AND j.user_id = s.user_id
        WHERE s.user_id = ? AND s.passada = 'final' AND j.busca IS NOT NULL
        GROUP BY j.busca
        """,
        (user_id,),
    ).fetchall()
    return {
        linha["busca"]: (int(linha["total"]), int(linha["fora"] or 0))
        for linha in linhas
    }


def _buscas_previstas(context: WebContext, repositorio, user_id: str) -> dict:
    """As buscas que o proximo run executaria, e as que o usuario acrescentou."""
    from ..pipeline.planner import PlannerError, load_filters, plan

    minhas = [
        linha["texto"]
        for linha in repositorio.for_user(user_id).select(
            "buscas_do_usuario", columns="texto", order_by="criado_em"
        )
    ]
    versao = context.profile_merger().current(user_id)
    if versao is None:
        return {"minhas": minhas, "todas": [], "nivel": None}
    try:
        queries = plan(
            {**versao.campos, "nivel_inferido": versao.nivel_inferido},
            versao.nivel_inferido,
            load_filters(),
            extras=tuple(minhas),
            # O mesmo teto e o mesmo historico que o run aplica. A pagina existe
            # para responder "por que este cargo nao apareceu?", e ela
            # responderia errado se listasse buscas que o run nao executa.
            limite=getattr(
                context.config.collection, "max_buscas_por_run", None
            ),
            rendimento=_rendimento_historico(repositorio, user_id),
        )
    except PlannerError:
        # Arquivo de filtros ausente ou ilegivel nao pode derrubar a pagina:
        # ela e justamente onde o usuario descobriria esse problema.
        return {"minhas": minhas, "todas": [], "nivel": versao.nivel_inferido}
    return {
        "minhas": minhas,
        "todas": [q.texto for q in queries],
        "nivel": versao.nivel_inferido,
    }


def _proximo_passo(estado: dict) -> dict | None:
    """O primeiro passo pendente, ou `None` quando o usuario pode buscar."""
    for passo in PASSOS:
        if passo["pronto"](estado):
            continue
        resolvido = dict(passo)
        if resolvido["acao"]:
            resolvido["acao"] = resolvido["acao"].format(
                resume_atual=estado.get("resume_atual") or ""
            )
        return resolvido
    return None


def _run_ativo(linhas) -> dict | None:
    """O run em curso e o quanto dele ja passou, ou `None` quando nao ha.

    Uma barra que so aparece no fim nao informa: quem clicou precisa saber
    agora que algo esta acontecendo, ha quanto tempo, e em que ponto -- senao a
    unica leitura possivel de uma tela parada e que nada funcionou.
    """
    for linha in linhas:
        if linha["estado"] not in ESTADOS_EM_CURSO:
            continue
        feitos = json.loads(linha["estagios_concluidos"] or "[]")
        atual = next(
            (e for e in ORDEM_DOS_ESTAGIOS if e not in feitos),
            ORDEM_DOS_ESTAGIOS[-1],
        )
        inicio = linha["iniciado_em"] or linha["solicitado_em"]
        return {
            "run_id": linha["run_id"],
            "estado": linha["estado"],
            "estagio": atual,
            "rotulo": ROTULO_DO_ESTAGIO.get(atual, atual),
            "feitos": len(feitos),
            "total": len(ORDEM_DOS_ESTAGIOS),
            "decorrido_s": _decorrido(inicio),
            "desde": inicio,
            # O pedido de parada ja registrado muda o que a tela oferece: o
            # botao vira aviso. Sem isso o usuario clica de novo achando que o
            # primeiro clique se perdeu, porque a parada so acontece quando a
            # consulta em curso termina.
            "cancelando": bool(_coluna(linha, "cancelado_em")),
        }
    return None


def _coluna(linha, nome: str):
    """Le uma coluna que pode nao existir no banco ainda em migracao."""
    try:
        return linha[nome]
    except (IndexError, KeyError):
        return None


def _decorrido(carimbo: str | None) -> int:
    """Segundos desde o carimbo, ou zero quando ele nao da para ler."""
    if not carimbo:
        return 0
    try:
        inicio = datetime.fromisoformat(carimbo)
    except ValueError:
        return 0
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - inicio).total_seconds()))


#: Unica origem externa autorizada a falar com esta aplicacao.
#:
#: O trecho que extrai os insights roda dentro da pagina da vaga, entao a
#: requisicao vem de la e nao de aqui. A liberacao e nominal e vale apenas para
#: as rotas de insight: liberar a aplicacao inteira permitiria que qualquer
#: pagina daquele dominio lesse relatorio e perfil com a sessao do usuario.
ORIGEM_DO_EXTRATOR = "https://www.linkedin.com"

#: Teto de sanidade do envio em lote. Uma pagina de resultados do LinkedIn
#: mostra 25 vagas por vez, e a varredura acumula o que o usuario rolou.
MAXIMO_DO_LOTE = 300


def _cabecalhos_de_origem() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": ORIGEM_DO_EXTRATOR,
        "Access-Control-Allow-Credentials": "true",
        # O cabecalho do token precisa estar aqui: o navegador so envia um
        # cabecalho fora da lista segura se o preflight o autorizar por nome.
        "Access-Control-Allow-Headers": f"content-type, {CABECALHO_DO_EXTRATOR}",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Vary": "Origin",
    }


def _quem_envia_o_insight(context: WebContext, request: Request) -> str:
    """O dono da varredura: pela sessao, ou pelo token do extrator.

    Duas credenciais para a mesma rota porque elas alcancam lugares diferentes.
    A sessao serve a quem chama daqui mesmo -- um teste, uma pagina do proprio
    app. O token serve ao unico chamador que esta rota foi feita para ter: um
    script rodando dentro da pagina do LinkedIn.

    E ele existe porque o cookie nao chega la. `samesite=lax` retem o cookie num
    POST cross-site, entao a rota tinha CORS, tinha origem liberada e era
    inalcancavel na pratica -- 401 em toda varredura. Cabecalho nao e governado
    por SameSite.

    A ordem importa pouco, mas o escopo importa muito: este par vale AQUI e em
    nenhuma outra rota. Aceitar o token na guarda geral o transformaria numa
    segunda sessao, e o ponto dele e ser menos que uma sessao.
    """
    try:
        return context.guard().require(request)
    except HTTPException:
        pass
    dono = context.extractor_tokens().resolve(
        request.headers.get(CABECALHO_DO_EXTRATOR)
    )
    if dono is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "sem sessao e sem token do extrator; abra /extensao no app "
                "para emitir um token e configure-o na extensao"
            ),
        )
    return dono


def build_insights_router(context: WebContext) -> APIRouter:
    """Recebe insights extraidos no navegador do proprio usuario.

    Nenhuma credencial atravessa esta rota. O que chega e o resultado de uma
    leitura que o usuario ja estava fazendo com os proprios olhos; o sistema
    nunca recebe o meio de refazer essa leitura sem ele.
    """
    router = APIRouter(prefix="/insights", tags=["insights"])

    @router.options("/lote")
    def preflight_do_lote():
        return Response(status_code=204, headers=_cabecalhos_de_origem())

    @router.post("/lote")
    def registrar_lote(request: Request, corpo: dict):
        """Recebe de uma vez os cards que a pagina de resultados mostra.

        A rota de uma vaga so serve a pagina de uma vaga, e ela e a errada para
        o sinal que motivou isto: "You'd be a top applicant" aparece no card da
        lista, e um run traz perto de cem vagas. Uma vaga por vez significaria
        abrir cem abas para colher o sinal de cem cards que ja estavam na tela.

        Vaga desconhecida nao e erro aqui, e ignorada. Uma varredura de lista
        passa por tudo o que o LinkedIn mostrar, inclusive o que este usuario
        nunca coletou, e recusar o lote inteiro por causa disso desperdicaria a
        leitura das outras.
        """
        cabecalhos = _cabecalhos_de_origem()
        try:
            user_id = _quem_envia_o_insight(context, request)
        except HTTPException as exc:
            return JSONResponse(
                {"erro": exc.detail}, status_code=exc.status_code, headers=cabecalhos
            )

        vagas = corpo.get("vagas")
        if not isinstance(vagas, list) or not vagas:
            return JSONResponse(
                {"erro": "envie `vagas` com pelo menos um item"},
                status_code=400, headers=cabecalhos,
            )
        if len(vagas) > MAXIMO_DO_LOTE:
            return JSONResponse(
                {"erro": f"lote acima de {MAXIMO_DO_LOTE} vagas"},
                status_code=400, headers=cabecalhos,
            )

        store = context.insight_store()
        gravadas, ignoradas = {}, []
        for item in vagas:
            job_id = item.get("job_id") if isinstance(item, dict) else None
            if not isinstance(job_id, str) or not job_id.strip():
                ignoradas.append({"job_id": None, "motivo": "sem job_id"})
                continue
            try:
                gravadas[job_id] = store.record(user_id, job_id, item)
            except InsightError as exc:
                ignoradas.append({"job_id": job_id, "motivo": str(exc)})
        return JSONResponse(
            {"gravadas": gravadas, "ignoradas": ignoradas}, headers=cabecalhos
        )

    @router.options("/{job_id}")
    def preflight(job_id: str):
        return Response(status_code=204, headers=_cabecalhos_de_origem())

    @router.post("/{job_id}")
    def registrar(request: Request, job_id: str, corpo: dict):
        cabecalhos = _cabecalhos_de_origem()
        try:
            user_id = _quem_envia_o_insight(context, request)
        except HTTPException as exc:
            return JSONResponse(
                {"erro": exc.detail}, status_code=exc.status_code, headers=cabecalhos
            )
        try:
            gravado = context.insight_store().record(user_id, job_id, corpo)
        except InsightError as exc:
            return JSONResponse(
                {"erro": str(exc)}, status_code=400, headers=cabecalhos
            )
        return JSONResponse(
            {"job_id": job_id, "gravado": gravado}, headers=cabecalhos
        )

    return router


def _extras_do_relatorio(context: WebContext, user_id: str, run_id: str) -> dict:
    """As secoes do relatorio que nao saem de uma leitura direta do run.

    O ranking de competencias e a higiene do historico sao parametros opcionais
    do renderizador, e ninguem os preenchia: o relatorio abria com a secao de
    habilidades mais pedidas vazia, que e justamente uma das perguntas que o
    produto existe para responder.

    Sao calculados aqui, e nao gravados pelo run, porque derivam inteiramente
    de dados ja persistidos -- refaze-los e barato e mantem o relatorio
    reexecutavel sobre qualquer run passado.
    """
    from ..scoring import gaps
    from ..scoring.ontology import load_ontology

    descricoes = [
        linha["texto"]
        for linha in context.repository().execute(
            "SELECT d.texto FROM scores s "
            "JOIN job_descriptions d ON d.job_id = s.job_id "
            "WHERE s.user_id = ? AND s.run_id = ? AND s.passada = 'final'",
            (user_id, run_id),
        ).fetchall()
    ]
    versao = context.profile_merger().current(user_id)
    return {
        "ranking_competencias": gaps.aggregate(load_ontology(), descricoes),
        "problemas_higiene": versao.problemas_higiene if versao else [],
    }


def build_extension_router(context: WebContext) -> APIRouter:
    """Emite o token do extrator e ensina a instalar a extensao.

    A emissao e POST e nao GET de proposito: ela invalida o token anterior, e um
    GET que muda estado seria disparado por qualquer pre-carregamento do
    navegador -- derrubando a extensao de quem so abriu a pagina para conferir
    se ela estava funcionando.
    """
    router = APIRouter(prefix="/extensao", tags=["extensao"])

    def _pagina(request: Request, token: str | None = None) -> HTMLResponse:
        user_id = context.guard().require(request)
        return HTMLResponse(
            _ambiente().get_template("extensao.html.j2").render(
                ctx={
                    "token": token,
                    "status": context.extractor_tokens().status(user_id),
                    "caminho_da_extensao": str(
                        (Path(__file__).resolve().parents[3] / "tools" / "extensao")
                    ),
                }
            )
        )

    @router.get("", response_class=HTMLResponse)
    def pagina(request: Request):
        return _pagina(request)

    @router.post("/token", response_class=HTMLResponse)
    def emitir(request: Request):
        user_id = context.guard().require(request)
        return _pagina(request, token=context.extractor_tokens().issue(user_id))

    return router


def build_reports_router(context: WebContext) -> APIRouter:
    router = APIRouter(prefix="/reports", tags=["relatorios"])

    @router.get("/{run_id}", response_class=HTMLResponse)
    def relatorio(request: Request, run_id: str):
        user_id = context.guard().require(request)
        # Filtro vem da URL, e nao de estado no servidor: o endereco filtrado
        # continua sendo um endereco, entao ele pode ser guardado, compartilhado
        # e reaberto -- e a pagina segue funcionando sem JavaScript.
        parametros = request.query_params
        filtros = {
            "cargo": parametros.get("cargo") or None,
            "score_minimo": parametros.get("score") or None,
            "so_com_descricao": parametros.get("descricao") == "1",
            "so_remoto": parametros.get("remoto") == "1",
            "ocultar_fora_do_raio": parametros.get("no_raio") == "1",
            "estado": parametros.get("estado") or None,
        }
        try:
            pagina = context.report_renderer().render_run(
                user_id, run_id,
                filtros=filtros,
                **_extras_do_relatorio(context, user_id, run_id),
            )
        except ValueError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=404)
        return HTMLResponse(pagina)

    return router


def create_app(
    config: Config,
    connect: Callable[[], sqlite3.Connection],
    **dependencias,
) -> FastAPI:
    """Monta a aplicacao web sobre uma fabrica de conexoes por thread."""
    context = WebContext(config=config, connect=connect, **dependencias)

    app = FastAPI(title="crivo", version="0.1.0")
    app.state.context = context

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "estado": "ok",
            "runs_enfileirados": context.queue().pending_count(),
        }

    for construir in (
        build_home_router,
        auth_module.build_router,
        linkedin_module.build_router,
        credentials_module.build_router,
        resume_module.build_router,
        build_profile_router,
        build_runs_router,
        build_insights_router,
        build_extension_router,
        build_reports_router,
    ):
        app.include_router(construir(context))

    # A folha de estilo vive num arquivo servido, e nao repetida dentro de cada
    # gabarito. A copia ja havia divergido: os tokens de `home` deixaram de ser
    # iguais aos de `conferencia` e `perfil` no espaco de uma sessao.
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )

    return app
