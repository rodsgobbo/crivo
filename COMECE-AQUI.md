# Comece aqui — instalar e usar o crivo

Este guia é para quem **não é da área de informática**. Siga na ordem, sem pular
passos. Cada passo diz o que fazer e o que você deve ver na tela depois.

O crivo lê o seu currículo, procura vagas no LinkedIn e monta um relatório com as
vagas que mais combinam com você, cada uma com uma nota.

> **Tempo:** a instalação leva uns 30 minutos, e é feita **uma vez só**. Depois,
> abrir o crivo leva 1 minuto.
>
> **Computador:** este guia é para **Windows 10 ou 11**.

---

## Antes de começar: como "digitar um comando"

Vários passos pedem para digitar um comando. É sempre assim:

1. Abra a pasta do crivo no Explorador de Arquivos (a janela de pastas do Windows).
2. Clique com o botão **direito** num espaço vazio da pasta e escolha
   **Abrir no Terminal**.
   - Se essa opção não aparecer: clique na barra de endereço lá em cima (onde
     aparece o caminho da pasta), apague o que está escrito, digite `powershell`
     e aperte **Enter**.
3. Abre uma janela com fundo escuro ou azul. É ali que você digita.
4. **Copie** o comando deste guia, **cole** na janela (botão direito do mouse cola)
   e aperte **Enter**.

Dicas:

- Copie o comando **inteiro**, exatamente como está. Uma letra trocada muda tudo.
- Espere o comando terminar: ele terminou quando a linha que começa com `PS C:\...>`
  aparece de novo, no fim.
- Mensagem em vermelho quase sempre é erro. Procure-a na seção
  [Deu errado?](#deu-errado) no final deste guia.

---

## Parte 1 — Instalar (uma vez só)

### Passo 1. Instalar o Python 3.12

O crivo é escrito em Python, e precisa **exatamente da versão 3.12** — a 3.13 e a
3.14 **não** funcionam.

1. Abra <https://www.python.org/downloads/release/python-31210/>.
2. Role até o fim da página, em **Files**, e clique em
   **Windows installer (64-bit)**.
3. Abra o arquivo baixado.
4. Na primeira tela do instalador, **marque a caixa "Add python.exe to PATH"**,
   lá embaixo. Depois clique em **Install Now**.
5. Quando aparecer **Setup was successful**, clique em **Close**.

**Confira:** abra um terminal (qualquer pasta serve) e digite:

```
py -3.12 --version
```

Deve aparecer `Python 3.12.10`. Se aparecer erro, veja
[Deu errado?](#deu-errado).

> Já tem outro Python instalado? Tudo bem, pode manter. O comando `py -3.12`
> escolhe a versão certa.

### Passo 2. Baixar o crivo

1. Abra o link do projeto que você recebeu (a página do GitHub).
2. Clique no botão verde **Code** e depois em **Download ZIP**.
3. Abra a pasta **Downloads**, clique com o botão direito no arquivo baixado e
   escolha **Extrair tudo…** → **Extrair**.
4. Mova a pasta extraída para um lugar fácil de achar, por exemplo a
   **Área de Trabalho**.

> A pasta pode se chamar `crivo` ou `crivo-master`. Tanto faz. Daqui em diante,
> "a pasta do crivo" é **a que tem dentro** os arquivos `README.md` e
> `pyproject.toml`. Se ao abrir você só vê outra pasta com o mesmo nome, entre
> nela.

### Passo 3. Instalar as peças do crivo

Abra um terminal **na pasta do crivo** (veja
[como digitar um comando](#antes-de-começar-como-digitar-um-comando)) e rode os
dois comandos abaixo, **um de cada vez**:

```
py -3.12 -m venv .venv
```

```
.venv\Scripts\python -m pip install -e .
```

O segundo demora de **2 a 10 minutos** e mostra muito texto passando. É normal.
Ele terminou quando aparecer, perto do fim, uma linha começando com
`Successfully installed`.

> O primeiro comando cria uma pasta escondida chamada `.venv`. É ali que ficam as
> peças do crivo. Não apague essa pasta.

### Passo 4. Criar o arquivo de configuração

O crivo guarda as senhas e configurações dele num arquivo chamado `.env`, dentro
da pasta do crivo. Você vai criá-lo agora.

**4.1.** No terminal, na pasta do crivo, rode:

```
copy .env.example .env
```

**4.2.** Gere a **chave mestra**, que protege as senhas que você cadastrar no
crivo:

```
.venv\Scripts\python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Aparece uma linha parecida com `XnXNgOseKPhTUNAY1JyDfg11DLV43W7DRtn5IooCwZk=`
(a sua será diferente). Selecione essa linha com o mouse e copie
(**Ctrl+C** funciona quando há texto selecionado).

**4.3.** Abra o arquivo no Bloco de Notas:

```
notepad .env
```

**4.4.** Preencha as linhas assim — cole a **sua** chave no lugar do exemplo:

```
CRIVO_MASTER_KEY=XnXNgOseKPhTUNAY1JyDfg11DLV43W7DRtn5IooCwZk=
CRIVO_DATABASE_URL=data/crivo.db
GOOGLE_CLIENT_ID=exemplo
GOOGLE_CLIENT_SECRET=exemplo
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=

CRIVO_DEV_LOGIN=1
```

Regras:

- Nada de espaço antes ou depois do `=`.
- `CRIVO_DATABASE_URL` é onde o crivo guarda os seus dados. Deixe `data/crivo.db`.
- As linhas do LinkedIn podem ficar vazias.

**4.5.** Salve (**Ctrl+S**) e feche o Bloco de Notas.

> ⚠️ **Guarde a chave mestra e nunca a troque.** Se ela mudar, o crivo não
> consegue mais abrir as chaves de API que você cadastrou, e você precisa
> cadastrá-las de novo. **Nunca envie o arquivo `.env` para ninguém.**

#### Sobre `CRIVO_DEV_LOGIN=1` e o "Google exemplo"

Com essas linhas, você entra no crivo **sem precisar de conta Google**, clicando
em **Entrar sem provedor**. É o caminho mais simples, e é seguro **quando só você
usa o crivo, neste computador** — do jeito que este guia ensina, o crivo só
aceita conexões do próprio computador.

Se **mais de uma pessoa** vai usar o mesmo crivo, cada uma precisa entrar com a
própria conta Google. Aí é preciso configurar o Google: veja o
[Anexo A](#anexo-a--entrar-com-a-conta-google). Deixe isso para depois; comece
pelo caminho simples.

---

## Parte 2 — Abrir o crivo (toda vez que for usar)

1. Abra um terminal **na pasta do crivo**.
2. Rode:

   ```
   .venv\Scripts\python -m crivo tudo
   ```

3. Espere aparecer a linha:

   ```
   Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
   ```

4. Abra o navegador (Chrome ou Edge) e entre em **<http://127.0.0.1:8000>**.

**Enquanto usar o crivo, deixe a janela do terminal aberta.** Pode minimizar, mas
não feche. É ela que faz o crivo funcionar.

### Para fechar o crivo

Clique na janela do terminal e aperte **Ctrl+C**. Espere aparecer
`[web] encerrado.` e só então feche a janela.

> ⚠️ **Não feche a janela no X com o crivo rodando.** Se fizer isso, na próxima vez
> o crivo pode se recusar a abrir por **15 minutos**. Veja
> [Deu errado?](#deu-errado).

### Não deixe o computador dormir durante uma busca

Uma busca de vagas leva **de 30 minutos a 1 hora**. Se o computador entrar em
suspensão no meio, a busca para junto e pode falhar.

Antes de buscar, confira em **Configurações → Sistema → Energia** que o
computador não suspende enquanto você espera. Ou, uma vez só, abra o menu
Iniciar, digite `powershell`, clique com o botão direito em
**Windows PowerShell** → **Executar como administrador**, e rode:

```
powercfg /requestsoverride PROCESS python.exe SYSTEM DISPLAY
```

Isso impede o Windows de suspender enquanto o crivo estiver trabalhando.

---

## Parte 3 — Primeiro uso, no navegador

A página inicial mostra **um passo por vez**, com um botão para o que falta. Siga
o que ela pede. A ordem é esta:

| # | Na tela | O que fazer |
|---|---|---|
| 1 | **Google — sua sessão** | Clique em **Entrar sem provedor** (o botão "Entrar com Google — vai falhar" é esperado no caminho simples; ignore). |
| 2 | **Importe seu currículo** | Escolha o arquivo do seu currículo em **Word (.docx)** ou **PDF** e clique em **Importar currículo**. |
| 3 | **Extraia os campos do currículo** | Clique em **Extrair campos**. Se você ainda não cadastrou uma chave de IA (veja abaixo), o crivo oferece preencher à mão. |
| 4 | **Confira o que foi extraído** | Clique em **Conferir extração**. **Leia com atenção** e corrija o que estiver errado. Um erro aqui estraga a busca inteira. |
| 5 | **Monte o seu perfil** | Clique em **Montar meu perfil**. |
| 6 | **Buscar vagas** | Escolha o período (30, 7 ou 1 dia) e clique em **Buscar vagas agora**. |

Depois do passo 6, espere. A busca leva **de 30 minutos a 1 hora**, e a página
não precisa ficar aberta — só o terminal. Quando terminar, o relatório aparece em
**Relatórios**, na página inicial. Clique na data para abrir.

> Há um limite de buscas por dia. Quando acabar, a página mostra
> **"volte amanhã"**.

### Chave de IA (recomendado, e pode ser grátis)

Com uma chave de IA, o crivo lê o currículo sozinho e escreve um resumo de cada
vaga. Sem ela, tudo funciona, mas você preenche o currículo à mão e o relatório
sai sem o texto de resumo.

Um jeito gratuito:

1. Entre em <https://aistudio.google.com/apikey> com sua conta Google.
2. Clique em **Create API key** e copie a chave.
3. No crivo, na página inicial, abra **Configuração** (lá embaixo).
4. Em **Credencial de modelo**, escolha **Google AI Studio**, cole a chave e
   clique em **Cadastrar**.

Outras opções gratuitas aparecem na mesma lista (Groq, Mistral, OpenRouter). Cada
opção diz **para onde vão os seus dados** — o seu currículo é enviado para essa
empresa ler.

> Chave de IA é como senha: não mostre para ninguém.

---

## Parte 4 (opcional) — Extensão do navegador, para quem tem LinkedIn Premium

Só serve se você **tem LinkedIn Premium**. Ela captura o aviso
*"You'd be a top applicant"* (você seria um dos melhores candidatos) enquanto
você navega no LinkedIn, e o crivo usa isso para subir a nota da vaga.

1. Com o crivo aberto, entre em <http://127.0.0.1:8000/extensao>.
2. Clique em **Emitir token** e **copie o código que aparece**. Ele só aparece
   **uma vez**. Se perder, emita outro.
3. Siga os passos de instalação que essa mesma página mostra.
4. Na janelinha da extensão, cole o token. Em **Endereço do app**, escreva
   `http://127.0.0.1:8000`. Clique em **Salvar** e depois em **Testar conexão**.

A extensão não lê nem envia senha, cookie ou mensagens do LinkedIn.

---

## Deu errado?

Procure a mensagem que apareceu. A primeira coluna é o que você vê; a segunda, o
que fazer.

| O que aparece | O que fazer |
|---|---|
| `'py' não é reconhecido como nome de cmdlet...` | O Python não foi instalado, ou foi sem a caixa **Add python.exe to PATH**. Rode o instalador do Passo 1 de novo, escolha **Modify**/**Repair**, ou desinstale e instale marcando a caixa. Depois **feche e abra** o terminal. |
| `No suitable Python runtime found` ou `Python 3.12 not found` | Você instalou outra versão do Python. Instale a **3.12** pelo link do Passo 1. |
| `Não foi possível carregar o módulo '.venv'` (ou `.venv\Scripts\python não é reconhecido`) | O terminal não está na pasta do crivo, ou o Passo 3 não foi feito. Confira se na pasta aberta existem `README.md` e `pyproject.toml`. |
| `No module named crivo` | Você digitou `python -m crivo ...` em vez de `.venv\Scripts\python -m crivo ...`. Use a forma completa, como no guia. |
| `requires a different Python` durante o `pip install` | O `.venv` foi criado com outra versão. Apague a pasta `.venv` e refaça o Passo 3, começando por `py -3.12`. |
| `nao foi possivel subir: modo tudo: variaveis de ambiente ausentes [...]` | Falta preencher uma linha do `.env` — a mensagem diz qual. Refaça o Passo 4. Confira também se o arquivo se chama `.env` mesmo, e não `.env.txt`. |
| `chave mestra ilegivel` ou `chave mestra com N bytes; esperado 32` | A `CRIVO_MASTER_KEY` foi copiada pela metade ou com algo a mais. Gere e cole de novo (Passo 4.2), sem espaços e sem aspas. |
| `WARNING ... trava de enricher sem sinal de vida desde ...; assumindo que o processo anterior morreu e tomando o lugar dele` | **Não é erro, e não precisa fazer nada.** Da última vez o crivo foi fechado no X, e esta partida assumiu o lugar do anterior. Para não ver de novo, feche com **Ctrl+C**. |
| `ja ha um processo de runs ativo` (ou `de enricher`) | Primeiro confira se não há outra janela do crivo aberta. Se não houver, alguma vez a janela foi fechada no X: **feche o crivo com Ctrl+C, espere 15 minutos** e abra de novo. |
| `address already in use` ou `[Errno 10048]` | O crivo já está aberto em outra janela de terminal. Use essa, ou feche-a com **Ctrl+C**. |
| O navegador diz **"Não é possível acessar esse site"** | A janela do terminal foi fechada ou o crivo ainda está subindo. Refaça a [Parte 2](#parte-2--abrir-o-crivo-toda-vez-que-for-usar) e espere a linha `Uvicorn running`. |
| **"Acesso bloqueado: erro de autorização"** ao entrar | Você clicou em **Entrar com Google** sem configurar o Google. Use **Entrar sem provedor**, ou siga o [Anexo A](#anexo-a--entrar-com-a-conta-google). |
| **"Entrar sem provedor"** não aparece | Falta a linha `CRIVO_DEV_LOGIN=1` no `.env`. Adicione, salve, feche o crivo com **Ctrl+C** e abra de novo. |
| A busca ficou parada por horas | O computador provavelmente dormiu. Veja [Não deixe o computador dormir](#não-deixe-o-computador-dormir-durante-uma-busca). |

Mudou alguma coisa no `.env`? Feche o crivo com **Ctrl+C** e abra de novo. Ele só
lê esse arquivo ao abrir.

---

## Pedir ajuda ao ChatGPT ou ao Claude

Pode, e ajuda muito. Mas a IA **não conhece o crivo**: sem este guia, ela chuta, e
alguns chutes estragam a instalação. Faça assim:

1. Abra uma conversa nova no ChatGPT ou no Claude.
2. **Anexe este arquivo** (`COMECE-AQUI.md`, na pasta do crivo): clique no clipe
   📎 ou no **+** da caixa de mensagem, ou arraste o arquivo para a conversa.
3. Copie e cole esta mensagem, completando o que está entre colchetes:

   ```
   Estou instalando o crivo seguindo o guia anexo. Não sou da área de
   informática. Leia primeiro o "Anexo B — Para o assistente de IA" no fim do
   guia e siga as regras dele. Estou no [Passo X / Parte Y]. Fiz [o que fez].
   Apareceu isto:

   [cole aqui a mensagem que apareceu]
   ```

4. Para copiar a mensagem do terminal: selecione o texto com o mouse, aperte
   **Enter** (ou **Ctrl+C**) e cole na conversa com **Ctrl+V**. Mande o texto
   copiado, e não uma foto da tela. Assim a IA lê a mensagem inteira, sem erro.

### Nunca cole na conversa

A IA **nunca precisa** destas coisas. Se ela pedir, diga não:

- o conteúdo do arquivo `.env` (nem abrir e fotografar);
- a chave mestra (`CRIVO_MASTER_KEY`);
- a chave de IA que você cadastrou (Google AI Studio, Groq etc.);
- o token da extensão;
- a chave secreta do Google (`GOOGLE_CLIENT_SECRET`).

Se uma mensagem de erro tiver algo parecido com essas chaves, troque por `XXXX`
antes de colar. Para a IA saber se o `.env` está preenchido **sem ver os
valores**, rode isto na pasta do crivo e cole o resultado:

```
.venv\Scripts\python -c "import pathlib;[print(l.split('=')[0].strip(), '- preenchido' if l.split('=',1)[1].strip() else '- VAZIO') for l in pathlib.Path('.env').read_text(encoding='utf-8').splitlines() if '=' in l and not l.strip().startswith('#')]"
```

### Desconfie se a IA mandar…

Pergunte **"isso está no guia?"** antes de fazer qualquer uma destas coisas:

- instalar outra versão do Python (3.13, 3.14, "a mais recente");
- rodar `pip install crivo` (sem o `-e .`): isso baixa outra coisa da internet;
- mudar a "política de execução" (`Set-ExecutionPolicy`), desligar o antivírus ou
  o firewall;
- rodar comandos "como administrador" (a única exceção é o `powercfg` da Parte 2);
- apagar a pasta `data`: é onde estão seu currículo, seu perfil e seus relatórios;
- trocar a chave mestra;
- abrir `localhost:8000` em vez de `127.0.0.1:8000`;
- editar arquivos que terminam em `.py`, `.toml` ou `.html`.

Se a IA puder **mexer no seu computador** (Claude com acesso a arquivos, ChatGPT em
modo agente), ela consegue ler o `.env` sozinha. Prefira o jeito acima, de copiar
e colar, e não autorize que ela apague pastas.

Se nem o guia nem a IA resolverem, peça ajuda a quem te passou o crivo, mandando
o passo em que parou e a mensagem de erro (sem as chaves).

---

## Anexo A — Entrar com a conta Google

Necessário só quando **mais de uma pessoa** usa o mesmo crivo. É a parte mais
trabalhosa; peça ajuda a alguém com experiência se puder.

1. Entre em <https://console.cloud.google.com> com sua conta Google e aceite os
   termos, se pedir.
2. No topo, clique no seletor de projeto → **Novo projeto**. Dê o nome `crivo` e
   clique em **Criar**. Confira que o projeto `crivo` está selecionado no topo.
3. No menu, abra **APIs e serviços** → **Tela de consentimento OAuth**
   (em algumas contas se chama **Google Auth Platform**) e clique em
   **Começar**/**Configurar**:
   - Nome do app: `crivo`. E-mail de suporte: o seu.
   - Público: **Externo**.
   - Conclua os passos com **Salvar e continuar**.
4. Em **Público** (ou **Usuários de teste**), adicione o e-mail Google de
   **cada pessoa** que vai usar o crivo. Quem não estiver na lista não entra.
5. Em **Clientes** (ou **Credenciais** → **Criar credenciais** →
   **ID do cliente OAuth**):
   - Tipo de aplicativo: **Aplicativo da Web**.
   - Em **URIs de redirecionamento autorizados**, clique em **Adicionar URI** e
     cole exatamente: `http://127.0.0.1:8000/auth/callback`
   - Clique em **Criar**.
6. Aparecem o **ID do cliente** e a **Chave secreta do cliente**. Copie os dois
   para o `.env`:

   ```
   GOOGLE_CLIENT_ID=<o ID do cliente>
   GOOGLE_CLIENT_SECRET=<a chave secreta>
   ```

7. No mesmo `.env`, **apague o `1`** de `CRIVO_DEV_LOGIN=` (deixe vazio). Com
   várias pessoas, a entrada sem provedor não pode ficar ligada.
8. Feche o crivo com **Ctrl+C** e abra de novo.

> Abra sempre **<http://127.0.0.1:8000>**, e não `localhost:8000`. Para o Google,
> os dois endereços são diferentes, e com `localhost` ele recusa a entrada com
> **redirect_uri_mismatch**.

Para as outras pessoas usarem o crivo **a partir de outros computadores**, é
preciso publicar o crivo na rede, o que vai além deste guia.

---

## Anexo B — Para o assistente de IA

*Esta seção é escrita para o ChatGPT, o Claude ou outro assistente que esteja
ajudando alguém com este guia. Quem está instalando pode pular.*

Você está ajudando uma pessoa **leiga em informática** a instalar e usar o crivo,
num **Windows**, no **PowerShell**. Este guia é a fonte de verdade. O crivo não é
um pacote conhecido: não complete lacunas com o que costuma valer para outros
projetos Python.

**Fatos que você não adivinharia:**

- O Python é **exatamente 3.12**, com `requires-python = ">=3.12,<3.13"`. Uma
  dependência fixa `numpy==1.26.3`, que só tem instalador até o 3.12. Com 3.13 ou
  3.14 a instalação falha. O 3.12.10 é a última versão 3.12 com instalador para
  Windows.
- O crivo é instalado a partir da pasta (`pip install -e .`), **não do PyPI**.
- Todo comando usa `.venv\Scripts\python` **sem ativar** o ambiente virtual. É
  proposital: evita `Activate.ps1` e a política de execução. Não mande ativar.
- Os comandos precisam rodar **na raiz da pasta do crivo**. `.env` e
  `config/default.toml` são lidos em relação à pasta atual.
- `.env`: `CRIVO_MASTER_KEY` são 32 bytes em base64 seguro para URL (44
  caracteres terminando em `=`). `CRIVO_DATABASE_URL` é o **caminho de um
  arquivo** SQLite (`data/crivo.db`), não uma URL. Valores de exemplo em
  `GOOGLE_CLIENT_*` são aceitos na partida.
- `CRIVO_DEV_LOGIN=1` libera o botão **Entrar sem provedor**. Neste guia, esse é
  o caminho padrão para **uma pessoa só no próprio computador**: o servidor escuta
  apenas em `127.0.0.1`. Com várias pessoas, vale o Anexo A.
- O endereço é `http://127.0.0.1:8000`. Com `localhost`, o login do Google falha
  com `redirect_uri_mismatch`.
- `crivo tudo` sobe três processos. Fechar a janela no X deixa uma trava no banco,
  que expira sozinha em **15 minutos** (`ja ha um processo de ... ativo`).
  Encerrar com **Ctrl+C** evita isso.
- Uma busca leva de 30 a 60 minutos. Se o computador suspender, ela congela.
- A seção **Deu errado?** lista os erros conhecidos com a solução. Consulte-a
  antes de propor outra coisa.

**Regras:**

1. **Um passo por vez.** Dê um comando, espere o resultado, só então o próximo.
   Diga o que a pessoa deve ver na tela quando der certo.
2. **Nunca peça segredos**: o conteúdo do `.env`, a chave mestra, chaves de API,
   o token da extensão, o `GOOGLE_CLIENT_SECRET`. Para conferir o `.env`, peça o
   comando da seção [Nunca cole na conversa](#nunca-cole-na-conversa), que mostra
   só os nomes das linhas e se cada uma está preenchida. Se a pessoa colar um
   segredo sem querer, avise e recomende trocá-lo: gerar outra chave de API; para
   a chave mestra, só quando ainda não houver chave de API cadastrada.
3. **Não proponha** outra versão do Python, `pip install crivo` vindo do PyPI,
   `Set-ExecutionPolicy`, desligar antivírus ou firewall, rodar como
   administrador (exceto o `powercfg` da Parte 2), apagar a pasta `data`, trocar
   a chave mestra, nem editar código ou `config/*.toml`. Apagar `.venv` e refazer
   o Passo 3 é seguro.
4. **Peça o texto exato do erro**, e não um resumo nem uma foto. Comandos seguros
   para diagnosticar: `py -0` (versões de Python instaladas), `dir` (o que há na
   pasta atual), `dir .env*` (se o arquivo virou `.env.txt`),
   `.venv\Scripts\python -V`, `.venv\Scripts\python -m pip show crivo`.
5. **Fale simples.** Sem jargão, ou explique o termo na primeira vez. Diga
   exatamente onde clicar.
6. Se o problema **não está coberto pelo guia** e você não tiver certeza da causa,
   diga isso. Então sugira levar o passo e a mensagem de erro a quem passou o
   crivo à pessoa, em vez de improvisar mudanças.

---

Para detalhes técnicos, organização do código e riscos conhecidos, veja o
[README](README.md).
