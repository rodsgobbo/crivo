"""Ciclo de vida da vaga.

O estado guarda duas coisas diferentes, e e bom que nao se confundam: a decisao
do usuario (visto, aplicado, descartado) e o fato de a vaga ter fechado
(expirado).

EXPIRADO e reservado a vaga que a propria origem declara encerrada -- a marca
"Nao aceita mais candidaturas", vista na leitura da descricao ou na
reconferencia do topo (`pipeline/enricher.py`, `expirar_encerrada`).

Havia uma segunda via, a expiracao por ausencia: tres runs seguidos cuja janela
cobria a vaga sem traze-la, e ela expirava. Ela foi aposentada em 23/10/2026
sem nunca ter rodado -- existia, tinha teste, e nenhum chamador. Liga-la teria
escondido vaga aberta: cada termo traz no maximo vinte e cinco resultados, e uma
vaga aberta fica de fora deles com facilidade; ausencia nao prova que saiu do
ar. E como reaparecer nao desfaz o estado, a vaga expirada por engano ficaria
escondida do relatorio para sempre. O que a regra queria -- nao acumular vaga
morta -- e feito pelo expurgo (`store/expurgo.py`), que apaga a vaga que nenhuma
busca traz ha trinta dias.
"""

from __future__ import annotations

from .repository import Repository

NOVO = "novo"
VISTO = "visto"
APLICADO = "aplicado"
DESCARTADO = "descartado"
EXPIRADO = "expirado"

#: Estados que o proprio usuario pode escolher.
ESCOLHAS_DO_USUARIO = (VISTO, APLICADO, DESCARTADO)


class LifecycleError(Exception):
    """Transicao de estado invalida."""


class JobLifecycle:
    """Aplica a marcacao que o usuario escolhe para a vaga."""

    def __init__(self, connection) -> None:
        self._repository = Repository(connection)

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


def esquecer_contatos(repository: Repository, job_id: str) -> bool:
    """Apaga os e-mails de contato quando ninguem mais tem a vaga ativa.

    O endereco de e-mail de um recrutador e dado pessoal de um terceiro que
    nunca usou o sistema, e a unica finalidade que justificava guarda-lo era
    permitir a candidatura. Vaga encerrada nao tem candidatura, entao a
    finalidade acabou e o dado sai -- o texto da descricao fica, porque nao e
    dado pessoal e serve o registro compartilhado.

    A checagem por outros usuarios existe porque o registro e compartilhado:
    quem se candidatou mantem a vaga, e com ela o contato.
    """
    ainda_ativa = repository.execute(
        "SELECT 1 FROM jobs WHERE job_id = ? AND estado != ? LIMIT 1",
        (job_id, EXPIRADO),
    ).fetchone()
    if ainda_ativa:
        return False
    repository.execute(
        "UPDATE job_descriptions SET emails_contato = '[]' WHERE job_id = ?",
        (job_id,),
    )
    return True
