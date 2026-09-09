# Implementation Tasks

## Overview

This implementation is organized into 6 phases:

1. **Fundação** - Configuração, segredos, esquema do banco, isolamento e fila
2. **Identidade, credenciais e perfil** - Entrada do usuário, origens conectadas, provedores de modelo e consolidação do perfil-alvo
3. **Pipeline de triagem** - Busca, coleta, contenção, cota e pontuação
4. **Síntese, relatório, ciclo e entrega** - Camada de julgamento, apresentação, agendamento, direitos do titular e a superfície por onde o usuário alcança tudo isso
5. **Acceptance Criteria Testing** - Verificação de cada critério de aceitação
6. **Final Checkpoint** - Conferência de completude e prontidão

**Estimated Effort**: Large (13-18 sessions)

As tarefas 4.23 a 4.32 foram acrescentadas depois do checkpoint final da primeira passada. Elas cobrem a superfície de entrega — endpoints e pontos de entrada dos processos — que o desenho sempre previu e que esta decomposição não havia coberto. A omissão passou por toda a validação porque nenhum critério de aceitação exige endereço HTTP: os requisitos falam de serviços, e serviços podem ser satisfeitos sem que ninguém consiga alcançá-los.

A segunda lição veio da mesma direção. O scorer devolve `0,5` no componente de competências quando não consegue ler a vaga — um valor neutro que significa *"não sei"*. Com descrição, ele devolve a proporção real, e uma vaga exigente fica abaixo de `0,5`. O relatório ordenava por score puro, então o desconhecido passava à frente do medido e as vagas em que gastamos requisição para saber a verdade caíam para o rodapé. Contra dado real isso ficou evidente: todas as vagas não lidas empataram em 78%, porque um número idêntico repetido não é ranking, é a ausência de medição aparecendo. A ordenação agora separa os dois regimes antes de comparar números. O mesmo defeito reapareceu um nível abaixo, dentro do próprio componente: uma descrição lida por inteiro sem um único termo técnico reconhecido também devolvia o valor neutro, como se não tivesse sido lida. Uma vaga de planejamento financeiro pontuava 78% sem uma lacuna sequer, acima de uma vaga de infraestrutura em que o perfil casava 4 de 10 requisitos. Ler e não achar nada é evidência; não ter lido é ausência de evidência. Agora são valores diferentes, e a vaga caiu para 59% contra o dado real.

A terceira não foi encontrada por ninguém: foi encontrada pelo relógio. Às 21h locais a sessão cruzou a meia-noite UTC e três testes de limite diário começaram a falhar sozinhos. A causa estava em produção, não nos testes — todo carimbo é gravado em UTC, mas `quota.py`, `scheduler.py` e `resume/importer.py` contavam "hoje" com `date.today()`, em hora local. No horário de Brasília isso fazia **todo limite diário deixar de valer das 21h à meia-noite**: cota de coleta, limite de importação e limite de runs imediatos zeravam três horas antes da virada. O mesmo `quota.py` já continha a versão correta em UTC, numa função ao lado da errada. Um quarto defeito acompanhava: um teste fixava `HOJE = "2026-08-21"` como literal enquanto a linha que ele contava era carimbada pelo relógio real, então ele só podia passar no dia em que foi escrito.

A lição das três é a mesma, e é a mesma do achado da camada operacional: a suíte só sabe o que alguém pensou em perguntar. Cobertura completa de critérios não é cobertura de realidade — nem da rede, nem do calendário, nem do relógio.

Roteamento entre provedores, coleta multi-portal e persistência vêm de biblioteca. As tarefas que antes construíam essas capacidades passam a construir o adaptador que as isola, o que reduz o esforço sem reduzir a cobertura de requisito.

A ordem das fases segue a direção das dependências de dados, não a ordem de valor para o usuário. A Fase 1 existe porque o isolamento entre usuários e a redação de segredos precisam estar na camada de acesso antes que qualquer dado pessoal seja gravado; construí-los depois exigiria reescrever toda consulta já escrita. A Fase 2 termina no perfil-alvo consolidado porque nenhum estágio da Fase 3 tem entrada sem ele.

## Repository Constraints

- Não existem `AGENTS.md`, `TESTING.md`, `STYLEGUIDE.md` nem `SECURITY.md` no repositório. Convenções de nomeação, colocação de arquivo e fronteira de pacote vêm da tabela `Code Anatomy` do design.
- O projeto é greenfield: nenhum padrão existente a reusar, e a memória contextual foi consultada nesta fase sem regras ativas.
- Toda tarefa que cria tabela ou coluna precisa acompanhar um passo de migração com inverso declarado, conforme o `Rollback Plan` do design.
- Nenhum componente pode emitir requisição à origem fora do governador de taxa, nem a provedor de modelo fora do cliente de modelo. Tarefas que precisem de rede devem depender da tarefa que constrói a fronteira correspondente.
- Testes de adaptador usam respostas gravadas em arquivo e não tocam a rede, conforme a seção `Testing Requirements` do design.
- Três capacidades vêm de biblioteca e não de código nosso: roteamento entre provedores de modelo, coleta multi-portal e persistência embarcada. Cada uma fica atrás de um adaptador, e nenhum módulo fora do adaptador pode conhecer os tipos da biblioteca.
- O coletor multi-portal oferece trazer a descrição já na chamada de busca. Nenhuma tarefa pode usar essa opção: pela definição dos requisitos isso é enriquecer antes do pré-filtro.

## Phase 1: Fundação

- [x] 1.1 Criar esqueleto do pacote e leitura de configuração
  - Criar a estrutura de diretórios da tabela `Code Anatomy` e a leitura do arquivo de configuração.
  - _Implements: DES-19, REQ-24.1_

- [x] 1.2 Adicionar validação por esquema da configuração
  - Validar tipo e faixa de cada parâmetro na inicialização e impedir a partida com mensagem que nomeia a chave.
  - _Depends: 1.1_
  - _Implements: DES-19, REQ-24.2, REQ-24.3_

- [x] 1.3 Adicionar cofre em memória de segredos
  - Ler segredos exclusivamente de variáveis de ambiente, sem qualquer leitura a partir do arquivo de configuração.
  - _Depends: 1.1_
  - _Implements: DES-19, REQ-23.1_

- [x] 1.4 Adicionar filtro de redação de log
  - Instalar na inicialização um filtro que remove segredos, testemunhos, credenciais e cookies de qualquer registro emitido.
  - _Depends: 1.3_
  - _Implements: DES-19, REQ-23.2, REQ-23.3_

- [x] 1.5 Criar esquema do banco
  - Definir as tabelas e índices da seção `Data Models` do design em SQLite, com modo de escrita antecipada e integridade referencial ativa.
  - _Depends: 1.2_
  - _Implements: DES-17_

- [x] 1.6 Adicionar versionamento e migração de esquema
  - Aplicar migrações pendentes em ordem na abertura e impedir a partida quando o arquivo estiver em versão superior à do código.
  - _Depends: 1.5_
  - _Implements: DES-17_

- [x] 1.7 Adicionar repositório com filtro de usuário injetado
  - Fazer o acesso a toda tabela de dado de usuário aplicar o filtro do identificador da sessão corrente sem depender de quem escreve a consulta, sobre a conexão da biblioteca padrão.
  - _Depends: 1.5_
  - _Implements: DES-17, REQ-19.1, REQ-19.2_

- [x] 1.8 Adicionar recusa registrada de referência cruzada
  - Recusar e registrar toda requisição que referencie recurso pertencente a outro usuário.
  - _Depends: 1.7_
  - _Implements: DES-17, REQ-19.3_

- [x] 1.9 Adicionar tratamento de falha de gravação
  - Encerrar o run nomeando a operação e a causa quando uma gravação falhar.
  - _Depends: 1.7_
  - _Implements: DES-17, REQ-21.6_

- [x] 1.10 Adicionar fila de runs em banco
  - Reservar o próximo run dentro de uma transação exclusiva curta que marca a linha antes de liberar o banco.
  - _Depends: 1.5_
  - _Implements: DES-1_

- [x] 1.11 Adicionar registro de run e configuração efetiva
  - Criar o registro de run com estado, contagens e a configuração efetiva que o produziu.
  - _Depends: 1.10_
  - _Implements: DES-1, DES-19, REQ-21.5, REQ-24.4_

- [x] 1.12 Adicionar aplicação web e roteamento
  - Levantar o processo web com as rotas do desenho, sem qualquer execução de estágio de run.
  - _Depends: 1.7_
  - _Implements: DES-1_

- [x] 1.13 Adicionar processo de runs e orquestração de estágios
  - Consumir a fila e executar os estágios do pipeline em ordem fixa, com estado nomeado a cada transição.
  - _Depends: 1.11_
  - _Implements: DES-1_

## Phase 2: Identidade, credenciais e perfil

- [x] 2.1 Adicionar fluxo de autorização Google
  - Implementar o fluxo com verificação de código e parâmetro de estado, resolvendo a identidade pelo identificador de assunto.
  - _Depends: 1.12_
  - _Implements: DES-2, REQ-1.1, REQ-1.2_

- [x] 2.2 Adicionar recusa de autorização cancelada
  - Recusar a criação de sessão nomeando a causa quando o fluxo falha ou é cancelado.
  - _Depends: 2.1_
  - _Implements: DES-2, REQ-1.3_

- [x] 2.3 Adicionar ciclo de vida da sessão
  - Guardar apenas o resumo do testemunho, expirar pelo relógio do servidor e recusar requisição sem sessão válida.
  - _Depends: 2.1_
  - _Implements: DES-2, REQ-1.4, REQ-1.5_

- [x] 2.4 Adicionar encerramento de sessão sem revogação de origens
  - Apagar apenas a sessão, preservando os testemunhos das origens conectadas.
  - _Depends: 2.3_
  - _Implements: DES-2, REQ-1.6_

- [x] 2.5 Adicionar conexão LinkedIn pelo fluxo oficial
  - Guardar os campos de identidade concedidos e a lista de escopos efetivamente concedidos.
  - _Depends: 2.3_
  - _Implements: DES-3, REQ-2.1, REQ-2.3_

- [x] 2.6 Adicionar marcação de campo indisponível por escopo
  - Distinguir campo ausente de campo que o escopo concedido não permite ler.
  - _Depends: 2.5_
  - _Implements: DES-3, REQ-2.4_

- [x] 2.7 Adicionar recusa de credencial LinkedIn submetida
  - Recusar na fronteira do conector qualquer senha, cookie ou sessão de LinkedIn enviada por um usuário.
  - _Depends: 2.5_
  - _Implements: DES-3, REQ-2.2_

- [x] 2.8 Adicionar desconexão e expiração da conexão LinkedIn
  - Apagar os dados de identidade e revogar o testemunho na desconexão, e pedir reconexão quando o testemunho expirar.
  - _Depends: 2.5_
  - _Implements: DES-3, REQ-2.5, REQ-2.6_

- [x] 2.9 Adicionar registro de provedores a partir de configuração
  - Carregar e validar cada provedor declarado, aceitando apenas formato de credencial de chave estática ou ausência de credencial.
  - _Depends: 1.2_
  - _Implements: DES-4, REQ-25.1, REQ-25.2, REQ-25.3, REQ-25.4_

- [x] 2.10 Adicionar verificação de disponibilidade de provedor
  - Excluir da oferta o provedor que não responde e registrar onde cada provedor executa.
  - _Depends: 2.9_
  - _Implements: DES-4, REQ-25.5, REQ-25.6_

- [x] 2.11 Adicionar apresentação dos provedores ao usuário
  - Mostrar, para cada provedor oferecido, o limite declarado e o destino dos dados enviados.
  - _Depends: 2.10_
  - _Implements: DES-4, REQ-25.7_

- [x] 2.12 Adicionar cofre de credenciais com cifragem envelopada
  - Validar a credencial contra o provedor antes de gravar e cifrar cada credencial com chave de dado própria protegida por chave mestra.
  - _Depends: 2.10_
  - _Implements: DES-4, REQ-23.4, REQ-26.1, REQ-26.2, REQ-26.3_

- [x] 2.13 Adicionar não reexibição e remoção de credencial
  - Impedir que qualquer leitura devolva o valor da credencial e apagar a credencial quando o usuário a remover.
  - _Depends: 2.12_
  - _Implements: DES-4, REQ-26.4, REQ-26.5_

- [x] 2.14 Adicionar aviso prévio ao primeiro cadastro de credencial
  - Mostrar quais dados serão enviados ao provedor escolhido antes do primeiro armazenamento.
  - _Depends: 2.12_
  - _Implements: DES-4, REQ-26.6_

- [x] 2.15 Adicionar ordem de tentativa por usuário
  - Guardar a ordem de tentativa do usuário e semeá-la da ordem padrão do operador no primeiro cadastro.
  - _Depends: 2.12_
  - _Implements: DES-4, REQ-26.11, REQ-26.12_

- [x] 2.16 Adicionar cliente de modelo sobre o roteador de provedores
  - Montar a lista ordenada de destinos do roteador a partir do cofre do usuário, traduzir o esgotamento da cadeia em modo determinístico e manter os tipos do roteador confinados a este módulo.
  - _Depends: 2.15_
  - _Implements: DES-5, REQ-26.7, REQ-26.8, REQ-26.9_

- [x] 2.17 Adicionar isolamento de credencial no cliente de modelo
  - Impedir o uso da credencial de um usuário em trabalho pertencente a outro.
  - _Depends: 2.16_
  - _Implements: DES-5, REQ-26.10_

- [x] 2.18 Adicionar contenção de injeção no cliente de modelo
  - Montar a instrução de sistema de gabarito fixo, envolver texto externo em delimitador de dado não confiável e truncá-lo no limite configurado.
  - _Depends: 2.16_
  - _Implements: DES-5, REQ-16.1, REQ-16.2, REQ-16.3_

- [x] 2.19 Adicionar importação de currículo do Google Drive
  - Exportar o documento selecionado como texto pedindo apenas o escopo de leitura necessário.
  - _Depends: 2.3_
  - _Implements: DES-6, REQ-3.1, REQ-3.7_

- [x] 2.20 Adicionar importação de arquivo Word e PDF
  - Extrair texto dos dois formatos e reduzi-los ao mesmo artefato produzido pela importação do Drive.
  - _Depends: 2.19_
  - _Implements: DES-6, REQ-3.2, REQ-3.3_

- [x] 2.21 Adicionar validações de aceitação do currículo
  - Recusar formato não aceito, arquivo acima do limite e importação acima da frequência diária, nomeando o limite violado.
  - _Depends: 2.20_
  - _Implements: DES-6, REQ-3.4, REQ-3.5, REQ-3.9_

- [x] 2.22 Adicionar detecção de documento sem camada de texto
  - Recusar o documento cujo texto extraído fique abaixo do comprimento mínimo, declarando a hipótese.
  - _Depends: 2.20_
  - _Implements: DES-6, REQ-3.6_

- [x] 2.23 Adicionar persistência de currículo por usuário
  - Gravar cada registro de currículo sob o identificador de quem o importou.
  - _Depends: 2.20_
  - _Implements: DES-6, REQ-3.8_

- [x] 2.24 Adicionar extração estruturada do currículo
  - Produzir campos, trechos de origem e lacunas pelo provedor do próprio usuário, envolvendo o texto como dado não confiável.
  - _Depends: 2.18, 2.23_
  - _Implements: DES-7, REQ-4.1, REQ-4.2, REQ-4.3, REQ-4.9, REQ-16.5_

- [x] 2.25 Adicionar proveniência da extração
  - Registrar o provedor e o modelo que produziram cada registro de extração.
  - _Depends: 2.24_
  - _Implements: DES-7, REQ-4.10_

- [x] 2.26 Adicionar reuso de extração por conteúdo
  - Indexar o resultado pelo conteúdo do currículo e reusá-lo quando conteúdo idêntico for importado.
  - _Depends: 2.24_
  - _Implements: DES-7, REQ-4.4, REQ-4.5_

- [x] 2.27 Adicionar portão de conferência da extração
  - Fazer o registro nascer não confirmado e impedir o uso de extração não confirmada em qualquer run.
  - _Depends: 2.24_
  - _Implements: DES-7, REQ-4.6, REQ-4.11_

- [x] 2.28 Adicionar correção manual de campo extraído
  - Gravar a correção como origem de edição manual sem sobrescrever o valor extraído.
  - _Depends: 2.27_
  - _Implements: DES-7, REQ-4.7_

- [x] 2.29 Adicionar caminhos alternativos de extração
  - Oferecer preenchimento manual quando a extração falha e quando o usuário não tem credencial, sem emitir requisição neste último caso.
  - _Depends: 2.24_
  - _Implements: DES-7, REQ-4.8, REQ-4.12_

- [x] 2.30 Adicionar consolidador de perfil com precedência
  - Produzir versão imutável a cada mudança de origem, resolvendo conflito por precedência e registrando a origem de cada campo.
  - _Depends: 2.27, 2.6_
  - _Implements: DES-8, REQ-5.1, REQ-5.2, REQ-5.3, REQ-5.6_

- [x] 2.31 Adicionar reuso da versão vigente do perfil
  - Reusar a versão vigente enquanto ela for mais recente que a última alteração de origem.
  - _Depends: 2.30_
  - _Implements: DES-8, REQ-5.7_

- [x] 2.32 Adicionar inferência determinística de nível
  - Derivar o nível do título do cargo mais recente por conjunto ordenado de regras.
  - _Depends: 2.30_
  - _Implements: DES-8, REQ-5.4_

- [x] 2.33 Adicionar recusa de run sem histórico profissional
  - Recusar o início do run e instruir a importação de currículo quando nenhuma origem fornece histórico.
  - _Depends: 2.30_
  - _Implements: DES-8, REQ-5.5_

- [x] 2.34 Adicionar diagnóstico de higiene do perfil
  - Detectar cargos simultâneos atuais, lacunas e sobreposições, registrando campo de origem e trecho, com lista sempre presente.
  - _Depends: 2.30_
  - _Implements: DES-8, REQ-6.1, REQ-6.2, REQ-6.3_

## Phase 3: Pipeline de triagem

- [x] 3.1 Adicionar planejador determinístico de buscas
  - Derivar eixos do perfil, combinar com rótulos de nível e âncoras, emitir em português e inglês e descartar termo único.
  - _Depends: 2.32_
  - _Implements: DES-9, REQ-7.1, REQ-7.2, REQ-7.3_

- [x] 3.2 Adicionar estabilidade e registro das buscas
  - Ordenar a saída por chave textual e gravar a lista completa no registro do run.
  - _Depends: 3.1_
  - _Implements: DES-9, REQ-7.4, REQ-7.5_

- [x] 3.3 Adicionar adaptador do coletor multi-portal
  - Executar a chamada de descoberta sem descrição e converter a saída tabular da biblioteca em card normalizado gravado sob o usuário do run, sem deixar o tipo da biblioteca sair do adaptador.
  - _Depends: 3.2_
  - _Implements: DES-10, REQ-8.1, REQ-8.7_

- [x] 3.4 Adicionar captura de sinais do card pela camada operacional
  - Gravar junto ao card os sinais que só a sessão operacional entrega, deixando a vaga sem sinais pontuável sem os ajustes de bônus.
  - _Depends: 3.3, 3.13_
  - _Implements: DES-10, DES-12, REQ-8.3_

- [x] 3.5 Adicionar deduplicação de cards
  - Descartar card cujo identificador já exista no run corrente.
  - _Depends: 3.3_
  - _Implements: DES-10, REQ-8.2_

- [x] 3.6 Adicionar distinção entre busca vazia e busca com falha
  - Registrar busca improdutiva e busca com falha de forma distinta e prosseguir com as restantes.
  - _Depends: 3.3_
  - _Implements: DES-10, REQ-8.5, REQ-8.6_

- [x] 3.8 Adicionar pré-filtro por título
  - Avaliar todos os cards antes de qualquer enriquecimento, descartando por padrão configurado e registrando o motivo.
  - _Depends: 3.5_
  - _Implements: DES-11, REQ-9.1, REQ-9.2, REQ-9.3_

- [x] 3.9 Adicionar avaliação geográfica e blocker
  - Manter vaga remota sem avaliação geográfica e manter presencial fora do raio com blocker que nomeia cidade e distância.
  - _Depends: 3.8_
  - _Implements: DES-11, REQ-9.4, REQ-9.5_

- [x] 3.10 Adicionar contagens do pré-filtro no run
  - Gravar a contagem de cards antes e depois do pré-filtro.
  - _Depends: 3.8_
  - _Implements: DES-11, REQ-9.6_

- [x] 3.11 Adicionar governador de taxa
  - Impor execução serial em todo o sistema, esperar intervalo aleatório entre chamadas e aplicar recuo crescente a cada tempo esgotado.
  - _Depends: 1.11_
  - _Implements: DES-12, REQ-11.1, REQ-11.2, REQ-11.3_

- [x] 3.12 Adicionar detecção e propagação de bloqueio de coleta
  - Gravar bloqueio ao terceiro fracasso consecutivo e recusar toda chamada subsequente enquanto ele estiver registrado.
  - _Depends: 3.11_
  - _Implements: DES-12, REQ-11.4, REQ-11.5_

- [x] 3.13 Adicionar processo único de enriquecimento
  - Isolar o enriquecimento em um processo único da instalação, de forma que a serialização exigida pela contenção seja consequência da topologia.
  - _Depends: 3.12_
  - _Implements: DES-12, REQ-11.6_

- [x] 3.14 Adicionar coleta da descrição da vaga
  - Pedir a descrição ao coletor multi-portal numa segunda chamada, restrita às vagas sobreviventes do pré-filtro, gravando o texto integral e o instante da coleta pelo enriquecedor e marcando a vaga como sem descrição quando não for possível obtê-la.
  - _Depends: 3.3, 3.8, 3.13_
  - _Implements: DES-12, REQ-10.1, REQ-10.2, REQ-10.3_

- [x] 3.15 Adicionar captura de contatos da descrição
  - Registrar pelo enriquecedor todo endereço de e-mail presente no texto da descrição obtida pela rota pública.
  - _Depends: 3.14_
  - _Implements: DES-12, REQ-10.5_

- [x] 3.16 Adicionar registro compartilhado de descrições
  - Guardar a descrição uma única vez para todo o sistema e reusá-la sem emitir requisição.
  - _Depends: 3.14_
  - _Implements: DES-12, REQ-10.6, REQ-10.7_

- [x] 3.17 Adicionar captura de concorrência pela camada operacional
  - Registrar número de candidatos e distribuição de senioridade a partir da página aberta sob sessão operacional.
  - _Depends: 3.13, 3.14_
  - _Implements: DES-12, REQ-10.4_

- [x] 3.18 Adicionar proteção do registro compartilhado
  - Impedir que o registro compartilhado revele quais usuários coletaram cada vaga.
  - _Depends: 3.16_
  - _Implements: DES-17, REQ-19.4_

- [x] 3.19 Adicionar ponto de suspensão e retomada do run
  - Encerrar o primeiro trecho gravando os pedidos de enriquecimento e reenfileirar o run para o segundo trecho ao esgotá-los.
  - _Depends: 3.13, 1.13_
  - _Implements: DES-1_

- [x] 3.20 Adicionar alocador de cota diária
  - Calcular o teto por usuário no momento do débito, debitar apenas enriquecimento efetivamente pago e registrar o consumo individual e total.
  - _Depends: 3.17_
  - _Implements: DES-13, REQ-12.1, REQ-12.2, REQ-12.4_

- [x] 3.21 Adicionar recusa por cota esgotada
  - Recusar novos enriquecimentos do usuário sem cota e registrar a recusa no run.
  - _Depends: 3.20_
  - _Implements: DES-13, REQ-12.3_

- [x] 3.22 Adicionar virada diária da cota
  - Criar as cotas do dia sem transportar saldo não consumido.
  - _Depends: 3.20_
  - _Implements: DES-13, REQ-12.5_

- [x] 3.23 Adicionar ontologia de sinônimos
  - Reduzir variantes declaradas de uma mesma tecnologia a um termo canônico antes de qualquer interseção.
  - _Depends: 1.2_
  - _Implements: DES-14, REQ-13.3_

- [x] 3.24 Adicionar componentes ponderados do score
  - Compor o score a partir dos cinco componentes e registrar o valor de cada um.
  - _Depends: 3.23_
  - _Implements: DES-14, REQ-13.1, REQ-13.2_

- [x] 3.25 Adicionar bônus, penalidades e limite final
  - Aplicar bônus e penalidades de sinais e de requisito eliminatório, limitando o resultado ao intervalo válido só depois.
  - _Depends: 3.24_
  - _Implements: DES-14, REQ-13.4, REQ-13.5, REQ-13.9, REQ-13.10_

- [x] 3.26 Adicionar pontuação em duas passadas
  - Ordenar a fila pela passada provisória sem sinais de sessão e recalcular na passada final com eles.
  - _Depends: 3.25, 3.19_
  - _Implements: DES-14, REQ-13.6_

- [x] 3.27 Adicionar determinismo e histórico de score
  - Garantir mesmo resultado para mesma entrada e gravar cada pontuação sem sobrescrever as anteriores.
  - _Depends: 3.26_
  - _Implements: DES-14, REQ-13.7, REQ-13.8_

- [x] 3.28 Adicionar detecção de lacunas e diferenciais
  - Produzir as duas listas a partir dos conjuntos canônicos, sem chamada de modelo.
  - _Depends: 3.23_
  - _Implements: DES-14, REQ-14.1, REQ-14.2, REQ-14.3_

- [x] 3.29 Adicionar agregação de competências por frequência
  - Somar a frequência de cada competência exigida entre as vagas com descrição do run.
  - _Depends: 3.28_
  - _Implements: DES-14, REQ-14.4_

- [x] 3.30 Adicionar máquina de estados da vaga
  - Criar a vaga como nova, atualizar a última ocorrência preservando a primeira e aplicar a marcação escolhida pelo usuário.
  - _Depends: 3.5_
  - _Implements: DES-17, REQ-21.1, REQ-21.2, REQ-21.3_

- [x] 3.31 Adicionar regra de expiração por janela elegível
  - Contar ausência apenas em run cuja janela de publicação cobria a vaga e expirar após três ausências elegíveis.
  - _Depends: 3.30_
  - _Implements: DES-17, REQ-21.4_

## Phase 4: Síntese, relatório, ciclo e entrega

- [x] 4.1 Adicionar montagem da requisição de síntese
  - Enviar uma requisição de síntese por run com as vagas de maior score até o limite, apenas com dados estruturados e sem marcação bruta.
  - _Depends: 2.18, 3.27_
  - _Implements: DES-15, REQ-15.1, REQ-15.2, REQ-15.8, REQ-15.10_

- [x] 4.2 Adicionar exigências de classificação e proveniência
  - Exigir da resposta a classificação de cada afirmação não trivial e a distinção entre vaga lida e vaga avaliada por card.
  - _Depends: 4.1_
  - _Implements: DES-15, REQ-15.3, REQ-15.5_

- [x] 4.3 Adicionar verificador de aterramento
  - Descartar a resposta que cite vaga, URL ou empresa ausente do envio e registrar a violação.
  - _Depends: 4.1_
  - _Implements: DES-15, REQ-15.4, REQ-16.4_

- [x] 4.4 Adicionar registro de falha, proveniência e tokens da síntese
  - Gravar falha de síntese, provedor e modelo usados e as contagens de tokens no registro do run.
  - _Depends: 4.1_
  - _Implements: DES-15, REQ-15.6, REQ-15.7, REQ-15.11, REQ-15.12_

- [x] 4.5 Adicionar renderizador do relatório
  - Montar a página do run apenas de dados persistidos, ordenada por score decrescente, com os campos por vaga exigidos.
  - _Depends: 3.29_
  - _Implements: DES-16, REQ-18.1, REQ-18.2, REQ-18.3, REQ-18.4_

- [x] 4.6 Adicionar contatos, blockers e descartadas no relatório
  - Exibir os endereços de contato, o texto do blocker e a lista de descartadas com o motivo de cada uma.
  - _Depends: 4.5_
  - _Implements: DES-16, REQ-18.5, REQ-18.6, REQ-18.7_

- [x] 4.7 Adicionar escape automático no gabarito
  - Ligar o escape automático do motor de gabarito para todo texto de origem e de currículo.
  - _Depends: 4.5_
  - _Implements: DES-16, REQ-18.8_

- [x] 4.8 Adicionar higiene e ranking de competências no relatório
  - Exibir a lista de problemas de higiene e o ranking de competências por frequência do run.
  - _Depends: 4.5_
  - _Implements: DES-16, REQ-18.9, REQ-18.10_

- [x] 4.9 Adicionar destaque de vagas novas
  - Destacar as vagas novas cujo score atinge ou supera o limiar configurado.
  - _Depends: 4.5_
  - _Implements: DES-16, REQ-18.12_

- [x] 4.10 Adicionar estados degradados no relatório
  - Marcar modo determinístico, falha de síntese, cota esgotada e ausência total de sobreviventes ao pré-filtro.
  - _Depends: 4.4, 4.5_
  - _Implements: DES-16, REQ-17.2, REQ-17.3, REQ-18.11, REQ-18.13_

- [x] 4.11 Adicionar modo determinístico do run
  - Concluir o run sem qualquer chamada de modelo quando o modo estiver ativo.
  - _Depends: 1.13, 4.1_
  - _Implements: DES-1, DES-5, REQ-17.1_

- [x] 4.12 Adicionar agendador com rodízio
  - Enfileirar runs recorrentes em rodízio entre usuários ativos, restringindo a janela ao intervalo incremental.
  - _Depends: 1.10_
  - _Implements: DES-18, REQ-22.1, REQ-22.5_

- [x] 4.13 Adicionar recusa de run concorrente
  - Recusar e registrar novo run para usuário que já tenha run em andamento.
  - _Depends: 4.12_
  - _Implements: DES-18, REQ-22.2_

- [x] 4.14 Adicionar portão de recuperação após bloqueio
  - Encerrar o run na entrada, sem tocar a rede, enquanto o intervalo de recuperação após um bloqueio não tiver passado.
  - _Depends: 3.12, 4.12_
  - _Implements: DES-18, REQ-22.3_

- [x] 4.15 Adicionar liberação de run travado
  - Marcar como interrompido o run cujo heartbeat exceda a duração máxima e liberar a vez.
  - _Depends: 4.13_
  - _Implements: DES-18, REQ-22.4_

- [x] 4.16 Adicionar run imediato com limite diário
  - Criar run solicitado pelo usuário com a janela ampla e recusar acima do número diário configurado.
  - _Depends: 4.13_
  - _Implements: DES-18, REQ-22.6, REQ-22.7_

- [x] 4.17 Adicionar exclusão de conta em cascata
  - Apagar currículo, extração, perfil, cards, scores, runs e relatórios do titular em uma transação.
  - _Depends: 1.7_
  - _Implements: DES-17, REQ-20.1_

- [x] 4.18 Adicionar retenção do registro compartilhado na exclusão
  - Preservar o registro compartilhado de descrição sem qualquer vínculo com o usuário excluído.
  - _Depends: 4.17, 3.17_
  - _Implements: DES-17, REQ-20.6_

- [x] 4.19 Adicionar exportação de dados do titular
  - Produzir arquivo com todo dado pessoal armazenado sob o identificador do usuário.
  - _Depends: 1.7_
  - _Implements: DES-17, REQ-20.2_

- [x] 4.20 Adicionar registro das operações de titular
  - Gravar instante e identificador em cada exclusão e exportação.
  - _Depends: 4.17, 4.19_
  - _Implements: DES-17, REQ-20.5_

- [x] 4.21 Adicionar avisos prévios de tratamento de dados
  - Informar, antes da primeira importação, quais dados serão guardados e por quanto tempo, e para qual provedor o currículo será enviado.
  - _Depends: 2.23_
  - _Implements: DES-17, REQ-20.3, REQ-20.7_

- [x] 4.33 Esquecer contatos de recrutador quando a vaga expira
  - Apagar os endereços de e-mail de contato do registro compartilhado quando nenhum usuário mantiver a vaga em estado ativo, preservando o texto da descrição.
  - _Implements: DES-17, REQ-20.8_

- [x] 4.22 Adicionar expurgo por período de retenção
  - Apagar currículos e extrações do usuário que fique sem sessão além do período configurado.
  - _Depends: 4.17_
  - _Implements: DES-17, REQ-20.4_

- [x] 4.23 Adicionar dependência de sessão para rotas protegidas
  - Resolver o usuário da sessão em cada requisição e recusar acesso a dado de perfil, de vaga ou de relatório quando não houver sessão válida.
  - _Depends: 2.3, 1.12_
  - _Implements: DES-2, REQ-1.5_

- [x] 4.24 Adicionar endpoints de identidade
  - Expor o início do fluxo Google, o retorno com o código e o encerramento de sessão, gravando o testemunho em cookie sem acesso a script.
  - _Depends: 4.23_
  - _Implements: DES-2, REQ-1.1, REQ-1.3, REQ-1.6_

- [x] 4.25 Adicionar endpoints da conexão LinkedIn
  - Expor o início do fluxo, o retorno e a desconexão, recusando na fronteira qualquer material de credencial submetido.
  - _Depends: 4.23_
  - _Implements: DES-3, REQ-2.1, REQ-2.2, REQ-2.5_

- [x] 4.26 Adicionar endpoints de credenciais de modelo
  - Expor a oferta de provedores, o cadastro com aviso prévio, a reordenação da cadeia e a remoção, sem nunca devolver o valor da credencial.
  - _Depends: 4.23_
  - _Implements: DES-4, REQ-25.7, REQ-26.4, REQ-26.6, REQ-26.11_

- [x] 4.27 Adicionar endpoints de importação de currículo
  - Expor a seleção no Drive e o envio de arquivo, aplicando as recusas de formato, tamanho, frequência e camada de texto.
  - _Depends: 4.23_
  - _Implements: DES-6, REQ-3.1, REQ-3.2, REQ-3.3_

- [x] 4.28 Adicionar a tela de conferência da extração
  - Exibir os campos extraídos com o trecho de origem, aceitar correção campo a campo e a confirmação que abre o portão.
  - _Depends: 4.27_
  - _Implements: DES-7, REQ-4.6, REQ-4.7, REQ-4.11_

- [x] 4.29 Adicionar endpoints de perfil e direitos do titular
  - Expor o perfil consolidado com a origem de cada campo, a exportação dos dados e a exclusão da conta.
  - _Depends: 4.23_
  - _Implements: DES-8, DES-17, REQ-5.3, REQ-20.1, REQ-20.2_

- [x] 4.30 Adicionar endpoints de runs e relatórios
  - Expor a solicitação de busca imediata, a lista de runs do usuário, a página do relatório e a marcação de estado de uma vaga.
  - _Depends: 4.23_
  - _Implements: DES-16, DES-18, REQ-18.1, REQ-21.3, REQ-22.6_

- [x] 4.31 Adicionar o processo de enriquecimento executável
  - Criar o laço do processo único que detém a sessão operacional, consumindo a fila de enriquecimento e devolvendo o run à fila ao esgotá-la.
  - _Depends: 3.13, 3.19_
  - _Implements: DES-1, DES-12, REQ-11.1_

- [x] 4.32 Adicionar os pontos de entrada dos processos
  - Criar o comando único que sobe o processo web, o worker de runs, o processo de enriquecimento ou um ciclo de agendamento, conforme o modo pedido.
  - _Depends: 4.24, 4.31, 4.12_
  - _Implements: DES-1, DES-19, REQ-24.3_

## Phase 5: Acceptance Criteria Testing

- [x] 5.1 Test: entrada por conta Google cria sessão estável
  - Verificar que o fluxo concluído cria sessão vinculada a identificador derivado do identificador de assunto, e não do e-mail.
  - Test type: integration
  - _Depends: 2.1_
  - _Implements: REQ-1.1, REQ-1.2_

- [x] 5.2 Test: autorização cancelada e sessão sem validade
  - Verificar recusa nomeando a causa, expiração pela duração máxima, bloqueio de acesso a dado sem sessão e encerramento que preserva as origens conectadas.
  - Test type: integration
  - _Depends: 2.4_
  - _Implements: REQ-1.3, REQ-1.4, REQ-1.5, REQ-1.6_

- [x] 5.3 Test: conexão LinkedIn guarda identidade e escopos
  - Verificar armazenamento dos campos concedidos, da lista de escopos e da marcação de campo indisponível por escopo.
  - Test type: integration
  - _Depends: 2.6_
  - _Implements: REQ-2.1, REQ-2.3, REQ-2.4_

- [x] 5.4 Test: credencial de LinkedIn de usuário é recusada
  - Verificar que senha, cookie e sessão submetidos são recusados antes de qualquer armazenamento.
  - Test type: integration
  - _Depends: 2.7_
  - _Implements: REQ-2.2_

- [x] 5.5 Test: desconexão e expiração da conexão LinkedIn
  - Verificar apagamento com revogação na desconexão e pedido de reconexão quando o testemunho expira.
  - Test type: integration
  - _Depends: 2.8_
  - _Implements: REQ-2.5, REQ-2.6_

- [x] 5.6 Test: importação de currículo pelas três origens
  - Verificar que Drive, Word e PDF produzem registro com texto e procedência, e que o Drive pede apenas o escopo de leitura necessário.
  - Test type: integration
  - _Depends: 2.20_
  - _Implements: REQ-3.1, REQ-3.2, REQ-3.3, REQ-3.7_

- [x] 5.7 Test: recusas de importação de currículo
  - Verificar recusa de formato não aceito nomeando os aceitos, de arquivo acima do limite, de importação acima da frequência diária e de documento sem camada de texto.
  - Test type: integration
  - _Depends: 2.22_
  - _Implements: REQ-3.4, REQ-3.5, REQ-3.6, REQ-3.9_

- [x] 5.8 Test: currículo é gravado sob o usuário que o importou
  - Verificar a associação do registro ao identificador de usuário.
  - Test type: integration
  - _Depends: 2.23_
  - _Implements: REQ-3.8_

- [x] 5.9 Test: extração produz campos, trechos e lacunas
  - Verificar campos extraídos com trecho de origem, campo ausente como nulo com entrada na lista de lacunas, e uso do provedor do próprio usuário.
  - Test type: integration
  - _Depends: 2.25_
  - _Implements: REQ-4.1, REQ-4.2, REQ-4.3, REQ-4.9, REQ-4.10_

- [x] 5.10 Test: extração é reusada por conteúdo idêntico
  - Verificar indexação pelo conteúdo e ausência de nova chamada quando o mesmo conteúdo é importado.
  - Test type: unit
  - _Depends: 2.26_
  - _Implements: REQ-4.4, REQ-4.5_

- [x] 5.11 Test: portão de conferência bloqueia extração não confirmada
  - Verificar exibição para conferência, recusa de uso em run antes da confirmação e gravação da correção como edição manual sem sobrescrever o extraído.
  - Test type: integration
  - _Depends: 2.28_
  - _Implements: REQ-4.6, REQ-4.7, REQ-4.11_

- [x] 5.12 Test: caminhos alternativos de extração
  - Verificar oferta de preenchimento manual na falha e, sem credencial, oferta sem que nenhuma requisição seja emitida.
  - Test type: integration
  - _Depends: 2.29_
  - _Implements: REQ-4.8, REQ-4.12_

- [x] 5.13 Test: consolidação aplica precedência e registra origem
  - Verificar nova versão a cada mudança de origem, prevalência de edição manual sobre currículo e de currículo sobre LinkedIn, origem por campo e gravação sob o usuário.
  - Test type: integration
  - _Depends: 2.30_
  - _Implements: REQ-5.1, REQ-5.2, REQ-5.3, REQ-5.6_

- [x] 5.14 Test: nível inferido, reuso de versão e recusa sem histórico
  - Verificar derivação determinística do nível, reuso da versão vigente sem recálculo e recusa de run com instrução quando não há histórico.
  - Test type: integration
  - _Depends: 2.33_
  - _Implements: REQ-5.4, REQ-5.5, REQ-5.7_

- [x] 5.15 Test: diagnóstico de higiene do perfil
  - Verificar detecção de cargos simultâneos, lacunas e sobreposições com campo e trecho de origem, e lista vazia quando nada é detectado.
  - Test type: unit
  - _Depends: 2.34_
  - _Implements: REQ-6.1, REQ-6.2, REQ-6.3_

- [x] 5.16 Test: planejamento de buscas é determinístico e bilíngue
  - Verificar ausência de chamada de modelo, descarte de termo único, presença de português e inglês, registro no run e lista idêntica para entrada idêntica.
  - Test type: unit
  - _Depends: 3.2_
  - _Implements: REQ-7.1, REQ-7.2, REQ-7.3, REQ-7.4, REQ-7.5_

- [x] 5.17 Test: coleta normaliza card e grava sob o usuário
  - Verificar os campos do card normalizado, a captura dos sinais devolvidos e a associação ao usuário do run.
  - Test type: integration
  - _Depends: 3.4_
  - _Implements: REQ-8.1, REQ-8.3, REQ-8.7_

- [x] 5.18 Test: deduplicação, busca vazia e busca com falha
  - Verificar descarte de identificador repetido no run e registro distinto de busca improdutiva e de busca com falha, com prosseguimento nas demais.
  - Test type: integration
  - _Depends: 3.6_
  - _Implements: REQ-8.2, REQ-8.5, REQ-8.6_

- [x] 5.20 Test: pré-filtro roda antes de qualquer busca de descrição e registra motivos
  - Verificar avaliação de todos os cards antes de qualquer busca de descrição, incluindo a rota pública, descarte por padrão com o motivo registrado e contagens antes e depois.
  - Test type: integration
  - _Depends: 3.10_
  - _Implements: REQ-9.1, REQ-9.2, REQ-9.3, REQ-9.6_

- [x] 5.21 Test: geografia não elimina vaga
  - Verificar que vaga remota ignora avaliação geográfica e que presencial fora do raio permanece com blocker nomeando cidade e distância.
  - Test type: unit
  - _Depends: 3.9_
  - _Implements: REQ-9.4, REQ-9.5_

- [x] 5.22 Test: enriquecimento grava descrição e marca indisponibilidade
  - Verificar texto integral com instante da coleta, marcação de vaga sem descrição com prosseguimento e o indicador de disponibilidade.
  - Test type: integration
  - _Depends: 3.14_
  - _Implements: REQ-10.1, REQ-10.2, REQ-10.3_

- [x] 5.23 Test: captura de concorrência e contatos da vaga
  - Verificar registro do número de candidatos vindo da sessão operacional e de todo endereço de e-mail presente na descrição obtida pela rota pública.
  - Test type: integration
  - _Depends: 3.15, 3.16_
  - _Implements: REQ-10.4, REQ-10.5_

- [x] 5.24 Test: descrição é guardada uma vez e reusada sem rede
  - Verificar armazenamento único para todo o sistema e reuso sem emissão de requisição.
  - Test type: integration
  - _Depends: 3.17_
  - _Implements: REQ-10.6, REQ-10.7_

- [x] 5.25 Test: governador serializa, espera e recua
  - Verificar uma vaga por vez em todo o sistema, intervalo aleatório dentro da faixa e recuo maior a cada tempo esgotado.
  - Test type: unit
  - _Depends: 3.11_
  - _Implements: REQ-11.1, REQ-11.2, REQ-11.3_

- [x] 5.26 Test: bloqueio de coleta interrompe e recusa
  - Verificar gravação do bloqueio ao terceiro fracasso consecutivo e recusa de toda chamada enquanto ele estiver registrado.
  - Test type: unit
  - _Depends: 3.12_
  - _Implements: REQ-11.4, REQ-11.5_

- [x] 5.27 Test: credencial de usuário nunca coleta vaga
  - Verificar que nenhuma credencial de usuário alcança a coleta e que o enriquecedor recebe apenas identificador e endereço de vaga.
  - Test type: integration
  - _Depends: 3.13_
  - _Implements: REQ-11.6_

- [x] 5.28 Test: cota é debitada, recusada e zerada na virada
  - Verificar teto por usuário a partir do orçamento e dos ativos, débito por enriquecimento pago, recusa registrada ao esgotar, registro de consumo individual e total, e ausência de transporte de saldo.
  - Test type: integration
  - _Depends: 3.22_
  - _Implements: REQ-12.1, REQ-12.2, REQ-12.3, REQ-12.4, REQ-12.5_

- [x] 5.29 Test: score compõe componentes e respeita o intervalo
  - Verificar score inteiro sem chamada de modelo, registro de cada componente, aplicação de bônus e penalidades e limite entre 0 e 100 com ajustes extremos.
  - Test type: unit
  - _Depends: 3.25_
  - _Implements: REQ-13.1, REQ-13.2, REQ-13.4, REQ-13.5, REQ-13.9, REQ-13.10_

- [x] 5.30 Test: ontologia iguala variantes de tecnologia
  - Verificar que variantes declaradas colapsam no mesmo termo canônico na comparação de competências.
  - Test type: unit
  - _Depends: 3.23_
  - _Implements: REQ-13.3_

- [x] 5.31 Test: score sem descrição, determinismo e histórico
  - Verificar score a partir do card com a marcação de ausência de leitura, resultado idêntico para entrada idêntica e preservação das pontuações anteriores.
  - Test type: integration
  - _Depends: 3.27_
  - _Implements: REQ-13.6, REQ-13.7, REQ-13.8_

- [x] 5.32 Test: lacunas, diferenciais e agregação por frequência
  - Verificar as duas listas para vaga com descrição, ausência de chamada de modelo e a soma de frequência entre as vagas do run.
  - Test type: unit
  - _Depends: 3.29_
  - _Implements: REQ-14.1, REQ-14.2, REQ-14.3, REQ-14.4_

- [x] 5.33 Test: uma requisição de síntese por run com o top-N
  - Verificar envio único por run, apenas dados estruturados, ausência de marcação bruta e limite de vagas configurado.
  - Test type: integration
  - _Depends: 4.1_
  - _Implements: REQ-15.1, REQ-15.2, REQ-15.8, REQ-15.10_

- [x] 5.34 Test: exigências de classificação e proveniência na resposta
  - Verificar a exigência de classificação de afirmação não trivial e a distinção entre vaga lida e vaga avaliada por card.
  - Test type: integration
  - _Depends: 4.2_
  - _Implements: REQ-15.3, REQ-15.5_

- [x] 5.35 Test: resposta sem aterramento é descartada
  - Verificar descarte de resposta que cite vaga, URL ou empresa ausente do envio, com a violação registrada.
  - Test type: unit
  - _Depends: 4.3_
  - _Implements: REQ-15.4, REQ-16.4_

- [x] 5.36 Test: falha de síntese, proveniência e tokens
  - Verificar registro da falha, do provedor e modelo usados, das contagens de tokens e relatório gerado sem a seção de síntese.
  - Test type: integration
  - _Depends: 4.4, 4.10_
  - _Implements: REQ-15.6, REQ-15.7, REQ-15.9, REQ-15.11, REQ-15.12_

- [x] 5.37 Test: contenção de injeção na fronteira do modelo
  - Verificar delimitador de dado não confiável em texto de origem e de currículo, instrução de sistema montada de gabarito fixo e truncamento no limite configurado.
  - Test type: unit
  - _Depends: 2.24_
  - _Implements: REQ-16.1, REQ-16.2, REQ-16.3, REQ-16.5_

- [x] 5.38 Test: modo determinístico entrega relatório sem modelo
  - Verificar run concluído sem qualquer chamada de modelo, relatório com vagas, links, scores, lacunas e diferenciais, e marcação das seções ausentes.
  - Test type: e2e
  - _Depends: 4.11, 4.10_
  - _Implements: REQ-17.1, REQ-17.2, REQ-17.3_

- [x] 5.39 Test: relatório apresenta vagas ordenadas e completas
  - Verificar associação a usuário e run, ordenação por score decrescente e os campos exigidos por vaga, incluindo a descrição integral quando disponível.
  - Test type: e2e
  - _Depends: 4.5_
  - _Implements: REQ-18.1, REQ-18.2, REQ-18.3, REQ-18.4_

- [x] 5.40 Test: relatório mostra contatos, blockers e descartadas
  - Verificar os endereços de contato por vaga, o texto do blocker e a lista de descartadas com o motivo.
  - Test type: e2e
  - _Depends: 4.6_
  - _Implements: REQ-18.5, REQ-18.6, REQ-18.7_

- [x] 5.41 Test: texto de origem aparece escapado
  - Verificar que marcação presente em descrição, título, empresa, contato e currículo é exibida escapada.
  - Test type: unit
  - _Depends: 4.7_
  - _Implements: REQ-18.8_

- [x] 5.42 Test: relatório traz higiene, ranking e destaque
  - Verificar a lista de problemas de higiene, o ranking de competências por frequência e o destaque das vagas novas acima do limiar.
  - Test type: e2e
  - _Depends: 4.9_
  - _Implements: REQ-18.9, REQ-18.10, REQ-18.12_

- [x] 5.43 Test: relatório expõe estados degradados
  - Verificar relatório com contagens e motivos quando nenhuma vaga sobrevive, e a quantidade de vagas sem descrição quando a cota se esgotou.
  - Test type: e2e
  - _Depends: 4.10_
  - _Implements: REQ-18.11, REQ-18.13_

- [x] 5.44 Test: isolamento entre usuários
  - Verificar gravação sob o identificador do titular, restrição de toda consulta à sessão corrente, recusa registrada em referência cruzada e ausência de vínculo de usuário no registro compartilhado.
  - Test type: integration
  - _Depends: 3.18_
  - _Implements: REQ-19.1, REQ-19.2, REQ-19.3, REQ-19.4_

- [x] 5.45 Test: exclusão apaga o titular e preserva o comum
  - Verificar remoção em cascata de todo dado do usuário e preservação do registro compartilhado sem vínculo com o excluído.
  - Test type: integration
  - _Depends: 4.18_
  - _Implements: REQ-20.1, REQ-20.6_

- [x] 5.46 Test: exportação, registro de operações e retenção
  - Verificar o arquivo com todo dado pessoal do titular, o registro de instante e identificador em cada operação e o expurgo após o período de retenção.
  - Test type: integration
  - _Depends: 4.20, 4.22_
  - _Implements: REQ-20.2, REQ-20.4, REQ-20.5_

- [x] 5.47 Test: avisos prévios de tratamento de dados
  - Verificar a informação sobre dados guardados e prazo antes da primeira importação e o nome do provedor de destino do currículo.
  - Test type: e2e
  - _Depends: 4.21_
  - _Implements: REQ-20.3, REQ-20.7_

- [x] 5.67 Test: contatos de recrutador são esquecidos ao expirar a vaga
  - Verificar remoção dos endereços de contato ao expirar a última referência, preservação do texto da descrição e permanência dos contatos enquanto outro usuário mantém a vaga ativa.
  - Test type: unit
  - _Depends: 4.33_
  - _Implements: REQ-20.8_

- [x] 5.48 Test: ciclo de vida e persistência da vaga
  - Verificar estado novo na primeira coleta, atualização da última ocorrência preservando a primeira, marcação escolhida pelo usuário e registro de run com contagens e bloqueios.
  - Test type: integration
  - _Depends: 3.30, 1.11_
  - _Implements: REQ-21.1, REQ-21.2, REQ-21.3, REQ-21.5_

- [x] 5.49 Test: expiração conta apenas ausência elegível
  - Verificar que vaga fora da janela de publicação do run não acumula ausência e que três ausências elegíveis expiram a vaga.
  - Test type: unit
  - _Depends: 3.31_
  - _Implements: REQ-21.4_

- [x] 5.50 Test: falha de gravação encerra o run com causa
  - Verificar encerramento nomeando a operação e a causa quando uma gravação falha.
  - Test type: integration
  - _Depends: 1.9_
  - _Implements: REQ-21.6_

- [x] 5.51 Test: agendamento recorrente é incremental e justo
  - Verificar restrição da janela ao intervalo configurado e rodízio que atende todos os ativos antes de repetir alguém.
  - Test type: integration
  - _Depends: 4.12_
  - _Implements: REQ-22.1, REQ-22.5_

- [x] 5.52 Test: concorrência, recuperação e run travado
  - Verificar recusa registrada de run concorrente do mesmo usuário, adiamento de todos após bloqueio e liberação do run cujo heartbeat vence.
  - Test type: integration
  - _Depends: 4.15, 4.14_
  - _Implements: REQ-22.2, REQ-22.3, REQ-22.4_

- [x] 5.53 Test: run imediato respeita limite diário
  - Verificar criação com a janela ampla e recusa informando quando novas solicitações serão aceitas.
  - Test type: integration
  - _Depends: 4.16_
  - _Implements: REQ-22.6, REQ-22.7_

- [x] 5.54 Test: segredos ficam fora de código, log e página
  - Verificar leitura exclusiva de configuração externa e ausência de segredos, testemunhos e cookies em log e em qualquer página apresentada.
  - Test type: integration
  - _Depends: 1.4, 4.7_
  - _Implements: REQ-23.1, REQ-23.2, REQ-23.3_

- [x] 5.55 Test: testemunhos e credenciais ficam cifrados
  - Verificar que testemunho de terceiro e credencial de provedor não aparecem em claro no armazenamento.
  - Test type: integration
  - _Depends: 2.12_
  - _Implements: REQ-23.4_

- [x] 5.56 Test: configuração é externa, validada e registrada
  - Verificar leitura de todo parâmetro configurado, validação de tipo e faixa, recusa de inicialização nomeando a chave e gravação da configuração efetiva no run.
  - Test type: integration
  - _Depends: 1.11_
  - _Implements: REQ-24.1, REQ-24.2, REQ-24.3, REQ-24.4_

- [x] 5.57 Test: registro de provedores recusa quem exige aplicação
  - Verificar carga de configuração externa, campos declarados por provedor, aceitação apenas de chave estática ou ausência de credencial e validação antes da oferta.
  - Test type: unit
  - _Depends: 2.9_
  - _Implements: REQ-25.1, REQ-25.2, REQ-25.3, REQ-25.4_

- [x] 5.58 Test: provedor indisponível some da oferta
  - Verificar exclusão do provedor que não responde, registro de onde ele executa e apresentação de limite declarado e destino dos dados.
  - Test type: integration
  - _Depends: 2.11_
  - _Implements: REQ-25.5, REQ-25.6, REQ-25.7_

- [x] 5.59 Test: cadastro de credencial valida e nunca reexibe
  - Verificar validação contra o provedor antes de gravar, recusa com a causa devolvida, gravação sob o usuário, ausência de reexibição do valor, remoção efetiva e aviso prévio.
  - Test type: integration
  - _Depends: 2.14_
  - _Implements: REQ-26.1, REQ-26.2, REQ-26.3, REQ-26.4, REQ-26.5, REQ-26.6_

- [x] 5.60 Test: cadeia de fallback e piso determinístico
  - Verificar run determinístico sem credencial, avanço para o próximo provedor da ordem do usuário na recusa por limite e conclusão em modo determinístico ao esgotar a cadeia.
  - Test type: integration
  - _Depends: 2.16_
  - _Implements: REQ-26.7, REQ-26.8, REQ-26.9_

- [x] 5.61 Test: credencial não vaza entre usuários
  - Verificar que a credencial de um usuário nunca é usada em run de outro.
  - Test type: integration
  - _Depends: 2.17_
  - _Implements: REQ-26.10_

- [x] 5.62 Test: ordem de tentativa pertence ao usuário
  - Verificar gravação da ordem do usuário e semeadura a partir da ordem padrão do operador no primeiro cadastro.
  - Test type: integration
  - _Depends: 2.15_
  - _Implements: REQ-26.11, REQ-26.12_

- [x] 5.63 Test: rota protegida recusa acesso sem sessão válida
  - Verificar que uma requisição a dado de perfil, de vaga ou de relatório sem sessão é recusada.
  - Test type: integration
  - _Depends: 4.23_
  - _Implements: REQ-1.5_

- [x] 5.64 Test: o fluxo de entrada e saída funciona pelo endereço HTTP
  - Verificar a entrada pelo retorno do provedor, o acesso a uma rota protegida e o encerramento que preserva as origens conectadas.
  - Test type: e2e
  - _Depends: 4.24_
  - _Implements: REQ-1.1, REQ-1.6_

- [x] 5.65 Test: uma requisição a recurso de outro usuário é recusada pelo endereço HTTP
  - Verificar que a sessão de um usuário não alcança relatório nem vaga de outro.
  - Test type: integration
  - _Depends: 4.30_
  - _Implements: REQ-19.3_

- [x] 5.66 Test: cada processo sobe no modo pedido
  - Verificar que o comando de entrada resolve os quatro modos e recusa modo desconhecido nomeando os aceitos.
  - Test type: integration
  - _Depends: 4.32_
  - _Implements: REQ-24.3_

## Phase 6: Final Checkpoint

- [x] 6.1 Verificar completude e prontidão da entrega
  - Executar a suíte completa, conferir que todo critério de aceitação tem teste correspondente que passa, confirmar que cada arquivo previsto na `Code Anatomy` existe, subir cada processo no seu modo, conferir que nenhuma ordenação compara scores calculados sob regimes de evidência diferentes, rodar a validação do spec e registrar os riscos residuais conhecidos.
  - _Implements: All requirements_
