"""Acesso ao banco com o filtro de usuario injetado pela propria camada.

O ponto deste modulo e que o isolamento entre usuarios nao dependa de quem
escreve a consulta. Uma consulta a tabela de dado de usuario so existe a partir
de um escopo ligado a um identificador, e o filtro entra na clausula sem que o
chamador precise escreve-lo. Um `SELECT` sem escopo sobre tabela de usuario nao
retorna nada: ele levanta erro.

As tabelas sao classificadas em tres grupos, e o grupo decide o comportamento.
Tabela de usuario recebe filtro. Tabela compartilhada nao recebe, porque nao
tem dono. Tabela de auditoria nao recebe e nao apaga em cascata, porque o
registro de uma exclusao nao pode sumir junto com a conta excluida.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .migrations import StoreError

#: Tabelas cujo dado pertence a um usuario. Toda consulta recebe o filtro.
USER_SCOPED_TABLES = frozenset({
    "sessions", "linkedin_connections", "provider_credentials", "resumes",
    "resume_extractions", "profile_versions", "runs", "jobs", "scores",
    "discards", "daily_quota", "enrichment_requests", "buscas_do_usuario",
    "extractor_tokens",
})

#: Tabelas sem dono. `job_descriptions` e compartilhada por decisao de projeto.
SHARED_TABLES = frozenset({
    "job_descriptions", "collection_blocks", "schema_meta", "users",
    "auth_flows",
})

#: Tabelas de evidencia. Sobrevivem a exclusao da conta a que se referem.
AUDIT_TABLES = frozenset({"data_subject_ops", "access_denials"})

KNOWN_TABLES = USER_SCOPED_TABLES | SHARED_TABLES | AUDIT_TABLES


class UnknownTableError(StoreError):
    """Tabela fora da classificacao: o comportamento de isolamento seria indefinido."""


class ScopeRequiredError(StoreError):
    """Consulta a tabela de usuario sem escopo ligado."""


class CrossUserAccessError(StoreError):
    """Tentativa de alcancar recurso pertencente a outro usuario."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _classify(table: str) -> str:
    if table in USER_SCOPED_TABLES:
        return "user"
    if table in SHARED_TABLES:
        return "shared"
    if table in AUDIT_TABLES:
        return "audit"
    raise UnknownTableError(
        f"{table}: tabela nao classificada; declare-a em USER_SCOPED_TABLES, "
        "SHARED_TABLES ou AUDIT_TABLES antes de acessa-la"
    )


class Repository:
    """Porta de entrada ao banco. Nao acessa tabela de usuario por conta propria."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def for_user(self, user_id: str) -> "ScopedRepository":
        """Devolve um repositorio ligado a este usuario."""
        if not user_id:
            raise ScopeRequiredError(
                "identificador de usuario vazio; o escopo precisa nomear um usuario"
            )
        return ScopedRepository(self._connection, user_id)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Executa uma instrucao livre. Uso restrito a tabelas sem dono."""
        return _run(self._connection, sql, params)

    def select(
        self,
        table: str,
        where: str | None = None,
        params: Sequence[Any] = (),
        columns: str = "*",
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        kind = _classify(table)
        if kind == "user":
            raise ScopeRequiredError(
                f"{table}: tabela de dado de usuario exige escopo; "
                "use Repository.for_user(...) antes de consultar"
            )
        sql = f"SELECT {columns} FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return _run(self._connection, sql, params).fetchall()

    def insert(self, table: str, values: Mapping[str, Any]) -> None:
        _classify(table)
        _insert(self._connection, table, values)

    def record_denial(self, user_id: str, recurso: str) -> None:
        """Registra uma tentativa de alcancar recurso de outro usuario."""
        _insert(
            self._connection,
            "access_denials",
            {
                "denial_id": str(uuid.uuid4()),
                "user_id": user_id,
                "recurso": recurso,
                "instante": _now(),
            },
        )


class ScopedRepository:
    """Repositorio ligado a um usuario. Injeta o filtro em toda consulta."""

    def __init__(self, connection: sqlite3.Connection, user_id: str) -> None:
        self._connection = connection
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id

    def _guard(self, table: str, values: Mapping[str, Any] | None = None) -> str:
        kind = _classify(table)
        if values is not None and "user_id" in values:
            informado = values["user_id"]
            if informado != self._user_id:
                self._deny(f"{table}:{informado}")
                raise CrossUserAccessError(
                    f"{table}: escopo pertence a {self._user_id} e a operacao "
                    f"nomeia {informado}"
                )
        return kind

    def _deny(self, recurso: str) -> None:
        # Gravado fora da transacao do chamador: a evidencia da tentativa precisa
        # sobreviver ao rollback que a propria recusa vai provocar.
        try:
            Repository(self._connection).record_denial(self._user_id, recurso)
        except StoreError:
            pass

    def select(
        self,
        table: str,
        where: str | None = None,
        params: Sequence[Any] = (),
        columns: str = "*",
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        kind = self._guard(table)
        sql = f"SELECT {columns} FROM {table}"
        bound: list[Any] = []
        clauses: list[str] = []
        if kind == "user":
            clauses.append("user_id = ?")
            bound.append(self._user_id)
        if where:
            clauses.append(f"({where})")
            bound.extend(params)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return _run(self._connection, sql, bound).fetchall()

    def insert(self, table: str, values: Mapping[str, Any]) -> None:
        kind = self._guard(table, values)
        payload = dict(values)
        if kind == "user":
            payload["user_id"] = self._user_id
        _insert(self._connection, table, payload)

    def update(
        self,
        table: str,
        values: Mapping[str, Any],
        where: str | None = None,
        params: Sequence[Any] = (),
    ) -> int:
        kind = self._guard(table, values)
        payload = {k: v for k, v in values.items() if k != "user_id"}
        if not payload:
            raise StoreError(f"{table}: atualizacao sem colunas a alterar")
        assignments = ", ".join(f"{column} = ?" for column in payload)
        sql = f"UPDATE {table} SET {assignments}"
        bound: list[Any] = list(payload.values())
        clauses: list[str] = []
        if kind == "user":
            clauses.append("user_id = ?")
            bound.append(self._user_id)
        if where:
            clauses.append(f"({where})")
            bound.extend(params)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return _run(self._connection, sql, bound).rowcount

    def delete(
        self, table: str, where: str | None = None, params: Sequence[Any] = ()
    ) -> int:
        kind = self._guard(table)
        sql = f"DELETE FROM {table}"
        bound: list[Any] = []
        clauses: list[str] = []
        if kind == "user":
            clauses.append("user_id = ?")
            bound.append(self._user_id)
        if where:
            clauses.append(f"({where})")
            bound.extend(params)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return _run(self._connection, sql, bound).rowcount


def _insert(
    connection: sqlite3.Connection, table: str, values: Mapping[str, Any]
) -> None:
    if not values:
        raise StoreError(f"{table}: insercao sem colunas")
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    _run(
        connection,
        f"INSERT INTO {table} ({columns}) VALUES ({marks})",
        list(values.values()),
    )


def _run(
    connection: sqlite3.Connection, sql: str, params: Iterable[Any] = ()
) -> sqlite3.Cursor:
    """Executa nomeando a operacao e a causa quando a gravacao falha."""
    try:
        return connection.execute(sql, tuple(params))
    except sqlite3.Error as exc:
        operacao = sql.strip().split(maxsplit=3)
        nome = " ".join(operacao[:3]) if operacao else sql
        raise StoreError(f"{nome}: {type(exc).__name__}: {exc}") from exc
