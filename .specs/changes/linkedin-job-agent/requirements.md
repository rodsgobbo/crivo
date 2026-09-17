# Requirements

## Overview

Um profissional de tecnologia que procura vaga gasta horas repetindo a mesma triagem manual: rodar buscas no LinkedIn, abrir dezenas de anúncios, ler descrições, descartar o que não serve e tentar lembrar o que já viu ontem. O trabalho é repetitivo, o critério de corte é inconsistente entre um dia e outro, e as vagas boas expiram antes de serem vistas.

Este sistema automatiza a triagem como aplicação web multiusuário. Cada candidato entra com a conta Google, conecta a conta LinkedIn e importa o próprio currículo do Google Drive, de um arquivo Word ou de um PDF. O sistema consolida essas origens em um perfil-alvo estruturado, deriva buscas a partir dele, coleta anúncios, descarta o que não se aplica antes de gastar requisições caras, pontua a aderência de forma determinística e apresenta um relatório com as vagas ordenadas, os links, os contatos e as descrições completas. Duas chamadas de modelo de linguagem por execução acrescentam a camada de julgamento — uma relê as vagas do topo, a outra escreve a síntese —, e o sistema continua funcionando sem elas quando são desativadas.

A camada de julgamento é alimentada por credencial do próprio usuário. Cada candidato escolhe um provedor de modelo de linguagem entre os que o operador habilitou e cola a chave que ele mesmo gerou no console daquele provedor. Isso mantém a cota gratuita de cada usuário sob o teto dele, em vez de dividir uma cota única entre todos, e mantém o custo do operador em zero. A lista de provedores vive em configuração e não no código, porque ofertas gratuitas mudam de limite e desaparecem em prazos curtos. Um usuário sem credencial não fica sem serviço: o run inteiro roda em modo determinístico.

A divisão de origens do perfil é assimétrica por limitação da plataforma, não por escolha de projeto. Os escopos abertos do LinkedIn devolvem identidade, foto e endereço de e-mail, e não devolvem histórico profissional, cargos nem competências. O currículo importado é, portanto, a origem substantiva do perfil, e a conexão LinkedIn é a origem de identidade. Nenhum usuário entrega senha ou sessão do LinkedIn ao sistema.

A coleta de dados de vaga é feita exclusivamente por rotas públicas, sem sessão autenticada de qualquer conta, e nunca pela conta de um usuário. A capacidade de coleta continua sendo um recurso compartilhado e limitado — a origem impõe limite de requisição independentemente de haver sessão — e por isso segue distribuída entre os usuários de forma explícita.

Os sinais que o LinkedIn calcula contra o perfil logado — "You'd be a top applicant", sobretudo — não existem na rota pública. Eles chegam por uma extensão que roda no navegador do próprio usuário, na página que ele já está vendo, e envia apenas o que leu. O sistema recebe o resultado da leitura, nunca a sessão que a faz.

## Glossary

| Term | Definition |
|------|------------|
| Usuário | Pessoa autenticada no sistema por meio da conta Google. |
| Identificador de usuário | Chave interna e estável que identifica um usuário, independente do endereço de e-mail. |
| Perfil-alvo | Conjunto estruturado de dados do candidato consolidado a partir das origens conectadas, usado como base para busca e pontuação. |
| Origem de perfil | Uma das procedências de dado do perfil-alvo: conexão LinkedIn, currículo importado ou edição manual do usuário. |
| Currículo | Documento de texto enviado ou importado pelo usuário, em formato Google Docs, Word ou PDF. |
| Precedência de origem | Regra que decide qual origem prevalece quando duas informam o mesmo campo do perfil-alvo. |
| Camada guest | Rota de coleta que usa endpoints públicos do LinkedIn, não requer sessão e opera sob orçamento diário. |
| Card | Item resumido de vaga na lista de resultados, contendo título, empresa, local e sinais, sem a descrição completa. |
| Sinal do card | Rótulo exibido pelo LinkedIn junto ao card, como "You'd be a top applicant" ou "Be an early applicant". |
| Enriquecimento | Ato de buscar a descrição completa de uma vaga já coletada como card. |
| Pré-filtro | Descarte determinístico feito sobre cards, antes de qualquer enriquecimento. |
| Blocker | Impedimento conhecido de uma vaga que não a elimina, como exigir presença fora do raio de deslocamento. |
| Score de aderência | Nota inteira de 0 a 100 calculada sem modelo de linguagem. |
| Eixo de função | Termo de área técnica derivado do perfil-alvo que serve de base para compor buscas, como "SRE" ou "Infraestrutura". |
| Requisito eliminatório | Exigência da vaga cujo não atendimento inviabiliza a candidatura, como proficiência em idioma não declarada no perfil-alvo. |
| Ontologia de sinônimos | Mapa que agrupa nomes distintos da mesma tecnologia, como `aurora postgresql` e `postgres`. |
| Gap de skill | Competência exigida pela vaga e ausente do perfil-alvo. |
| Modo determinístico | Modo de operação em que nenhuma chamada a modelo de linguagem é feita. |
| Bloqueio de coleta | Estado em que a origem para de retornar conteúdo por excesso de requisições. |
| Run | Uma execução completa do pipeline para um usuário, do planejamento de buscas até o relatório. |
| Cota de coleta | Parcela do orçamento diário de coleta atribuída a um usuário. |
| Testemunho de acesso | Credencial temporária emitida por um provedor de autorização e usada para acessar dados em nome do usuário. |
| Provedor de modelo | Serviço de modelo de linguagem habilitado pelo operador e escolhível pelo usuário. |
| Credencial de provedor | Chave estática que o usuário gera no console do provedor e informa ao sistema. |
| Registro de provedores | Lista de provedores habilitados, definida em configuração e não em código. |
| Requisição de síntese | Unidade lógica de uma síntese por run. Tentativas sucessivas em provedores diferentes após recusa por limite de uso pertencem à mesma requisição de síntese. |
| Extensão | Código que roda no navegador do próprio usuário, dentro da página do LinkedIn, e envia ao sistema os insights que leu nos cards. |
| Insight | Dado de concorrência lido pela extensão: contagem de candidatos, distribuição de senioridade ou sinal do card. |
| Token do extrator | Credencial de escopo único emitida ao usuário para que a extensão grave insights sem sessão de navegador. |
| Releitura | Segunda avaliação das vagas do topo por modelo de linguagem, gravada ao lado do score de aderência sem substituí-lo. |

## Assumptions

- A conexão LinkedIn usa o fluxo de autorização oficial da plataforma. Os escopos disponíveis sem parceria comercial devolvem identidade, foto e endereço de e-mail, e não devolvem histórico profissional, cargos nem competências. Todo requisito que dependeria de histórico vindo do LinkedIn foi atribuído ao currículo.
- O sistema nunca solicita, recebe nem armazena senha, cookie ou sessão de LinkedIn, seja de um usuário ou do operador. Não existe sessão LinkedIn no sistema.
- A coleta automatizada de dados de vaga contraria os Termos de Serviço do LinkedIn mesmo por rota pública e sem sessão. O operador do serviço assume esse risco de forma consciente, e os requisitos de contenção existem para reduzi-lo, não para eliminá-lo. Sem conta autenticada envolvida, não há credencial a ser suspensa: a exposição é de bloqueio por endereço, não de perda de conta.
- O currículo é escrito em português ou em inglês e descreve experiência profissional em prosa ou em tópicos. Currículo em imagem digitalizada sem camada de texto está fora do escopo.
- A extração estruturada do currículo é uma operação por documento, executada uma vez e armazenada. Ela não conta contra a requisição de síntese por run definida em REQ-15 nem contra a releitura definida em REQ-28.
- Os provedores de modelo habilitados aceitam chave estática gerada pelo próprio usuário no console do provedor. Provedor que exija o registro de uma aplicação ou um fluxo de autorização por login está fora do escopo, porque o registro de aplicação é responsabilidade do operador e anularia o modelo de credencial por usuário.
- Assume-se que ao menos tres provedores atendem simultaneamente ao criterio de chave self-service sem registro de aplicacao. Essa premissa foi aceita por decisao explicita, sem verificacao nesta fase, e sustenta a cadeia de fallback de REQ-26. Se na implementacao apenas um provedor se confirmar viavel, a cadeia perde funcao e o modo deterministico deixa de ser excecao.
- A oferta gratuita de cada provedor tem limite próprio, muda sem aviso e pode ser encerrada. Por isso a lista de provedores é configuração, nenhum provedor é nomeado nestes requisitos, e o sistema precisa continuar útil quando todos recusarem a requisição.
- A qualidade da extração de currículo varia conforme o provedor e o modelo escolhidos pelo usuário. A conferência humana dos campos extraídos é a defesa contra extração incorreta, e por isso é um portão obrigatório e não uma etapa opcional.
- O sistema opera sob a Lei Geral de Proteção de Dados brasileira, tratando currículo, histórico profissional e endereço de e-mail como dado pessoal de titular identificado.

## Requirements

### REQ-1: Autenticação do usuário por conta Google

**User Story:** As a candidato, I want entrar no sistema com minha conta Google, so that eu não precise criar mais uma senha para usar o serviço.

#### Acceptance Criteria

1.1 WHEN um visitante conclui o fluxo de autorização Google, THEN the Identity Service SHALL create uma sessão vinculada a um identificador de usuário estável.

1.2 THE Identity Service SHALL derive o identificador de usuário do identificador de assunto devolvido pelo provedor, e não do endereço de e-mail.

1.3 IF o fluxo de autorização Google falha ou é cancelado, THEN the Identity Service SHALL reject a criação de sessão e display uma mensagem que nomeia a causa.

1.4 WHEN uma sessão excede a duração máxima configurada, THEN the Identity Service SHALL delete a sessão e require nova autorização.

1.5 WHILE nenhuma sessão válida está presente, the Job Agent SHALL reject qualquer requisição a dado de perfil, de vaga ou de relatório.

1.6 WHEN o usuário solicita o encerramento da sessão, THEN the Identity Service SHALL delete a sessão sem revogar os testemunhos de acesso das origens conectadas.

### REQ-2: Conexão da conta LinkedIn do usuário

**User Story:** As a candidato, I want conectar minha conta LinkedIn pelo fluxo oficial, so that o sistema confirme minha identidade profissional sem que eu entregue minha senha.

#### Acceptance Criteria

2.1 WHEN o usuário conclui o fluxo de autorização do LinkedIn, THEN the LinkedIn Connector SHALL store os campos de identidade devolvidos pelos escopos concedidos sob o identificador de usuário.

2.2 THE LinkedIn Connector SHALL reject qualquer entrada de senha, de cookie ou de sessão de LinkedIn fornecida por um usuário.

2.3 THE LinkedIn Connector SHALL record, junto aos dados armazenados, a lista de escopos efetivamente concedidos pelo usuário.

2.4 IF um campo de perfil não é devolvido pelos escopos concedidos, THEN the LinkedIn Connector SHALL record esse campo como não disponível por escopo.

2.5 WHEN o usuário solicita a desconexão da conta LinkedIn, THEN the LinkedIn Connector SHALL delete os dados de identidade obtidos por essa conexão e revoke o testemunho de acesso.

2.6 IF o testemunho de acesso do LinkedIn expira, THEN the LinkedIn Connector SHALL record a conexão como expirada e display ao usuário o pedido de reconexão.

### REQ-3: Importação de currículo

**User Story:** As a candidato, I want trazer meu currículo do Google Drive ou de um arquivo, so that o sistema conheça meu histórico sem que eu redigite nada.

#### Acceptance Criteria

3.1 WHEN o usuário seleciona um documento do Google Drive, THEN the Resume Importer SHALL create um registro de currículo contendo o texto extraído do documento e a origem da importação.

3.2 WHEN o usuário envia um arquivo em formato Word, THEN the Resume Importer SHALL create um registro de currículo contendo o texto extraído do arquivo.

3.3 WHEN o usuário envia um arquivo em formato PDF, THEN the Resume Importer SHALL create um registro de currículo contendo o texto extraído do arquivo.

3.4 THE Resume Importer SHALL reject qualquer arquivo cujo formato não esteja entre os formatos aceitos, nomeando os formatos aceitos na mensagem de recusa.

3.5 THE Resume Importer SHALL reject qualquer arquivo cujo tamanho exceda o limite configurado.

3.6 IF o texto extraído de um documento tem comprimento inferior ao mínimo configurado, THEN the Resume Importer SHALL reject a importação e display a suspeita de documento sem camada de texto.

3.7 THE Resume Importer SHALL request ao Google apenas o escopo de leitura necessário para exportar o documento selecionado.

3.8 THE Resume Importer SHALL store cada registro de currículo sob o identificador de usuário que o importou.

3.9 IF um usuário excede o número diário configurado de importações de currículo, THEN the Resume Importer SHALL reject a importação e display o instante em que novas importações serão aceitas.

### REQ-4: Extração estruturada do currículo

**User Story:** As a candidato, I want que meu currículo vire dados organizados, so that a pontuação use meu histórico real em vez de uma busca por palavra solta no texto.

#### Acceptance Criteria

4.1 WHEN um registro de currículo é criado, THEN the Resume Parser SHALL create um registro de extração contendo experiências, competências, formação, localização e idiomas.

4.2 THE Resume Parser SHALL record, para cada campo extraído, o trecho do texto original que o originou.

4.3 IF um campo não pode ser extraído do currículo, THEN the Resume Parser SHALL store esse campo como nulo e record o nome do campo em uma lista de lacunas.

4.4 THE Resume Parser SHALL store o resultado da extração indexado pelo conteúdo do currículo.

4.5 WHEN um currículo com conteúdo idêntico a um já extraído é importado, THEN the Resume Parser SHALL reuse o resultado armazenado sem emitir nova chamada de extração.

4.6 WHEN a extração termina, THEN the Resume Parser SHALL display ao usuário os campos extraídos para conferência antes de qualquer uso na triagem.

4.7 WHEN o usuário corrige um campo extraído, THEN the Resume Parser SHALL store a correção como origem de edição manual sem sobrescrever o valor extraído.

4.8 IF a extração estruturada falha, THEN the Resume Parser SHALL record a falha e display ao usuário a opção de preencher o perfil manualmente.

4.9 THE Resume Parser SHALL send a requisição de extração ao provedor de modelo escolhido pelo usuário dono do currículo.

4.10 THE Resume Parser SHALL record o provedor e o modelo que produziram cada registro de extração.

4.11 THE Resume Parser SHALL prevent o uso de um registro de extração em um run antes que o usuário confirme os campos extraídos.

4.12 IF nenhuma credencial de provedor está armazenada para o dono do currículo, THEN the Resume Parser SHALL display a opção de preencher o perfil manualmente sem emitir requisição a provedor.

### REQ-5: Consolidação do perfil-alvo

**User Story:** As a candidato, I want um perfil único montado a partir de tudo que conectei, so that eu entenda de onde veio cada informação que o sistema usa sobre mim.

#### Acceptance Criteria

5.1 WHEN uma origem de perfil é adicionada, alterada ou removida, THEN the Profile Merger SHALL create uma nova versão do perfil-alvo do usuário.

5.2 THE Profile Merger SHALL resolve conflito entre origens aplicando a precedência configurada, na qual a edição manual prevalece sobre o currículo e o currículo prevalece sobre a conexão LinkedIn.

5.3 THE Profile Merger SHALL record, para cada campo do perfil-alvo, a origem que forneceu o valor vigente.

5.4 THE Profile Merger SHALL derive o nível inferido a partir do título do cargo mais recente aplicando um conjunto de regras determinístico.

5.5 IF nenhuma origem fornece histórico profissional, THEN the Profile Merger SHALL reject o início de um run e display ao usuário a exigência de importar um currículo.

5.6 THE Profile Merger SHALL store cada versão do perfil-alvo sob o identificador de usuário.

5.7 WHILE a versão vigente do perfil-alvo é mais recente que a última alteração de origem, the Profile Merger SHALL reuse essa versão sem recalcular a consolidação.

5.8 THE Profile Merger SHALL accept, como campo opcional de origem manual, o número máximo de dias de escritório por semana que o candidato aceita, incluindo o valor zero.

### REQ-6: Diagnóstico de higiene do perfil

**User Story:** As a candidato, I want ser avisado dos defeitos do meu histórico, so that eu corrija o que afasta recrutadores antes de me candidatar.

#### Acceptance Criteria

6.1 WHEN uma versão do perfil-alvo é criada, THEN the Profile Merger SHALL record cada problema de higiene detectado, incluindo cargos simultâneos marcados como atuais, lacunas temporais entre experiências e sobreposições de período.

6.2 THE Profile Merger SHALL record, para cada problema de higiene, o campo de origem e o trecho de dado que motivou a detecção.

6.3 IF nenhum problema de higiene é detectado, THEN the Profile Merger SHALL record uma lista de problemas vazia em vez de omitir o resultado do diagnóstico.

### REQ-7: Planejamento determinístico de buscas

**User Story:** As a candidato, I want que as buscas sejam derivadas do meu perfil por regra fixa, so that eu obtenha resultados reproduzíveis sem pagar por geração de texto.

#### Acceptance Criteria

7.1 WHEN um run começa, THEN the Query Planner SHALL create a lista de buscas a partir do perfil-alvo sem emitir chamada a modelo de linguagem.

7.2 THE Query Planner SHALL reject qualquer busca composta por um único termo.

7.3 THE Query Planner SHALL create buscas em português e em inglês para cada eixo de função derivado do perfil-alvo.

7.4 THE Query Planner SHALL record a lista completa de buscas executadas junto ao registro do run.

7.5 WHEN o mesmo perfil-alvo e a mesma configuração são usados em runs diferentes, THEN the Query Planner SHALL create a mesma lista de buscas.

### REQ-8: Coleta e deduplicação de vagas

**User Story:** As a candidato, I want que o sistema junte os resultados de todas as buscas em uma lista sem repetição, so that eu não leia a mesma vaga várias vezes.

#### Acceptance Criteria

8.1 WHEN uma busca é executada, THEN the Collector SHALL create um card para cada vaga retornada, contendo identificador da vaga, título, empresa, local, data de publicação e URL canônica.

8.2 THE Collector SHALL discard qualquer card cujo identificador de vaga já exista no run corrente.

8.3 THE Collector SHALL record todo sinal de card retornado pela origem junto ao card correspondente.

8.5 IF uma busca retorna zero resultados, THEN the Collector SHALL record a busca como improdutiva e continue com as buscas restantes.

8.6 IF uma busca falha por erro de rede ou por resposta inválida da origem, THEN the Collector SHALL record a busca como falha e continue com as buscas restantes.

8.7 THE Collector SHALL store cada card sob o identificador de usuário do run corrente.

### REQ-9: Pré-filtro antes do enriquecimento

**User Story:** As a candidato, I want que vagas irrelevantes sejam descartadas antes da etapa cara, so that a capacidade de coleta seja gasta apenas com o que tem chance de servir.

#### Acceptance Criteria

9.1 WHEN a coleta de cards termina, THEN the Pre-Filter SHALL evaluate todos os cards antes que qualquer enriquecimento seja iniciado.

9.2 WHEN o título de um card corresponde a um padrão de rejeição configurado, THEN the Pre-Filter SHALL discard o card e record o padrão que causou o descarte.

9.3 THE Pre-Filter SHALL record, para cada card descartado, o título, a empresa e o motivo do descarte.

9.4 IF uma vaga exige presença física fora do raio de deslocamento do perfil-alvo, THEN the Pre-Filter SHALL retain o card e record um blocker que nomeia a cidade e a distância.

9.5 THE Pre-Filter SHALL retain qualquer card cujo modelo de trabalho seja remoto, independentemente da cidade da vaga.

9.6 THE Pre-Filter SHALL record a contagem de cards antes e depois do pré-filtro junto ao registro do run.

### REQ-10: Enriquecimento de descrição

**User Story:** As a candidato, I want a descrição completa das vagas que sobreviveram à triagem, so that a avaliação de aderência se apoie no texto do anúncio e não apenas no título.

#### Acceptance Criteria

10.1 WHEN uma vaga sobrevive ao pré-filtro, THEN the Enricher SHALL create um registro de descrição contendo o texto integral do anúncio e o instante da coleta.

10.2 IF a descrição de uma vaga não pode ser obtida, THEN the Enricher SHALL record essa vaga como sem descrição disponível e continue com as vagas restantes.

10.3 THE Enricher SHALL record, para cada vaga, um indicador booleano de disponibilidade da descrição.

10.4 WHEN a página de uma vaga apresenta dados de concorrência, THEN the Enricher SHALL record o número de candidatos e a distribuição de senioridade dos candidatos.

10.5 THE Enricher SHALL record todo endereço de e-mail de contato presente no texto da descrição coletada.

10.6 THE Enricher SHALL store a descrição de uma vaga uma única vez para todo o sistema.

10.7 WHEN uma vaga já possui descrição armazenada, THEN the Enricher SHALL reuse a descrição armazenada sem emitir requisição de rede.

### REQ-11: Contenção de bloqueio na coleta

**User Story:** As a operador do serviço, I want que o sistema respeite os limites da origem, so that o serviço não seja bloqueado pela origem e não pare para todos os usuários.

#### Acceptance Criteria

11.1 THE Enricher SHALL enrich no máximo uma vaga por vez em todo o sistema.

11.2 WHEN o enriquecimento de uma vaga termina, THEN the Enricher SHALL wait um intervalo aleatório dentro da faixa configurada antes de iniciar o próximo enriquecimento.

11.3 IF uma tentativa de enriquecimento excede o tempo limite, THEN the Enricher SHALL retry a mesma vaga após um intervalo de espera maior que o intervalo da tentativa anterior.

11.4 IF três tentativas consecutivas de enriquecimento falham, THEN the Enricher SHALL stop o enriquecimento e record um bloqueio de coleta.

11.5 WHILE um bloqueio de coleta está registrado, the Enricher SHALL reject qualquer nova requisição de enriquecimento.

11.6 THE Job Agent SHALL prevent o uso de qualquer credencial de usuário para coletar dados de vaga.

### REQ-12: Distribuição da cota de coleta entre usuários

**User Story:** As a candidato, I want receber uma parcela previsível da capacidade de coleta, so that outro usuário não consuma sozinho o orçamento do dia.

#### Acceptance Criteria

12.1 THE Budget Allocator SHALL create uma cota de coleta por usuário a partir do orçamento diário de coleta e do número de usuários ativos.

12.2 WHEN um enriquecimento é executado para um usuário, THEN the Budget Allocator SHALL decrease a cota de coleta desse usuário.

12.3 IF a cota de coleta de um usuário se esgota, THEN the Budget Allocator SHALL reject novos enriquecimentos para esse usuário e record a recusa no registro do run.

12.4 THE Budget Allocator SHALL record o consumo diário de cada usuário e o consumo total do sistema.

12.5 WHEN um novo dia começa, THEN the Budget Allocator SHALL create as cotas do dia sem transferir saldo não consumido do dia anterior.

### REQ-13: Pontuação determinística de aderência

**User Story:** As a candidato, I want uma nota comparável entre vagas calculada sempre pelo mesmo critério, so that eu ordene a fila de candidatura por evidência e não por impressão.

#### Acceptance Criteria

13.1 WHEN uma vaga termina a etapa de enriquecimento, THEN the Scorer SHALL create um score de aderência inteiro entre 0 e 100 sem emitir chamada a modelo de linguagem.

13.2 THE Scorer SHALL record o valor de cada componente que compôs o score junto ao score final.

13.3 THE Scorer SHALL equate variantes de uma mesma tecnologia declaradas na ontologia de sinônimos ao comparar competências do perfil com competências da vaga.

13.4 WHEN um sinal de card configurado como bônus está presente, THEN the Scorer SHALL increase o score pelo valor configurado para aquele sinal.

13.5 WHEN um sinal de card configurado como penalidade está presente, THEN the Scorer SHALL decrease o score pelo valor configurado para aquele sinal.

13.6 IF a descrição de uma vaga não está disponível, THEN the Scorer SHALL create o score a partir dos dados do card e record que o score foi calculado sem leitura da descrição.

13.7 WHEN o mesmo perfil-alvo e a mesma vaga são pontuados novamente sem alteração de configuração, THEN the Scorer SHALL create o mesmo score.

13.8 THE Scorer SHALL record o histórico de scores anteriores de cada vaga sem sobrescrever os valores passados.

13.9 WHEN a descrição de uma vaga declara um requisito eliminatório ausente do perfil-alvo, THEN the Scorer SHALL decrease o score pelo valor configurado para requisito eliminatório não atendido.

13.10 THE Scorer SHALL store o score final limitado ao intervalo de 0 a 100 após a aplicação de todos os bônus e penalidades.

13.11 WHERE o perfil-alvo declara um número máximo de dias de escritório, IF a descrição de uma vaga anunciada como remota exige mais dias que esse número, THEN the Scorer SHALL treat a vaga como presencial na passada final e record o aviso de presença junto ao aviso de deslocamento correspondente.

### REQ-14: Detecção de lacunas e diferenciais

**User Story:** As a candidato, I want saber quais palavras-chave da vaga faltam no meu perfil, so that eu tenha uma ação concreta para executar hoje.

#### Acceptance Criteria

14.1 WHEN uma vaga com descrição disponível é pontuada, THEN the Scorer SHALL create a lista de gaps de skill dessa vaga.

14.2 WHEN uma vaga com descrição disponível é pontuada, THEN the Scorer SHALL create a lista de competências do perfil-alvo que a vaga cita como desejáveis.

14.3 THE Scorer SHALL create as listas de gaps e de diferenciais sem emitir chamada a modelo de linguagem.

14.4 THE Scorer SHALL aggregate a frequência de cada competência exigida entre todas as vagas com descrição disponível do run corrente.

14.5 THE Scorer SHALL treat como competência do perfil-alvo tanto a competência declarada quanto a evidenciada no headline ou no título ou descrição de uma experiência.

14.6 WHERE existe perfil-alvo consolidado, the Report Renderer SHALL mark, na agregação de 14.4, as competências ausentes do perfil-alvo.

### REQ-15: Síntese estratégica por modelo de linguagem

**User Story:** As a candidato, I want uma leitura crítica das vagas mais aderentes, so that eu entenda o trade-off de cada candidatura em vez de receber só uma lista ordenada.

#### Acceptance Criteria

15.1 WHILE o modo determinístico está desativado, WHEN a pontuação de todas as vagas termina, THEN the Synthesizer SHALL send exatamente uma requisição de síntese por run.

15.2 THE Synthesizer SHALL send ao modelo de linguagem apenas dados estruturados já filtrados e pontuados.

15.3 THE Synthesizer SHALL require que cada afirmação não trivial da resposta seja classificada como certa, provável ou suposição.

15.4 THE Synthesizer SHALL reject qualquer resposta que cite URL, empresa ou vaga ausente dos dados enviados na requisição.

15.5 THE Synthesizer SHALL require que a resposta distinga as vagas avaliadas com leitura da descrição das vagas avaliadas apenas por card.

15.6 IF a requisição ao modelo de linguagem falha, THEN the Synthesizer SHALL record a falha de síntese no registro do run.

15.7 THE Synthesizer SHALL record a contagem de tokens enviados e recebidos junto ao registro do run.

15.8 THE Synthesizer SHALL prevent o envio de marcação HTML bruta da origem ao modelo de linguagem.

15.9 WHILE uma falha de síntese está registrada no run corrente, the Report Renderer SHALL create o relatório sem a seção de síntese.

15.10 THE Synthesizer SHALL restrict a requisição às vagas de maior score até o limite de vagas configurado.

15.11 THE Synthesizer SHALL send a requisição ao provedor de modelo escolhido pelo usuário dono do run corrente.

15.12 THE Synthesizer SHALL record o provedor e o modelo usados junto ao registro do run.

### REQ-16: Contenção de injeção de prompt

**User Story:** As a candidato, I want que um anúncio malicioso não consiga desviar o comportamento do sistema, so that o relatório continue confiável mesmo com conteúdo hostil coletado da web.

#### Acceptance Criteria

16.1 WHEN um texto coletado da origem é incluído em uma requisição a modelo de linguagem, THEN the Job Agent SHALL enclose esse texto em um delimitador que o marca como dado não confiável.

16.2 THE Job Agent SHALL create a instrução de sistema exclusivamente a partir de um gabarito fixo do sistema, sem interpolar texto coletado da origem nem texto de currículo.

16.3 THE Job Agent SHALL truncate cada texto coletado no limite de caracteres configurado antes de incluí-lo em uma requisição a modelo de linguagem.

16.4 IF a resposta do modelo de linguagem contém uma vaga cujo identificador não estava na requisição, THEN the Synthesizer SHALL discard a resposta e record uma violação de aterramento.

16.5 WHEN o texto de um currículo é incluído em uma requisição de extração, THEN the Resume Parser SHALL enclose esse texto em um delimitador que o marca como dado não confiável.

### REQ-17: Modo determinístico sem modelo de linguagem

**User Story:** As a operador do serviço, I want rodar a triagem sem custo de API, so that a rotina diária continue funcionando mesmo sem crédito ou sem rede para o provedor.

#### Acceptance Criteria

17.1 WHILE o modo determinístico está ativado, the Job Agent SHALL complete o run sem emitir nenhuma chamada a modelo de linguagem.

17.2 WHILE o modo determinístico está ativado, the Report Renderer SHALL create o relatório com vagas, links, locais, scores, gaps e diferenciais.

17.3 WHILE o modo determinístico está ativado, the Report Renderer SHALL display uma marcação explícita nas seções ausentes por falta de síntese.

### REQ-18: Apresentação do relatório ao usuário

**User Story:** As a candidato, I want abrir uma página e ver tudo que preciso para decidir, so that eu não precise voltar ao LinkedIn para ler cada vaga.

#### Acceptance Criteria

18.1 WHEN um run termina, THEN the Report Renderer SHALL create um relatório associado ao identificador de usuário e ao identificador do run.

18.2 THE Report Renderer SHALL display as vagas ordenadas por score de aderência decrescente.

18.3 THE Report Renderer SHALL display, para cada vaga, a URL canônica, o local, o modelo de trabalho, a data de publicação e o score de aderência.

18.4 THE Report Renderer SHALL display, para cada vaga com descrição disponível, o texto integral da descrição coletada.

18.5 THE Report Renderer SHALL display, para cada vaga, todo endereço de e-mail de contato registrado para aquela vaga.

18.6 THE Report Renderer SHALL display, para cada vaga que possui blocker, o texto do blocker.

18.7 THE Report Renderer SHALL display a lista de vagas descartadas com o motivo de cada descarte.

18.8 THE Report Renderer SHALL escape todo texto proveniente da origem e todo texto proveniente de currículo antes de inseri-lo na página.

18.9 THE Report Renderer SHALL display a lista de problemas de higiene do perfil-alvo registrada no run corrente.

18.10 THE Report Renderer SHALL display o ranking de competências exigidas por frequência entre as vagas com descrição disponível do run corrente.

18.11 IF nenhuma vaga sobrevive ao pré-filtro, THEN the Report Renderer SHALL create o relatório com a contagem de cards coletados e a lista de motivos de descarte.

18.12 THE Report Renderer SHALL display em destaque as vagas cujo estado é novo e cujo score de aderência atinge ou supera o limiar configurado.

18.13 IF a cota de coleta do usuário se esgotou durante o run, THEN the Report Renderer SHALL display a quantidade de vagas que ficaram sem descrição por esse motivo.

18.14 THE Report Renderer SHALL display, para cada empresa com mais de um título distinto coletado dentro da janela de contratação configurada, a quantidade de títulos distintos e a data da coleta mais recente.

### REQ-19: Isolamento entre usuários

**User Story:** As a candidato, I want certeza de que ninguém mais vê meu currículo nem meu relatório, so that eu confie meus dados profissionais ao serviço.

#### Acceptance Criteria

19.1 THE Store SHALL store todo currículo, perfil-alvo, card, score, run e relatório sob o identificador de usuário que o originou.

19.2 WHEN uma consulta a dado de usuário é executada, THEN the Store SHALL restrict o resultado ao identificador de usuário da sessão corrente.

19.3 IF uma requisição referencia um recurso pertencente a outro usuário, THEN the Job Agent SHALL reject a requisição e record a tentativa.

19.4 THE Store SHALL prevent que o registro compartilhado de descrição de vaga revele quais usuários coletaram aquela vaga.

### REQ-20: Direitos do titular sobre os dados pessoais

**User Story:** As a candidato, I want apagar meus dados quando eu quiser, so that eu mantenha controle sobre o meu histórico profissional.

#### Acceptance Criteria

20.1 WHEN o usuário solicita a exclusão da conta, THEN the Job Agent SHALL delete o currículo, a extração, o perfil-alvo, os cards, os scores, os runs e os relatórios daquele usuário.

20.2 WHEN o usuário solicita a exportação dos seus dados, THEN the Job Agent SHALL create um arquivo contendo todo dado pessoal armazenado sob o identificador daquele usuário.

20.3 THE Job Agent SHALL display, antes da primeira importação de currículo, quais dados serão armazenados e por quanto tempo.

20.4 IF um usuário permanece sem sessão por mais que o período de retenção configurado, THEN the Job Agent SHALL delete os currículos e as extrações daquele usuário.

20.5 THE Job Agent SHALL record cada operação de exclusão e de exportação com o instante e o identificador de usuário.

20.6 WHEN a exclusão de uma conta é executada, THEN the Job Agent SHALL retain o registro compartilhado de descrição de vaga sem qualquer vínculo com o usuário excluído.

20.7 THE Job Agent SHALL display, antes da primeira extração de currículo, o nome do provedor para o qual o texto do currículo será enviado.

20.8 WHEN a última vaga que referencia um registro compartilhado de descrição entra em estado expirado, THEN the Job Agent SHALL delete os endereços de e-mail de contato daquele registro.

### REQ-21: Persistência e ciclo de vida das vagas

**User Story:** As a candidato, I want que o sistema lembre o que já me mostrou, so that cada run me apresente apenas o que é novo.

#### Acceptance Criteria

21.1 WHEN uma vaga é coletada pela primeira vez para um usuário, THEN the Store SHALL create o registro dessa vaga com estado novo.

21.2 WHEN uma vaga já registrada é coletada novamente, THEN the Store SHALL update o instante de última ocorrência sem alterar o instante de primeira ocorrência.

21.3 WHEN o usuário marca uma vaga como vista, aplicada ou descartada, THEN the Store SHALL update o estado dessa vaga para o valor informado.

21.4 IF uma vaga registrada está ausente de três runs consecutivos cuja janela de publicação cobre a data de publicação dessa vaga, THEN the Store SHALL update o estado dessa vaga para expirado.

21.5 THE Store SHALL create um registro de run contendo o identificador de usuário, o instante de execução, as buscas executadas, a contagem de cards brutos, a contagem após pré-filtro, a contagem de vagas novas, o consumo de cota e os bloqueios de coleta observados.

21.6 IF uma gravação na base de dados falha, THEN the Store SHALL reject o run com uma mensagem que nomeia a operação e a causa da falha.

### REQ-22: Execução recorrente

**User Story:** As a candidato, I want que a triagem rode sozinha todo dia com volume pequeno, so that eu veja vagas novas cedo sem provocar bloqueio.

#### Acceptance Criteria

22.1 WHEN um run recorrente é iniciado, THEN the Scheduler SHALL restrict a janela de publicação das buscas ao intervalo incremental configurado.

22.2 IF um run é iniciado para um usuário que já possui run em andamento, THEN the Scheduler SHALL reject o novo run e record a rejeição.

22.3 IF um run termina com bloqueio de coleta registrado, THEN the Scheduler SHALL delay os próximos runs de todos os usuários pelo intervalo de recuperação configurado.

22.4 IF um run marcado como em andamento excede a duração máxima configurada, THEN the Scheduler SHALL update esse run para o estado interrompido e accept o início de um novo run.

22.5 THE Scheduler SHALL order os runs recorrentes de modo que nenhum usuário seja atendido duas vezes antes que todos os usuários ativos tenham sido atendidos uma vez.

22.6 WHEN o usuário solicita um run imediato, THEN the Scheduler SHALL create esse run com a janela de publicação ampla configurada.

22.7 IF um usuário excede o número diário configurado de runs imediatos, THEN the Scheduler SHALL reject a solicitação e display o instante em que novas solicitações serão aceitas.

### REQ-23: Proteção de credenciais e segredos

**User Story:** As a operador do serviço, I want que segredos e testemunhos de acesso fiquem protegidos, so that um vazamento não comprometa contas de usuários nem credenciais de provedor.

#### Acceptance Criteria

23.1 THE Job Agent SHALL read segredos, chaves de API e credenciais de cliente exclusivamente de configuração externa ao código-fonte.

23.2 THE Job Agent SHALL prevent a gravação de segredos, de testemunhos de acesso e de cookies de sessão em registros de log.

23.3 THE Report Renderer SHALL prevent a inclusão de segredos, de testemunhos de acesso e de cookies de sessão em qualquer página apresentada.

23.4 THE Store SHALL store todo testemunho de acesso de terceiros e toda credencial de provedor em forma cifrada.


### REQ-24: Configuração do serviço

**User Story:** As a operador do serviço, I want ajustar os limites e critérios em um único lugar, so that eu recalibre o sistema sem tocar no código.

#### Acceptance Criteria

24.1 THE Job Agent SHALL read de uma fonte de configuração externa todo parâmetro descrito como configurado nestes requisitos, incluindo a janela de publicação, a faixa de intervalo entre enriquecimentos, o intervalo de recuperação, a duração máxima de um run, a duração máxima de sessão, o período de retenção, o orçamento diário de coleta, o limite de tamanho de arquivo, o comprimento mínimo de texto de currículo, o número diário de importações de currículo por usuário, o número diário de runs imediatos por usuário, a janela de publicação ampla, o limite de vagas enviadas ao modelo de linguagem, o número de vagas relidas pelo modelo, a janela de contratação por empresa, o limiar de destaque, o registro de provedores de modelo, a ordem padrão de tentativa entre provedores, os pesos dos componentes de score, os valores de bônus e de penalidade e a precedência entre origens de perfil.

24.2 WHEN a configuração é carregada, THEN the Job Agent SHALL validate cada valor configurado contra o seu tipo e a sua faixa permitida.

24.3 IF um valor de configuração é inválido ou ausente, THEN the Job Agent SHALL reject a inicialização com uma mensagem que nomeia a chave e o valor esperado.

24.4 THE Job Agent SHALL record a configuração efetiva do run junto ao registro do run.

### REQ-25: Registro de provedores de modelo de linguagem

**User Story:** As a operador do serviço, I want acrescentar e remover provedores de IA por configuração, so that o sistema acompanhe as ofertas gratuitas sem que eu altere código.

#### Acceptance Criteria

25.1 THE Provider Registry SHALL read a lista de provedores habilitados de uma fonte de configuração externa ao código-fonte.

25.2 THE Provider Registry SHALL record, para cada provedor habilitado, o endereço do serviço, o formato de credencial aceito, os modelos oferecidos e o limite de requisições declarado.

25.3 THE Provider Registry SHALL accept apenas provedores cujo formato de credencial declarado seja chave estática ou ausência de credencial.

25.4 WHEN a configuração é carregada, THEN the Provider Registry SHALL validate a configuração de cada provedor antes de oferecê-lo ao usuário.

25.5 IF um provedor habilitado não responde à verificação de disponibilidade, THEN the Provider Registry SHALL record a indisponibilidade e exclude esse provedor da lista oferecida ao usuário.

25.6 THE Provider Registry SHALL record, para cada provedor habilitado, se ele executa em máquina do operador ou por rede externa.

25.7 THE Provider Registry SHALL display ao usuário, para cada provedor oferecido, o limite de requisições declarado e o destino dos dados enviados.

### REQ-26: Credencial de modelo fornecida pelo usuário

**User Story:** As a candidato, I want usar minha própria chave de IA gratuita, so that eu tenha a camada de julgamento sem depender da cota de outra pessoa.

#### Acceptance Criteria

26.1 WHEN o usuário informa uma credencial de provedor, THEN the Credential Vault SHALL validate essa credencial contra o provedor antes de armazená-la.

26.2 IF a validação de uma credencial falha, THEN the Credential Vault SHALL reject o armazenamento e display a causa devolvida pelo provedor.

26.3 THE Credential Vault SHALL store cada credencial de provedor sob o identificador de usuário que a informou.

26.4 THE Credential Vault SHALL prevent a exibição do valor de uma credencial após o seu armazenamento.

26.5 WHEN o usuário remove uma credencial, THEN the Credential Vault SHALL delete essa credencial.

26.6 THE Job Agent SHALL display, antes do primeiro armazenamento de credencial, quais dados serão enviados ao provedor escolhido.

26.7 IF nenhuma credencial de provedor está armazenada para o usuário, THEN the Job Agent SHALL execute o run desse usuário em modo determinístico.

26.8 IF um provedor recusa uma requisição por limite de uso excedido, THEN the Job Agent SHALL retry a requisição no próximo provedor da ordem definida por aquele usuário.

26.9 IF todos os provedores configurados por um usuário recusam a requisição, THEN the Job Agent SHALL record a falha e complete o run em modo determinístico.

26.10 THE Job Agent SHALL prevent o uso da credencial de um usuário em um run pertencente a outro usuário.

26.11 THE Credential Vault SHALL store a ordem de tentativa entre os provedores configurados por um usuário.

26.12 WHEN a primeira credencial de um usuário é armazenada, THEN the Credential Vault SHALL create a ordem de tentativa desse usuário a partir da ordem padrão configurada pelo operador.

### REQ-27: Insights de concorrência lidos no navegador do usuário

**User Story:** As a candidato com LinkedIn Premium, I want que o sistema use o que a minha conta enxerga enquanto eu navego, so that a triagem considere sinais como "You'd be a top applicant", que a coleta por rota pública não recebe.

#### Acceptance Criteria

27.1 WHEN a extensão envia insights de uma vaga do usuário, THEN the Insight Store SHALL record a contagem de candidatos, a distribuição de senioridade e os sinais reconhecidos.

27.2 THE Insight Store SHALL discard todo sinal ausente da lista de sinais aceitos.

27.3 THE Insight Store SHALL merge os sinais recebidos aos sinais já gravados pela coleta, sem substituí-los.

27.4 IF um envio não contém nenhum insight reconhecível, THEN the Insight Store SHALL reject o envio com uma mensagem que nomeia a causa.

27.5 WHEN um envio cita vaga que o usuário ainda não tem e traz título e endereço, THEN the Insight Store SHALL create a vaga para esse usuário com a origem registrada como extensão.

27.6 IF um envio cita vaga desconhecida sem título ou sem endereço, THEN the Insight Store SHALL reject esse envio.

27.7 WHEN a extensão envia uma varredura em lote, THEN the Job Agent SHALL record cada item de forma independente, sem que a recusa de um item impeça a gravação dos demais.

27.8 IF uma varredura em lote está vazia ou excede o limite de itens por lote, THEN the Job Agent SHALL reject o lote inteiro.

27.9 THE Job Agent SHALL prevent que um envio de insight altere vaga pertencente a outro usuário.

27.10 IF um envio de insight não traz sessão válida nem token do extrator válido, THEN the Job Agent SHALL reject o envio.

27.11 WHEN um envio de insight traz token do extrator válido em cabeçalho, THEN the Job Agent SHALL accept o envio sem cookie de sessão.

27.12 THE Job Agent SHALL restrict o token do extrator às rotas de insight.

27.13 WHEN o usuário emite um token do extrator, THEN the Job Agent SHALL invalidate o token emitido anteriormente para esse usuário.

27.14 THE Job Agent SHALL issue token do extrator apenas por requisição de escrita de um usuário com sessão válida.

27.15 THE Job Agent SHALL restrict a liberação de origem externa ao endereço do LinkedIn e às rotas de insight.

27.16 WHEN o enriquecimento de um run é enfileirado, THEN the Job Agent SHALL order as vagas com sinal de top applicant à frente das demais.

27.17 WHEN uma vaga tem sinal de top applicant, THEN the Report Renderer SHALL recommend a candidatura imediata, inclusive quando a vaga também tem sinal de muitos candidatos.

27.18 IF uma vaga tem sinal de top applicant e requisito eliminatório não atendido, THEN the Report Renderer SHALL prevent que o sinal substitua a recomendação ditada pelo requisito.

### REQ-28: Releitura das vagas do topo por modelo de linguagem

**User Story:** As a candidato, I want que as vagas mais bem colocadas sejam relidas por um modelo, so that um anúncio vago que pede pouco não passe à frente de uma vaga exigente que serve para mim.

#### Acceptance Criteria

28.1 WHILE o modo determinístico está desativado, WHEN a pontuação final termina, THEN the Judge SHALL send ao modelo de linguagem o perfil-alvo e as vagas de maior posição no relatório, até o limite de vagas relidas.

28.2 THE Judge SHALL send a releitura como tarefa própria, com gabarito de sistema distinto do gabarito da síntese.

28.3 THE Judge SHALL restrict o corpo da requisição ao teto de texto externo do cliente de modelo, dividindo o espaço de descrição igualmente entre as vagas enviadas.

28.4 IF uma vaga não tem descrição coletada ou não há espaço de descrição no teto, THEN the Judge SHALL mark essa condição na requisição em vez de omitir a vaga.

28.5 WHEN a resposta do modelo chega, THEN the Judge SHALL read uma nota de 0 a 100 e um motivo por vaga, tolerando marcadores de lista, colchetes, travessões e denominador na nota.

28.6 THE Judge SHALL discard nota atribuída a identificador ausente da requisição e nota fora da faixa de 0 a 100.

28.7 IF nenhum cliente de modelo está disponível para o usuário, THEN the Job Agent SHALL complete o run sem releitura e preservar a ordem determinística.

28.8 IF a cadeia de provedores se esgota, o provedor falha ou a resposta não tem nenhuma linha legível, THEN the Job Agent SHALL record o motivo e preservar a ordem determinística.

28.9 THE Store SHALL store a nota e o motivo do modelo ao lado do score de aderência, sem substituí-lo, e the Report Renderer SHALL display a origem de cada número.

28.10 WHERE uma vaga tem nota do modelo, the Report Renderer SHALL order o relatório por essa nota à frente da ordem determinística.
