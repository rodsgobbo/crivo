"""Pontuacao de aderencia, deterministica e sem modelo de linguagem.

Duas propriedades sustentam este modulo.

A primeira e o determinismo. A mesma entrada precisa produzir o mesmo score,
porque o score ordena a fila de candidatura e o historico dele e o unico
indicador de que mexer no perfil adiantou. Um score que variasse sozinho tornaria
essa medida inutil.

A segunda e a ordem das operacoes. Bonus e penalidades sao aplicados sobre a
soma ponderada e so entao o resultado e limitado ao intervalo valido. Limitar
antes produziria valores diferentes para a mesma entrada, porque um bonus sobre
um valor ja teto some e uma penalidade sobre um valor ja piso tambem.

O scorer roda duas vezes por run com a mesma funcao e entradas diferentes. A
passada provisoria usa apenas o que a rota publica entregou e serve para ordenar
a fila de enriquecimento; a final acrescenta os sinais obtidos sob sessao. Sao
duas passadas porque escolher o alvo do orcamento pelo score final seria
circular: o score final depende justamente do que o orcamento pagaria.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..profile.seniority import ESCALA, PLENO, infer
from .ontology import Ontology, normalize

PROVISORIA = "provisoria"
FINAL = "final"

#: Proximidade de setor. Sem informacao, assume-se adjacente.
DOMINIO_IGUAL = 1.0
DOMINIO_ADJACENTE = 0.6
DOMINIO_DISTANTE = 0.3

#: Compatibilidade geografica por modelo de trabalho.
GEO_REMOTO = 1.0
GEO_NO_RAIO = 0.8
GEO_FORA_DO_RAIO = 0.15


@dataclass(frozen=True)
class ScoreBreakdown:
    """Score final e o valor de cada componente que o compos."""

    score: int
    componentes: dict[str, float]
    ajustes: dict[str, int] = field(default_factory=dict)
    bruto: float = 0.0
    descricao_disponivel: bool = False
    sinais_disponiveis: bool = False
    passada: str = PROVISORIA
    #: Tetos que a nota nao pode ultrapassar, por motivo. Ficam registrados
    #: mesmo quando nao morderam, porque o relatorio precisa explicar a nota e
    #: "por que nao e mais alta" e a pergunta que o usuario faz primeiro.
    tetos: dict[str, int] = field(default_factory=dict)

    @property
    def teto_aplicado(self) -> str | None:
        """O teto que de fato limitou a nota, se algum limitou."""
        if not self.tetos:
            return None
        motivo = min(self.tetos, key=lambda k: self.tetos[k])
        return motivo if self.tetos[motivo] <= self.score else None


#: Peso do desconhecido na proporcao de competencias. Uma vaga que cita tres
#: requisitos e uma que cita doze nao merecem a mesma confianca, e sem esta
#: suavizacao a primeira levava vantagem: casar 3 de 3 dava 1.0 e casar 6 de 8
#: dava 0.75. Descricao pobre era premiada.
SUAVIZACAO = 3.0


#: Componente quando nao houve o que ler. Neutro de proposito: sem descricao,
#: a ausencia de competencia nao informa nada sobre a vaga.
DESCONHECIDO = 0.5

#: Componente quando a descricao FOI lida e nao trouxe competencia tecnica
#: alguma. Nao e zero porque a falha de leitura pode ser nossa -- a ontologia
#: pode nao cobrir o vocabulario daquela vaga -- mas nao pode ser o valor
#: neutro: uma descricao inteira sem um unico termo reconhecido e evidencia de
#: que a vaga esta fora do dominio, e nao ausencia de evidencia.
#:
#: Enquanto os dois casos devolviam o mesmo 0,5, uma vaga de planejamento
#: financeiro pontuava 78% sem uma lacuna sequer, acima de uma vaga de
#: infraestrutura em que o perfil casava 4 de 10 requisitos. Nao ter o que
#: medir vencia ter medido.
SEM_SINAL_TECNICO = 0.15


def _componente_competencias(
    perfil: frozenset, vaga: frozenset, tem_descricao: bool = False
) -> float:
    if not vaga:
        return SEM_SINAL_TECNICO if tem_descricao else DESCONHECIDO
    casadas = len(perfil & vaga)
    return (casadas + SUAVIZACAO * 0.5) / (len(vaga) + SUAVIZACAO)


def _componente_nivel(nivel_perfil: str, nivel_vaga: str) -> float:
    try:
        distancia = ESCALA.index(nivel_vaga) - ESCALA.index(nivel_perfil)
    except ValueError:
        return 0.5
    if distancia == 0:
        return 1.0
    if distancia == 1:
        return 0.85
    if distancia == -1:
        return 0.5
    return 0.1


def _componente_dominio(setores_perfil, setor_vaga: str | None) -> float:
    if not setor_vaga or not setores_perfil:
        return DOMINIO_ADJACENTE
    alvo = normalize(setor_vaga)
    normalizados = {normalize(s) for s in setores_perfil}
    if alvo in normalizados:
        return DOMINIO_IGUAL
    return DOMINIO_ADJACENTE


def _componente_escopo(liderados_perfil: int, liderados_vaga: int | None) -> float:
    if not liderados_vaga:
        return 0.6
    if not liderados_perfil:
        return 0.2
    return min(1.0, liderados_perfil / liderados_vaga)


#: Marca que o pre-filtro poe no card quando o titulo e de engenharia de
#: software generica sem termo de infraestrutura. Ela nasce la porque a
#: distincao esta no cargo, e nao na descricao -- o texto das duas familias
#: cita nuvem, confiabilidade e escala do mesmo jeito.
BLOCKER_OUTRA_TRILHA = "trilha:"
BLOCKER_GEOGRAFIA = "geografia:"

#: Trecho que identifica o aviso de engenharia de software generica entre os
#: avisos de trilha. Ele e o unico que admite escape pelo proprio historico --
#: ver `_fora_da_trilha`.
MARCA_DE_SOFTWARE = "engenharia de software"

#: Trecho que identifica o aviso de titulo sem nenhuma marca de tecnologia.
#: Separado do de software porque os dois tetos nao valem o mesmo: um diz que
#: o titulo nao revela QUAL familia da tecnologia, o outro diz que o titulo
#: esta fora dela.
MARCA_SEM_TECNOLOGIA = "sem nenhum termo"


def _e_geografico(blocker) -> bool:
    """O aviso trata de distancia?

    Tudo o que nao esta marcado como trilha e tratado como geografia, e nao o
    contrario. Runs anteriores a este prefixo gravaram avisos geograficos sem
    marca nenhuma, e exigir a marca faria todas aquelas vagas perderem a
    penalidade de distancia -- reescrevendo em silencio o score de um historico
    que existe justamente para ser comparavel.
    """
    texto = str(blocker or "").strip()
    if not texto:
        return False
    partes = [p.strip() for p in texto.split(";") if p.strip()]
    return any(not p.startswith(BLOCKER_OUTRA_TRILHA) for p in partes)


def _fora_da_tecnologia(vaga: dict) -> bool:
    """O titulo nao trouxe nenhuma marca de tecnologia?

    Diferente de `_fora_da_trilha`, esta pergunta se responde sem a descricao:
    a evidencia dela e o titulo, que o pre-filtro ja leu. Ver `Scorer._tetos`.
    """
    partes = [p.strip() for p in str(vaga.get("blocker") or "").split(";")]
    return any(
        p.startswith(BLOCKER_OUTRA_TRILHA) and MARCA_SEM_TECNOLOGIA in normalize(p)
        for p in partes
    )


def _fora_da_trilha(perfil: dict, vaga: dict) -> bool:
    """A vaga pertence a outra familia de carreira?

    O caso concreto: uma vaga de Engineering Manager que pede base de backend
    hands-on, para um candidato que gere infraestrutura. Nao e descasamento de
    senioridade -- os dois sao gestores -- e por isso nenhum componente do
    score o captura. A distincao veio do pre-filtro, que ja marcou o card.

    Ha dois avisos de trilha e eles nao se resolvem do mesmo jeito.

    O de engenharia de software generica admite escape pelo proprio historico:
    um gestor que veio de backend nao esta fora da trilha de uma vaga de
    backend, e o aviso existe so porque o titulo nao diz qual das duas familias
    e. Quem tem a familia declarada no perfil responde a pergunta.

    O de titulo sem nenhum termo de tecnologia nao admite escape nenhum. Ele nao
    fala de qual familia dentro da tecnologia -- fala de estar fora dela. Nenhum
    historico faz "Coordenador de Redacao" virar a vaga certa, e aplicar aqui o
    escape do outro aviso desligaria a regra para todo candidato cuja headline
    cite software, que e quase todos.
    """
    partes = [p.strip() for p in str(vaga.get("blocker") or "").split(";")]
    de_trilha = [p for p in partes if p.startswith(BLOCKER_OUTRA_TRILHA)]
    if not de_trilha:
        return False
    if any(MARCA_DE_SOFTWARE not in normalize(p) for p in de_trilha):
        return True
    proprio = normalize(" ".join(
        str(perfil.get(campo) or "") for campo in ("headline", "titulo")
    ))
    return "software" not in proprio and "backend" not in proprio


def _componente_geografia(remoto: bool, blocker: str | None) -> float:
    if remoto:
        return GEO_REMOTO
    return GEO_FORA_DO_RAIO if blocker else GEO_NO_RAIO


class Scorer:
    """Compoe o score a partir de perfil, vaga e configuracao."""

    def __init__(self, config, ontology: Ontology) -> None:
        self._pesos = dict(config.scoring.pesos)
        self._bonus = dict(config.scoring.bonus)
        self._penalidades = dict(config.scoring.penalidades)
        # `tetos` e opcional na configuracao: instalacao antiga continua valendo
        # com os padroes, e nao quebra por causa de uma secao ausente.
        self._tetos_config = dict(getattr(config.scoring, "tetos", {}) or {})
        self._ontology = ontology

    def score(
        self,
        perfil: dict,
        vaga: dict,
        descricao: str | None = None,
        sinais: list[str] | None = None,
        passada: str = PROVISORIA,
    ) -> ScoreBreakdown:
        """Devolve o score e o detalhamento por componente."""
        # Idioma conta como competencia para efeito de casamento. Ele vive num
        # campo proprio do perfil, e enquanto so `competencias` era lido um
        # candidato que declarava ingles em `idiomas` era tratado como quem nao
        # o tem -- justamente o termo que a ontologia marca como eliminatorio.
        competencias_perfil = self._ontology.canonical_set(
            [*(perfil.get("competencias") or []), *(perfil.get("idiomas") or [])]
        )
        tem_descricao = bool((descricao or "").strip())
        competencias_vaga = (
            self._ontology.extract(descricao) if tem_descricao
            else self._ontology.extract(vaga.get("titulo"))
        )

        nivel_vaga = infer(vaga.get("titulo"), descricao).nivel
        componentes = {
            "competencias": _componente_competencias(
                competencias_perfil, competencias_vaga, tem_descricao
            ),
            "nivel": _componente_nivel(
                perfil.get("nivel_inferido") or PLENO, nivel_vaga
            ),
            "dominio": _componente_dominio(
                perfil.get("setores") or [], vaga.get("setor")
            ),
            "escopo_gestao": _componente_escopo(
                int(perfil.get("liderados") or 0), vaga.get("liderados")
            ),
            "geografia": _componente_geografia(
                bool(vaga.get("remoto")),
                vaga.get("blocker") if _e_geografico(vaga.get("blocker")) else None,
            ),
        }

        bruto = sum(
            self._pesos.get(nome, 0.0) * valor for nome, valor in componentes.items()
        ) * 100.0

        ajustes = self._ajustes(
            sinais or [], competencias_perfil, competencias_vaga, tem_descricao,
            fora_do_raio=_e_geografico(vaga.get("blocker")) and not vaga.get("remoto"),
        )
        # Limite por ultimo: aplicar antes faria bonus sobre teto sumir e
        # penalidade sobre piso tambem, mudando o resultado da mesma entrada.
        final = max(0, min(100, round(bruto + sum(ajustes.values()))))

        # E so entao os tetos. Uma vaga com noventa por cento das palavras
        # batendo e um requisito eliminatorio faltando nao e noventa por cento:
        # a penalidade sozinha deixava passar 85 numa vaga que o candidato nao
        # pode aceitar, e e isso que separa esta nota de uma contagem de
        # palavras-chave.
        tetos = self._tetos(
            perfil, vaga, competencias_perfil, competencias_vaga,
            tem_descricao, descricao,
        )
        if tetos:
            final = min(final, min(tetos.values()))

        return ScoreBreakdown(
            score=final,
            componentes={k: round(v, 4) for k, v in componentes.items()},
            ajustes=ajustes,
            bruto=round(bruto, 4),
            descricao_disponivel=tem_descricao,
            sinais_disponiveis=bool(sinais),
            passada=passada,
            tetos=tetos,
        )

    def _tetos(
        self,
        perfil: dict,
        vaga: dict,
        competencias_perfil: frozenset,
        competencias_vaga: frozenset,
        tem_descricao: bool,
        descricao: str | None = None,
    ) -> dict[str, int]:
        """Limites superiores da nota, com o motivo de cada um.

        Diferente de penalidade: penalidade desconta e pode ser compensada por
        bonus; teto nao. Uma vaga que exige o que o candidato nao tem nao vira
        boa porque ele tem conexoes na empresa.

        Nem todo teto espera a descricao, e tratar os dois do mesmo jeito era
        um erro caro. `requisito_eliminatorio` de fato so se afirma com o texto
        do anuncio na mao. O teto de trilha nao: a evidencia dele e o titulo,
        que o pre-filtro ja leu -- e enquanto ele esperava, "Supervisor(a) de
        Turbinas" ficava empatada no topo com "Gerente de Infraestrutura".
        Medido no run e49b12fe: 51 vagas empatadas na nota mais alta da passada
        provisoria, e 60 das 154 sem nenhuma marca de tecnologia no titulo.
        """
        tetos: dict[str, int] = {}

        teto_de_trilha = self._teto_de_trilha(perfil, vaga)
        if teto_de_trilha is not None:
            tetos["outra_trilha"] = teto_de_trilha

        if not tem_descricao:
            return tetos

        # Duas origens de eliminatoriedade, e ambas contam. A lista da ontologia
        # cobre o que vale para qualquer vaga -- idioma, tipicamente. O texto do
        # anuncio cobre o que so aquela vaga exige, que e a maioria dos casos e
        # o que nenhuma lista fechada acertaria de antemao.
        faltando = (
            (self._ontology.eliminatorios & competencias_vaga)
            | self._ontology.extract_obrigatorias(descricao)
        ) - competencias_perfil
        faltando = {t for t in faltando if self._perguntamos(perfil, t)}
        if faltando:
            tetos["requisito_eliminatorio"] = self._config_teto(
                "requisito_eliminatorio", 75
            )
        return tetos

    def _teto_de_trilha(self, perfil: dict, vaga: dict) -> int | None:
        """O teto que o aviso de trilha impoe, ou `None` se nao houver aviso.

        Os dois avisos nao pesam igual. "Titulo de engenharia de software sem
        termo de infraestrutura" e uma duvida sobre qual familia da tecnologia,
        e 65 deixa a vaga logo abaixo do topo, de onde ela sobe se a descricao
        confirmar. "Titulo sem nenhum termo de tecnologia" nao e duvida: e
        outra profissao, e um teto proximo do topo so troca o lugar dela na
        mesma pagina. Cobrar a diferenca e o que separa "Gerente financeiro" de
        "Software Engineering Manager" num relatorio de SRE.

        A ordem importa: o aviso mais forte vence quando os dois estao no mesmo
        card, e nao o ultimo avaliado.
        """
        if _fora_da_tecnologia(vaga):
            return self._config_teto("fora_da_tecnologia", 40)
        if _fora_da_trilha(perfil, vaga):
            return self._config_teto("outra_trilha", 65)
        return None

    def _perguntamos(self, perfil: dict, termo: str) -> bool:
        """O perfil chegou a registrar algo sobre este requisito?

        Ausencia de evidencia nao e evidencia de ausencia. O campo `idiomas` sai
        vazio da extracao com frequencia, e o teto entao afirmava "este
        candidato nao fala ingles" a partir de um campo que ninguem preencheu.
        Medido no run 2a292735: **121 das 249 vagas** ficaram travadas em 75 por
        isso -- metade do relatorio, por um dado nunca coletado.

        Vale so para o termo cuja evidencia mora num campo declarado na
        ontologia. O que se apura pelas competencias segue como antes: ali a
        lista existe e estar fora dela e informacao.
        """
        campo = self._ontology.campo_do_eliminatorio.get(termo)
        if campo is None:
            return True
        return bool(perfil.get(campo))

    def _config_teto(self, nome: str, padrao: int) -> int:
        return int(self._tetos_config.get(nome, padrao))

    def _ajustes(
        self,
        sinais: list[str],
        competencias_perfil: frozenset,
        competencias_vaga: frozenset,
        tem_descricao: bool,
        fora_do_raio: bool = False,
    ) -> dict[str, int]:
        ajustes: dict[str, int] = {}
        if fora_do_raio and "fora_do_raio" in self._penalidades:
            ajustes["fora_do_raio"] = -self._penalidades["fora_do_raio"]
        for sinal in sinais:
            chave = normalize(sinal).replace(" ", "_")
            if chave in self._bonus:
                ajustes[chave] = self._bonus[chave]
            elif chave in self._penalidades:
                ajustes[chave] = -self._penalidades[chave]

        if tem_descricao:
            faltando = (
                self._ontology.eliminatorios & competencias_vaga
            ) - competencias_perfil
            if faltando:
                ajustes["requisito_eliminatorio"] = -self._penalidades.get(
                    "requisito_eliminatorio", 0
                ) * len(faltando)
        return ajustes
