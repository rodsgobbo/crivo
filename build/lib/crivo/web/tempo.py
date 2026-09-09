"""Apresentacao de carimbo de tempo, num lugar so.

O banco grava tudo em UTC de proposito: contador diario, ordenacao de fila e
comparacao de heartbeat dependem de um relogio unico, e misturar fuso local ali
faria o limite diario deixar de valer das 21h a meia-noite no horario de
Brasilia -- defeito que o proprio agendador ja registra ter tido.

Guardar em UTC e mostrar em UTC, porem, sao decisoes diferentes. Quem le a tela
esta num fuso, e um run pedido as 09:21 aparecendo como "12:21" nao e um detalhe
de precisao: e a pagina afirmando algo que o usuario sabe ser falso, o que
derruba a confianca no resto do que ela mostra.

A conversao usa o fuso do proprio processo. Numa instalacao local isso e o fuso
de quem esta olhando; num servidor remoto seria preciso saber o fuso do usuario,
e ai a escolha certa passa a ser outra -- por isso a funcao vive aqui, e nao
espalhada pelos gabaritos.
"""

from __future__ import annotations

from datetime import datetime, timezone


def local(carimbo, formato: str = "%d/%m %H:%M") -> str:
    """Converte um carimbo ISO em UTC para a hora local, ja formatada.

    Devolve o texto original quando nao consegue interpreta-lo: um carimbo
    ilegivel e melhor exibido cru do que escondido atras de um traco.
    """
    if not carimbo:
        return ""
    texto = str(carimbo)
    try:
        instante = datetime.fromisoformat(texto)
    except ValueError:
        return texto
    # Carimbo sem fuso vem do banco, onde tudo e UTC por convencao.
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return instante.astimezone().strftime(formato)


def local_completo(carimbo) -> str:
    """Forma longa, para o cabecalho de um relatorio."""
    return local(carimbo, "%d/%m/%Y %H:%M")
