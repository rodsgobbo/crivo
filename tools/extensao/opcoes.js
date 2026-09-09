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
                "ultima_url", "cards_vistos", "cards_com_sinal", "sem_token"];

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

  function diagnostico(g) {
    if (!g.token) return ["Sem token. Cole o token de /extensao e salve.", true];
    if (!g.ultima_varredura_em) {
      return [
        "O script nunca rodou nesta máquina.\n\n" +
        "Recarregue a extensão em chrome://extensions e DEPOIS recarregue a " +
        "aba do LinkedIn (F5). Aba já aberta não recebe o script.",
        true
      ];
    }
    if (!g.cards_vistos) {
      return [
        "Rodou em " + local(g.ultima_varredura_em) + " (" +
        (g.ultima_url || "?") + ") mas não encontrou nenhum card de vaga.\n\n" +
        "Abra uma lista de vagas e role a página.",
        true
      ];
    }
    if (!g.cards_com_sinal) {
      return [
        "Viu " + g.cards_vistos + " cards, nenhum com aviso reconhecível.\n\n" +
        "Ou essas vagas não mostram os avisos, ou a marcação mudou.",
        true
      ];
    }
    return [null, false];
  }

  function pintar(g) {
    var caixa = document.getElementById("estado");
    caixa.className = "estado";

    if (g.ultimo_erro) {
      caixa.className = "estado erro";
      caixa.textContent = "Erro em " + local(g.ultimo_erro_em) + ":\n" +
        g.ultimo_erro;
      return;
    }

    var par = diagnostico(g);
    if (par[0]) {
      caixa.className = par[1] ? "estado erro" : "estado";
      caixa.textContent = par[0];
      return;
    }

    caixa.innerHTML =
      '<div><span class="numero">' + (g.total || 0) + "</span> vagas gravadas</div>" +
      '<div><span class="numero">' + (g.destaques || 0) +
      "</span> com “top applicant”</div>" +
      '<div style="margin-top:.4rem;opacity:.75">último envio: ' +
      local(g.ultimo_envio_em) + "<br>última varredura: " +
      local(g.ultima_varredura_em) + " — " + (g.cards_vistos || 0) +
      " cards, " + (g.cards_com_sinal || 0) + " com aviso</div>";
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
