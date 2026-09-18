# crivo — backlog e roadmap

Estado em **2026-09-18**. Esquema do banco na v9, 882 testes passando. De
26/ago para cá entrou uma coisa grande, a extensão que traz os sinais Premium
(§3.5), três vindas da Fase 5 (§7.1, §7.2, §7.3) e os consertos de 18/set, que
saíram todos de uso real.

Cada item traz a evidência que o sustenta. Item sem evidência é suposição, e
suposição não entra em roadmap — vira pergunta na seção final.

---

## Restrições que não se negociam

Estas moldam toda solução abaixo. Uma proposta que as viole está errada, por
melhor que pareça.

| Restrição | Por quê |
|---|---|
| **HTML servidor puro, sem JavaScript** | Decisão de produto. Qualquer proposta que dependa de JS no navegador é inaceitável, inclusive diálogo de confirmação. Vale para as telas do app: a extensão em `tools/extensao/` é JavaScript por natureza, roda dentro do LinkedIn e não abre exceção para elas. |
| **Nunca aceitar credencial, cookie ou sessão de LinkedIn do usuário** | Escrito no contrato do módulo: a recusa acontece antes de qualquer gravação. Esteve em revisão de 26/ago a 09/set e **continua valendo**: os sinais Premium chegam pela extensão, que envia números lidos e nunca credencial (§3.5). |
| **Governador de taxa é obrigatório** | Bloqueio de coleta custa **1 hora de recuperação no IP do usuário**. Toda requisição nova a origem passa por ele. |
| **Python 3.12, não 3.13** | `python-jobspy` fixa `numpy==1.26.3`, cujo último wheel é cp312. Sai quando o pin relaxar a montante. |

---

## Onde estamos

Três runs de 24h já rodaram de ponta a ponta. O último, `1418e378` em 26/ago,
com todas as correções: 201 coletadas, 159 sobreviventes, 159/159 descrições
lidas, zero bloqueios. **O encanamento funciona.** O que ainda não funciona é o
julgamento — ver §1.1.

### Corrigido em 2026-08-25

| O quê | Onde |
|---|---|
| Ordem das palavras em português (`Gerente de SRE`, não `SRE Gerente`) | `pipeline/planner.py` |
| Teto de 25 buscas por run, com rodízio por eixo e por idioma | `pipeline/planner.py`, `config/default.toml` |
| Governador aplicado à coleta — era o único caminho sem contenção | `pipeline/collector.py` |
| 429 deixou de ser indistinguível de mercado vazio | `pipeline/sources/guest.py` |
| Gravação e progresso por busca, em vez de só no fim | `pipeline/collector.py`, `pipeline/stages.py` |
| Botão de cancelar busca (esquema v5) | `web/app.py`, `worker/queue.py`, `home.html.j2` |
| Trava de instância extraída e aplicada ao `crivo runs` | `store/locks.py`, `__main__.py` |
| Teto de trilha por reconhecimento, não por lista de negação (§1.2) | `config/filters.toml`, `pipeline/prefilter.py`, `scoring/scorer.py` |
| Teto eliminatório deixa de supor o que nunca foi perguntado (§1.3) | `config/ontology.toml`, `scoring/ontology.py`, `scoring/scorer.py` |
| Procedência da vaga e rendimento por termo de busca, esquema v6 (§2.1) | `store/migrations.py`, `pipeline/collector.py`, `report/renderer.py` |
| Recência real gravada e data retroalimentada, esquema v7 (§2.2) | `pipeline/enricher.py`, `pipeline/sources/guest.py` |
| Desenvolvimento reconhecido como outra trilha (§1.6) | `config/filters.toml` |
| Vaga remota deixou de ser pontuada como presencial (§1.7) | `pipeline/stages.py` |
| Data de publicação se atualiza no reaparecimento (§2.5) | `pipeline/collector.py` |
| Ctrl+C encerra sem traceback (§6) | `__main__.py` |
| Releitura do topo por modelo de linguagem, esquema v8 (§1.1) | `scoring/judge.py`, `pipeline/stages.py`, `report/renderer.py` |
| Supervisor deixa o filho sair limpo antes de terminar (§6.1) | `__main__.py` |

> As correções de 25/ago foram vistas no run `a6d5a060`; as de 26/ago ainda
> não passaram por um run completo, exceto a v7, cujo efeito já aparece em
> `1418e378`.

### Feito em 2026-09-09

| O quê | Onde |
|---|---|
| Extensão de navegador que lê os sinais Premium nos cards (§3.5) | `tools/extensao/`, `store/insights.py`, `web/app.py` |
| Token do extrator, porque o cookie `samesite=lax` não chega ao POST vindo do LinkedIn (esquema v9) | `store/extractor_tokens.py`, `store/migrations.py` |
| `top_applicant` vai na frente da fila de enriquecimento | `pipeline/stages.py` |
| `top_applicant` vira "aplicar agora", sem atropelar requisito eliminatório | `report/renderer.py` |
| Licença MIT | `LICENSE`, `pyproject.toml` |

### Feito em 2026-09-18 — tudo veio de uso real, nenhum de teste

| O quê | Onde |
|---|---|
| `KeyError: 'buscas'` na retomada derrubava o processo executor; as buscas agora são gravadas no planejamento e lidas de lá | `pipeline/stages.py`, `worker/queue.py` |
| Run que falha é marcado interrompido com a causa, e o executor segue vivo. Antes ele morria, o run continuava em andamento, a partida seguinte o devolvia à fila e matava o processo de novo — **um run ruim parava os runs de todos, em ciclo** | `worker/runner.py` |
| Título da vaga vinha do cabeçalho da seção ("Jobs based on your preferences"); passa a sair do link da vaga | `tools/extensao/conteudo.js` |
| A janelinha escondia o acumulado atrás da última varredura, e contava como card o que estava fora da tela | `tools/extensao/opcoes.js` |
| A janelinha mostra a amostra do texto lido: é o que permite depurar a leitura sem abrir o console | `tools/extensao/conteudo.js`, `opcoes.html` |

> Dois testes trocaram de lado junto: `test_a_failing_stage_propagates` e a
> recusa por falta de perfil exigiam que a exceção subisse, e nenhum dos dois
> dizia por quê. Quem chama `run_once` é um laço `while True` — subir era matar
> o executor.

---

## Fase 1 — a triagem precisa acertar

O produto entrega uma lista ordenada, e a ordem está errada. Enquanto isso não
mudar, nada mais importa: melhorar a coleta só traz mais vaga errada.

### 1.1 — O score premia anúncio vago · ✔ **feito em 26/ago**

O componente de competências é uma proporção: quanto do que a vaga pede o perfil
cobre. Anúncio genérico pede pouco e o perfil cobre quase tudo; vaga de SRE de
verdade lista doze tecnologias e o perfil casa seis. **Quanto menos específica a
vaga, maior a nota.**

Medido no run `2a292735`:

```
Coordenador de Redação (Letras/MKT)  competencias = 0.625  → 71%
Staff Platform Engineer              competencias  < 0.5   → 56%
```

Melhor vaga fora da trilha: **76%**. Melhor vaga na trilha: **69%**.

É esta a resposta para *"por que é tão diferente o resultado que você traz?"*
comparado ao plugin no navegador — ele lia cada vaga e julgava; este scorer
calcula uma proporção que sobe quando a vaga exige menos.

**Decidido em 26/ago: o modelo relê o topo.** Um estágio `julgamento`, entre a
pontuação final e a síntese, manda as 20 melhores ao modelo e recebe nota e
motivo por vaga. Custa ~1 chamada por run.

A nota do modelo fica **ao lado** do score, em colunas próprias, e não no lugar
dele: a ordem determinística continua gravada, reprodutível e é a que responde
quando não há credencial. Falha de modelo, cadeia esgotada e resposta ilegível
mantêm a ordem determinística de pé e registram o motivo — releitura que
inventasse ordem por não ter entendido a resposta seria pior que não reler.

**Visto no run `938b7c5b` (26/ago).** 20 de 20 relidas, e **todas caíram**:

| cálculo | modelo | vaga | motivo do modelo |
|---:|---:|---|---|
| 70% | **0%** | Banco de Talentos \| Tecnologia da Informação | banco de talentos não é vaga efetiva |
| 74% | 10% | Data Science Lead | outra família técnica |
| 71% | 20% | Gerente de TI | descrição vaga, sem detalhes técnicos |
| 61% | 0% | Gerente de Engenharia de Processos | engenharia de processos industriais |

Nenhuma subiu. O topo 20 da ordem determinística era ruído inteiro — o que
confirma o diagnóstico e mede o tamanho dele.

**Duas leituras do mesmo run não dão o mesmo número.** Dois julgamentos
seguidos deram 60% e 70% à mesma vaga. É o preço declarado desta opção, e é por
isso que a ordem determinística continua gravada ao lado.

### O defeito que quase escondeu tudo isso

O primeiro run com o estágio ligado releu **zero** vagas, e o run não falhou —
disse `sem releitura do modelo` e seguiu, que é o desenho.

A causa era minha: montei o pedido maior que o teto que o cliente de modelo
aplica ao texto de origem (8.000 caracteres). Ele corta **pelo fim**, e num
pedido de modelo o fim é onde moram as instruções. O modelo recebia uma lista
truncada sem nenhuma instrução, improvisava o formato — `[li-x] - 20/100 - ...`
em vez de `li-x|20|...` — e respondia uma linha só.

Três correções: o formato da resposta mudou para o gabarito de sistema, que não
passa por truncamento; a descrição é fatiada igualmente entre as vagas, para que
a última seja julgada com a mesma evidência que a primeira; e o leitor tolera a
pontuação que um modelo real usa, sem afrouxar nunca o identificador.

As duas alternativas descartadas ficam registradas: peso por raridade
(determinístico, mas não resolve "Banco de Talentos") e piso de especificidade
(pequeno, mas limita o dano em vez de corrigir a fórmula).

### 6.1 — ~~Supervisor matava o filho antes da limpeza~~ · ✔ **feito em 26/ago**

`terminate()` no Windows é `TerminateProcess`: não roda `finally` nenhum. O
supervisor terminava os acompanhantes antes de esperar, correndo contra a
limpeza que eles já faziam — e quando ganhava, o enriquecedor morria segurando a
trava de instância. Como ela só vence após 15 minutos de silêncio, subir de novo
antes disso era recusado por causa de um processo inexistente.

Observado em 26/ago: trava de 14:14 ainda presente às 18:02.

### 1.2 — O teto de "outra trilha" não dispara · ✔ **feito em 25/ago**

`_fora_da_trilha()` depende do pré-filtro ter marcado o card, e o pré-filtro
rejeita por **lista de negação** que cresce um caso observado por vez.

```
lista:   (gerente|supervisor|coordenador|técnico) de manutenção
título:  LÍDER DE MANUTENÇÃO ELÉTRICA/REFRIGERAÇÃO   → passou, 76%
```

**190 das 249 vagas (76%) não têm nenhum termo de tecnologia no título.**

**Resolvido invertendo o critério.** A seção `[trilha]` de `filters.toml`
declara o vocabulário que *reconhece* tecnologia, e título sem nenhum desses
termos recebe o teto de 65. Reconhecer o que serve é finito; enumerar o que não
serve não é. **Não descarta**: "Diretor de Tecnologia" e outros títulos
genéricos legítimos continuam no relatório, só saem do topo.

O escape do aviso de software (`headline` cita software → não está fora da
trilha) foi restrito a esse aviso. Aplicado ao novo, desligaria a regra para
quase todo candidato de tecnologia.

Reexecutado sobre as 249 vagas reais: **150 caíram, nenhuma subiu**, e as 15 do
topo mantiveram a nota exata.

### 1.3 — `idiomas` vazio no perfil derruba tudo para 75 · ✔ **feito em 25/ago**

O campo não era preenchido pela extração, então `inglês` contava como requisito
eliminatório ausente. **121 das 249 vagas travadas em 75 por um dado nunca
coletado.**

Duas correções: o scorer passou a ler `idiomas` junto de `competencias`; e
`ontology.toml` agora declara de qual campo do perfil vem a evidência de cada
termo eliminatório — campo vazio não gera teto, porque ausência de evidência não
é evidência de ausência.

Medido nas mesmas 249 vagas: **121 → 21**, restando só onde o anúncio de fato
marca como imprescindível algo que o perfil não tem.

### 1.6 — ~~Desenvolvimento no topo~~ · ✔ **feito em 26/ago**

Sete das trinta melhores eram desenvolvimento puro — *Coordenador backend
(Java/.NET)*, *Tech Lead Fullstack*. A regra que separa desenvolvimento de
infraestrutura só reconhecia a frase literal "engenharia de software". Ampliada
em `filters.toml`: **7 → 0** no topo 30.

### 1.7 — ~~Vaga remota pontuada como presencial~~ · ✔ **feito em 26/ago**

A passada final comparava `modelo_trabalho == "remoto"` e a origem grava
`"remote"`. Sempre falso: toda vaga remota entrava na conta final como
presencial e levava a penalidade de distância. As duas passadas discordavam
sobre a mesma vaga, e a que o relatório mostra era a errada.

Nenhum teste pegou porque o dublê também escrevia `"remoto"` — validava um
contrato que a produção não cumpre.

### 1.5 — A extração não captura `idiomas` · **médio** · P

A causa de origem de §1.3, que o teto agora contorna sem resolver. Enquanto o
campo não for preenchido, um candidato sem inglês e um candidato não perguntado
são indistinguíveis — e o sistema trata os dois como o segundo.

### 1.4 — ~~Datas do currículo lidas como `Ago/2025`~~ · **não existe**

Item errado meu, removido em 26/ago depois de verificar. O módulo
`profile/periodo.py` já lê `Ago/2025`, `08/2025`, `2025-08` e `2019`, e o
perfil real não tem nenhum problema de higiene do tipo "período ilegível". O
nível sai `manager`, correto.

Ficava no backlog por observação antiga, de antes de esse módulo existir. Fica
registrado como não-item para ninguém reabrir.

---

## Fase 2 — a coleta precisa ser explicável

Depois que a ordem estiver certa, a pergunta seguinte é sempre *"por que esta
vaga apareceu?"* — e hoje ela não tem resposta sem ler o código.

### 2.1 — Vaga não guarda a busca que a trouxe · ✔ **feito em 26/ago**

`Collector._persist()` descartava `card.busca`. Sem isso não dava para atribuir
resultado ruim a termo ruim, nem provar que uma correção no planejador
funcionou — dava para ver que o relatório mudou, não que mudou por causa dela.

Esquema v6 acrescenta `jobs.busca`, gravada na primeira vez que a vaga é vista
(reaparecer não muda de onde algo veio). O relatório agora diz *achada por
&lt;termo&gt;* em cada vaga e ganhou uma seção **Rendimento das buscas** —
por termo: quantas vagas, melhor nota, mediana, quantas fora da trilha —
ordenada do pior para o melhor, porque ela não serve para escolher vaga e sim
para escolher o que remover da busca.

### 2.2 — Data de publicação some nas vagas recentes · ✔ **feito em 26/ago**

| coleta | com data | sem data |
|---|---|---|
| 24/ago — janela 30 dias | 777 | 17 |
| 25/ago — janela 24 horas | 0 | **299** |

A data mais recente já gravada, em qualquer coleta, é `2026-08-23` — nunca
ontem, nunca hoje.

**Leitura provável, ainda não confirmada:** o LinkedIn marca anúncio recente com
`job-search-card__listdate--new`, e o `jobspy` procura só o token exato
`job-search-card__listdate`. Quanto mais estreita a janela, mais datas se
perdem; com 24h, todas.

**Resolvido pelo caminho barato.** O enriquecimento já baixava a recência por
extenso (`"há 3 semanas"`) e a descartava. Esquema v7 grava, e um conversor
preenche `jobs.publicada_em` quando a busca não trouxe — sem nenhuma requisição
a mais.

Medido no run `1418e378`: das 117 vagas coletadas pela primeira vez, **39 têm
data e todas são de 0–1 dia**. A janela parece estar sendo respeitada.

**Duas conclusões minhas foram retiradas aqui.** Afirmei que a janela vazava,
primeiro a partir de um modelo de ID que se mostrou enviesado em −12,6 dias
(republicação mantém o ID antigo), depois a partir de 10 vagas com data velha —
que eram revistas, com data nossa desatualizada (§2.5). Nas duas vezes tratei
dado desatualizado como medição.

Fica de pé: vaga reusada do cache de descrições não ganha recência nova, então
a idade só chega para vaga inédita.

### 2.5 — ~~Data envelhecia sozinha~~ · ✔ **feito em 26/ago**

`publicada_em` nunca era atualizada no reaparecimento. Anúncio republicado
mantém o identificador e ganha data nova; guardávamos para sempre a data da
primeira vez que o vimos. Foi isso que produziu a leitura errada de §2.2.

Card sem data não apaga a que existe — o LinkedIn omite a data justamente nos
anúncios recentes.

### 2.6 — Buscas que só produzem ruído · ✔ **feito em 26/ago**

Era mensurável pela tabela de rendimento do run `1418e378`:

| busca | vagas | fora da trilha | melhor |
|---|---|---|---|
| FinOps Director | 10 | **10** | 44% |
| Coordenador de FinOps | 5 | **5** | 12% |
| Coordenador de Plataforma | 6 | **6** | 34% |
| Coordenador de Infraestrutura | 8 | 6 | 48% |

**Resolvido pelos dados, não por lista fixa.** O planejador consulta o histórico
(`jobs.busca` + tetos de trilha) e rebaixa termo cujo passado inteiro é vaga de
outra carreira; o teto de 25 o corta.

Contra o banco real: `FinOps Director` (16/16 fora), `Coordenador de Plataforma`
(11/11) e `Coordenador de FinOps` (9/9) saíram — **36 de 36 tentativas erradas**
— e entraram `FinOps Engineering Manager`, `Infrastructure Engineering Manager`
e `Observability Engineering Manager`.

Rebaixa em vez de apagar: o mercado muda e o termo volta sozinho quando a lista
couber inteira. Amostra mínima de 5, para não condenar termo bom por azar de um
run. Busca escrita pelo usuário nunca é rebaixada.

### 2.7 — O conjunto coletado é fraco · **alto** · G

Descoberto ao ler o resultado de §1.1. Depois da releitura, a **melhor vaga do
run `938b7c5b` marca 70%**, e é um cargo de especialista, não de gestão. O topo
não tem nenhuma vaga de gestão de SRE ou infraestrutura.

Consertar a ordem tornou a lista honesta e expôs o problema seguinte: as buscas
não estão encontrando o cargo procurado. Não é ranqueamento — é cobertura.

Candidatos, nesta ordem: §2.3 (filtros `f_E` de senioridade na origem, que
tiraria os cargos técnicos abaixo do nível), mais âncoras de nível executivo, e
a revisão de quais eixos rendem cargo de gestão de fato.

### 2.3 — Filtros de URL do LinkedIn não usados · **médio** · M

O `jobradar.html` especifica `f_E` (senioridade), `f_WT` (modelo de trabalho) e
`f_JT` (tipo de vaga). Filtrar na origem é mais barato que filtrar depois: cada
vaga descartada no pré-filtro já custou a requisição que a trouxe.

### 2.4 — Sinal `Promoted` e idade do anúncio · **médio** · P

Ambos obtidos sem sessão, ambos previstos na especificação, nenhum coletado.

---

## 3.5 — Usar a conta LinkedIn Premium do próprio usuário · ✔ **resolvido em 09/set, sem credencial**

**Como foi resolvido.** Nenhuma das rotas avaliadas abaixo foi adotada. Uma
extensão em `tools/extensao/` roda no navegador de quem usa, lê nos cards os
avisos que a conta Premium calcula — `top_applicant`, `early_applicant`,
contagem de candidatos — e manda só esses números ao app. Nem cookie nem `li_at`
atravessam: a restrição do topo continua de pé. O preço que sobra é o de
marcação sem contrato — um redesenho do LinkedIn cala a extensão, e §5.3 passa a
valer para ela também.

O que segue é a avaliação de 26/ago, mantida como registro do porquê.

**Pedido em 26/ago.** Colide de frente com uma restrição do topo deste
documento: *"nunca aceitar credencial, cookie ou sessão de LinkedIn do
usuário"*, escrita no contrato do módulo com recusa antes de qualquer gravação.

A restrição é uma decisão de produto, não uma lei — e quem a tomou pode
revogá-la. O que este item faz é dizer o que se ganha, o que se arrisca e o que
custa, para a decisão ser tomada com o preço à vista.

### O que a sessão dá, e o que é Premium

Vale separar, porque não é a mesma coisa:

| | precisa de quê |
|---|---|
| `f_E` senioridade, `f_WT` remoto, `f_JT` tipo, `f_TPR` recência | **nada** — são parâmetros de URL na busca anônima (§2.3) |
| Data de publicação confiável, contagem de candidatos, mais resultados por consulta | **estar logado** (conta grátis basta) |
| Estado `Applied` / `Saved`, `Top Applicant`, conexões na empresa | **estar logado** |
| "Vagas com menos de 10 candidatos", posição relativa entre candidatos, `Featured Applicant` | **Premium Career** |

O filtro de "menos de 10 candidatos" é o que mais casaria com este produto: ele
é exatamente o sinal de baixa concorrência que o score já tenta inferir de
`"Seja um dos 25 primeiros"`.

**Premium não acrescenta filtro de busca de vaga.** Ele acrescenta *insight* por
vaga. Boa parte do ganho vem de estar logado, e não de pagar.

### O que existe pronto

- [`spinlud/linkedin-jobs-scraper`](https://github.com/spinlud/linkedin-jobs-scraper)
  — Node, dois modos. Anônimo por padrão; autenticado por `li_at`. Cobre
  senioridade, tipo, presencial/remoto/híbrido, recência, faixa salarial e
  indústria. 186 estrelas, 142 commits.
- [`joeyism/linkedin_scraper`](https://github.com/joeyism/linkedin_scraper)
  — Python, exige arquivo de sessão. Cobre perfil, empresa, publicações e vagas.
- Levantamento comparativo:
  [scrapfly](https://scrapfly.io/blog/posts/best-linkedin-scrapers-github),
  [vayne.io](https://www.vayne.io/en/blog/linkedin-scraper-github-review).

### O preço, dito antes da decisão

1. **A conta é a sua.** Hoje um bloqueio custa uma hora de recuperação no IP.
   Com sessão, o que está em risco é a conta — e o levantamento de 2026 relata
   que o limite autenticado é **mais estrito**, não menos: menos de 50
   requisições por dia em IP residencial.
2. **Contraria os Termos de Uso do LinkedIn**, que proíbem acesso automatizado
   com credencial. Isso não é impedimento técnico; é risco que passa a existir.
3. **O `li_at` é uma credencial de sessão completa** — quem o tem é você para
   todos os efeitos. Guardá-lo puxa para dentro deste produto uma classe de
   segredo que ele hoje recusa por construção, com o cofre e a rotação que isso
   exige.
4. **Some a reprodutibilidade da recusa.** O contrato atual é verificável por
   teste: nenhum caminho aceita senha ou cookie. Abrir uma porta torna a
   ausência de outras portas algo a auditar, e não algo garantido.

### Caminho intermediário, se a decisão for meio-termo

Fazer §2.3 primeiro — os filtros de URL — que **não exigem sessão nenhuma** e
entregam senioridade, remoto e tipo de vaga direto na origem. Isso é boa parte
do ganho prático de filtragem, sem tocar em credencial. Só depois, com o efeito
medido, decidir se o resto compensa o preço acima.

---

## Fora de alcance por desenho

Não são backlog. Estão aqui para que ninguém os proponha de novo achando que
foram esquecidos.

O produto recusa credencial de LinkedIn do usuário antes de qualquer gravação,
e essa recusa é desenho, não lacuna. Sinais que só existem numa sessão
autenticada entram apenas pela extensão (§3.5): `top applicant`, `early
applicant` e a contagem de candidatos já entram. Conexões na empresa e estado
`Applied`/`Saved` continuam fora — a extensão não os lê.

---

## Fase 3 — dívida de verificação

### 5.1 — ~~Nada de 2026-08-25 foi visto num run real~~ · ✔ **visto em 26/ago**

As correções de 25/ago apareceram no run `a6d5a060`, e o run `1418e378` rodou
com todas elas (ver "Onde estamos"). A extensão e o token do extrator, de 09/set,
ainda não têm run observado registrado aqui.

### 5.2 — Telas redesenhadas nunca abertas num navegador · **médio** · P

A folha de estilo única, a home reordenada, a navegação compartilhada e o filtro
do relatório passam nos testes de conteúdo. Ninguém olhou.

### 5.3 — Sem canário para o HTML real da origem · **alto** · M

A especificação chama a deriva do DOM de maior custo de manutenção do sistema, e
não existe fixture com HTML real nem teste que acuse mudança de marcação. O
defeito de §2.2 é exatamente disso: uma classe CSS mudou e nada avisou.

---

## Fase 4 — acabamento

| Item | Tamanho |
|---|---|
| Remover busca gerada (hoje só dá para acrescentar) | P |
| Esconder notas explicativas longas depois do primeiro uso | P |
| Orçamento diário de 400 contra 656 sobreviventes de um run amplo | M |
| Dois processos `crivo runs` simultâneos — **resolvido** em 25/ago pela trava de instância | ✔ |

---

## Fase 5 — o que veio do funil de prospecção e do `linkedin-skills`

Avaliados em 15/set: um carrossel de prompts de prospecção (@guilhermemorais.ia)
e o repositório `sergebulaev/linkedin-skills`. Quase tudo ali parte da empresa
ou do conteúdo, e o crivo parte da vaga. Entrou só o que usa dado que o crivo
já tem ou já coleta.

### 7.1 — ~~O ranking não dizia o que falta no perfil~~ · ✔ **feito em 15/set**

`gaps.missing_across` existia, com teste, e nada a chamava: o relatório listava
as competências mais pedidas sem marcar quais o candidato não tem. Agora marca,
e só quando há perfil consolidado — sem perfil, "falta" seria acusação sem
pergunta.

No caminho apareceu um defeito maior. `skills_from_profile`, que evita mandar
acrescentar "SRE" a quem lidera time de SRE, também não era chamada: as lacunas
de cada vaga comparavam só com a lista declarada. Os dois lugares agora usam o
mesmo conjunto, declaradas mais histórico (REQ-14.5, REQ-14.6), para que ranking
e card não discordem na mesma página. Muda lacunas e diferenciais exibidos; não
muda score.

Ainda não visto num run real.

### 7.2 — ~~Empresas que estão contratando~~ · ✔ **feito em 17/set**

O banco já guardava empresa e `primeira_vez_em` de cada vaga: a tabela sai sem
requisição nova e sem pesquisar a empresa em lugar nenhum.

Três decisões, e as três estão na página para quem lê não se enganar: conta
**título distinto** e não vaga, porque anúncio republicado chega com
identificador novo e uma empresa que repete a mesma vaga toda semana lideraria a
tabela sem ter aberto nada; exige **mais de um** título, porque uma vaga só é a
vaga que você já está lendo; e **atravessa runs**, porque contratação é
movimento de semanas e uma tabela presa ao run de hoje mostraria sempre o mesmo
número baixo. A janela é configurável (`report.dias_de_contratacao`, 30 dias).

A ressalva que fica: mede o que **as suas buscas** trouxeram, não o mercado. A
página diz isso.

No caminho apareceu outro parâmetro que a especificação dizia ser configurável e
não era: `vagas_relidas`, o tamanho do topo que o modelo relê. O código lia com
`getattr` e um padrão embutido, e a chave não existia na configuração. Agora
existe.

Ainda não visto num run real.

### 7.3 — ~~Vaga "remota" que é híbrida~~ · ✔ **feito em 17/set**

A origem só entrega booleano — `guest.py` grava `remote` ou `on-site` — e vaga
remota **nunca é avaliada geograficamente**. Uma vaga que pede três dias por
semana no escritório chegava marcada como remota, e o deslocamento que ela exige
sumia do sistema inteiro.

Quem sabe disso é a descrição, que só existe depois do enriquecimento. Por isso
a correção mora na passada final e não no pré-filtro: é o primeiro momento em
que o texto está disponível. `pipeline/presenca.py` lê o número de dias por
padrão de texto, nos dois idiomas que a coleta traz.

O cálculo de distância foi **extraído** do pré-filtro (`blocker_geografico`) e é
o mesmo nos dois lugares. Duas implementações da mesma distância discordariam
sobre a mesma vaga, que é o defeito de §1.7 de novo.

Três recusas deliberadas: "híbrido" sem número não vira número, porque não saber
quantos dias não é saber que são zero; sem preferência declarada a regra inteira
fica desligada (§1.3); e a vaga é **marcada**, nunca descartada — a decisão de
encarar o deslocamento é de quem se candidata.

Junto veio a tela que faltava. `ProfileMerger.consolidate` já aceitava
`manual_fields`, e a origem manual já vencia as demais, mas o formulário do
perfil não tinha campo nenhum: ninguém nunca mandava dado manual. Agora tem o
seletor de dias, opcional, e a consolidação preserva a escolha quando o botão de
reconsolidar é usado — sem isso, cada clique apagaria a preferência.

Ainda não visto num run real.

### 7.4 — Faixa salarial · **médio** · M

O card traz `salario` e nada além do coletor o lê. Marcar "abaixo da sua faixa"
só quando o anúncio publica valor; ausência não penaliza, pelo mesmo motivo de
§1.3.

A tela de preferências já existe desde §7.3, então falta um campo a mais nela e a
regra que o consome. O campo só entra junto com a regra: campo preenchido que não
faz nada é mentira na tela.

### 7.5 — Rascunho de pedido de indicação · **médio** · G

Só nas vagas em que o relatório já diz "pedir indicação antes". Até 150
palavras, a partir da descrição e do perfil confirmado, pelo modelo do usuário e
aterrado como a síntese, gerado sob demanda e não em todo run. O crivo nunca
envia. Do `linkedin-interviewer` vem a regra de perguntar uma vez pelos números
reais e guardar, para a mensagem não inventar feito. Pede requisito.

### 7.6 — Acompanhamento da candidatura · pergunta em aberto

`aplicado` hoje não tem data nem próximo passo. Lembrete único e regra de
encerramento caberiam no desenho, mas aproximam o crivo de um CRM. Ver pergunta
5.

### Descartado por desenho

Apify e Publora (raspagem e publicação por terceiro, contra a restrição de
credencial), Pixfaro, posts e marca pessoal, pesquisa de empresa na web,
abordagem sem vaga aberta e os benchmarks sem fonte do `profile-optimizer`.

---

## Ordem sugerida

```
1.2  teto de trilha          ✔    1.6  desenvolvimento no topo  ✔
1.3  idiomas vazio           ✔    1.7  remoto como presencial   ✔
2.1  guardar a busca         ✔    2.2  data de publicação       ✔
5.1  rodar e olhar           ✔    2.5  data envelhecida         ✔
2.6  cortar buscas de ruído  ✔    1.1  score que premia vago    ✔
3.5  sinais Premium          ✔    7.1  o que falta no perfil    ✔
7.2  quem está contratando   ✔    7.3  híbrido disfarçado       ✔
     ↓
2.7  as buscas não acham o cargo      ← o único "alto" de pé fora da verificação;
                                        começa por 2.3, os filtros de URL
     ↓
5.3  canário de HTML                  ← agora protege a coleta e a extensão
```

O raciocínio: **1.2 e 1.3 eram pequenos e mexiam no topo da lista** — feitos, e
o efeito foi medido reexecutando as regras sobre as 249 vagas reais do run
`2a292735`, sem gastar requisição nova. **2.1 vem antes de 1.1** porque sem
saber qual busca trouxe cada vaga não há como medir se a mudança no score
adiantou. **1.1 é o único item que precisa de decisão antes de código**, e é o
mais caro; entra quando houver como medir.

### O que a simulação mostrava sobre 1.1, antes da releitura

Com 1.2 e 1.3 aplicados, o topo do relatório fica assim:

```
69%  Gerente de Operações de Cibersegurança e Fraudes
69%  Chief Technology Officer (CTO) - SP
69%  Engineering Lead | AI & Data Platform
62%  Arquiteto(a) de Sistemas
```

Todas de tecnologia — o problema de trilha saiu. Mas **nenhuma alcança 70%**, o
limiar de destaque. As vagas certas continuam pontuando baixo porque são
exigentes, que é exatamente §1.1. Consertar a trilha tornou a lista honesta;
não tornou as notas corretas.

---

## Perguntas em aberto

Coisas que mudam o que se constrói e que só o dono do produto responde.

1. **Vaga fora da trilha deve sumir ou só descer?** O teto (65) mantém no
   relatório; o descarte tira. Hoje 76% do relatório é fora da trilha.
2. ~~**O modelo de linguagem pode entrar no julgamento por vaga**, ou fica só na
   síntese?~~ **Respondida em 26/ago:** entra, relendo as 20 do topo (§1.1). Resolve §1.1 de um jeito que nenhuma fórmula determinística resolve,
   mas custa por vaga e quebra a reprodutibilidade que o planejador protege.
3. **Qual janela é a padrão?** Hoje 30 dias. Com 24h o volume é gerenciável e a
   vaga é fresca; com 30 dias entram vagas que já receberam 200 candidaturas.
4. **Vale confirmar §2.2 com uma requisição ao LinkedIn?** É uma só, mas conta
   contra o mesmo limite que impõe 1 hora de bloqueio se estourar.
5. **Acompanhamento de candidatura entra no crivo?** Data, lembrete único e
   regra de encerramento são determinísticos, mas mudam o que o produto é (§7.6).
