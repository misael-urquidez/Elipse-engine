window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;
  const $ = Elipse.$;

  function showBanner(el, message){
    el.textContent = message;
    el.classList.toggle("show", !!message);
  }

  let chatHasMessages = false;

  function clearEmptyState(log){
    if(!chatHasMessages){ log.innerHTML = ""; chatHasMessages = true; }
  }

  function appendMsg(log, role, text, meta){
    clearEmptyState(log);
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.textContent = text;
    if(meta){
      const m = document.createElement("span");
      m.className = "meta"; m.textContent = meta;
      div.appendChild(m);
    }
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  async function sendChat(){
    const input = $("#chatInput");
    const log = $("#chatLog");
    const sendBtn = $("#chatSendBtn");
    const banner = $("#chatBanner");

    const text = input.value.trim();
    if(!text) return;

    showBanner(banner, "");
    input.value = "";
    appendMsg(log, "user", text);
    const pending = appendMsg(log, "assistant", "pensando...");
    sendBtn.disabled = true;

    try{
      const result = await Elipse.api.runTask("/v1/chat", { message: text });
      if(result.status === "error"){
        pending.remove();
        showBanner(banner, result.result?.detail || result.result?.reply || "Ocurrió un error procesando el mensaje.");
      }else if(result.status === "esperando_confirmacion"){
        pending.textContent = result.result.reply;
      }else{
        const r = result.result;
        pending.textContent = r.reply;
        const m = document.createElement("span");
        m.className = "meta";
        m.textContent = r.provider + (r.used_tools ? " · usó herramientas" : "");
        pending.appendChild(m);
      }
    }catch(e){
      pending.remove();
      showBanner(banner, e.message);
    }finally{
      sendBtn.disabled = false;
      // La respuesta reemplaza al "pensando..." y cambia la altura: bajar al final.
      log.scrollTop = log.scrollHeight;
      input.focus();
    }
  }

  function init(){
    const sendBtn = $("#chatSendBtn");
    const input = $("#chatInput");
    sendBtn.addEventListener("click", sendChat);
    input.addEventListener("keydown", (e) => {
      if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); sendChat(); }
    });
  }

  Elipse.chat = { init };
})();