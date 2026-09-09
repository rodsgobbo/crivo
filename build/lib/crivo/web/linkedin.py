"""Conexao da conta LinkedIn do usuario pelo fluxo de autorizacao oficial.

O que esta camada entrega e menos do que o nome sugere, e isso e uma restricao
da plataforma e nao do desenho. Os escopos abertos devolvem identidade, foto e
endereco de e-mail; nao devolvem historico profissional, cargos nem
competencias, que exigem parceria comercial. Por isso o conector registra os
escopos efetivamente concedidos e marca como indisponivel por escopo todo campo
que o consentimento nao alcanca: um campo vazio precisa ser distinguivel entre
"o usuario nao tem" e "nao temos permissao de ler".

O conector tambem e a fronteira que recusa material de credencial. Nao existe
caminho aqui que aceite senha, cookie ou sessao de LinkedIn de um usuario, e a
recusa acontece antes de qualquer gravacao.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlencode

from urllib.parse import quote

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..providers.vault import EnvelopeCipher, SealedSecret
from ..store.repository import Repository
from .auth import AuthError, _now, _parse, _s256, _stamp

#: Campos do perfil que o sistema gostaria de ter, com o escopo que os libera.
#: Os tres ultimos nao tem escopo aberto: existem aqui para que a ausencia deles
#: seja um dado explicito, e nao um silencio que alguem interprete como vazio.
CAMPOS_POR_ESCOPO: dict[str, str | None] = {
    "nome": "profile",
    "foto": "profile",
    "email": "email",
    "headline": None,
    "experiencias": None,
    "competencias": None,
}

#: Formas de material de credencial que o conector recusa na fronteira.
PADROES_DE_CREDENCIAL = (
    re.compile(r"(?i)\bli_at\b"),
    re.compile(r"(?i)\bJSESSIONID\b"),
    re.compile(r"(?i)\b(senha|password|passwd)\b"),
    re.compile(r"(?i)\bcookie\b"),
    re.compile(r"(?i)\bset-cookie\b"),
)

ATIVA = "ativa"
EXPIRADA = "expirada"


class CredentialSubmissionError(AuthError):
    """Material de credencial de usuario submetido ao conector."""


@dataclass(frozen=True)
class LinkedInIdentity:
    """O que a conexao devolveu, e o que ela nao pode devolver."""

    subject: str
    campos: dict[str, Any] = field(default_factory=dict)
    escopos: tuple[str, ...] = ()

    def indisponiveis(self) -> list[str]:
        """Campos que os escopos concedidos nao permitem ler."""
        faltando = []
        for campo, escopo in CAMPOS_POR_ESCOPO.items():
            if escopo is None or escopo not in self.escopos:
                faltando.append(campo)
        return faltando


class LinkedInProvider(Protocol):
    name: str

    def authorization_url(
        self, state: str, code_challenge: str, redirect_uri: str
    ) -> str: ...

    def exchange(
        self, code: str, code_verifier: str, redirect_uri: str
    ) -> tuple[LinkedInIdentity, str, int]: ...

    def revoke(self, token: str) -> None: ...


class OfficialLinkedInProvider:
    """Fluxo oficial da plataforma, com escopos abertos."""

    name = "linkedin"
    AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
    TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
    REVOKE_URL = "https://www.linkedin.com/oauth/v2/revoke"
    SCOPES = "openid profile email"

    def __init__(self, client_id: str, client_secret: str, http=None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http

    def authorization_url(
        self, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        # PKCE fica de fora de proposito. O LinkedIn separa o fluxo padrao,
        # autenticado por `client_secret`, do fluxo nativo com PKCE, que vive
        # em `/oauth/native-pkce/authorization`, dispensa o segredo e precisa
        # ser habilitado caso a caso pela propria LinkedIn. Misturar os dois
        # -- desafio aqui e segredo na troca -- faz o provedor recusar a
        # autenticacao do cliente com `invalid_client`.
        #
        # O `code_challenge` continua na assinatura porque o conector o gera
        # para ambos os provedores; aqui ele simplesmente nao viaja. A protecao
        # contra CSRF permanece no `state`, conferido em `_consume_flow`.
        query = urlencode({
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": self.SCOPES,
        })
        return f"{self.AUTHORIZE_URL}?{query}"

    def exchange(
        self, code: str, code_verifier: str, redirect_uri: str
    ) -> tuple[LinkedInIdentity, str, int]:
        response = self._client().post(
            self.TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if response.status_code != 200:
            # O status sozinho nao distingue codigo expirado de credencial
            # errada; o provedor nomeia a causa no corpo, e sem ela o
            # diagnostico vira adivinhacao.
            raise AuthError(
                f"provedor linkedin recusou a troca do codigo: HTTP "
                f"{response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
        claims = _claims(payload.get("id_token", ""))
        subject = claims.get("sub")
        if not subject:
            raise AuthError(
                "provedor linkedin nao devolveu identificador de assunto"
            )
        # O LinkedIn devolve os escopos separados por virgula, e nao pelo
        # espaco que a especificacao OAuth usa no pedido. Quebrar so por espaco
        # produzia um unico item -- "email,openid,profile" -- que nao casava com
        # nenhum escopo conhecido, e todo campo concedido aparecia como
        # indisponivel. Aceitar as duas formas nos deixa imunes a qual delas o
        # provedor escolher.
        escopos = tuple(sorted(_escopos(payload.get("scope", "")))) or ("openid",)
        identity = LinkedInIdentity(
            subject=subject,
            campos={
                "nome": claims.get("name"),
                "foto": claims.get("picture"),
                "email": claims.get("email"),
            },
            escopos=escopos,
        )
        return identity, payload["access_token"], int(payload.get("expires_in", 0))

    def revoke(self, token: str) -> None:
        try:
            self._client().post(
                self.REVOKE_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "token": token,
                },
            )
        except Exception:  # pragma: no cover - revogacao e melhor-esforco
            pass

    def _client(self):
        if self._http is None:  # pragma: no cover - exige rede
            import httpx

            self._http = httpx.Client(timeout=15)
        return self._http


class LinkedInConnector:
    """Conecta, desconecta e mantem o estado da conexao de cada usuario."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        provider: LinkedInProvider,
        cipher: EnvelopeCipher,
    ) -> None:
        self._connection = connection
        self._repository = Repository(connection)
        self._provider = provider
        self._cipher = cipher

    # ---------------------------------------------------------- fronteira
    @staticmethod
    def reject_credential_material(payload: Any) -> None:
        """Recusa senha, cookie ou sessao de LinkedIn antes de qualquer gravacao."""
        texto = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, default=str
        )
        for padrao in PADROES_DE_CREDENCIAL:
            if padrao.search(texto):
                raise CredentialSubmissionError(
                    "o sistema nao aceita senha, cookie nem sessao de LinkedIn; "
                    "conecte a conta pelo fluxo de autorizacao oficial"
                )

    # ------------------------------------------------------------ conexao
    def begin(self, user_id: str, redirect_uri: str) -> str:
        """Cria o estado do fluxo e devolve o desvio ao provedor."""
        import secrets as _secrets

        if self._provider is None:
            raise AuthError(
                "conexao LinkedIn nao configurada neste servidor; defina "
                "LINKEDIN_CLIENT_ID e LINKEDIN_CLIENT_SECRET e reinicie"
            )
        state = _secrets.token_urlsafe(32)
        verifier = _secrets.token_urlsafe(64)
        agora = _now()
        self._repository.insert(
            "auth_flows",
            {
                "state": state,
                "provedor": self._provider.name,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "user_id": user_id,
                "criado_em": _stamp(agora),
                "expira_em": _stamp(agora + timedelta(seconds=600)),
            },
        )
        return self._provider.authorization_url(state, _s256(verifier), redirect_uri)

    def complete(self, state: str, code: str | None = None, error: str | None = None):
        """Conclui a conexao e grava identidade, escopos e lacunas de escopo."""
        flow = self._consume_flow(state)
        if flow is None:
            raise AuthError("estado de conexao desconhecido, expirado ou ja usado")
        if error:
            raise AuthError(f"provedor linkedin recusou a conexao: {error}")
        if not code:
            raise AuthError("provedor linkedin voltou sem codigo de autorizacao")

        identity, token, expires_in = self._provider.exchange(
            code, flow["code_verifier"], flow["redirect_uri"]
        )
        user_id = flow["user_id"]
        sealed = self._cipher.seal(token, associated=user_id)
        scope = self._repository.for_user(user_id)
        scope.delete("linkedin_connections")
        scope.insert(
            "linkedin_connections",
            {
                "campos_identidade": json.dumps(identity.campos, ensure_ascii=False),
                "escopos_concedidos": json.dumps(list(identity.escopos)),
                "campos_indisponiveis": json.dumps(identity.indisponiveis()),
                "estado": ATIVA,
                "token_cifrado": _pack(sealed),
                "conectada_em": _stamp(_now()),
            },
        )
        return identity

    def connection_for(self, user_id: str) -> sqlite3.Row | None:
        rows = self._repository.for_user(user_id).select("linkedin_connections")
        return rows[0] if rows else None

    def disconnect(self, user_id: str) -> bool:
        """Apaga os dados de identidade e revoga o testemunho."""
        row = self.connection_for(user_id)
        if row is None:
            return False
        if row["token_cifrado"]:
            try:
                token = self._cipher.open(
                    _unpack(row["token_cifrado"]), associated=user_id
                )
                self._provider.revoke(token)
            except Exception:
                # A revogacao e melhor-esforco: o dado local sai de qualquer jeito.
                pass
        self._repository.for_user(user_id).delete("linkedin_connections")
        return True

    def mark_expired(self, user_id: str) -> None:
        """Marca a conexao como expirada, pedindo reconexao ao usuario."""
        self._repository.for_user(user_id).update(
            "linkedin_connections", {"estado": EXPIRADA, "token_cifrado": None}
        )

    def needs_reconnection(self, user_id: str) -> bool:
        row = self.connection_for(user_id)
        return row is not None and row["estado"] == EXPIRADA

    # ------------------------------------------------------------ interno
    def _consume_flow(self, state: str) -> sqlite3.Row | None:
        if not state:
            return None
        self._connection.execute("BEGIN IMMEDIATE")
        row = self._connection.execute(
            "SELECT * FROM auth_flows WHERE state = ? AND provedor = ?",
            (state, self._provider.name),
        ).fetchone()
        if row is not None:
            self._connection.execute("DELETE FROM auth_flows WHERE state = ?", (state,))
        self._connection.execute("COMMIT")
        if row is None or _parse(row["expira_em"]) <= _now():
            return None
        return row


def _pack(sealed: SealedSecret) -> bytes:
    return json.dumps({
        "c": base64.b64encode(sealed.ciphertext).decode("ascii"),
        "k": base64.b64encode(sealed.wrapped_key).decode("ascii"),
        "s": sealed.suffix,
    }).encode("utf-8")


def _unpack(payload: bytes) -> SealedSecret:
    data = json.loads(payload.decode("utf-8"))
    return SealedSecret(
        ciphertext=base64.b64decode(data["c"]),
        wrapped_key=base64.b64decode(data["k"]),
        suffix=data.get("s", ""),
    )


def _escopos(bruto: str) -> list[str]:
    """Escopos concedidos, aceitando virgula ou espaco como separador."""
    return [pedaco for pedaco in re.split(r"[\s,]+", bruto) if pedaco]


def _claims(id_token: str) -> dict:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise AuthError("testemunho de identidade malformado")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except ValueError as exc:
        raise AuthError("testemunho de identidade ilegivel") from exc


def build_router(context) -> APIRouter:
    """Rotas da conexao LinkedIn: inicio, retorno e desconexao."""
    router = APIRouter(prefix="/linkedin", tags=["conexao linkedin"])

    @router.get("/connect")
    def conectar(request: Request):
        user_id = context.guard().require(request)
        try:
            url = context.linkedin_connector().begin(
                user_id, str(request.url_for("linkedin_callback"))
            )
        except AuthError as exc:
            return RedirectResponse(f"/?erro={quote(str(exc))}", status_code=303)
        return RedirectResponse(url, status_code=302)

    @router.get("/callback", name="linkedin_callback")
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        context.guard().require(request)
        try:
            identidade = context.linkedin_connector().complete(
                state=state, code=code or None, error=error or None
            )
        except AuthError as exc:
            return RedirectResponse(f"/?erro={quote(str(exc))}", status_code=303)
        # Como no retorno do Google: quem chega aqui esta num navegador, no meio
        # de uma tarefa, e precisa voltar ao produto e nao a um objeto JSON.
        if identidade.indisponiveis():
            faltando = ", ".join(identidade.indisponiveis())
            return RedirectResponse(
                f"/?aviso={quote('LinkedIn conectado sem: ' + faltando)}",
                status_code=303,
            )
        return RedirectResponse("/", status_code=303)

    @router.post("/credentials")
    def recusar_credencial(request: Request, corpo: dict = Body(...)):
        """Existe para recusar. Nenhum caminho aceita senha ou cookie de usuario."""
        context.guard().require(request)
        try:
            LinkedInConnector.reject_credential_material(corpo)
        except CredentialSubmissionError as exc:
            return JSONResponse({"erro": str(exc)}, status_code=400)
        return JSONResponse(
            {
                "erro": "conecte a conta pelo fluxo de autorizacao oficial em "
                        "/linkedin/connect"
            },
            status_code=400,
        )

    @router.get("")
    def estado(request: Request):
        user_id = context.guard().require(request)
        conector = context.linkedin_connector()
        linha = conector.connection_for(user_id)
        if linha is None:
            return {"conectada": False}
        import json as _json

        return {
            "conectada": True,
            "estado": linha["estado"],
            "escopos": _json.loads(linha["escopos_concedidos"]),
            "campos_indisponiveis": _json.loads(linha["campos_indisponiveis"]),
            "precisa_reconectar": conector.needs_reconnection(user_id),
        }

    @router.delete("")
    def desconectar(request: Request):
        user_id = context.guard().require(request)
        if not context.linkedin_connector().disconnect(user_id):
            return JSONResponse({"erro": "nenhuma conexao ativa"}, status_code=404)
        return {"desconectada": True}

    return router
