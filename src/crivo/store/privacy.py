"""Direitos do titular sobre os dados pessoais.

A exclusao cabe numa transacao porque toda tabela de dado de usuario apaga em
cascata a partir de `users`. Isso nao e detalhe de implementacao: uma exclusao
feita por uma sequencia de comandos precisaria ser mantida alinhada com o
esquema para sempre, e o dia em que alguem acrescentasse uma tabela sem lembrar
de acrescentar o comando, dado pessoal ficaria para tras sem ninguem notar.

Duas coisas sobrevivem de proposito. O registro compartilhado de descricao de
vaga permanece, porque nao e dado pessoal do titular e outros usuarios dependem
dele; ele nao guarda vinculo com quem coletou, entao nao ha o que apagar ali. E
o registro de que a exclusao ocorreu permanece, porque a prova da exclusao nao
pode desaparecer junto com a conta excluida.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .repository import AUDIT_TABLES, USER_SCOPED_TABLES, Repository

EXCLUSAO = "exclusao"
EXPORTACAO = "exportacao"

#: Tabelas cujo conteudo entra na exportacao do titular.
EXPORTAVEIS = (
    "resumes", "resume_extractions", "profile_versions", "linkedin_connections",
    "jobs", "scores", "runs", "discards", "daily_quota",
)

#: Colunas que nunca saem na exportacao, mesmo pertencendo ao titular.
COLUNAS_OMITIDAS = frozenset({
    "chave_cifrada", "chave_de_dado_cifrada", "token_cifrado", "testemunho_hash",
})


class PrivacyError(Exception):
    """Operacao de titular que nao pode ser concluida."""


@dataclass(frozen=True)
class DataSubjectOperation:
    """Registro de que uma exclusao ou exportacao aconteceu."""

    op_id: str
    user_id: str
    tipo: str
    instante: str


class PrivacyService:
    """Executa exclusao, exportacao, aviso previo e expurgo por retencao."""

    def __init__(self, connection, config, agora=None) -> None:
        self._connection = connection
        self._repository = Repository(connection)
        self._config = config
        self._agora = agora or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------- aviso
    def disclosure(self) -> str:
        """Texto mostrado antes da primeira importacao de curriculo."""
        dias = self._config.retention.periodo_sem_sessao_dias
        return (
            "Ao importar seu curriculo, o sistema guarda o texto do documento, os "
            "dados extraidos dele, o perfil consolidado, as vagas coletadas para "
            "voce e os relatorios gerados. Esses dados ficam enquanto sua conta "
            f"existir e sao apagados apos {dias} dias sem acesso. Voce pode "
            "exportar ou excluir tudo a qualquer momento."
        )

    # ---------------------------------------------------------- exclusao
    def delete_account(self, user_id: str) -> DataSubjectOperation:
        """Apaga todo dado do titular numa transacao, e registra que apagou."""
        if not self._exists(user_id):
            raise PrivacyError(f"usuario {user_id} nao encontrado")
        operacao = self._record(user_id, EXCLUSAO)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        return operacao

    def remaining_rows(self, user_id: str) -> dict[str, int]:
        """Quantas linhas do titular restam por tabela. Deve ser tudo zero."""
        restantes: dict[str, int] = {}
        for tabela in sorted(USER_SCOPED_TABLES):
            linha = self._connection.execute(
                f"SELECT count(*) FROM {tabela} WHERE user_id = ?", (user_id,)
            ).fetchone()
            if linha[0]:
                restantes[tabela] = int(linha[0])
        return restantes

    # -------------------------------------------------------- exportacao
    def export_account(self, user_id: str) -> dict:
        """Reune todo dado pessoal do titular, sem material de credencial."""
        if not self._exists(user_id):
            raise PrivacyError(f"usuario {user_id} nao encontrado")
        pacote: dict = {
            "user_id": user_id,
            "exportado_em": _stamp(self._agora()),
            "dados": {},
        }
        usuario = self._connection.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        pacote["dados"]["users"] = [_limpar(usuario)]
        for tabela in EXPORTAVEIS:
            linhas = self._connection.execute(
                f"SELECT * FROM {tabela} WHERE user_id = ?", (user_id,)
            ).fetchall()
            pacote["dados"][tabela] = [_limpar(linha) for linha in linhas]
        self._record(user_id, EXPORTACAO)
        return pacote

    def export_json(self, user_id: str) -> str:
        return json.dumps(self.export_account(user_id), ensure_ascii=False, indent=2)

    # --------------------------------------------------------- retencao
    def purge_inactive(self) -> list[str]:
        """Apaga curriculo e extracao de quem ficou sem sessao alem do periodo."""
        limite = _stamp(
            self._agora()
            - timedelta(days=self._config.retention.periodo_sem_sessao_dias)
        )
        candidatos = self._connection.execute(
            "SELECT user_id FROM users WHERE coalesce(ultima_sessao_em, criado_em) <= ?",
            (limite,),
        ).fetchall()
        expurgados = []
        for linha in candidatos:
            user_id = linha["user_id"]
            scope = self._repository.for_user(user_id)
            scope.delete("resume_extractions")
            scope.delete("resumes")
            expurgados.append(user_id)
        return expurgados

    # ------------------------------------------------------------ registro
    def operations(self, user_id: str | None = None) -> list[DataSubjectOperation]:
        if user_id:
            linhas = self._repository.select(
                "data_subject_ops", where="user_id = ?", params=(user_id,),
                order_by="rowid",
            )
        else:
            linhas = self._repository.select("data_subject_ops", order_by="rowid")
        return [
            DataSubjectOperation(
                op_id=linha["op_id"], user_id=linha["user_id"],
                tipo=linha["tipo"], instante=linha["instante"],
            )
            for linha in linhas
        ]

    def _record(self, user_id: str, tipo: str) -> DataSubjectOperation:
        operacao = DataSubjectOperation(
            op_id=str(uuid.uuid4()), user_id=user_id, tipo=tipo,
            instante=_stamp(self._agora()),
        )
        self._repository.insert(
            "data_subject_ops",
            {
                "op_id": operacao.op_id, "user_id": operacao.user_id,
                "tipo": operacao.tipo, "instante": operacao.instante,
            },
        )
        return operacao

    def _exists(self, user_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            is not None
        )


def _limpar(linha) -> dict:
    if linha is None:
        return {}
    return {
        chave: valor
        for chave, valor in dict(linha).items()
        if chave not in COLUNAS_OMITIDAS
    }


def _stamp(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")
