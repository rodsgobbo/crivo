"""Ciclo de vida da vaga e regra de expiracao.

A regra de expiracao e o ponto delicado deste modulo, e o motivo esta na
interacao entre dois requisitos que parecem independentes. O run recorrente
restringe a busca a uma janela curta de publicacao; a expiracao conta ausencias
em runs consecutivos. Lidos juntos de forma ingenua, toda vaga expiraria em tres
dias: um run de vinte e quatro horas jamais reencontra uma vaga publicada ha
vinte dias, e essa ausencia nao significa que ela saiu do ar.

Por isso a ausencia so conta quando a janela do run de fato cobria a data de
publicacao da vaga. O contador vive no proprio registro da vaga e zera a cada
reaparecimento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .repository import Repository

NOVO = "novo"
VISTO = "visto"
APLICADO = "aplicado"
DESCARTADO = "descartado"
EXPIRADO = "expirado"

#: Estados que o proprio usuario pode escolher.
ESCOLHAS_DO_USUARIO = (VISTO, APLICADO, DESCARTADO)

#: Estados a partir dos quais a vaga ainda pode expirar.
EXPIRAVEIS = (NOVO, VISTO)

#: Ausencias elegiveis que caracterizam desaparecimento.
LIMITE_DE_AUSENCIAS = 3


class LifecycleError(Exception):
    """Transicao de estado invalida."""


@dataclass(frozen=True)
class AbsenceSweep:
    """Resultado de uma varredura de ausencia."""

    contadas: list[str]
    expiradas: list[str]
    ignoradas: list[str]


class JobLifecycle:
    """Faz a vaga avancar de estado e aplica a expiracao por ausencia."""

    def __init__(self, connection) -> None:
        self._repository = Repository(connection)

    # ------------------------------------------------------------ escolha
    def mark(self, user_id: str, job_id: str, estado: str) -> None:
        """Aplica a marcacao escolhida pelo usuario."""
        if estado not in ESCOLHAS_DO_USUARIO:
            raise LifecycleError(
                f"estado {estado!r} nao pode ser escolhido pelo usuario; "
                f"esperado um de {list(ESCOLHAS_DO_USUARIO)}"
            )
        alteradas = self._repository.for_user(user_id).update(
            "jobs", {"estado": estado}, where="job_id = ?", params=(job_id,)
        )
        if alteradas == 0:
            raise LifecycleError(f"vaga {job_id} nao encontrada para este usuario")

    def state_of(self, user_id: str, job_id: str) -> str | None:
        linhas = self._repository.for_user(user_id).select(
            "jobs", where="job_id = ?", params=(job_id,), columns="estado"
        )
        return linhas[0]["estado"] if linhas else None

    # ---------------------------------------------------------- expiracao
    def sweep_absences(
        self,
        user_id: str,
        vistos_no_run: set[str],
        janela_horas: int,
        agora: datetime | None = None,
    ) -> AbsenceSweep:
        """Conta ausencia apenas nas vagas que a janela do run cobria."""
        agora = agora or datetime.now(timezone.utc)
        limite = (agora - timedelta(hours=janela_horas)).date()

        contadas: list[str] = []
        expiradas: list[str] = []
        ignoradas: list[str] = []

        scope = self._repository.for_user(user_id)
        for linha in scope.select(
            "jobs",
            where=f"estado IN ({', '.join('?' * len(EXPIRAVEIS))})",
            params=EXPIRAVEIS,
        ):
            job_id = linha["job_id"]
            if job_id in vistos_no_run:
                continue
            if not _coberta(
                linha["publicada_em"], linha["primeira_vez_em"], limite
            ):
                # Fora da janela do run: a ausencia nao informa nada.
                ignoradas.append(job_id)
                continue

            ausencias = int(linha["ausencias_elegiveis"] or 0) + 1
            if ausencias >= LIMITE_DE_AUSENCIAS:
                scope.update(
                    "jobs",
                    {"estado": EXPIRADO, "ausencias_elegiveis": ausencias},
                    where="job_id = ?",
                    params=(job_id,),
                )
                expiradas.append(job_id)
                self._forget_contacts(job_id)
            else:
                scope.update(
                    "jobs",
                    {"ausencias_elegiveis": ausencias},
                    where="job_id = ?",
                    params=(job_id,),
                )
                contadas.append(job_id)

        return AbsenceSweep(
            contadas=contadas, expiradas=expiradas, ignoradas=ignoradas
        )

    def _forget_contacts(self, job_id: str) -> None:
        """Esquece os contatos da vaga quando ninguem mais a tem ativa.

        O endereco de e-mail de um recrutador e dado pessoal de um terceiro que
        nunca usou o sistema, e a unica finalidade que justificava guarda-lo era
        permitir a candidatura. Vaga expirada nao tem candidatura, entao a
        finalidade acabou e o dado sai -- o texto da descricao fica, porque nao
        e dado pessoal e serve o registro compartilhado.

        A checagem por outros usuarios existe porque o registro e compartilhado:
        expirar para um nao expira para todos.
        """
        ainda_ativa = self._repository.execute(
            "SELECT 1 FROM jobs WHERE job_id = ? AND estado != ? LIMIT 1",
            (job_id, EXPIRADO),
        ).fetchone()
        if ainda_ativa:
            return
        self._repository.execute(
            "UPDATE job_descriptions SET emails_contato = '[]' WHERE job_id = ?",
            (job_id,),
        )

    def absences_of(self, user_id: str, job_id: str) -> int:
        linhas = self._repository.for_user(user_id).select(
            "jobs", where="job_id = ?", params=(job_id,), columns="ausencias_elegiveis"
        )
        return int(linhas[0]["ausencias_elegiveis"] or 0) if linhas else 0


def _coberta(
    publicada_em: str | None, primeira_vez_em: str | None, limite: date
) -> bool:
    """A janela do run alcancava esta vaga?

    A origem devolve data de publicacao em pouco mais da metade das vagas. Sem
    um substituto, as demais nunca acumulariam ausencia e nunca expirariam --
    o acervo do usuario cresceria indefinidamente com vagas que sairam do ar.

    O instante da primeira vez que vimos a vaga e o substituto: se ela apareceu
    dentro da janela, a janela a alcancava.
    """
    for valor in (publicada_em, primeira_vez_em):
        if not valor:
            continue
        try:
            referencia = date.fromisoformat(str(valor)[:10])
        except ValueError:
            continue
        return referencia >= limite
    # Sem data nenhuma nao da para afirmar nada; nao contar preserva a vaga.
    return False
