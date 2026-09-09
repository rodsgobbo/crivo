# Especificação — Agente Autônomo de Busca de Vagas no LinkedIn

Documento de engenharia para reimplementar, em código, o pipeline que foi executado manualmente nesta sessão.

---

## 0. Leia isto antes de codar (verdades desconfortáveis)

**[Certo] "Sem gastar token" só é possível para ~70% do pipeline.**
O pipeline tem duas naturezas distintas:

| Etapa | Natureza | Precisa de LLM? |
|---|---|---|
| Login/sessão, navegação, coleta, dedupe, parsing | Determinística | **Não** |
| Filtro por título/local/data/senioridade | Determinística (regex + listas) | **Não** |
| Score de aderência | Híbrido — 80% via TF-IDF/keyword overlap | Opcional |
| "Por que combina" / "o que ajustar" / posicionamento estratégico | Julgamento | **Sim** |

Se você quer custo zero de LLM, aceite perder a camada de julgamento e entregue: vaga + link + local + score numérico + lista de keywords faltantes. É útil, mas é 60% do valor.
**Recomendação de custo:** rode tudo determinístico e chame um LLM **uma única vez no fim**, com um payload já filtrado (top 10 vagas, ~8k tokens de entrada). Isso custa centavos, não dólares.

**[Certo] Scraping do LinkedIn viola o ToS e você vai tomar rate limit.**
Eu tomei nesta própria sessão: após ~15 aberturas de página de detalhe, o endpoint de descrição da vaga parou de responder (esqueleto infinito de loading) por vários minutos. Consequências possíveis escalam de rate limit → CAPTCHA → checkpoint de verificação → banimento da conta.

Alternativas legítimas, em ordem de robustez:
1. **LinkedIn Job Search API** (parceiro Talent Solutions) — caro, aprovação necessária.
2. **RSS/JSON do endpoint público** `linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` — não requer login, retorna HTML de cards, muito mais estável que a UI logada. **É por aqui que eu começaria.**
3. **Agregadores com API** — Adzuna (grátis até certo volume), Jooble, Indeed Publisher, Google Jobs via SerpAPI.
4. **Automação de browser com sessão logada** (o que eu fiz) — maior cobertura, maior risco.

**[Provável] O gargalo do seu app não vai ser IA. Vai ser a descrição da vaga.**
Coletar título/empresa/local/link é fácil. Coletar o **texto completo da descrição** — que é onde mora todo o sinal de aderência — é a parte frágil, lenta e que dispara o bloqueio.

---

## 1. Arquitetura do pipeline

```
[1] PERFIL          → extrai o perfil-alvo do LinkedIn (uma vez, cacheia)
      ↓
[2] ESTRATÉGIA      → deriva N queries de busca a partir do perfil
      ↓
[3] COLETA          → executa as queries, coleta cards, dedupe por jobId
      ↓
[4] PRÉ-FILTRO      → descarta por título/local/data (determinístico, barato)
      ↓
[5] ENRIQUECIMENTO  → busca descrição completa só dos sobreviventes (caro/frágil)
      ↓
[6] SCORE           → aderência 0–100 determinística
      ↓
[7] SÍNTESE (LLM)   → 1 chamada com o top-N para gerar narrativa
      ↓
[8] RELATÓRIO
```

Regra de ouro: **nunca enriqueça antes de pré-filtrar.** No meu run, 40 cards coletados viraram 12 após pré-filtro. Isso é 70% menos requisições no endpoint que bloqueia.

---

## 2. Etapa 1 — Extração do perfil

### URLs (usuário logado)
```
https://www.linkedin.com/in/me/                              → redireciona para o perfil próprio
https://www.linkedin.com/in/{slug}/                          → headline, localização, About
https://www.linkedin.com/in/{slug}/details/experience/       → histórico completo (confiável)
https://www.linkedin.com/in/{slug}/details/skills/           → skills (lazy-loaded, precisa scroll)
```

**Descoberta prática:** as páginas `/details/*` renderizam texto limpo e completo, enquanto a página principal do perfil é lazy-loaded em cards que demoram 10–20s. **Sempre prefira `/details/experience/` para o histórico.**

### Schema de saída
```json
{
  "nome": "string",
  "headline": "string",
  "localizacao": {"cidade":"Osasco","uf":"SP","pais":"BR"},
  "about": "string",
  "open_to_work": {"ativo": true, "visibilidade": "recruiters_only",
                   "modelos": ["on-site","hybrid","remote"], "local": "Osasco, SP"},
  "cargo_atual": {"titulo":"...","empresa":"...","desde":"2025-08","modelo":"remote"},
  "anos_experiencia_total": 20,
  "anos_no_dominio": 6,
  "nivel_inferido": "manager",
  "tamanho_time_liderado": 9,
  "setor": ["fintech","pagamentos"],
  "experiencias": [{"titulo","empresa","inicio","fim","local","modelo","descricao","skills"}],
  "skills_extraidas": ["AWS","Kubernetes","Terraform","MongoDB", "..."],
  "metricas_resultado": ["-93% triagem","-75% deploy","R$414k/ano","99,69%"],
  "idiomas_declarados": [],
  "problemas_higiene": ["dois cargos simultâneos marcados como Present"]
}
```

### Inferência de nível (determinística)
```python
NIVEL = [
  (r'\b(cto|vp|chief|diretor|director|head)\b',            'executive'),
  (r'\b(gerente|manager|gerência|coordenador|coordinator)\b','manager'),
  (r'\b(staff|principal|especialista|specialist|lead|líder)\b','lead'),
  (r'\b(s[êe]nior|senior|sr\.?)\b',                         'senior'),
]
# aplica sobre o título do cargo ATUAL; primeiro match vence
# reforço: se descrição menciona "lidero|gestão de time|N pessoas" → sobe para manager
```

### Extração de métricas (para o relatório e para a narrativa do LLM)
```python
RE_METRICA = r'(?:reduz|aument|econom|dispon)\w*[^.]{0,60}?(\d{1,3}(?:[.,]\d+)?\s?%|R\$\s?[\d.,]+\s?(?:mil|milhões?)?)'
```

---

## 3. Etapa 2 — Geração de queries (o "cérebro" barato)

Não peça ao LLM para inventar as queries. Use uma **matriz de expansão** determinística.

```python
# 1) Extraia os "eixos" do headline + títulos de cargo
EIXOS_FUNCAO   = ["SRE", "Site Reliability", "Platform Engineering", "Infraestrutura",
                  "Infrastructure", "DevOps", "Cloud", "DBA", "Observabilidade", "FinOps"]
EIXOS_NIVEL    = {"manager": ["Manager","Gerente","Gerência","Head","Coordenador","Tech Manager",
                              "Engineering Manager","Líder","Lead","Director","Diretor"]}

# 2) Produto cartesiano filtrado (evite combinações absurdas)
queries = [f"{f} {n}" for f in EIXOS_FUNCAO for n in EIXOS_NIVEL[nivel]]

# 3) Acrescente queries "âncora" — títulos literais que o mercado usa
ANCORAS = ["Engineering Manager Infrastructure", "Site Reliability Engineering Manager",
           "Gerente de Infraestrutura e Cloud", "Gerente de Plataforma de Engenharia",
           "Head de Infraestrutura", "Coordenação de Cloud", "SRE Manager"]

# 4) Bilíngue obrigatório no Brasil: rode PT e EN. Metade das vagas boas está em inglês.
```

**Aprendizado do run real:** buscas de uma palavra só (`"SRE"`, `"Reliability"`, `"FinOps"`) retornam lixo — o matching do LinkedIn é fuzzy e traz "Chief Operating Officer" e "Engenheiro de Estruturas". **Queries de 2–4 palavras com função + nível têm precisão muito maior.** Descarte queries de 1 token.

---

## 4. Etapa 3 — Coleta

### 4a. Rota recomendada: endpoint guest (sem login, sem bloqueio agressivo)
```
GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
    ?keywords={q}&location={loc}&f_TPR=r2592000&f_E=4,5&start={0,10,20,...}
```
Retorna HTML de `<li>` com: jobId (`data-entity-urn`), título, empresa, local, data ISO em `<time datetime>`.
Descrição completa: `GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jobId}`

### 4b. Rota que eu usei: UI logada
```
https://www.linkedin.com/jobs/search/?keywords={q}&location=Brasil&f_TPR={r}&f_E={e}&sortBy=R
```

### Tabela de parâmetros de filtro (validados)
| Param | Valores |
|---|---|
| `f_TPR` | `r86400` 24h · `r604800` 7d · `r2592000` 30d |
| `f_E` | `1` estágio · `2` júnior · `3` pleno · `4` sênior/pleno-sênior · `5` diretor · `6` executivo |
| `f_WT` | `1` presencial · `2` remoto · `3` híbrido |
| `f_JT` | `F` full-time · `C` contrato · `P` part-time |
| `f_C` | IDs de empresa (CSV) |
| `sortBy` | `R` relevância · `DD` data |
| `start` | paginação, passo 25 |
| `geoId` | `106057199` = Brasil |

### Armadilha crítica: virtualização da lista
A lista de resultados usa `<li data-occludable-job-id>`. **Os `<li>` existem no DOM (25 por página), mas o conteúdo interno só é renderizado quando o item entra no viewport.** Um `querySelectorAll` ingênuo retorna 25 elementos e apenas ~7 com texto.

O container rolável **não** é `.scaffold-layout__list` (essa div tem `scrollHeight == clientHeight`). É um filho com classe ofuscada e aleatória. Localize-o dinamicamente:

```js
function getScroller() {
  const li = document.querySelector('li[data-occludable-job-id]');
  let el = li && li.parentElement;
  while (el && el.scrollHeight <= el.clientHeight + 50) el = el.parentElement;
  return el;
}

async function harvest() {
  const scroller = getScroller();
  const found = new Map();
  const grab = () => document.querySelectorAll('li[data-occludable-job-id]').forEach(li => {
    const id = li.getAttribute('data-occludable-job-id');
    if (found.has(id)) return;
    const lines = li.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    if (!lines.length) return;                 // ainda occluded
    found.set(id, {id, titulo: lines[0], empresa: lines[1], local: lines[2], flags: lines.slice(3)});
  });
  for (let i = 0; i < 20; i++) { grab(); scroller.scrollTop += 500; await sleep(250); }
  grab();
  return [...found.values()];
}
```

Em Playwright/Puppeteer isso é mais simples: `page.locator('li[data-occludable-job-id]').nth(i).scrollIntoViewIfNeeded()` em loop.

### Sinais de ouro nos flags do card (colete sempre)
| Flag | Significado | Uso no score |
|---|---|---|
| `You'd be a top applicant` | matching do próprio LinkedIn | **+10 no score** |
| `Actively reviewing applicants` | recrutador ativo | +5 |
| `Be an early applicant` | baixa concorrência | +5 |
| `N connections work here` | caminho de indicação | +5 e vira ação no relatório |
| `Over 100 people clicked apply` | alta concorrência | −5 |
| `Promoted` | anúncio pago | neutro |
| `Viewed` | já visto | dedupe de histórico |

### Fonte gratuita de descoberta que quase ninguém usa
A seção **"More jobs"** no rodapé de qualquer página de vaga é o motor de recomendação do LinkedIn, já personalizado para o perfil logado. No meu run ela entregou 12 vagas altamente relevantes que **nenhuma das minhas queries encontrou** (VTEX, Magalu Cloud, Kraken, Housecall Pro, Avenue Code, Peloton). **Trate cada página de vaga aberta como uma nova fonte de sementes** e alimente de volta na fila de coleta. Também vale: `linkedin.com/jobs/collections/recommended/`.

---

## 5. Etapa 4 — Pré-filtro (descarte barato, antes de enriquecer)

```python
# HARD REJECT por título — economiza a etapa cara
REJECT_TITULO = [
  r'quality assurance|\bqa\b', r'product manager', r'delivery manager',
  r'scrum|agile coach', r'\bpre[- ]?sales|comercial|vendas\b',
  r'paralegal|tax|fiscal|jurídic', r'estrutur(al|as)\b',   # "Engenheiro de Estruturas"
  r'\bsuporte n[12]\b', r'estágio|intern|trainee|júnior|junior',
]

# SOFT REJECT — engenharia de SOFTWARE quando o alvo é INFRA
# Só descarta se NÃO houver termo de infra no título
SOFT = r'(engenharia|engineering) de software|software engineering'
INFRA = r'sre|reliability|infra|infrastructure|platform|cloud|devops|dba|database|observab'

# HARD REJECT geográfico
def reject_local(vaga, perfil):
    if vaga.modelo == 'remote': return False
    if vaga.uf == perfil.uf and dist_km(vaga.cidade, perfil.cidade) <= RAIO_KM: return False
    return True   # presencial/híbrido fora do raio → descarta ou marca como "blocker"
```

**Decisão de produto:** eu não descartei silenciosamente vagas fora do raio (Agibank/Campinas, Sicredi/Porto Alegre). Mostrei com o blocker explícito. Silenciar é pior — o usuário pode estar disposto a mudar. Faça um flag `blocker: "presencial em Campinas, ~100km"`, não um `DELETE`.

---

## 6. Etapa 5 — Enriquecimento (a parte frágil)

```
GET https://www.linkedin.com/jobs/view/{jobId}/
```

**Comportamento real observado:**
- A descrição vive em `#job-details` / `.jobs-description__content`, renderizada **assincronamente**.
- Requer **scroll para dentro do viewport** para disparar o fetch. Sem scroll, nunca carrega.
- Tempo típico: 8–15s. Sob rate limit: **nunca** carrega (esqueleto cinza permanente).
- Não há erro visível. O sinal de bloqueio é `document.body.innerText.length` travado em ~1500 chars e ausência da string `"About the job"`.

```js
async function getDescricao(page) {
  await page.mouse.wheel(0, 1200);
  await page.waitForFunction(() => document.body.innerText.includes('About the job'),
                             { timeout: 20000 });
  return page.evaluate(() => {
    const b = document.body.innerText;
    const i = b.indexOf('About the job');
    const j = b.indexOf('Set alert for similar');
    return b.slice(i, j > i ? j : i + 8000);
  });
}
```

### Anti-bloqueio (obrigatório, não opcional)
- **Concorrência 1.** Nada de paralelizar detalhes de vaga.
- Delay aleatório 8–20s entre detalhes. Eu quebrei isso e fui bloqueado.
- Backoff exponencial no timeout: 60s → 120s → 300s. Se 3 falhas seguidas, **pare o run** e retome depois.
- Sessão persistente (cookies em disco), um único user-agent estável, viewport fixo.
- Cacheie descrições por `jobId` para sempre. Uma vaga só é enriquecida uma vez na vida.
- Rode incremental: `f_TPR=r86400` diário em vez de `r2592000` semanal. Muito menos volume por run.

### Nota sobre o texto extraído
A página de vaga também traz, de graça, dados valiosos para o relatório:
`N candidatos que clicaram em aplicar`, `distribuição de senioridade dos candidatos`, `X pessoas contratadas da empresa Y`, `pessoas da sua rede que trabalham lá`. Capture — isso virou o insight mais acionável do meu relatório ("apenas 10% dos candidatos são nível manager").

---

## 7. Etapa 6 — Score de aderência (0–100, sem LLM)

```
score = 0.40 * A + 0.20 * B + 0.15 * C + 0.15 * D + 0.10 * E   (0..1)  × 100
        + bônus_sinais − penalidades
```

| | Componente | Cálculo |
|---|---|---|
| **A** | Skills técnicas | `|skills_perfil ∩ skills_vaga| / |skills_vaga_obrigatórias|` — use ontologia com sinônimos |
| **B** | Nível/senioridade | 1.0 mesmo nível · 0.85 um acima · 0.5 um abaixo · 0.1 dois de distância |
| **C** | Domínio/setor | 1.0 mesmo setor (fintech→fintech) · 0.6 adjacente (banco, e-commerce) · 0.3 distante |
| **D** | Escopo de gestão | `min(1, liderados_vaga_esperado / liderados_perfil)` |
| **E** | Compatibilidade geográfica | 1.0 remoto · 0.9 híbrido no raio · 0.6 presencial no raio · 0.15 fora do raio |

**Bônus/penalidades (dos flags do card):**
`+10` top applicant · `+5` early applicant · `+5` conexões na empresa · `+5` actively reviewing
`−5` >100 candidatos · `−10` vaga repostada há >3 semanas · `−15` requisito eliminatório não atendido (ex.: inglês fluente sem proficiência declarada)

### Ontologia de sinônimos — é aqui que o score ganha ou perde
Sem isso, "Aurora" ≠ "PostgreSQL" e você perde matches óbvios. Mínimo viável:
```yaml
postgresql: [postgres, pgsql, aurora postgresql, rds postgres]
sqlserver:  [sql server, mssql, t-sql]
mongodb:    [mongo, documentdb, atlas]
observability: [observabilidade, dynatrace, datadog, grafana, prometheus, new relic, apm, opentelemetry]
iac:        [terraform, terragrunt, pulumi, cloudformation, infrastructure as code]
k8s:        [kubernetes, eks, aks, gke, openshift, k8s]
cicd:       [jenkins, argo, argocd, tekton, github actions, gitlab ci, harness, spinnaker]
finops:     [otimização de custo, cost optimization, savings plan, reserved instance, cloud cost]
compliance_fin: [lgpd, pci-dss, pci dss, bacen, sox, resolução 4658]
```

### Detecção de gaps (gera o "o que ajustar" sem LLM)
```python
gaps = skills_obrigatorias_vaga - skills_perfil       # → "keywords ausentes no seu perfil"
extras = skills_perfil & skills_desejaveis_vaga       # → "seus diferenciais nesta vaga"
```
Só isso já produz recomendação acionável: *"a vaga cita PostgreSQL, DynamoDB, Azure e Datadog — ausentes no seu perfil"*. Foi exatamente essa a recomendação de maior valor no meu relatório, e ela é **100% determinística**.

---

## 8. Etapa 7 — A única chamada de LLM

Envie o payload já filtrado. Nunca mande HTML bruto.

### System prompt

```
Você é um conselheiro estratégico de carreira em tecnologia, especializado no mercado
brasileiro. Você NÃO é um coach motivacional.

REGRAS INVIOLÁVEIS:
1. Precisão acima de concordância. Se a evidência for fraca, diga que é fraca.
2. Classifique toda afirmação não-óbvia como [Certo], [Provável] ou [Suposição].
   - [Certo]     = observado diretamente nos dados fornecidos
   - [Provável]  = inferência com base sólida, mas não verificada
   - [Suposição] = hipótese sua; marque como tal e diga o que a validaria
3. Apresente riscos, trade-offs e consequências de cada recomendação.
4. Se houver uma verdade desconfortável relevante, ela vem PRIMEIRO, não no rodapé.
5. Nunca invente vaga, link, empresa, requisito ou número que não esteja no INPUT.
   Se um campo estiver vazio, escreva "não coletado" — jamais preencha com plausibilidade.
6. Zero elogio vazio. Zero frase de validação automática.
7. Discorde do usuário quando houver evidência.
8. Seja direto. Corte adjetivo que não carrega informação.
9. Distinga explicitamente o que você LEU (descrição completa) do que você INFERIU
   (título + empresa + sinais).
```

### User prompt (template)

```
## PERFIL
{perfil_json}

## VAGAS PRÉ-FILTRADAS E PONTUADAS
{vagas_json}   # top 10–15. Cada uma: id, titulo, empresa, url, local, modelo,
               # data, flags, score, componentes_do_score, skills_match,
               # skills_gap, descricao_completa (ou null), descricao_disponivel: bool

## VAGAS DESCARTADAS
{descartadas_json}   # titulo, empresa, motivo_do_descarte

## CONTEXTO DE MERCADO
- Queries executadas: {queries}
- Total de resultados brutos: {n_brutos}
- Total após pré-filtro: {n_filtrados}
- Janela temporal: últimos {dias} dias
- Data de hoje: {hoje}

## TAREFA

### Bloco 1 — VERDADES DESCONFORTÁVEIS (3 a 5 itens, no topo)
Diga primeiro o que o usuário não quer ouvir. Baseie-se nos NÚMEROS acima:
liquidez real do mercado para o cargo dele, ilusões de posicionamento, erros de
higiene do perfil, gaps que travam as vagas de maior teto. Classifique cada uma.

### Bloco 2 — VAGAS (ordenadas por score decrescente)
Para cada uma:
- **Título — Empresa**
- Link direto (use EXATAMENTE a url do INPUT)
- Local · modelo de trabalho · data de publicação
- **Por que combina:** ancore em trechos concretos da descrição quando houver.
  Se descricao_disponivel = false, escreva a avaliação e diga que foi inferida
  de título/empresa/sinais, sem leitura da descrição.
- **Aderência: N%** (use o score do INPUT; não recalcule)
- **O que ajustar:** derive de skills_gap. Recomendação executável hoje,
  não conselho genérico.
- **Trade-off / blocker:** se houver (local, senioridade, idioma, setor, concorrência),
  diga sem suavizar. Se a vaga não vale a pena para este perfil, diga para não aplicar.

### Bloco 3 — DESCARTADAS
Uma linha por vaga explicando o critério. Isso prova que houve curadoria.

### Bloco 4 — HABILIDADES MAIS PEDIDAS
Agregue por frequência sobre as vagas com descrição. Separe em:
(a) núcleo obrigatório  (b) diferenciadores  (c) lacunas de keyword do usuário.

### Bloco 5 — AJUSTES ESTRATÉGICOS DE POSICIONAMENTO
Máximo 6 itens, ordenados por (impacto ÷ esforço) decrescente.
Cada item: o que mudar, por que, e qual o custo/trade-off.
Inclua ao menos um item que NÃO seja sobre o perfil do LinkedIn
(rede, canal, timing, competência a construir).

FORMATO: markdown. Sem emoji decorativo. Sem introdução. Comece pelo Bloco 1.
```

**Custo estimado:** ~10–15k tokens de entrada, ~3k de saída. Poucos centavos por execução.

---

## 9. Modo 100% sem LLM (fallback)

Se quiser custo literalmente zero, substitua o Bloco 2 por templates:

```python
POR_QUE = ("Match em {n_match} de {n_total} requisitos técnicos: {top_skills}. "
           "Nível {nivel_vaga} compatível com {nivel_perfil}. "
           "Setor {setor} {'igual ao' if mesmo else 'adjacente ao'} seu histórico.")

AJUSTAR = ("Adicione ao perfil, se tiver vivência real: {gaps}. "
           "{'Vaga com alta concorrência ({n} candidatos) — busque indicação.' if n>80 else ''}")
```

Você perde nuance e perde os Blocos 1 e 5 (que são o valor estratégico), mas mantém a triagem funcionando sozinha, diariamente, de graça.

---

## 10. Persistência e execução

```sql
CREATE TABLE vagas (
  job_id TEXT PRIMARY KEY, titulo TEXT, empresa TEXT, url TEXT,
  local TEXT, modelo TEXT, publicada_em DATE, flags JSON,
  descricao TEXT, descricao_coletada_em TIMESTAMP,
  score REAL, score_componentes JSON,
  status TEXT DEFAULT 'novo',      -- novo|visto|aplicado|descartado|expirado
  primeira_vez_em TIMESTAMP, ultima_vez_em TIMESTAMP
);
CREATE TABLE runs (id, executado_em, queries JSON, n_brutos, n_filtrados, n_novos, bloqueios);
```

- **Cron diário** com `f_TPR=r86400`. Volume baixo → risco de bloqueio baixo.
- Notifique **apenas** vagas com `status='novo' AND score >= 70`.
- Marque `expirado` quando o `jobId` sumir dos resultados por 3 runs seguidos.
- Guarde o histórico de `score` — se você mudar o perfil, dá para medir se a aderência média subiu. Esse é o KPI real do produto.

---

## 11. O que vai quebrar (planeje agora)

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Classes CSS ofuscadas mudam | Alta, a cada semanas | Ancore em `data-*` e em texto, **nunca** em classe. `li[data-occludable-job-id]` é o único seletor estável que encontrei. |
| Rate limit / CAPTCHA | **Certa** se abusar | Concorrência 1, delays, backoff, cache agressivo, run incremental |
| Layout A/B testado por conta | Média | Extração baseada em `innerText` + regex é mais resiliente que DOM query |
| Vaga sem descrição no LinkedIn | Média | Vagas "Responses managed off LinkedIn" às vezes só têm descrição no ATS externo — siga o link de Apply |
| Conta bloqueada | Baixa/Média | Use conta secundária ou rota guest para coleta; reserve a conta principal para candidatura |

---

## 12. Checklist de implementação

- [ ] Extração de perfil via `/details/experience/` → JSON validado
- [ ] Detector de problemas de higiene do perfil (cargos "Present" duplicados, gaps, sobreposições)
- [ ] Matriz de queries PT + EN, mínimo 2 tokens por query
- [ ] Coletor com harvest anti-occlusion + captura de flags
- [ ] Realimentação da fila via seção "More jobs" e `/jobs/collections/recommended/`
- [ ] Pré-filtro com listas de reject e regra soft de infra vs. software
- [ ] Enriquecedor serial com backoff e cache permanente por jobId
- [ ] Ontologia de sinônimos (comece com 50 entradas, cresce com o uso)
- [ ] Scorer com os 5 componentes + bônus/penalidades
- [ ] SQLite + dedupe + máquina de estados
- [ ] Chamada única de LLM com os prompts da seção 8
- [ ] Modo fallback sem LLM
- [ ] Cron diário + notificação só de score ≥ 70
