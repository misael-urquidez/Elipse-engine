window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;
  const $ = Elipse.$;

  function showBanner(el, message){
    el.textContent = message;
    el.classList.toggle("show", !!message);
  }

  async function load(){
    const banner = $("#keysBanner");
    const body = $("#keysTableBody");
    showBanner(banner, "");
    try{
      const data = await Elipse.api.apiFetch("/v1/auth/keys", { headers: Elipse.api.authHeaders() });
      body.innerHTML = "";
      data.keys.forEach(k => {
        const tr = document.createElement("tr");
        if(k.revoked) tr.className = "revoked";
        tr.innerHTML = `
          <td>${k.name}</td>
          <td>${k.revoked ? "revocada" : "activa"}</td>
          <td>${k.created_at || "—"}</td>
          <td>${k.last_used_at || "nunca"}</td>
          <td>${k.revoked ? "" : `<button class="revoke-btn" data-id="${k.id}">Revocar</button>`}</td>
        `;
        body.appendChild(tr);
      });
      body.querySelectorAll(".revoke-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          if(!confirm("¿Revocar esta llave? El dispositivo que la use dejará de tener acceso.")) return;
          try{
            await Elipse.api.apiFetch("/v1/auth/keys/" + btn.dataset.id + "/revoke", {
              method: "POST", headers: Elipse.api.authHeaders(),
            });
            load();
          }catch(e){
            showBanner(banner, e.message);
          }
        });
      });
    }catch(e){
      showBanner(banner, e.message);
    }
  }

  async function createKey(){
    const banner = $("#keysBanner");
    const name = $("#newKeyName").value.trim();
    if(!name) return;
    showBanner(banner, "");
    try{
      const data = await Elipse.api.apiFetch("/v1/auth/keys", {
        method: "POST",
        headers: Elipse.api.authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ name }),
      });
      $("#newKeyValue").textContent = data.api_key;
      $("#newKeyBox").classList.add("show");
      $("#newKeyName").value = "";
      load();
    }catch(e){
      showBanner(banner, e.message);
    }
  }

  function init(){
    $("#createKeyBtn").addEventListener("click", createKey);
    $("#copyKeyBtn").addEventListener("click", () => {
      navigator.clipboard.writeText($("#newKeyValue").textContent);
      const btn = $("#copyKeyBtn");
      btn.textContent = "Copiado";
      setTimeout(() => { btn.textContent = "Copiar"; }, 1500);
    });
  }

  Elipse.keys = { init, load };
})();
