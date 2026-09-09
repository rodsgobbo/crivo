"""Entrada do usuario por conta Google e ciclo de vida da sessao.

Duas decisoes estruturais moram aqui.

A identidade interna vem do identificador de assunto devolvido pelo provedor, e
nunca do endereco de e-mail. E-mail e mutavel e reatribuivel: usa-lo como chave
transformaria uma troca de endereco em perda de conta, e a reatribuicao de um
endereco corporativo em acesso indevido ao historico de outra pessoa.

A sessao e uma linha no banco referenciada por um testemunho opaco. O banco
guarda apenas o resumo criptografico dele, de modo que ler a tabela nao rende
sessoes utilizaveis. Encerrar a sessao apaga a sessao e nada mais: sair do
sistema nao e desconectar as origens.
"""

from __future__ import annotations

import os
import base64
import hashlib
import secrets as _secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..store.migrations import StoreError
from ..store.repository import Repository

#: Vida do estado intermediario entre o desvio ao provedor e a volta.
FLOW_TTL_SECONDS = 600


class AuthError(Exception):
    """Fluxo de autorizacao recusado."""


@dataclass(frozen=True)
class Identity:
    """O que o provedor devolve sobre quem entrou."""

    subject: str
    email: str | None = None


@dataclass(frozen=True)
class AuthorizationRequest:
    """Desvio ao provedor, com o estado que precisa voltar."""

    url: str
    state: str


@dataclass(frozen=True)
class Session:
    """Sessao recem-criada. O testemunho em claro existe apenas aqui."""

    token: str
    session_id: str
    user_id: str
    expira_em: datetime


class IdentityProvider(Protocol):
    """Porta do provedor de identidade, para que o fluxo seja testavel."""

    name: str

    def authorization_url(
        self, state: str, code_challenge: str, redirect_uri: str
    ) -> str: ...

    def exchange(
        self, code: str, code_verifier: str, redirect_uri: str
    ) -> Identity: ...


class GoogleIdentityProvider:
    """Fluxo de autorizacao do Google com verificacao de codigo."""

    name = "google"
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    SCOPES = "openid email profile"

    def __init__(self, client_id: str, client_secret: str, http=None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http

    def authorization_url(
        self, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        query = urlencode({
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        })
        return f"{self.AUTHORIZE_URL}?{query}"

    def exchange(
        self, code: str, code_verifier: str, redirect_uri: str
    ) -> Identity:
        if self._http is None:  # pragma: no cover - exige rede
            import httpx

            self._http = httpx.Client(timeout=15)
        response = self._http.post(
            self.TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        if response.status_code != 200:
            raise AuthError(
                f"provedor google recusou a troca do codigo: HTTP "
                f"{response.status_code}"
            )
        payload = response.json()
        claims = _decode_id_token(payload.get("id_token", ""))
        subject = claims.get("sub")
        if not subject:
            raise AuthError(
                "provedor google nao devolveu identificador de assunto"
            )
        return Identity(subject=subject, email=claims.get("email"))


class AuthService:
    """Comeca o fluxo, conclui a entrada e administra a sessao."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: IdentityProvider,
        session_ttl_seconds: int,
    ) -> None:
        self._connection = connection
        self._repository = Repository(connection)
        self._provider = provider
        self._ttl = session_ttl_seconds

    # -------------------------------------------------------------- entrada
    def begin(self, redirect_uri: str) -> AuthorizationRequest:
        """Cria o estado do fluxo e devolve o desvio ao provedor."""
        state = _secrets.token_urlsafe(32)
        verifier = _secrets.token_urlsafe(64)
        challenge = _s256(verifier)
        now = _now()
        self._repository.insert(
            "auth_flows",
            {
                "state": state,
                "provedor": self._provider.name,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "criado_em": _stamp(now),
                "expira_em": _stamp(now + timedelta(seconds=FLOW_TTL_SECONDS)),
            },
        )
        return AuthorizationRequest(
            url=self._provider.authorization_url(state, challenge, redirect_uri),
            state=state,
        )

    def complete(
        self,
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> Session:
        """Conclui a entrada e devolve a sessao criada.

        A linha do fluxo e consumida antes de qualquer coisa, inclusive quando o
        provedor devolve erro: um estado reapresentado nao pode valer duas vezes.
        """
        flow = self._consume_flow(state)
        if flow is None:
            raise AuthError(
                "estado de autorizacao desconhecido, expirado ou ja usado"
            )
        if error:
            raise AuthError(f"provedor {flow['provedor']} recusou a entrada: {error}")
        if not code:
            raise AuthError(
                f"provedor {flow['provedor']} voltou sem codigo de autorizacao"
            )
        identity = self._provider.exchange(
            code, flow["code_verifier"], flow["redirect_uri"]
        )
        user_id = self._resolve_user(identity)
        return self._open_session(user_id)

    def open_local_session(self) -> Session:
        """Abre sessao sem provedor de identidade.

        Existe para destravar o desenvolvimento quando ainda nao ha cliente
        OAuth registrado: sem sessao, nenhuma rota alem da inicial responde, e o
        produto nao pode ser exercitado.

        Ela passa pelo mesmo `_resolve_user` e `_open_session` do fluxo real, e
        nao por um atalho paralelo. Uma sessao local que fosse um caso especial
        exercitaria um codigo que nao e o de producao, o que derrotaria o
        proposito de usa-la para testar o produto.
        """
        user_id = self._resolve_user(
            Identity(subject=SUBJECT_LOCAL, email="local@desenvolvimento")
        )
        return self._open_session(user_id)

    # -------------------------------------------------------------- sessao
    def session_for(self, token: str | None) -> sqlite3.Row | None:
        """Devolve a sessao valida do testemunho, ou `None`."""
        if not token:
            return None
        rows = self._repository.execute(
            "SELECT * FROM sessions WHERE testemunho_hash = ?", (_digest(token),)
        ).fetchall()
        if not rows:
            return None
        session = rows[0]
        if _parse(session["expira_em"]) <= _now():
            self.logout(token)
            return None
        return session

    def logout(self, token: str | None) -> bool:
        """Apaga a sessao. Nao toca nos testemunhos das origens conectadas."""
        if not token:
            return False
        cursor = self._repository.execute(
            "DELETE FROM sessions WHERE testemunho_hash = ?", (_digest(token),)
        )
        return cursor.rowcount > 0

    def purge_expired(self) -> int:
        """Remove sessoes e fluxos vencidos."""
        agora = _stamp(_now())
        removed = self._repository.execute(
            "DELETE FROM sessions WHERE expira_em <= ?", (agora,)
        ).rowcount
        self._repository.execute(
            "DELETE FROM auth_flows WHERE expira_em <= ?", (agora,)
        )
        return removed

    # -------------------------------------------------------------- interno
    def _consume_flow(self, state: str) -> sqlite3.Row | None:
        if not state:
            return None
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM auth_flows WHERE state = ?", (state,)
            ).fetchone()
            if row is not None:
                self._connection.execute(
                    "DELETE FROM auth_flows WHERE state = ?", (state,)
                )
            self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StoreError(f"consumir fluxo de autorizacao: {exc}") from exc
        if row is None or _parse(row["expira_em"]) <= _now():
            return None
        return row

    def _resolve_user(self, identity: Identity) -> str:
        existing = self._repository.execute(
            "SELECT user_id FROM users WHERE subject_google = ?", (identity.subject,)
        ).fetchone()
        if existing is not None:
            self._repository.execute(
                "UPDATE users SET email = ?, ultima_sessao_em = ? WHERE user_id = ?",
                (identity.email, _stamp(_now()), existing["user_id"]),
            )
            return existing["user_id"]
        user_id = str(uuid.uuid4())
        self._repository.insert(
            "users",
            {
                "user_id": user_id,
                "subject_google": identity.subject,
                "email": identity.email,
                "criado_em": _stamp(_now()),
                "ultima_sessao_em": _stamp(_now()),
            },
        )
        return user_id

    def _open_session(self, user_id: str) -> Session:
        token = _secrets.token_urlsafe(32)
        expira = _now() + timedelta(seconds=self._ttl)
        session_id = str(uuid.uuid4())
        self._repository.insert(
            "sessions",
            {
                "session_id": session_id,
                "user_id": user_id,
                "testemunho_hash": _digest(token),
                "criada_em": _stamp(_now()),
                "expira_em": _stamp(expira),
            },
        )
        return Session(
            token=token, session_id=session_id, user_id=user_id, expira_em=expira
        )


# ------------------------------------------------------------------ utilidades
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _decode_id_token(id_token: str) -> dict:
    """Le as afirmacoes do testemunho de identidade.

    A assinatura nao e verificada aqui de proposito: o testemunho chega pelo
    canal direto com o provedor, autenticado por transporte cifrado e pelo
    segredo do cliente, que e o cenario em que a propria especificacao dispensa
    a verificacao local.
    """
    import json

    parts = id_token.split(".")
    if len(parts) != 3:
        raise AuthError("testemunho de identidade malformado")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("testemunho de identidade ilegivel") from exc


# ------------------------------------------------------------------ HTTP

#: Nome do cookie que carrega o testemunho de sessao.
SESSION_COOKIE = "crivo_session"

#: Liga a entrada local. Ausente ou diferente destes valores, a rota nao existe
#: -- nao fica desabilitada, nao e registrada.
VARIAVEL_DE_ENTRADA_LOCAL = "CRIVO_DEV_LOGIN"
_LIGADO = frozenset({"1", "true", "sim", "yes", "on"})

#: Identificador do provedor da sessao local. Fica gravado na linha do usuario,
#: entao um banco que passou por aqui sempre pode ser distinguido de um que so
#: viu entradas reais.
SUBJECT_LOCAL = "local:desenvolvimento"


def entrada_local_ligada(environ=None) -> bool:
    """A entrada sem provedor esta ligada neste ambiente?"""
    fonte = os.environ if environ is None else environ
    return (fonte.get(VARIAVEL_DE_ENTRADA_LOCAL) or "").strip().lower() in _LIGADO

#: Nome do cookie curto que carrega o estado do fluxo em andamento.
FLOW_COOKIE = "crivo_flow"


def de_navegador(request) -> bool:
    """O pedido veio de um formulario de pagina, e nao de um cliente de API?

    Os mesmos enderecos servem os dois publicos: a interface precisa de um
    caminho sem JavaScript, e quem integra precisa de JSON. Quem declara
    aceitar HTML recebe redirecionamento; todo o resto continua recebendo o
    objeto, e nenhum contrato de API muda por causa da tela.
    """
    return "text/html" in request.headers.get("accept", "")


def set_session_cookie(response, token: str, max_age: int) -> None:
    """Grava o testemunho com as protecoes que o tornam util.

    Um testemunho opaco nao vale nada se viaja em cookie legivel por script ou
    se outro sitio consegue aciona-lo. As tres protecoes andam juntas.
    """
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


class SessionGuard:
    """Resolve o usuario da requisicao, ou recusa o acesso.

    A recusa acontece aqui e nao em cada rota. Uma rota que esqueca de checar
    sessao seria um vazamento silencioso; depender da guarda torna a checagem
    parte da assinatura da rota.
    """

    def __init__(self, service: "AuthService") -> None:
        self._service = service

    def resolve(self, request) -> str | None:
        token = request.cookies.get(SESSION_COOKIE)
        sessao = self._service.session_for(token)
        return sessao["user_id"] if sessao is not None else None

    def require(self, request) -> str:
        user_id = self.resolve(request)
        if user_id is None:
            raise HTTPException(
                status_code=401,
                detail="sessao ausente ou expirada; entre novamente para "
                       "acessar perfil, vagas ou relatorios",
            )
        return user_id


def build_router(context) -> APIRouter:
    """Rotas de identidade: entrada, retorno do provedor e saida."""
    router = APIRouter(prefix="/auth", tags=["identidade"])

    @router.get("/login")
    def login(request: Request):
        servico = context.auth_service()
        pedido = servico.begin(str(request.url_for("callback")))
        return RedirectResponse(pedido.url, status_code=302)

    @router.get("/callback", name="callback")
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        servico = context.auth_service()
        try:
            sessao = servico.complete(state=state, code=code or None, error=error or None)
        except AuthError as exc:
            # O erro volta na pagina inicial, onde ha contexto para entende-lo.
            return RedirectResponse(
                f"/?erro={quote(str(exc))}", status_code=303
            )
        # Devolve o usuario ao produto, e nao um objeto JSON no navegador.
        # Este endereco so e alcancado por redirecionamento do provedor: quem
        # chega aqui esta numa janela de navegador, no meio de uma tarefa.
        resposta = RedirectResponse("/", status_code=303)
        set_session_cookie(
            resposta, sessao.token, context.config.session.duracao_maxima_s
        )
        return resposta

    # A rota so passa a existir com a variavel ligada. Registrar e depois
    # recusar deixaria um endereco de autenticacao publicado no `/docs` de
    # producao, o que e um convite a tentativa.
    if entrada_local_ligada():

        @router.get("/local")
        def entrada_local():
            """Entrada sem provedor, ligada por variavel de ambiente."""
            sessao = context.auth_service().open_local_session()
            resposta = JSONResponse(
                {
                    "estado": "autenticado",
                    "user_id": sessao.user_id,
                    "aviso": "sessao local de desenvolvimento, sem provedor de "
                             "identidade; nao use fora da sua maquina",
                }
            )
            set_session_cookie(
                resposta, sessao.token, context.config.session.duracao_maxima_s
            )
            return resposta

    @router.post("/logout")
    def logout(request: Request):
        servico = context.auth_service()
        servico.logout(request.cookies.get(SESSION_COOKIE))
        # Continua POST: sair e uma acao com efeito, e link nao deve dispara-la.
        # O retorno e a pagina inicial, porque quem clicou esta num navegador.
        resposta = RedirectResponse("/", status_code=303)
        clear_session_cookie(resposta)
        return resposta

    @router.get("/me")
    def me(request: Request):
        user_id = context.guard().require(request)
        return {"user_id": user_id}

    return router
