/* Janelinha da extensao: guarda o token, mostra se ela esta funcionando e diz
 * ONDE parou quando nao esta.
 *
 * O modo de falhar desta extensao e o silencio -- ela roda dentro de outra
 * pagina, sem nada visivel. E "0 vagas gravadas" tem quatro causas com a mesma
 * cara: o script nao rodou, rodou e nao achou card, achou card e nenhum trazia
 * aviso, ou achou tudo e o envio falhou. Cada uma pede uma acao diferente, e
 * adivinhar qual delas e custa uma rodada de tentativa e erro.
 *
 * Por isso a janelinha mostra o diario da ultima varredura, e nao so o total.
 */
(function () {
  "use strict";

  var campos = ["token", "app", "total", "destaques", "ultimo_envio_em",
                "ultimo_erro", "ultimo_erro_em", "ultima_varredura_em",
                "ultima_url", "cards_vistos", "cards_com_sinal", "sem_token",
                "cards_vazios", "amostra"];

  var APP_PADRAO = "http://localhost:8000";

  /* Endereco sem barra final. `http://127.0.0.1:8000/` + `/insights/lote` vira
   * `//insights/lote`, e o roteador responde 404 -- um erro que parece "a rota
   * nao existe" quando e "voce digitou uma barra a mais". Quem cola o endereco
   * copiado da barra do navegador cola com a barra. */
  function normalizar(valor) {
    return (String(valor || "").trim() || APP_PADRAO).replace(/\/+$/, "");
  }

  function local(iso) {
    if (!iso) return "nunca";
    try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
  }

  /* O que dizer sobre a ULTIMA varredura, e se aquilo e problema.
   *
   * "Problema" aqui nao e o mesmo que "nao achou aviso agora". Uma varredura
   * roda a cada rolagem, e a lista do LinkedIn e virtualizada: passar por uma
   * tela sem nenhum aviso e normal. O que e problema mesmo e nunca ter gravado
   * nada -- por isso o total entra na conta. */
  function ultimaVarredura(g) {
    if (!g.token) return ["Sem token. Cole o token de /extensao e salve.", true];
    if (!g.ultima_varredura_em) {
      return [
        "O script nunca rodou nesta máquina.\n\n" +
        "Recarregue a extensão em chrome://extensions e DEPOIS recarregue a " +
        "aba do LinkedIn (F5). Aba já aberta não recebe o script.",
        true
      ];
    }

    var vazios = g.cards_vazios || 0;
    var legiveis = Math.max(0, (g.cards_vistos || 0) - vazios);
    var quando = local(g.ultima_varredura_em) + " em " + (g.ultima_url || "?");
    var nunca_gravou = !(g.total > 0);

    if (!g.cards_vistos) {
      return [
        "Última varredura: " + quando + ", sem nenhum card de vaga.\n" +
        "Abra uma lista de vagas e role a página.",
        nunca_gravou
      ];
    }
    if (!legiveis) {
      return [
        "Última varredura: " + quando + ".\n" + g.cards_vistos +
        " cards, todos sem texto — é a lista virtualizada, que só monta o que " +
        "está na tela. Role a página devagar.",
        nunca_gravou
      ];
    }
    if (!g.cards_com_sinal) {
      return [
        "Última varredura: " + quando + ".\n" + legiveis + " cards lidos" +
        (vazios ? " (" + vazios + " fora da tela)" : "") +
        ", nenhum com aviso reconhecível.",
        nunca_gravou
      ];
    }
    return [
      "Última varredura: " + quando + ".\n" + legiveis + " cards lidos" +
      (vazios ? " (" + vazios + " fora da tela)" : "") + ", " +
      g.cards_com_sinal + " com aviso.",
      false
    ];
  }

  function linha(texto, classe) {
    var div = document.createElement("div");
    if (classe) div.className = classe;
    div.textContent = texto;
    return div;
  }

  function pintar(g) {
    var caixa = document.getElementById("estado");
    caixa.className = "estado";
    caixa.textContent = "";

    if (g.ultimo_erro) {
      caixa.className = "estado erro";
      caixa.textContent = "Erro em " + local(g.ultimo_erro_em) + ":\n" +
        g.ultimo_erro;
      return;
    }

    // O acumulado vem primeiro, e existe mesmo quando a varredura de agora nao
    // achou nada. A versao anterior escondia "28 vagas gravadas" atras de
    // "nenhum aviso nesta tela", e quem lia concluia que nada funcionava.
    if (g.total > 0) {
      var n = document.createElement("div");
      var forte = document.createElement("span");
      forte.className = "numero";
      forte.textContent = String(g.total);
      n.appendChild(forte);
      n.appendChild(document.createTextNode(" vagas gravadas · " +
        (g.destaques || 0) + " com “top applicant”"));
      caixa.appendChild(n);
      caixa.appendChild(linha("último envio: " + local(g.ultimo_envio_em),
                              "discreto"));
    }

    var par = ultimaVarredura(g);
    if (par[1]) caixa.className = "estado erro";
    caixa.appendChild(linha(par[0], "discreto"));

    /* A amostra e texto do LinkedIn, escrito por terceiros. Vai por
     * `textContent` e nunca por `innerHTML`: a janelinha nao executa o que le
     * de uma pagina que nao controla. */
    if (g.amostra) {
      caixa.appendChild(linha("o que ela leu do primeiro card:", "discreto"));
      caixa.appendChild(linha(g.amostra, "amostra"));
    }
  }

  function recarregar() {
    chrome.storage.local.get(campos, function (g) { pintar(g || {}); });
  }

  chrome.storage.local.get(campos, function (g) {
    g = g || {};
    document.getElementById("token").value = g.token || "";
    document.getElementById("app").value = g.app || APP_PADRAO;
    pintar(g);
  });

  document.getElementById("salvar").addEventListener("click", function () {
    var token = document.getElementById("token").value.trim();
    var app = normalizar(document.getElementById("app").value);
    document.getElementById("app").value = app;
    // Zera os contadores junto: eles contam o que aquele token mandou, e
    // mante-los depois de trocar a credencial faria o numero descrever uma
    // configuracao que nao existe mais.
    chrome.storage.local.set({
      token: token, app: app, total: 0, destaques: 0,
      ultimo_erro: null, ultimo_envio_em: null
    }, recarregar);
  });

  /* Testa token e conectividade sem depender do DOM do LinkedIn.
   *
   * Manda uma vaga que nao existe. O servidor responde 200 com ela em
   * `ignoradas` -- prova que autenticou --, 401 se o token nao vale, e nada se
   * o app estiver fora do ar. Tres desfechos distintos, e nenhum grava dado. */
  document.getElementById("testar").addEventListener("click", function () {
    var caixa = document.getElementById("estado");
    var token = document.getElementById("token").value.trim();
    var app = normalizar(document.getElementById("app").value);
    caixa.className = "estado";
    caixa.textContent = "testando…";

    var cabecalhos = { "content-type": "application/json" };
    cabecalhos["x-crivo-extrator"] = token;
    fetch(app + "/insights/lote", {
      method: "POST",
      headers: cabecalhos,
      body: JSON.stringify({
        vagas: [{ job_id: "li-teste-de-conexao", sinais: ["top_applicant"] }]
      })
    })
      .then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); })
      .then(function (par) {
        if (par[0] === 200) {
          caixa.textContent =
            "Token e conexão OK.\n\nO app respondeu e reconheceu a credencial. " +
            "Se mesmo assim nada é gravado, o problema está na leitura da " +
            "página — recarregue a aba do LinkedIn.";
          return;
        }
        caixa.className = "estado erro";
        caixa.textContent = "O app respondeu " + par[0] + ":\n" +
          ((par[1] && par[1].erro) || "sem detalhe");
      })
      .catch(function (e) {
        caixa.className = "estado erro";
        caixa.textContent = "Não consegui falar com " + app + "\n(" +
          e.message + ")\n\nO crivo está rodando?";
      });
  });
})();
