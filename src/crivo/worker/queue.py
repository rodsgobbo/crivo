"""Fila de runs no proprio banco, e o ciclo de vida do registro de run.

A fila vive no banco por dois motivos. O primeiro e dispensar um servico
separado que o operador teria de manter. O segundo, mais importante, e que a
reserva de um run e a gravacao do seu resultado passam a caber na mesma
transacao, o que elimina a janela em que um run aparece reservado sem que nada
esteja executando.

A reserva usa transacao exclusiva curta e nao reserva com salto de linhas
travadas: existe um unico consumidor de runs e um unico de enriquecimento, de
modo que nunca ha dois leitores disputando a mesma linha.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..store.migrations import StoreError

#: Estados possiveis de um run. A ordem reflete o caminho normal.
ENFILEIRADO = "enfileirado"
EM_ANDAMENTO = "em_andamento"
AGUARDANDO_ENRIQUECIMENTO = "aguardando_enriquecimento"
CONCLUIDO = "concluido"
INTERROMPIDO = "interrompido"
RECUSADO = "recusado"

#: Estados a partir dos quais o run ainda vai consumir trabalho.
ESTADOS_ATIVOS = (ENFILEIRADO, EM_ANDAMENTO, AGUARDANDO_ENRIQUECIMENTO)

#: Estados que um consumidor de runs pode reservar.
ESTADOS_RESERVAVEIS = (ENFILEIRADO,)


class QueueError(StoreError):
    """Operacao invalida sobre a fila ou sobre o registro de run."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat(timespec="seconds")


def _as_json(value: Any) -> str:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


class RunQueue:
    """Enfileira, reserva e faz o registro de run avancar de estado."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # ------------------------------------------------------------ escrita
    def enqueue(
        self,
        user_id: str,
        janela: str = "incremental",
        config_efetiva: Any = None,
        janela_horas: int | None = None,
    ) -> str:
        """Cria um run enfileirado e devolve o seu identificador.

        Recusa quando o usuario ja tem run ativo: capacidade de coleta e um
        recurso compartilhado, e dois runs do mesmo usuario disputando-a nao
        entregam nada mais rapido.
        """
        if janela not in ("incremental", "ampla"):
            raise QueueError(
                f"janela {janela!r}: esperado 'incremental' ou 'ampla'"
            )
        run_id = str(uuid.uuid4())
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            ativo = self._connection.execute(
                "SELECT run_id FROM runs WHERE user_id = ? AND estado IN "
                f"({', '.join('?' * len(ESTADOS_ATIVOS))}) LIMIT 1",
                (user_id, *ESTADOS_ATIVOS),
            ).fetchone()
            if ativo is not None:
                self._connection.execute(
                    "INSERT INTO runs (run_id, user_id, estado, janela, "
                    "solicitado_em, encerrado_em, motivo_recusa) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, user_id, RECUSADO, janela, _stamp(), _stamp(),
                        f"run {ativo['run_id']} ainda ativo para este usuario",
                    ),
                )
                self._connection.execute("COMMIT")
                raise QueueError(
                    f"usuario {user_id} ja possui o run {ativo['run_id']} ativo; "
                    f"a solicitacao foi registrada como recusada em {run_id}"
                )
            self._connection.execute(
                "INSERT INTO runs (run_id, user_id, estado, janela, "
                "solicitado_em, config_efetiva, janela_horas) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, user_id, ENFILEIRADO, janela, _stamp(),
                    _as_json(config_efetiva) if config_efetiva is not None else None,
                    janela_horas,
                ),
            )
            self._connection.execute("COMMIT")
        except QueueError:
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise QueueError(f"enfileirar run: {type(exc).__name__}: {exc}") from exc
        return run_id

    def reserve_next(self) -> sqlite3.Row | None:
        """Reserva o run enfileirado mais antigo, ou devolve `None` se nao ha."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM runs WHERE estado IN "
                f"({', '.join('?' * len(ESTADOS_RESERVAVEIS))}) "
                "ORDER BY solicitado_em, run_id LIMIT 1",
                ESTADOS_RESERVAVEIS,
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            self._connection.execute(
                "UPDATE runs SET estado = ?, iniciado_em = ?, heartbeat_em = ? "
                "WHERE run_id = ?",
                (EM_ANDAMENTO, _stamp(), _stamp(), row["run_id"]),
            )
            reserved = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (row["run_id"],)
            ).fetchone()
            self._connection.execute("COMMIT")
            return reserved
        except sqlite3.Error as exc:
            self._rollback()
            raise QueueError(f"reservar run: {type(exc).__name__}: {exc}") from exc

    def heartbeat(self, run_id: str) -> None:
        """Marca que o run continua vivo."""
        self._update(run_id, {"heartbeat_em": _stamp()})

    def suspend(self, run_id: str) -> None:
        """Passa o run a aguardando enriquecimento e solta o worker."""
        self._update(
            run_id,
            {"estado": AGUARDANDO_ENRIQUECIMENTO, "heartbeat_em": _stamp()},
        )

    def resume(self, run_id: str) -> None:
        """Devolve a fila um run cujo enriquecimento terminou."""
        self._update(run_id, {"estado": ENFILEIRADO, "heartbeat_em": _stamp()})

    # -------------------------------------------------------- cancelamento
    def request_cancel(self, run_id: str, user_id: str | None = None) -> bool:
        """Registra o pedido de parada. Devolve `False` se o run ja acabou.

        Nao muda o estado do run. Quem clica esta na face web; quem executa a
        coleta e outro processo, tipicamente no meio de uma sequencia de
        requisicoes a origem. Encerrar o run por baixo dele deixaria requisicao
        pela metade, e o processo executor voltaria em seguida gravando sobre um
        run que a web ja considera morto. O pedido fica gravado, e quem manda
        parar de verdade e quem esta executando.

        `user_id` existe para que a rota nao consiga cancelar run de terceiro
        por adivinhar um identificador.
        """
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            condicao = "run_id = ? AND estado IN " \
                f"({', '.join('?' * len(ESTADOS_ATIVOS))})"
            params: list[Any] = [run_id, *ESTADOS_ATIVOS]
            if user_id is not None:
                condicao += " AND user_id = ?"
                params.append(user_id)
            cursor = self._connection.execute(
                f"UPDATE runs SET cancelado_em = ? WHERE {condicao} "
                "AND cancelado_em IS NULL",
                (_stamp(), *params),
            )
            self._connection.execute("COMMIT")
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            self._rollback()
            raise QueueError(
                f"cancelar run {run_id}: {type(exc).__name__}: {exc}"
            ) from exc

    def cancel_requested(self, run_id: str) -> bool:
        """O usuario pediu para parar este run?"""
        row = self._connection.execute(
            "SELECT cancelado_em FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return bool(row and row["cancelado_em"])

    def cancel(self, run_id: str, **counters: Any) -> None:
        """Encerra o run atendendo ao pedido de parada.

        Termina em `interrompido`, e nao em estado proprio: o que interessa a
        todo o resto -- relatorio, cota, recusa de run concorrente -- e que o
        run acabou sem concluir, e o motivo ja vai escrito no proprio registro.
        Um estado a mais obrigaria cada um desses lugares a aprender uma
        palavra nova para dizer o que ja sabiam dizer.
        """
        payload: dict[str, Any] = {
            "estado": INTERROMPIDO,
            "encerrado_em": _stamp(),
            "heartbeat_em": _stamp(),
            "motivo_recusa": "cancelado por você",
        }
        payload.update(_normalize_counters(counters))
        self._update(run_id, payload)

    def record_counters(self, run_id: str, **counters: Any) -> None:
        """Grava contadores sem encerrar o run.

        Existe para o trecho que termina em suspensao: o que ele contou precisa
        estar no banco antes de o worker ser solto, porque o contexto em
        memoria nao atravessa a espera do enriquecimento.
        """
        if not counters:
            return
        self._update(run_id, _normalize_counters(counters))

    def fail(self, run_id: str, motivo: str) -> None:
        """Encerra um run que quebrou, nomeando o estagio e a causa.

        Existe porque a alternativa era a excecao subir ate o laco do processo.
        Um run defeituoso derrubava o executor inteiro -- e, como ele continuava
        `em_andamento`, a partida seguinte o devolvia a fila e morria de novo.
        Um run ruim parava os runs de todos os usuarios, em ciclo, e o sintoma
        era o processo sumir da janela sem ninguem entender por que.
        """
        self._update(
            run_id,
            {
                "estado": INTERROMPIDO,
                "encerrado_em": _stamp(_now()),
                # Truncado porque a mensagem carrega o texto da excecao, que nao
                # tem tamanho previsivel.
                "motivo_recusa": str(motivo)[:500],
            },
        )

    def finish(self, run_id: str, **counters: Any) -> None:
        """Encerra o run, gravando os contadores do resultado."""
        payload: dict[str, Any] = {
            "estado": CONCLUIDO,
            "encerrado_em": _stamp(),
            "heartbeat_em": _stamp(),
        }
        payload.update(_normalize_counters(counters))
        self._update(run_id, payload)

    def record_synthesis(
        self,
        run_id: str,
        provedor: str | None = None,
        modelo: str | None = None,
        tokens_entrada: int | None = None,
        tokens_saida: int | None = None,
        falha: str | None = None,
        texto: str | None = None,
    ) -> None:
        """Grava a proveniencia e o custo da sintese no registro do run."""
        self._update(
            run_id,
            {
                "provedor_sintese": provedor,
                "modelo_sintese": modelo,
                "tokens_entrada": tokens_entrada or None,
                "tokens_saida": tokens_saida or None,
                "falha_sintese": falha,
                "sintese": texto,
            },
        )

    def record_effective_config(self, run_id: str, config: Any) -> None:
        """Grava a configuracao que produziu este run."""
        self._update(run_id, {"config_efetiva": _as_json(config)})

    def reclaim_abandoned(self) -> list[str]:
        """Devolve a fila os runs que ficaram sem dono, e diz quais foram.

        Chamada na partida do processo de runs, que e unico na instalacao: se
        um run esta `em_andamento` no momento em que este processo sobe, ele so
        pode pertencer a um antecessor que morreu, porque ninguem mais executa
        estagio. Sem isto, reiniciar o servidor no meio de uma coleta prendia o
        run ate o teto de duracao -- seis horas em que todo pedido novo do
        usuario era recusado por concorrencia com um run que ninguem estava
        executando.

        Devolver a `enfileirado` e nao interromper: os estagios ja concluidos
        ficam gravados, entao a retomada continua de onde parou em vez de
        repetir requisicao paga.
        """
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                "SELECT run_id FROM runs WHERE estado = ?", (EM_ANDAMENTO,)
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    "UPDATE runs SET estado = ?, heartbeat_em = ? WHERE run_id = ?",
                    (ENFILEIRADO, _stamp(), row["run_id"]),
                )
            self._connection.execute("COMMIT")
            return [row["run_id"] for row in rows]
        except sqlite3.Error as exc:
            self._rollback()
            raise QueueError(
                f"recuperar runs abandonados: {type(exc).__name__}: {exc}"
            ) from exc

    def reap_stalled(self, max_duration_s: int) -> list[str]:
        """Interrompe runs cujo heartbeat venceu e devolve os identificadores.

        Sem isto, um processo morto prenderia a vez do usuario para sempre: o
        run ficaria ativo, e a recusa de run concorrente nunca deixaria outro
        entrar.
        """
        limite = _stamp(_now() - timedelta(seconds=max_duration_s))
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                "SELECT run_id FROM runs WHERE estado IN "
                f"({', '.join('?' * len(ESTADOS_ATIVOS))}) "
                # Comparacao inclusiva: os carimbos sao truncados em segundos, e com `<`
                # um heartbeat no mesmo segundo do corte escaparia da interrupcao.
                "AND heartbeat_em IS NOT NULL AND heartbeat_em <= ?",
                (*ESTADOS_ATIVOS, limite),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    "UPDATE runs SET estado = ?, encerrado_em = ?, "
                    "motivo_recusa = ? WHERE run_id = ?",
                    (
                        INTERROMPIDO, _stamp(),
                        f"sem sinal de vida por mais de {max_duration_s}s",
                        row["run_id"],
                    ),
                )
            self._connection.execute("COMMIT")
            return [row["run_id"] for row in rows]
        except sqlite3.Error as exc:
            self._rollback()
            raise QueueError(f"interromper runs: {type(exc).__name__}: {exc}") from exc

    # ------------------------------------------------------------ leitura
    def get(self, run_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def pending_count(self) -> int:
        return self._connection.execute(
            "SELECT count(*) FROM runs WHERE estado = ?", (ENFILEIRADO,)
        ).fetchone()[0]

    # ------------------------------------------------------------ interno
    def _update(self, run_id: str, values: Mapping[str, Any]) -> None:
        assignments = ", ".join(f"{column} = ?" for column in values)
        try:
            cursor = self._connection.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?",
                (*values.values(), run_id),
            )
        except sqlite3.Error as exc:
            raise QueueError(
                f"atualizar run {run_id}: {type(exc).__name__}: {exc}"
            ) from exc
        if cursor.rowcount == 0:
            raise QueueError(f"run {run_id} nao encontrado")

    def _rollback(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


_COUNTER_COLUMNS = frozenset({
    "n_brutos", "n_filtrados", "n_novos", "cota_consumida", "cota_esgotada",
    "bloqueios", "buscas", "provedor_sintese", "modelo_sintese",
    "tokens_entrada", "tokens_saida", "falha_sintese",
})


def _normalize_counters(counters: Mapping[str, Any]) -> dict[str, Any]:
    desconhecidas = set(counters) - _COUNTER_COLUMNS
    if desconhecidas:
        raise QueueError(
            f"contador desconhecido {sorted(desconhecidas)}; "
            f"esperado um de {sorted(_COUNTER_COLUMNS)}"
        )
    payload = dict(counters)
    if isinstance(payload.get("buscas"), (list, tuple, dict)):
        payload["buscas"] = _as_json(payload["buscas"])
    return payload
