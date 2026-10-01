"""Montagem e renderizacao do relatorio de um run.

O relatorio le apenas dados ja persistidos e nao emite requisicao. Isso o torna
reexecutavel sobre qualquer run passado e independente do estado da conta
operacional: reabrir o relatorio de tres semanas atras nao toca a origem.

O escape do texto de origem e obtido por construcao. O motor de gabarito opera
com escape automatico ligado, de modo que exibir texto sem escapar exigiria um
gesto explicito que nao existe no gabarito. Descricao de vaga, titulo, empresa,
endereco de contato e texto de curriculo recebem o mesmo tratamento.

Estados degradados sao visiveis em vez de silenciosos. Modo deterministico,
falha de sintese, cota esgotada e ausencia total de sobreviventes tem cada um a
sua marcacao: um relatorio que simplesmente omite a secao faria o usuario achar
que nao havia nada a dizer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..web import tempo

TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class ReportContext:
    """Tudo o que a pagina mostra, ja resolvido a partir do banco."""

    run_id: str
    user_id: str
    gerado_em: str
    vagas: list[dict] = field(default_factory=list)
    descartadas: list[dict] = field(default_factory=list)
    problemas_higiene: list[dict] = field(default_factory=list)
    ranking_competencias: list[tuple[str, int]] = field(default_factory=list)
    #: Das mais pedidas, as que o perfil nao tem. `None` quando nao ha perfil
    #: para comparar, e nao lista vazia: vazia afirmaria que nada falta.
    competencias_ausentes: list[str] | None = None
    sintese: str | None = None
    falha_sintese: str | None = None
    modo_deterministico: bool = False
    limiar_destaque: int = 70
    contagens: dict = field(default_factory=dict)
    sem_descricao_por_cota: int = 0
    #: Filtros em vigor e quantas vagas existiam antes deles. Esconder sem
    #: dizer quanto foi escondido faria o relatorio parecer menor do que e.
    filtros: dict = field(default_factory=dict)
    total_sem_filtro: int = 0
    #: Quanto cada termo de busca rendeu neste run.
    rendimento_das_buscas: list[dict] = field(default_factory=list)
    #: Empresas com mais de um titulo distinto coletado na janela.
    empresas_contratando: list[dict] = field(default_factory=list)
    #: Tamanho dessa janela, em dias, para a pagina poder dize-lo.
    dias_de_contratacao: int = 30
    #: Janela de publicacao que o usuario pediu, em horas.
    janela_horas: int | None = None

    @property
    def fora_da_janela(self) -> list[dict]:
        """Vagas publicadas antes do alcance pedido.

        A origem nao respeita a janela: um run de 24 horas trouxe uma vaga
        publicada 22 dias antes, e o relatorio a exibia entre as melhores sem
        dizer nada. Quem pediu 24 horas precisa ver que nem tudo ali tem 24
        horas -- e precisa ver no proprio relatorio, nao descobrindo pelo
        anuncio depois de clicar.

        Vaga sem data nao entra: nao se pode afirmar que ela esta fora de uma
        janela que nao se sabe se ela cumpre.
        """
        return [v for v in self.vagas if v.get("fora_da_janela")]

    @property
    def houve_sobrevivente(self) -> bool:
        return bool(self.vagas)

    @property
    def destaques(self) -> list[dict]:
        """Vaga nova, intocada e boa.

        As tres condicoes sao independentes e nenhuma substitui a outra.
        `estado` diz que o usuario ainda nao decidiu nada sobre ela;
        `vista_antes` diz que nenhum run anterior ja a mostrou; o score diz que
        ela vale a leitura.

        A segunda entrou depois, e e a que faltava: `estado` fica `novo` para
        sempre em vaga que ninguem toca, entao o destaque marcava em verde a
        vaga que o usuario ja tinha visto em dez relatorios -- e destaque que
        aponta tudo nao aponta nada.
        """
        return [
            v for v in self.vagas
            if v.get("estado") == "novo"
            and not v.get("vista_antes")
            and int(v.get("score") or 0) >= self.limiar_destaque
        ]


_ACENTOS = str.maketrans("áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ", "aaaaeeiooouucAAAAEEIOOOUUC")


def _normalizar(texto) -> str:
    """Minusculas e sem acento, para comparar titulo com o que foi digitado."""
    return str(texto or "").translate(_ACENTOS).lower().strip()


#: O que fazer com cada vaga. Sao tres, e nao uma nota de zero a cem, porque o
#: usuario precisa de uma decisao e nao de mais um numero para interpretar.
APLICAR = "aplicar agora"
INDICACAO = "pedir indicação antes"
DESCARTAR = "descartar"
JA_APLICADA = "já aplicada"


def recomendar(vaga: dict, limiar: int) -> tuple[str, str]:
    """Acao recomendada e o motivo dela, derivados do que ja foi medido.

    Sem isto o relatorio entrega uma lista ordenada e deixa a decisao inteira
    com quem le -- e ordenar nao e decidir. Tres saidas bastam: candidatar-se,
    buscar quem apresente antes, ou nao gastar tempo.

    A regra e deterministica e reusa o que o score ja apurou. Um modelo poderia
    dar uma recomendacao mais rica, mas esta precisa existir mesmo quando nao
    ha credencial de modelo -- e precisa ser a mesma entre duas leituras do
    mesmo run.
    """
    if vaga.get("estado") == "aplicado":
        return JA_APLICADA, "você já se candidatou a esta vaga"

    tetos = vaga.get("tetos") or {}
    if "outra_trilha" in tetos:
        return DESCARTAR, (
            "o cargo pertence a outra família de carreira; não é questão de "
            "senioridade"
        )

    score = int(vaga.get("score") or 0)
    sinais = set(vaga.get("sinais") or [])

    if score < limiar - 20:
        return DESCARTAR, f"aderência de {score}%, muito abaixo do seu perfil"

    if "requisito_eliminatorio" in tetos:
        faltando = ", ".join(vaga.get("lacunas") or []) or "um requisito marcado"
        return INDICACAO, (
            f"o anúncio marca como imprescindível o que falta ({faltando}); "
            "uma indicação é o que contorna filtro eliminatório"
        )

    # Antes de "muitos candidatos", e a ordem e a regra inteira. Os dois sinais
    # falam da mesma fila e dizem coisas opostas sobre ela: um conta quantos
    # entraram, o outro diz onde você entra. Duzentos candidatos mandariam pedir
    # indicação; o LinkedIn dizendo que você está entre os melhores responde
    # justamente a pergunta que a indicação existe para contornar.
    #
    # Só chega aqui pela varredura do navegador: a rota pública é anônima, e
    # este sinal é calculado contra o seu perfil. Ver `tools/extrator.js`.
    if "top_applicant" in sinais:
        return APLICAR, (
            "o LinkedIn coloca você entre os melhores candidatos desta vaga"
        )

    if "muitos_candidatos" in sinais:
        return INDICACAO, "muitos candidatos; sem indicação a fila é longa"

    if "early_applicant" in sinais:
        return APLICAR, "poucos candidatos até agora — a janela é curta"

    if score >= limiar:
        return APLICAR, f"aderência de {score}%, acima do seu limiar de destaque"

    return INDICACAO, (
        f"aderência de {score}%, abaixo do destaque; vale se houver caminho "
        "de indicação"
    )


def _aplicar_filtros(vagas: list[dict], filtros: dict) -> list[dict]:
    """Recorta a lista pelo que o leitor pediu, sem reordenar nada.

    A ordem vem da consulta e tem razao propria -- descricao lida primeiro,
    porque score medido e score desconhecido nao medem a mesma coisa. Filtrar
    nao pode embaralhar isso.

    Filtro ausente nao filtra. E a diferenca entre "quero so remoto" e "nao me
    pronunciei sobre modelo de trabalho", e confundir as duas faria a pagina
    abrir vazia por omissao.
    """
    resultado = vagas
    cargo = _normalizar(filtros.get("cargo"))
    if cargo:
        # Casa por pedaco e sem acento: quem procura "gerente" quer achar
        # "Gerência" e "Gerente Sênior", e digitar o acento certo nao pode ser
        # requisito para encontrar o proprio cargo.
        termos = cargo.split()
        resultado = [
            v for v in resultado
            if all(termo in _normalizar(v["titulo"]) for termo in termos)
        ]
    minimo = filtros.get("score_minimo")
    if minimo:
        resultado = [v for v in resultado if v["score"] >= int(minimo)]
    if filtros.get("so_com_descricao"):
        resultado = [v for v in resultado if v["descricao_disponivel"]]
    if filtros.get("so_remoto"):
        resultado = [v for v in resultado if v.get("modelo") == "remote"]
    if filtros.get("ocultar_fora_do_raio"):
        resultado = [v for v in resultado if not v.get("blocker")]
    if filtros.get("so_novas"):
        # Novidade e do dado, e nao do estado: a vaga que nunca foi pontuada em
        # outro run. Quem roda todo dia quer ver so o que chegou desde ontem, e
        # sem isto precisava varrer a lista inteira procurando o marcador.
        resultado = [v for v in resultado if not v.get("vista_antes")]
    estado = filtros.get("estado")
    if estado:
        resultado = [v for v in resultado if v.get("estado") == estado]
    return resultado


def ordenar(vagas: list[dict]) -> list[dict]:
    """Ordem final: a nota do modelo manda onde ela existe.

    A consulta ja devolve na ordem deterministica, que continua sendo o piso --
    e a unica que duas leituras do mesmo run garantem ser igual, e a unica que
    existe sem credencial de modelo.

    Onde houve releitura, ela vence. O calculo deterministico erra numa coisa e
    erra caro: por ser proporcao, ele premia anuncio vago. Num run real um
    "Banco de Talentos | Tecnologia da Informacao" ficou acima de um
    "Especialista de SRE", e nenhum ajuste de peso conserta isso -- a falha e
    confundir "cobre tudo o que foi pedido" com "serve para esta pessoa".

    Vaga relida fica antes de vaga nao relida: o modelo so le o topo, entao nao
    ter nota dele significa "nao chegou a ser considerada", e nao "foi
    considerada e reprovada".

    Abaixo da releitura vale a mesma regra de sempre, e ela precisa continuar
    valendo: descricao lida vem antes de descricao ausente. Score sem descricao
    e score com descricao nao medem a mesma coisa -- sem descricao o componente
    de competencias devolve o valor neutro, que significa "nao foi possivel
    ler", e ordenar so pelo numero poe a vaga que ninguem leu na frente da que
    foi medida.
    """
    def chave(v: dict) -> tuple:
        nota = v.get("nota_do_modelo")
        return (
            0 if nota is not None else 1,
            -int(nota) if nota is not None else 0,
            0 if v.get("descricao_disponivel") else 1,
            -int(v.get("score") or 0),
            v.get("job_id") or "",
        )

    return sorted(vagas, key=chave)


def marcar_fora_da_janela(vagas: list[dict], janela_horas: int | None, hoje=None):
    """Anota que vagas foram publicadas antes do alcance que o run pediu.

    Existe porque a origem nao cumpre o filtro de recencia. Num run de 24 horas
    apareceu uma vaga publicada 22 dias antes, entre as melhores pontuadas, sem
    nenhuma marca. O sistema tinha o dado -- a data estava gravada -- e nao o
    usava para conferir o que ele mesmo tinha pedido.

    Um dia de folga sobre a janela: a data da origem tem granularidade de dia e
    fuso proprio, e marcar como atrasada uma vaga de ontem por causa de algumas
    horas seria ruido em vez de aviso.
    """
    if not janela_horas:
        return vagas
    limite = (hoje or datetime.now(timezone.utc).date()) - timedelta(
        days=janela_horas / 24 + 1
    )
    for vaga in vagas:
        publicada = vaga.get("publicada_em")
        if not publicada:
            # Sem data nao ha afirmacao a fazer. Chamar de fora da janela o que
            # nao tem data marcaria quase todo run, e a marca perderia sentido.
            continue
        try:
            vaga["fora_da_janela"] = date.fromisoformat(str(publicada)) < limite
        except ValueError:
            continue
    return vagas


def _rendimento(vagas: list[dict]) -> list[dict]:
    """Quanto cada termo de busca rendeu, do pior para o melhor.

    Existe para tornar o planejador mensuravel. Antes dava para ver que o
    relatorio tinha melhorado depois de mexer nas buscas, mas nao que tinha
    melhorado por causa disso -- e um termo que so traz ruido era impossivel de
    identificar sem ler o codigo do planejador.

    Ordenado pelo pior primeiro, ao contrario do resto do relatorio. Esta tabela
    nao serve para escolher vaga; serve para escolher o que remover da busca, e
    o que se remove esta no fim de qualquer lista ordenada por qualidade.
    """
    por_busca: dict[str, list[dict]] = {}
    for vaga in vagas:
        if vaga.get("busca"):
            por_busca.setdefault(vaga["busca"], []).append(vaga)

    linhas = []
    for termo, achadas in por_busca.items():
        notas = [int(v.get("score") or 0) for v in achadas]
        linhas.append({
            "termo": termo,
            "vagas": len(achadas),
            "melhor": max(notas),
            "mediana": sorted(notas)[len(notas) // 2],
            # Teto de trilha e o sinal de casamento acidental: a origem casou
            # por palavra solta e trouxe outra carreira.
            "fora_da_trilha": sum(
                1 for v in achadas if "outra_trilha" in (v.get("tetos") or {})
            ),
        })
    # Pior primeiro: mais ruido, depois pior melhor-nota.
    linhas.sort(key=lambda r: (-r["fora_da_trilha"], r["melhor"], r["termo"]))
    return linhas


def build_environment() -> Environment:
    """Cria o motor de gabarito com escape automatico ligado."""
    ambiente = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        # Escape por construcao: desligar isto seria a unica forma de vazar
        # marcacao vinda da origem para a pagina.
        autoescape=select_autoescape(default_for_string=True, default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    ambiente.filters["local"] = tempo.local
    ambiente.filters["local_completo"] = tempo.local_completo
    return ambiente


class ReportRenderer:
    """Monta o contexto a partir do banco e renderiza a pagina."""

    def __init__(self, connection, config) -> None:
        from ..store.repository import Repository

        self._repository = Repository(connection)
        self._config = config
        self._env = build_environment()

    # ------------------------------------------------------------ montagem
    def build_context(
        self,
        user_id: str,
        run_id: str,
        sintese: str | None = None,
        ranking_competencias=None,
        problemas_higiene=None,
        filtros: dict | None = None,
        competencias_ausentes=None,
    ) -> ReportContext:
        """Le o run, as vagas pontuadas e os descartes ja gravados."""
        scope = self._repository.for_user(user_id)
        runs = scope.select("runs", where="run_id = ?", params=(run_id,))
        if not runs:
            raise ValueError(f"run {run_id} nao encontrado para este usuario")
        run = runs[0]

        todas = ordenar(self._vagas(user_id, run_id))
        # A janela gravada com o run, e nao a configuracao de hoje: e ela que
        # diz o que este run prometeu cobrir.
        janela = run["janela_horas"]
        marcar_fora_da_janela(todas, janela)
        limiar = self._config.report.limiar_destaque
        for vaga in todas:
            vaga["acao"], vaga["acao_porque"] = recomendar(vaga, limiar)
        filtros = filtros or {}
        vagas = _aplicar_filtros(todas, filtros)
        descartes = [
            {"titulo": d["titulo"], "empresa": d["empresa"], "motivo": d["motivo"]}
            for d in scope.select("discards", where="run_id = ?", params=(run_id,))
        ]
        return ReportContext(
            run_id=run_id,
            user_id=user_id,
            gerado_em=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            vagas=vagas,
            descartadas=descartes,
            problemas_higiene=list(problemas_higiene or []),
            ranking_competencias=list(ranking_competencias or []),
            competencias_ausentes=(
                None if competencias_ausentes is None
                else list(competencias_ausentes)
            ),
            # O texto vem do proprio run. Antes era um parametro que ninguem
            # preenchia, e a secao mais cara do relatorio abria vazia mesmo
            # depois de o modelo ter sido pago para produzi-la.
            sintese=sintese if sintese is not None else run["sintese"],
            falha_sintese=run["falha_sintese"],
            modo_deterministico=self._config.synthesis.modo_deterministico,
            limiar_destaque=self._config.report.limiar_destaque,
            contagens={
                "brutos": run["n_brutos"],
                "filtrados": run["n_filtrados"],
                "novos": run["n_novos"],
            },
            sem_descricao_por_cota=int(run["cota_esgotada"] or 0),
            filtros=filtros,
            total_sem_filtro=len(todas),
            rendimento_das_buscas=_rendimento(todas),
            empresas_contratando=self._empresas_contratando(
                user_id, self._config.report.dias_de_contratacao
            ),
            dias_de_contratacao=self._config.report.dias_de_contratacao,
            janela_horas=janela,
        )

    #: Uma vaga sozinha nao e sinal de nada: e a vaga que voce ja esta lendo.
    MINIMO_DE_VAGAS_DA_EMPRESA = 2

    def _empresas_contratando(self, user_id: str, dias: int) -> list[dict]:
        """Empresas que abriram mais de um titulo distinto na janela.

        Sai inteiro do que ja esta no banco -- cada vaga guarda a empresa e a
        data em que foi vista pela primeira vez --, entao nao custa requisicao
        nem depende de pesquisar a empresa em lugar nenhum.

        Conta titulo distinto, e nao vaga: anuncio republicado chega com
        identificador novo, e sem isso a empresa que repete a mesma vaga toda
        semana lideraria a tabela sem ter aberto coisa alguma.

        Atravessa runs de proposito. Contratacao e um movimento de semanas, e
        uma tabela presa ao run de hoje mostraria sempre o mesmo numero baixo.
        """
        corte = (
            datetime.now(timezone.utc) - timedelta(days=dias)
        ).isoformat(timespec="seconds")
        linhas = self._repository.execute(
            """
            SELECT empresa, COUNT(DISTINCT titulo) AS vagas,
                   MAX(primeira_vez_em) AS mais_recente
            FROM jobs
            WHERE user_id = ? AND empresa IS NOT NULL AND empresa <> ''
              AND primeira_vez_em >= ?
            GROUP BY empresa
            HAVING vagas >= ?
            ORDER BY vagas DESC, empresa
            """,
            (user_id, corte, self.MINIMO_DE_VAGAS_DA_EMPRESA),
        ).fetchall()
        return [dict(linha) for linha in linhas]

    def render(self, contexto: ReportContext) -> str:
        """Devolve a pagina montada a partir do contexto."""
        return self._env.get_template("report.html.j2").render(ctx=contexto)

    def render_run(self, user_id: str, run_id: str, **extras) -> str:
        return self.render(self.build_context(user_id, run_id, **extras))

    # ------------------------------------------------------------ interno
    def _vagas(self, user_id: str, run_id: str) -> list[dict]:
        """Junta score final, dados do card e descricao compartilhada."""
        linhas = self._repository.execute(
            """
            SELECT s.job_id, s.score, s.lacunas, s.diferenciais, s.componentes,
                   s.descricao_disponivel, s.nota_do_modelo, s.motivo_do_modelo,
                   j.titulo, j.empresa, j.url, j.local,
                   j.modelo_trabalho, j.publicada_em, j.flags, j.blocker, j.estado,
                   j.busca, d.texto AS descricao, d.emails_contato, d.candidatos,
                   -- Esta vaga ja foi pontuada em outro run deste usuario?
                   --
                   -- O criterio e ter aparecido em relatorio anterior, e nao a
                   -- data de primeira ocorrencia comparada ao inicio do run:
                   -- `iniciado_em` e reescrito quando um run e retomado, e um
                   -- run retomado passaria a chamar de velha toda vaga que ele
                   -- mesmo acabou de trazer.
                   --
                   -- `estado` nao serve para isso. Ele e a decisao do usuario
                   -- -- vista, aplicada, descartada -- e uma vaga que o
                   -- usuario nunca tocou continua `novo` por quantos runs
                   -- forem, mesmo tendo sido mostrada em todos eles.
                   EXISTS (
                       SELECT 1 FROM scores anterior
                       WHERE anterior.user_id = s.user_id
                         AND anterior.job_id = s.job_id
                         AND anterior.run_id <> s.run_id
                   ) AS vista_antes
            FROM scores s
            JOIN jobs j ON j.job_id = s.job_id AND j.user_id = s.user_id
            LEFT JOIN job_descriptions d ON d.job_id = s.job_id
            WHERE s.user_id = ? AND s.run_id = ? AND s.passada = 'final'
            -- A descricao vem primeiro na ordenacao porque score sem descricao
            -- e score com descricao nao medem a mesma coisa. Sem descricao o
            -- componente de competencias devolve o valor neutro de 0.5, que
            -- significa "nao foi possivel ler"; com descricao ele devolve a
            -- proporcao real, que para uma vaga exigente fica abaixo disso.
            -- Ordenar so por score colocava o desconhecido na frente do medido
            -- e empurrava para o rodape justamente as vagas em que gastamos
            -- requisicao para saber a verdade.
            ORDER BY s.descricao_disponivel DESC, s.score DESC, s.job_id
            """,
            (user_id, run_id),
        ).fetchall()
        return [
            {
                "job_id": linha["job_id"],
                "titulo": linha["titulo"],
                "empresa": linha["empresa"],
                "url": linha["url"],
                "local": linha["local"],
                "modelo": linha["modelo_trabalho"],
                "publicada_em": linha["publicada_em"],
                "score": int(linha["score"]),
                "estado": linha["estado"],
                "blocker": linha["blocker"],
                # Vaga anterior a coluna vem sem busca, e isso e um estado
                # legitimo -- nao um vazio a esconder.
                "busca": linha["busca"],
                "sinais": json.loads(linha["flags"] or "[]"),
                "lacunas": json.loads(linha["lacunas"] or "[]"),
                "diferenciais": json.loads(linha["diferenciais"] or "[]"),
                "descricao": linha["descricao"],
                "descricao_disponivel": bool(linha["descricao_disponivel"]),
                "tetos": json.loads(linha["componentes"] or "{}").get("tetos") or {},
                # Nulo e o estado normal: sem credencial de modelo o relatorio
                # continua inteiro, so sem esta camada.
                "nota_do_modelo": linha["nota_do_modelo"],
                "motivo_do_modelo": linha["motivo_do_modelo"],
                "emails": json.loads(linha["emails_contato"] or "[]"),
                "candidatos": linha["candidatos"],
                "vista_antes": bool(linha["vista_antes"]),
            }
            for linha in linhas
        ]
