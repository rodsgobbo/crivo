"""Credencial de escopo unico do extrator que roda no navegador do usuario.

Existe por causa de uma barreira do navegador, e nao de uma escolha de desenho.
O cookie de sessao e `samesite=lax`: o navegador so o entrega em navegacao de
topo com metodo seguro. Uma varredura na pagina do LinkedIn faz um POST
cross-site para este servidor, e ali o cookie fica retido -- `credentials:
"include"` nao muda isso, porque quem decide e o navegador e nao o script. A
rota de insights, construida com cabecalhos CORS justamente para ser chamada de
la, era inalcancavel: toda tentativa voltaria 401.

Afrouxar o cookie para `samesite=none` resolveria essa rota e abriria CSRF em
todas as outras -- as tres protecoes do cookie andam juntas, e trocar a
seguranca da aplicacao inteira pelo alcance de uma rota e o negocio errado.

Um token proprio, mandado em cabecalho, atravessa: cabecalho nao e governado
por SameSite. E nao reintroduz o que o SameSite protege, porque o que ele
protege e credencial *ambiente* -- a que o navegador anexa sozinho. Um
cabecalho so existe se quem chamou ja tinha o token.

O escopo e a outra metade. Este token escreve insight e nada mais: nao le
relatorio, nao le perfil, nao alcanca credencial de provedor. Vazado, ele
permite mentir sobre quantos candidatos uma vaga tem -- nao permite agir como o
usuario. E o mesmo raciocinio que levou o projeto a recusar uma sessao de conta
operacional guardada: receber o resultado da leitura, nunca o meio de refaze-la.

O banco guarda o hash. Quem le o arquivo nao consegue usar o token, e por isso
ele aparece uma unica vez -- na tela que o emite.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from .repository import Repository

#: Cabecalho que carrega o token. Nome proprio em vez de `Authorization` para
#: que fique obvio, na leitura de um log ou de um proxy, que esta credencial nao
#: e a da aplicacao e nao abre o que a sessao abre.
CABECALHO_DO_EXTRATOR = "x-crivo-extrator"

#: Bytes de entropia do token. 32 bytes viram 43 caracteres em base64 urlsafe.
BYTES_DE_ENTROPIA = 32


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digerir(token: str) -> str:
    """Hash do token, que e a unica forma dele que o banco conhece."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ExtractorTokens:
    """Emite, resolve e revoga o token do extrator."""

    def __init__(self, connection) -> None:
        self._repository = Repository(connection)

    def issue(self, user_id: str) -> str:
        """Emite um token novo e devolve o texto dele, uma unica vez.

        Emitir revoga o anterior. Um token por usuario torna "gerar de novo" um
        ato com consequencia visivel -- a extensao antiga para de funcionar e
        quem a instalou percebe --, em vez de deixar credenciais esquecidas
        acumulando sem que ninguem saiba quantas existem.
        """
        self.revoke(user_id)
        token = secrets.token_urlsafe(BYTES_DE_ENTROPIA)
        # Insercao pelo escopo, leitura sem ele. Nao e inconsistencia: gravar
        # tem dono conhecido, e a autenticacao e justamente a pergunta "de quem
        # e este token?" -- filtrar por usuario antes de saber quem ele e seria
        # circular. E o mesmo par que a tabela de sessoes usa.
        self._repository.for_user(user_id).insert(
            "extractor_tokens",
            {"token_hash": digerir(token), "criado_em": _agora()},
        )
        return token

    def resolve(self, token: str | None) -> str | None:
        """Devolve o dono do token, ou `None` se ele nao vale.

        Marca o uso: a tela que emite mostra a data do ultimo uso, e sem ela o
        usuario nao tem como saber se a extensao esta de fato falando com o
        servidor ou se instalou e nunca funcionou.
        """
        if not token or not isinstance(token, str):
            return None
        linhas = self._repository.execute(
            "SELECT user_id FROM extractor_tokens WHERE token_hash = ?",
            (digerir(token),),
        ).fetchall()
        if not linhas:
            return None
        user_id = linhas[0]["user_id"]
        self._repository.execute(
            "UPDATE extractor_tokens SET ultimo_uso_em = ? WHERE token_hash = ?",
            (_agora(), digerir(token)),
        )
        return user_id

    def revoke(self, user_id: str) -> None:
        self._repository.execute(
            "DELETE FROM extractor_tokens WHERE user_id = ?", (user_id,)
        )

    def status(self, user_id: str) -> dict | None:
        """Ha token emitido para este usuario, e quando ele falou pela ultima vez?"""
        linhas = self._repository.execute(
            "SELECT criado_em, ultimo_uso_em FROM extractor_tokens "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        if not linhas:
            return None
        return {
            "criado_em": linhas[0]["criado_em"],
            "ultimo_uso_em": linhas[0]["ultimo_uso_em"],
        }
