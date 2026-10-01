# crivo — triagem automatizada de vagas

> **Primeira vez? Não é da área de informática?** Siga o
> [**COMECE-AQUI.md**](COMECE-AQUI.md): instalação passo a passo no Windows,
> primeiro uso e o que fazer quando algo dá errado. O resto desta página é
> técnico.

Aplicação web multiusuário que transforma um currículo em um relatório diário de
vagas pontuadas. Cada candidato entra com a conta Google, importa o currículo de
um Word ou de um PDF, conecta a conta LinkedIn pelo fluxo oficial e traz a
própria chave de um provedor de modelo de linguagem. A importação pelo Google
Drive existe como rota (`POST /resume/drive`), mas exige um token de acesso ao
Drive que o app nunca obtém — o login pede só `openid email profile` — e a
interface não a oferece.

A especificação completa está em [`.specs/changes/linkedin-job-agent/`](.specs/changes/linkedin-job-agent/):
28 requisitos, 214 critérios de aceitação, 21 elementos de design.

O que falta e em que ordem está em [`BACKLOG.md`](BACKLOG.md).

## Como rodar

Requer **Python 3.12** — não 3.13 nem 3.14. O teto existe porque `python-jobspy`
fixa `numpy==1.26.3`, cujo último wheel é cp312. Sai quando o pin for relaxado a
montante.

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e .
cp .env.example .env      # preencha os segredos
```

No `.env`, `CRIVO_MASTER_KEY` são 32 bytes em base64 seguro para URL, e
`CRIVO_DATABASE_URL` é o **caminho de um arquivo** SQLite (`data/crivo.db`), não
uma URL. Gerar a chave:

```bash
.venv/Scripts/python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

`.env` e `config/default.toml` são lidos em relação ao diretório corrente: rode
tudo a partir da raiz do repositório.

Quatro processos, e um comando que sobe os três de que o uso normal depende. Os
comandos abaixo supõem o `.venv` ativado; sem ativá-lo, troque `python` por
`.venv/Scripts/python`, senão o interpretador do sistema responde
`No module named crivo`.

```bash
python -m crivo tudo       # web + runs + enricher, numa janela só
```

A face web atende em <http://127.0.0.1:8000>. Use esse endereço, e não
`localhost`: o retorno do OAuth é montado a partir do host da requisição, e o
Google só aceita o que foi cadastrado.

Ou cada um no seu terminal, quando você quiser vê-los separados:

```bash
python -m crivo web        # atende o navegador
python -m crivo runs       # executa os estágios dos runs
python -m crivo enricher   # segura a trava de instância única; um por instalação
python -m crivo schedule   # um ciclo de agendamento; chame pelo cron
```

`schedule` **não** é subido pelo `tudo`, e a omissão é deliberada: nenhuma busca
nasce sem alguém pedir. Quem quiser recorrência chama esse comando pelo cron.

Testes: `.venv/Scripts/python -m pytest tests/ -q`

### Sem configurar o Google

Para desenvolver, ponha `CRIVO_DEV_LOGIN=1` no `.env`: aparece uma entrada local
que dispensa o fluxo de autorização. Sem a variável a rota nem é registrada. Os
segredos do Google continuam exigidos para subir `web` e `tudo` — qualquer valor
não vazio serve, e a página inicial avisa quando eles são de exemplo.

Para ver um relatório sem rede nenhuma, `python demo/run_demo.py` roda um run
completo com os serviços de verdade e uma fonte de vagas falsa.

### Se um processo morrer à força

As travas de instância única vivem no banco, em `schema_meta`, e são soltas pelo
`finally` de cada laço. `Ctrl+C` solta; fechar a janela no X, não. Nesse caso a
partida seguinte recusa por quinze minutos, citando um concorrente que não
existe — o silêncio a partir do qual a trava é dada como abandonada. Para não
esperar, apague as duas linhas `enricher_em_execucao` e `runs_em_execucao`.

### A máquina não pode dormir no meio de um run

Um run leva de trinta minutos a uma hora, quase tudo em espera deliberada entre
requisições. Modern Standby congela o processo junto com o resto: um run real
levou 3h31 de relógio a mais por isso, e passar de seis horas o faria ser morto
por falta de sinal de vida ao acordar.

```powershell
powercfg /requestsoverride PROCESS python.exe SYSTEM DISPLAY
```

## Como está organizado

| Camada | Onde |
|---|---|
| Configuração, segredos, redação de log | `config.py`, `secrets_vault.py`, `logging_filters.py` |
| Banco, isolamento entre usuários, LGPD | `store/` |
| Identidade, conexões, superfície HTTP | `web/` |
| Provedores de modelo e credenciais | `providers/` |
| Currículo e perfil-alvo | `resume/`, `profile/` |
| Coleta, pré-filtro, contenção, cota | `pipeline/` |
| Pontuação, lacunas e releitura do topo | `scoring/` |
| Síntese e aterramento | `synthesis/` |
| Relatório | `report/` |
| Processos e agendamento | `worker/`, `scheduler.py`, `__main__.py` |
| Extensão de navegador | `tools/extensao/` |

Os caminhos são relativos a `src/crivo/`, exceto `tools/`. Na raiz ficam ainda:

| Arquivo | O que é |
|---|---|
| `demo/run_demo.py` | Run completo sem rede, com fonte de vagas falsa |
| `tools/extrator.js`, `tools/bookmarklet.html` | O extrator em forma de bookmarklet, anterior à extensão. Autentica só por cookie, que o navegador retém num POST vindo do LinkedIn — hoje recebe 401; use a extensão |
| `specagentevagaslinkedin.md` | A especificação original, anterior a `.specs/`; o design a lê como restrição |
| `jobradar.html`, `_jobradar.txt` | Reconstrução da varredura manual que deu origem ao projeto, com os parâmetros de URL |

## Preferências, e o que "vazio" significa

Em `/profile` há um seletor opcional: **quantos dias de escritório por semana
você aceita**. Vazio, o crivo não avalia presença. Preenchido, a vaga anunciada
como remota cuja descrição exige mais dias que isso deixa de contar como remota
e entra na conta de distância, como qualquer presencial — marcada, nunca
descartada.

A origem só responde remoto ou presencial, e nada entre os dois. Quem sabe dos
dias é a descrição, que só existe depois do enriquecimento; por isso a regra roda
na pontuação final e não no pré-filtro.

Vazio é o padrão e desliga a regra. Não saber quantos dias alguém aceita não
autoriza supor que aceita zero — a mesma linha que faz um campo de idioma vazio
não gerar teto de nota.

## A extensão: o que a coleta anônima não vê

O LinkedIn Premium calcula, **contra o seu perfil**, coisas que a rota pública
nunca devolve — sobretudo o aviso *“You'd be a top applicant”*. Ele não existe na
página que o coletor busca: existe na sessão logada de quem tem Premium.

A extensão em [`tools/extensao/`](tools/extensao/) lê esses avisos nos cards
enquanto você navega e manda para o app. Ela **não lê nem envia credencial** —
nem cookie, nem token do LinkedIn. O app recebe números já extraídos: o resultado
da leitura que você já estava fazendo com os próprios olhos, nunca o meio de
refazer essa leitura sem você.

Abra `/extensao` no app para emitir o token e ver os passos de instalação.

### Por que ela usa um token próprio, e não a sessão

O cookie de sessão é `samesite=lax`, então o navegador o retém num POST vindo do
LinkedIn — que é cross-site. A rota de insights tinha CORS, tinha origem liberada
e mesmo assim era inalcançável: 401 em toda tentativa.

Afrouxar o cookie para `samesite=none` resolveria uma rota e abriria CSRF em
todas as outras. O token viaja em cabeçalho, que SameSite não governa, e não é
credencial ambiente — só quem já o tem consegue enviá-lo. O escopo é a outra
metade: ele **escreve insight e nada mais**. Vazado, não abre relatório, perfil
nem credencial de provedor. O banco guarda só o hash.

### O que os sinais fazem

| sinal | efeito |
|---|---|
| `top_applicant` | +10 na nota, e a recomendação vira “aplicar agora” |
| `early_applicant` | +5 |
| `muitos_candidatos` | −5 |
| `premium_insight` | gravado, sem efeito na nota nem na recomendação |

Os valores vêm de `[scoring.bonus]` e `[scoring.penalidades]` em `config/default.toml`.

`top_applicant` passa na frente de `muitos_candidatos` na recomendação: os dois
falam da mesma fila e dizem coisas opostas — um conta quantos entraram, o outro
diz onde você entra. Ele **não** atropela requisito eliminatório: o LinkedIn
ordena candidatos, não lê o que o anúncio exige.

Vaga que o crivo ainda não conhece é criada ali mesmo, com origem `extensao`
gravada, e entra no run seguinte. Sem isso a varredura seria um beco sem saída:
as buscas do planejador são focadas no Brasil e a navegação de quem usa não é,
então a sobreposição entre as duas é estruturalmente próxima de zero.

A rastreabilidade requisito → teste vive em [`tests/coverage_map.py`](tests/coverage_map.py)
e é verificada por [`tests/test_coverage.py`](tests/test_coverage.py): critério
sem teste ou teste renomeado quebram a suíte.

## Riscos residuais conhecidos

Registrados no checkpoint final. Nenhum é bloqueio; todos são coisas que quem
operar precisa saber.

### Os testes não tocam as origens reais, e o uso real só cobriu parte delas

Os 882 testes usam fontes e provedores falsos. Fora deles, a coleta e a releitura
já rodaram de verdade: runs de 24h contra o LinkedIn e um modelo real relendo o
topo, registrados no [`BACKLOG.md`](BACKLOG.md) — e cada um encontrou defeito que
os testes não previam. Dos fluxos de autorização do Google e do LinkedIn não há
execução real registrada, e a extensão com o token do extrator ainda não tem run
observado.

Isso já cobrou o preço uma vez, e vale como aviso concreto: a rota de insights
passou nos testes desde o primeiro dia e era **inalcançável na prática**, porque
o `TestClient` do FastAPI não implementa política de SameSite — ele manda o
cookie que você puser. O teste provava a lógica do servidor; a barreira era do
navegador.

### A extensão depende de marcação que ninguém publica

O LinkedIn não tem contrato de marcação, e a parte Premium só aparece para quem
tem Premium. A leitura é por padrão de texto e não por classe CSS — classe muda a
cada deploy, `"You'd be a top applicant"` é mais estável —, e o id da vaga é
procurado em quatro formas porque as telas de busca, de recomendação e de vaga
usam formas diferentes. Ainda assim, um redesenho do LinkedIn quebra a leitura.

O modo de falhar é o silêncio: a extensão roda dentro de outra página, sem nada
visível, e “0 vagas gravadas” tem quatro causas com a mesma cara. Por isso a
janelinha dela mostra o diário da última varredura — quantos cards viu, quantos
tinham aviso — em vez de só o total.

### Um bloqueio da origem suspende os runs de todos

Não há mais conta operacional: a coleta é toda por rota pública, sem sessão.
Isso remove o risco de perder uma conta, e não remove o risco de bloqueio — ele
passa a recair sobre o endereço de saída da instalação, e um bloqueio suspende
os runs de todos os usuários, não apenas de quem o provocou. A contenção reduz
a chance; não a elimina. Raspar o LinkedIn contraria os Termos de Serviço mesmo
sem sessão, e o risco recai sobre quem opera.

### O teto de vazão não escala com usuários

Um enriquecimento por vez em toda a instalação. Acrescentar usuários divide a
mesma capacidade — não a aumenta. A cota diária torna isso previsível, e não
resolvido.

### A qualidade da extração depende do modelo que o usuário escolher

Um modelo pequeno de cota gratuita erra mais. A defesa é a conferência humana,
que é um portão de estado no banco: nenhum run usa extração não confirmada. Se o
usuário confirmar sem ler, o erro passa.

### Ofertas gratuitas de provedor mudam e somem

O registro em `config/providers.toml` reflete o que se sabia em agosto de 2026 e
precisa ser reconferido. A cadeia de fallback e o modo determinístico existem
para que o desaparecimento de um provedor degrade em vez de quebrar.

### Rota ATS: fora de escopo por decisão

Greenhouse, Lever, Ashby, Workable, Recruitee e Personio expõem vagas em JSON
sem autenticação e sem violar ToS. É a rota mais limpa que existe. Ficou **fora
de escopo por decisão**, não por esquecimento: não há endpoint de busca nesses
sistemas, então seria preciso saber o identificador de cada empresa de antemão,
e montar essa lista é um produto diferente do que este é. Reabrir exige
requisito novo.

### Só o LinkedIn está habilitado no coletor

A biblioteca cobre oito portais. Uma versão anterior deste documento dizia que
ligar os demais era mudança de configuração — **não é**. `describe()` monta o
endereço público de anúncio do LinkedIn a partir de qualquer identificador, então
um card de outro portal produziria endereço inválido: falta despacho por portal.
Além disso o glossário da Fase 1 define camada guest, card e sinal do card todos
em termos do LinkedIn, e a biblioteca só entrega descrição do Indeed na própria
chamada de busca — o que colide com o requisito de pré-filtrar antes de
enriquecer. Ligar outros portais é mudança de desenho, não de configuração.

### A licença não diz nada sobre os Termos de Serviço

O código é [MIT](LICENSE) — use, modifique e redistribua à vontade. Isso concede
direitos sobre **este código** e não sobre a atividade: raspar o LinkedIn
contraria os Termos de Serviço da plataforma, e essa relação é entre quem opera e
ela. A isenção de garantia do MIT existe justamente porque o risco recai sobre
quem executa, não sobre quem escreveu.

### A assinatura do testemunho de identidade não é verificada

Ele chega pelo canal direto com o provedor, sob transporte cifrado e autenticado
pelo segredo do cliente — o cenário em que a especificação dispensa verificação
local. Verificar mesmo assim é uma mudança contida em `web/auth.py`.
