window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;
  const $ = Elipse.$;

  function showBanner(el, message){
    el.textContent = message;
    el.classList.toggle("show", !!message);
  }

  async function runMemorySearch(){
    const q = $("#memoryInput").value.trim();
    if(!q) return;
    const banner = $("#memoryBanner");
    const box = $("#memoryResults");
    showBanner(banner, "");
    box.innerHTML = "";
    try{
      const data = await Elipse.api.apiFetch(
        "/v1/memory/semantic/search?q=" + encodeURIComponent(q) + "&n=6",
        { headers: Elipse.api.authHeaders() }
      );
      if(!data.results.length){
        box.innerHTML = '<div class="empty">Sin resultados para esa búsqueda.</div>';
        return;
      }
      data.results.forEach(hit => {
        const type = hit.metadata?.type || "otro";
        const card = document.createElement("div");
        card.className = "result-card";
        card.innerHTML = `
          <span class="badge ${type}">${type}</span>
          <span class="dist">distancia: ${hit.distance.toFixed(3)}</span>
          <p style="margin-top:8px">${hit.content}</p>
        `;
        box.appendChild(card);
      });
    }catch(e){
      showBanner(banner, e.message);
    }
  }

  function init(){
    $("#memoryBtn").addEventListener("click", runMemorySearch);
    $("#memoryInput").addEventListener("keydown", (e) => { if(e.key === "Enter") runMemorySearch(); });
  }

  Elipse.memory = { init };
})();
