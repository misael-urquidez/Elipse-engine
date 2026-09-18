window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;
  const $ = Elipse.$;

  function showBanner(el, message){
    el.textContent = message;
    el.classList.toggle("show", !!message);
  }

  async function runResearch(){
    const btn = $("#researchBtn");
    const input = $("#researchInput");
    const banner = $("#researchBanner");
    const topic = input.value.trim();
    if(!topic) return;

    showBanner(banner, "");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Investigando...';

    try{
      const result = await Elipse.api.runTask("/v1/research", { topic });
      if(result.status === "error" || result.result?.status === "error"){
        showBanner(banner, result.result?.detail || "No se pudo completar la investigación.");
      }else{
        const r = result.result;
        const card = document.createElement("div");
        card.className = "result-card";
        const sources = (r.sources || []).map(s => `<li><a href="${s}" target="_blank" rel="noopener">${s}</a></li>`).join("");
        card.innerHTML = `
          <h3>${r.topic}</h3>
          <p>${r.summary}</p>
          ${sources ? `<ul class="src-list">${sources}</ul>` : ""}
        `;
        $("#researchResults").prepend(card);
        input.value = "";
      }
    }catch(e){
      showBanner(banner, e.message);
    }finally{
      btn.disabled = false;
      btn.textContent = "Investigar";
    }
  }

  function init(){
    $("#researchBtn").addEventListener("click", runResearch);
    $("#researchInput").addEventListener("keydown", (e) => { if(e.key === "Enter") runResearch(); });
  }

  Elipse.research = { init };
})();
