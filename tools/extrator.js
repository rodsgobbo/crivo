/* Extrator de insights de vaga, para rodar no navegador do proprio usuario.
 *
 * Roda como bookmarklet numa pagina do LinkedIn que voce ja esta vendo logado.
 * Le o que a sua conta enxerga e manda para o app local.
 *
 * Tem dois modos, e ele escolhe sozinho pelo que a pagina mostra:
 *
 *   lista  -- a pagina de resultados ou de vagas salvas. Varre todos os cards
 *             visiveis de uma vez. E o modo que importa para "You'd be a top
 *             applicant": esse aviso aparece no proprio card, um run traz perto
 *             de cem vagas, e uma por vez seria uma aba por vaga.
 *   vaga   -- a pagina de uma vaga so. Le tambem a contagem de candidatos e a
 *             distribuicao de senioridade, que o card da lista nao mostra.
 *
 * Role a lista ate o fim antes de acionar: o LinkedIn so monta o card quando
 * ele entra na tela, e o que nao foi montado nao tem texto para ler.
 *
 * O que ele NAO faz, e por que isso importa: ele nao le, nao copia e nao envia
 * cookie, token ou qualquer credencial. O app recebe numeros ja extraidos --
 * o resultado da leitura, nunca o meio de refazer a leitura sem voce.
 *
 * Sobre os seletores: o LinkedIn nao publica contrato de marcacao, e a parte
 * Premium desta pagina so aparece para quem tem Premium. Por isso a leitura e
 * por padrao de texto e nao por classe CSS -- classe muda a cada deploy, o
 * texto "Mais de 200 candidaturas" e mais estavel. Quando nada casa, o extrator
 * diz o que viu em vez de falhar em silencio.
 */
(function () {
  var APP = window.CRIVO_URL || "http://localhost:8000";

  function idDaVaga() {
    var busca = new URLSearchParams(location.search).get("currentJobId");
    if (busca) return "li-" + busca;
    var caminho = location.pathname.match(/\/jobs\/view\/(\d+)/);
    return caminho ? "li-" + caminho[1] : null;
  }

  function textoVisivel() {
    var alvo =
      document.querySelector(".jobs-details__main-content") ||
      document.querySelector(".jobs-search__job-details") ||
      document.body;
    return (alvo.innerText || "").replace(/\s+/g, " ");
  }

  /* "Mais de 200 candidaturas", "87 candidatos", "Over 200 applicants". */
  function contagem(texto) {
    var piso = texto.match(
      /(?:mais de|over)\s+([\d.,]+)\s*(?:candidatura|candidato|applicant|pessoa)/i
    );
    if (piso) return parseInt(piso[1].replace(/[.,]/g, ""), 10);
    var exato = texto.match(
      /\b([\d.,]+)\s*(?:candidatura|candidato|applicant)/i
    );
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
      /* O grupo nao-capturante e obrigatorio: todas as faixas sao alternacoes,
       * e sem ele `coordena|lead\b` + a contagem vira "coordena" OU
       * "lead\b<contagem>". O primeiro ramo casa sozinho, sem grupo 1, e a
       * contagem sai NaN -- e era isso que acontecia com o quadro Premium
       * inteiro: nenhuma distribuicao de senioridade chegou a ser gravada. */
      var re = new RegExp("(?:" + par[1].source + ")[^\\d]{0,24}(\\d{1,6})", "i");
      var m = texto.match(re);
      var contagem = m ? parseInt(m[1], 10) : NaN;
      if (!isNaN(contagem)) achado[par[0]] = contagem;
    });
    return achado;
  }

  function sinais(texto) {
    var achados = [];
    if (/top applicant|principais candidatos|candidato de destaque/i.test(texto))
      achados.push("top_applicant");
    if (/seja um dos \d+ primeiros|be among the first/i.test(texto))
      achados.push("early_applicant");
    if (/(?:mais de|over)\s+[\d.,]+\s*(?:candidatura|candidato|applicant)/i.test(texto))
      achados.push("muitos_candidatos");
    return achados;
  }

  function avisar(mensagem, cor) {
    var caixa = document.createElement("div");
    caixa.textContent = mensagem;
    caixa.style.cssText =
      "position:fixed;z-index:2147483647;top:16px;right:16px;max-width:380px;" +
      "padding:14px 16px;border-radius:8px;font:14px/1.45 system-ui,sans-serif;" +
      "color:#fff;white-space:pre-wrap;box-shadow:0 6px 24px rgba(0,0,0,.3);" +
      "background:" + cor;
    document.body.appendChild(caixa);
    setTimeout(function () { caixa.remove(); }, 9000);
  }

  function ler(texto) {
    return {
      candidatos: contagem(texto),
      senioridade: senioridade(texto),
      sinais: sinais(texto)
    };
  }

  function vazia(leitura) {
    return leitura.candidatos === null && !leitura.sinais.length &&
      !Object.keys(leitura.senioridade).length;
  }

  /* Os cards da lista, por id de vaga.
   *
   * A busca comeca pelo link da vaga porque ele e a unica parte do card que o
   * LinkedIn nao pode renomear sem quebrar o proprio site. Classe CSS muda a
   * cada deploy; `/jobs/view/<id>` e o endereco publico da vaga.
   */
  function cards() {
    var achados = {};
    var links = document.querySelectorAll('a[href*="/jobs/view/"]');
    for (var i = 0; i < links.length; i++) {
      var m = links[i].getAttribute("href").match(/\/jobs\/view\/(\d+)/);
      if (!m) continue;
      var caixa = links[i].closest("li") || links[i].parentElement;
      if (!caixa) continue;
      var id = "li-" + m[1];
      var texto = (caixa.innerText || "").replace(/\s+/g, " ");
      // O card menor vence quando o mesmo link aparece aninhado: o texto de um
      // <li> que embrulha a lista inteira traria o sinal do vizinho.
      if (!achados[id] || texto.length < achados[id].length) achados[id] = texto;
    }
    return achados;
  }

  function enviarLote(leituras) {
    fetch(APP + "/insights/lote", {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ vagas: leituras })
    })
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (par) {
        if (!par[0]) { avisar("Recusado: " + (par[1].erro || "erro"), "#b4232a"); return; }
        var gravadas = Object.keys(par[1].gravadas || {});
        var destaque = leituras.filter(function (v) {
          return v.sinais.indexOf("top_applicant") >= 0 &&
            gravadas.indexOf(v.job_id) >= 0;
        }).length;
        avisar(
          gravadas.length + " de " + leituras.length + " vagas gravadas\n" +
          destaque + " com \"top applicant\"\n" +
          (par[1].ignoradas || []).length + " ignoradas (nao coletadas por voce)",
          gravadas.length ? "#1c6b3f" : "#8a6d1f"
        );
      })
      .catch(function (e) {
        avisar("App local nao respondeu em " + APP + "\n(" + e.message + ")", "#b4232a");
      });
  }

  /* Modo lista: so quando ha mais de um card. Uma pagina de vaga tambem casa o
   * seletor de link -- pelas vagas parecidas do rodape --, e trata-la como
   * lista jogaria fora a contagem e a senioridade que so ela tem. */
  var doDom = cards();
  var ids = Object.keys(doDom);
  if (ids.length > 1) {
    var lote = [];
    for (var k = 0; k < ids.length; k++) {
      var leitura = ler(doDom[ids[k]]);
      if (vazia(leitura)) continue;
      leitura.job_id = ids[k];
      lote.push(leitura);
    }
    if (!lote.length) {
      avisar(
        "Nenhum dos " + ids.length + " cards trouxe insight.\n\nOu a lista nao " +
        "mostra esses avisos, ou a marcacao mudou.\nRole a lista ate o fim antes " +
        "de acionar.", "#8a6d1f"
      );
      return;
    }
    enviarLote(lote);
    return;
  }

  var jobId = idDaVaga();
  if (!jobId) {
    avisar("Nao consegui identificar a vaga nesta pagina.\nAbra uma vaga especifica.", "#b4232a");
    return;
  }

  var texto = textoVisivel();
  var corpo = ler(texto);

  if (vazia(corpo)) {
    avisar(
      "Nada reconhecido nesta pagina.\n\nOu a vaga nao mostra esses dados, ou a " +
      "marcacao mudou.\nTrecho lido:\n\n" + texto.slice(0, 220),
      "#8a6d1f"
    );
    return;
  }

  fetch(APP + "/insights/" + jobId, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(corpo)
  })
    .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
    .then(function (par) {
      if (!par[0]) { avisar("Recusado: " + (par[1].erro || "erro"), "#b4232a"); return; }
      avisar(jobId + " gravada\n" + JSON.stringify(par[1].gravado, null, 1), "#1c6b3f");
    })
    .catch(function (e) {
      avisar("App local nao respondeu em " + APP + "\n(" + e.message + ")", "#b4232a");
    });
})();
