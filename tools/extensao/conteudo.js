/* Varredura automatica dos cards de vaga, dentro da pagina do LinkedIn.
 *
 * A diferenca para o bookmarklet nao e o que ele le -- e quando. O bookmarklet
 * le no clique, e por isso so ve a lista que estava na tela naquele instante.
 * O LinkedIn e uma aplicacao de pagina unica: os cards entram e saem do DOM
 * conforme voce rola e troca de busca, sem recarregar nada. Um observador
 * acompanha isso; um clique nao.
 *
 * O que ele NAO faz, e por que isso importa: nao le, nao copia e nao envia
 * cookie, token ou qualquer credencial da sua conta. O app recebe numeros ja
 * extraidos -- o resultado da leitura, nunca o meio de refazer a leitura sem
 * voce.
 *
 * Sobre os seletores: o LinkedIn nao publica contrato de marcacao, e a parte
 * Premium so aparece para quem tem Premium. Por isso a leitura e por padrao de
 * texto e nao por classe CSS -- classe muda a cada deploy, o texto "You'd be a
 * top applicant" e mais estavel.
 */
(function () {
  "use strict";

  var APP_PADRAO = "http://localhost:8000";

  /* Espera antes de varrer, em ms. O observador dispara dezenas de vezes por
   * segundo enquanto a lista se monta; varrer a cada disparo seria varrer o
   * mesmo card meio construido varias vezes. */
  var REPOUSO_MS = 1200;

  /* Ja enviados nesta aba, por id de vaga e conteudo lido. Evita reenviar o
   * mesmo card a cada rolagem -- o servidor aceitaria, mas seria uma requisicao
   * por scroll. A chave inclui os sinais: se o card passar a mostrar algo que
   * antes nao mostrava, aquilo e uma leitura nova e vale enviar. */
  var enviados = Object.create(null);

  var temporizador = null;

  // ------------------------------------------------------------- leitura
  /* Como a pagina nomeia quem se candidatou.
   *
   * "people" entrou depois de ver o painel Premium escrever "Over 100 people
   * clicked apply": a contagem estava na tela e passava batido, porque a lista
   * tinha a forma portuguesa "pessoa" e a inglesa "applicant" e nao esta.
   *
   * Uma constante so, usada pela contagem e pelo sinal. Antes eram duas copias
   * do mesmo vocabulario e elas ja tinham divergido -- "pessoa" existia numa e
   * nao na outra --, entao um termo novo consertava metade do comportamento. */
  var QUEM_SE_CANDIDATOU = "candidatura|candidato|applicant|pessoa|people";

  /* "Mais de 200 candidaturas", "87 candidatos", "Over 100 people clicked". */
  function contagem(texto) {
    var piso = texto.match(new RegExp(
      "(?:mais de|over)\\s+([\\d.,]+)\\s*(?:" + QUEM_SE_CANDIDATOU + ")", "i"
    ));
    if (piso) return parseInt(piso[1].replace(/[.,]/g, ""), 10);
    var exato = texto.match(new RegExp(
      "\\b([\\d.,]+)\\s*(?:" + QUEM_SE_CANDIDATOU + ")", "i"
    ));
    if (exato) return parseInt(exato[1].replace(/[.,]/g, ""), 10);
    return null;
  }

  /* O quadro Premium lista faixas com contagem: "Senior 34", "Pleno 12". */
  function senioridade(texto) {
    var faixas = [
      ["estagio", /est[aá]gi[oa]/i], ["junior", /j[uú]nior|entry/i],
      ["pleno", /pleno|associate|mid/i], ["senior", /s[eê]nior(?!\s*manager)/i],
      ["coordenacao", /coordena|lead\b/i], ["gerencia", /ger[eê]ncia|manager/i],
      ["diretoria", /diretor|director|executiv/i]
    ];
    var achado = {};
    faixas.forEach(function (par) {
      /* Duas defesas, e as duas vieram de leitura errada observada.
       *
       * O grupo nao-capturante e obrigatorio: as faixas sao alternacoes, e sem
       * ele o primeiro ramo casa sozinho, sem grupo 1, e a contagem sai NaN.
       *
       * A recusa de unidade de tempo e a segunda. O quadro Premium so existe na
       * pagina de uma vaga; nos cards da lista o numero mais proximo de
       * "Manager" nao e contagem de candidato, e sim a data -- "Senior
       * Engineering Manager ... 3 days ago" virava `gerencia: 3`, um numero
       * inventado que ia para o banco com cara de dado. */
      var re = new RegExp(
        "(?:" + par[1].source + ")[^\\d]{0,24}(\\d{1,6})" +
        "(?!\\s*(?:dia|day|semana|week|m[eê]s|month|hora|hour|ano|year|min))",
        "i"
      );
      var m = texto.match(re);
      var n = m ? parseInt(m[1], 10) : NaN;
      if (!isNaN(n)) achado[par[0]] = n;
    });
    return achado;
  }

  function sinais(texto) {
    var achados = [];
    if (/top applicant|principais candidatos|candidato de destaque/i.test(texto))
      achados.push("top_applicant");
    // "Be an early applicant" e a forma que o card de fato usa -- vista no
    // proprio LinkedIn, ao lado de "You'd be a top applicant". Os padroes
    // antigos cobriam so a frase longa da pagina da vaga ("Seja um dos 25
    // primeiros") e perdiam o aviso curto da lista inteiro.
    if (/seja um dos \d+ primeiros|be among the first|early applicant|candidate-se primeiro/i
        .test(texto))
      achados.push("early_applicant");
    if (new RegExp("(?:mais de|over)\\s+[\\d.,]+\\s*(?:" + QUEM_SE_CANDIDATOU + ")", "i")
        .test(texto))
      achados.push("muitos_candidatos");
    return achados;
  }

  function ler(texto) {
    return {
      candidatos: contagem(texto),
      senioridade: senioridade(texto),
      sinais: sinais(texto)
    };
  }

  function vazia(l) {
    return l.candidatos === null && !l.sinais.length &&
      !Object.keys(l.senioridade).length;
  }

  /* Onde o id de uma vaga pode estar, e por que sao tres lugares.
   *
   * A primeira versao procurava so `a[href*="/jobs/view/"]`, e isso vale na
   * pagina de UMA vaga. Na busca de duas colunas nao vale: os cards da esquerda
   * nao navegam para `/jobs/view/` -- eles trocam `currentJobId` na propria URL
   * e trazem o id num atributo. O unico `/jobs/view/` da tela e o painel da
   * direita, e era por isso que a varredura via "1 card" com seis na lista.
   *
   * A ordem nao importa; o conjunto sim. Cada forma cobre uma tela diferente, e
   * o LinkedIn usa as tres ao mesmo tempo. */
  var SELETOR_DE_VAGA = [
    'a[href*="/jobs/view/"]',
    'a[href*="currentJobId="]',
    "[data-occludable-job-id]",
    "[data-job-id]"
  ].join(",");

  function idDoElemento(el) {
    var href = el.getAttribute ? (el.getAttribute("href") || "") : "";
    var m = href.match(/\/jobs\/view\/(\d+)/) ||
            href.match(/currentJobId=(\d+)/);
    if (m) return m[1];
    var attr = (el.getAttribute && (el.getAttribute("data-occludable-job-id") ||
                                    el.getAttribute("data-job-id"))) || "";
    return /^\d+$/.test(attr) ? attr : null;
  }

  /* Quantas vagas distintas existem dentro deste elemento. */
  function vagasDentro(elemento) {
    var vistos = Object.create(null);
    var n = 0;
    var alvos = elemento.querySelectorAll(SELETOR_DE_VAGA);
    for (var i = 0; i < alvos.length; i++) {
      var id = idDoElemento(alvos[i]);
      if (id && !vistos[id]) { vistos[id] = true; n++; }
    }
    return n;
  }

  /* O card de uma vaga, subindo a partir do link dela.
   *
   * A primeira versao usava `closest("li")`, e isso valia so na pagina de
   * resultados. Em "Jobs based on your preferences" o card nao e um <li>: o
   * seletor voltava vazio, caia no elemento-pai -- que embrulha so o titulo --
   * e o "You'd be a top applicant", que e irmao e nao filho, ficava de fora. A
   * leitura saia vazia e o card era descartado em silencio.
   *
   * O criterio novo nao olha tag nenhuma. Sobe enquanto o ancestral contiver
   * UMA vaga so; no degrau em que ele passa a conter duas, subiu demais -- ali
   * ja e a lista, e o texto traria o sinal do vizinho. O ultimo degrau com uma
   * vaga e o card, qualquer que seja a marcacao que o LinkedIn use amanha. */
  function cardDoElemento(elemento) {
    // O proprio elemento pode ja ser o card: um <li data-occludable-job-id>
    // embrulha o card inteiro, e comecar pelo pai passaria do ponto.
    var atual = vagasDentro(elemento) === 1 ? elemento : elemento.parentElement;
    var melhor = null;
    for (var passo = 0; atual && passo < 8; passo++) {
      if (vagasDentro(atual) > 1) break;
      melhor = atual;
      atual = atual.parentElement;
    }
    return melhor;
  }

  /* Os cards presentes agora, por id de vaga.
   *
   * A busca comeca pelo id da vaga porque ele e a unica parte do card que o
   * LinkedIn nao pode renomear sem quebrar o proprio site. Classe CSS muda a
   * cada deploy; o id da vaga, nao. */
  /* As linhas visiveis do card, sem as repeticoes.
   *
   * O LinkedIn duplica texto para leitor de tela -- o titulo costuma aparecer
   * duas vezes seguidas --, e sem tirar isso a "empresa" viria como o proprio
   * titulo. */
  function linhasDe(caixa) {
    var cruas = String(caixa.innerText || "").split("\n");
    var limpas = [];
    for (var i = 0; i < cruas.length; i++) {
      var linha = cruas[i].trim();
      if (!linha) continue;
      if (limpas.length && limpas[limpas.length - 1] === linha) continue;
      limpas.push(linha);
    }
    return limpas;
  }

  /* Titulo, empresa, local e endereco -- o bastante para o crivo criar a vaga.
   *
   * Isto existe porque a vaga que voce esta olhando quase nunca e uma que o
   * crivo achou: as buscas do planejador sao focadas no Brasil e a navegacao
   * nao e. Sem estes campos o servidor recusava o card desconhecido, e a
   * varredura gravava nada sem erro nenhum.
   *
   * O endereco e montado do id em vez de lido do href: na busca de duas colunas
   * o href e `?currentJobId=`, que carrega a busca inteira junto e nao serve
   * como link permanente para a vaga. */
  /* Linhas que sao aviso, data ou estado -- nunca nome de empresa. */
  var NAO_E_EMPRESA = new RegExp(
    "top applicant|early applicant|clicked apply|" + QUEM_SE_CANDIDATOU +
    "|\\bago\\b|h[aá] \\d|atr[aá]s|promoted|patrocinad|viewed|visualizad|" +
    "saved|salva|easy apply|candidatura simplificada|connection|conex[oõ]es|" +
    "premium|reposted|republicad|actively|reviewing|\\bnew\\b|\\bnova\\b",
    "i"
  );

  function metadados(caixa, id, titulo) {
    var linhas = linhasDe(caixa).filter(function (l) { return l !== titulo; });

    /* So o nome da empresa, e so quando da para ter certeza.
     *
     * O corte no ponto medio existe porque o painel da direita escreve tudo
     * numa linha: "Kikoff San Francisco, CA - Reposted 2 days ago - Over 100
     * people clicked apply". Sem o corte, isso inteiro virava o nome da
     * empresa.
     *
     * `local` nao e enviado de proposito, e a omissao e a decisao. Nao ha linha
     * confiavel para ele -- no card da lista a posicao dele e ocupada pelo
     * "You'd be a top applicant" -- e local errado e PIOR que ausente: ele
     * alimenta o calculo de distancia do pre-filtro, que penaliza 25 pontos por
     * estar fora do raio. Ausente, a avaliacao geografica simplesmente nao
     * acontece, que e o padrao seguro. */
    var empresa = null;
    for (var i = 0; i < linhas.length; i++) {
      var candidata = linhas[i].split("·")[0].trim();
      if (candidata && candidata.length <= 100 && !NAO_E_EMPRESA.test(candidata)) {
        empresa = candidata;
        break;
      }
    }

    return {
      titulo: titulo,
      empresa: empresa,
      url: "https://www.linkedin.com/jobs/view/" + id + "/"
    };
  }

  function cards() {
    var achados = Object.create(null);
    var alvos = document.querySelectorAll(SELETOR_DE_VAGA);
    for (var i = 0; i < alvos.length; i++) {
      var bruto = idDoElemento(alvos[i]);
      if (!bruto) continue;
      var caixa = cardDoElemento(alvos[i]);
      if (!caixa) continue;
      var id = "li-" + bruto;
      var texto = (caixa.innerText || "").replace(/\s+/g, " ");
      // O card MAIOR vence, e a escolha acompanha o criterio acima: todos os
      // candidatos aqui contem uma vaga so, entao o mais alto e o que reune
      // titulo, empresa e aviso -- e nao um pedaco do proprio card.
      if (achados[id] && texto.length <= achados[id].texto.length) continue;
      var linhas = linhasDe(caixa);
      achados[id] = {
        texto: texto,
        vaga: metadados(caixa, bruto, linhas[0] || "")
      };
    }
    return achados;
  }

  // ------------------------------------------------------------- envio
  /* O script sobrevive a propria extensao, e precisa perceber isso.
   *
   * Recarregar a extensao nao mata as instancias que ja estavam em abas
   * abertas: elas continuam rodando, mas perdem a ligacao com o runtime, e toda
   * chamada a `chrome.storage` passa a lancar "Extension context invalidated".
   * Com um observador ligado e um teto de tempo, isso vira erro a cada cinco
   * segundos, para sempre.
   *
   * A cura de verdade e recarregar a aba -- mas o codigo velho nao pode gritar
   * enquanto ela nao e recarregada. Ele desliga o observador e sai de cena. */
  var observador = null;
  var aposentado = false;

  function contextoVivo() {
    try { return !!(chrome.runtime && chrome.runtime.id); } catch (e) { return false; }
  }

  function aposentar() {
    if (aposentado) return;
    aposentado = true;
    if (observador) { try { observador.disconnect(); } catch (e) {} }
    if (temporizador) { clearTimeout(temporizador); temporizador = null; }
  }

  /* Toda conversa com o runtime passa por aqui. Uma so guarda em vez de um
   * try/catch por chamada: sao quatro pontos de acesso e esquecer um traria o
   * erro de volta sem aviso. */
  function guardar(valores) {
    if (aposentado || !contextoVivo()) { aposentar(); return; }
    try { chrome.storage.local.set(valores); } catch (e) { aposentar(); }
  }

  function configurar(callback) {
    if (aposentado || !contextoVivo()) { aposentar(); return; }
    try {
      chrome.storage.local.get(["token", "app"], function (guardado) {
        if (chrome.runtime && chrome.runtime.lastError) { aposentar(); return; }
        callback(guardado || {});
      });
    } catch (e) {
      aposentar();
    }
  }

  /* Endereco sem barra final.
   *
   * `http://127.0.0.1:8000/` + `/insights/lote` vira `//insights/lote`, e o
   * roteador responde 404 -- um erro que parece "a rota nao existe" quando na
   * verdade e "voce digitou uma barra a mais". Normalizar aqui e no lugar onde
   * o valor e salvo: quem cola um endereco copiado do navegador cola com a
   * barra, e isso nao pode custar uma tarde de depuracao. */
  function base(cfg) {
    return String(cfg.app || APP_PADRAO).trim().replace(/\/+$/, "");
  }

  function enviar(lote, cfg) {
    var cabecalhos = { "content-type": "application/json" };
    cabecalhos["x-crivo-extrator"] = cfg.token;
    fetch(base(cfg) + "/insights/lote", {
      method: "POST",
      headers: cabecalhos,
      body: JSON.stringify({ vagas: lote })
    })
      .then(function (r) {
        return r.json().then(function (j) { return [r.ok, j]; });
      })
      .then(function (par) {
        if (!par[0]) {
          // Marca a falha para a janelinha da extensao mostrar. Um erro que so
          // existe no console de uma aba do LinkedIn e um erro invisivel.
          guardar({
            ultimo_erro: (par[1] && par[1].erro) || "recusado pelo app",
            ultimo_erro_em: new Date().toISOString()
          });
          return;
        }
        var gravadas = Object.keys(par[1].gravadas || {});
        gravadas.forEach(function (id) { enviados[id] = true; });
        var destaque = lote.filter(function (v) {
          return v.sinais.indexOf("top_applicant") >= 0 &&
            gravadas.indexOf(v.job_id) >= 0;
        }).length;
        if (aposentado || !contextoVivo()) { aposentar(); return; }
        try {
          chrome.storage.local.get(["total", "destaques"], function (g) {
            g = g || {};
            guardar({
              total: (g.total || 0) + gravadas.length,
              destaques: (g.destaques || 0) + destaque,
              ultimo_envio_em: new Date().toISOString(),
              ultimo_erro: null
            });
          });
        } catch (e) { aposentar(); }
      })
      .catch(function (e) {
        guardar({
          ultimo_erro: "nao respondeu em " + base(cfg) + " (" + e.message + ")",
          ultimo_erro_em: new Date().toISOString()
        });
      });
  }

  function varrer() {
    configurar(function (cfg) {
      var doDom = cards();
      var ids = Object.keys(doDom);
      var comSinal = 0;
      var lote = [];

      ids.forEach(function (id) {
        var achado = doDom[id];
        var leitura = ler(achado.texto);
        if (vazia(leitura)) return;
        comSinal++;
        var assinatura = id + ":" + leitura.sinais.join(",") + ":" +
          leitura.candidatos;
        if (enviados[assinatura]) return;
        enviados[assinatura] = true;
        leitura.job_id = id;
        // Vai junto para que o servidor consiga criar a vaga quando ela ainda
        // nao existir. Ele ignora este campo quando ja conhece o id.
        leitura.vaga = achado.vaga;
        lote.push(leitura);
      });

      /* O diario da varredura, gravado SEMPRE -- inclusive quando nada e
       * enviado, que e justamente o caso que precisava de explicacao.
       *
       * Sem ele, "0 vagas gravadas" tem quatro causas com a mesma cara: o
       * script nao rodou, rodou e nao achou card, achou card e nenhum trazia
       * aviso, ou achou tudo e o envio falhou. Um carimbo de hora mais duas
       * contagens separam as quatro sem abrir o console. */
      guardar({
        ultima_varredura_em: new Date().toISOString(),
        ultima_url: location.pathname,
        cards_vistos: ids.length,
        cards_com_sinal: comSinal,
        sem_token: !cfg.token
      });

      if (!cfg.token) return;          // sem token nao ha o que tentar
      if (lote.length) enviar(lote, cfg);
    });
  }

  /* Repouso com teto, e o teto e o que faz a coisa funcionar.
   *
   * A primeira versao era um debounce puro: cada mutacao cancelava o
   * temporizador e reagendava. Isso pressupoe que a pagina fica quieta em algum
   * momento -- e o LinkedIn nao fica. Imagem que carrega, indicador de digitacao,
   * contador de notificacao, card que entra e sai da tela: as mutacoes chegam
   * mais juntas que o repouso de 1,2s, entao o temporizador era eternamente
   * adiado e a varredura NUNCA rodava. Nao havia erro para mostrar, porque nada
   * chegou a ser tentado.
   *
   * O teto garante progresso: por mais agitada que a pagina esteja, passou o
   * limite, varre. O repouso continua valendo para o caso normal, em que a
   * lista se monta e sossega. */
  var ULTIMA_VARREDURA = 0;
  var TETO_MS = 5000;

  function agendar() {
    var desde = Date.now() - ULTIMA_VARREDURA;
    if (desde >= TETO_MS) { disparar(); return; }
    if (temporizador) clearTimeout(temporizador);
    temporizador = setTimeout(disparar, Math.min(REPOUSO_MS, TETO_MS - desde));
  }

  function disparar() {
    if (aposentado) return;
    if (temporizador) { clearTimeout(temporizador); temporizador = null; }
    ULTIMA_VARREDURA = Date.now();
    varrer();
  }

  // O observador cobre as duas coisas que um clique nao cobre: a rolagem, que
  // monta card novo, e a troca de busca, que troca a lista inteira sem
  // recarregar a pagina.
  observador = new MutationObserver(agendar);
  observador.observe(document.body, { childList: true, subtree: true });
  agendar();
})();
