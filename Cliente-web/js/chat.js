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

  function escapeHtml(s){
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function langLabel(lang){
    if(!lang) return "texto";
    const map = {
      js: "JavaScript", javascript: "JavaScript", ts: "TypeScript", typescript: "TypeScript",
      py: "Python", python: "Python", html: "HTML", css: "CSS", json: "JSON",
      md: "Markdown", markdown: "Markdown", sh: "Shell", bash: "Shell", shell: "Shell",
      sql: "SQL", rs: "Rust", rust: "Rust", go: "Go", java: "Java", c: "C", cpp: "C++",
      txt: "texto", text: "texto", plain: "texto"
    };
    return map[lang.toLowerCase()] || lang;
  }

  function extFor(lang){
    if(!lang) return "txt";
    const map = {
      javascript: "js", js: "js", typescript: "ts", ts: "ts",
      python: "py", py: "py", markdown: "md", md: "md",
      shell: "sh", bash: "sh", sh: "sh", plaintext: "txt", text: "txt", plain: "txt"
    };
    return map[lang.toLowerCase()] || lang.toLowerCase().replace(/[^a-z0-9]/g, "") || "txt";
  }

  function copyText(text, btn){
    const done = () => {
      if(!btn) return;
      const prev = btn.textContent;
      btn.textContent = "Copiado";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = prev; btn.classList.remove("copied"); }, 1400);
    };
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    }else{
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done){
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try{ document.execCommand("copy"); done(); }catch(_){}
    document.body.removeChild(ta);
  }

  function downloadText(text, filename){
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "archivo.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---------- Inline markdown (negrita, cursiva, código, enlaces) ----------
  function inlineMd(text){
    let s = escapeHtml(text);
    // código inline `...`
    s = s.replace(/`([^`]+)`/g, "<code class=\"md-inline-code\">$1</code>");
    // negrita **...** o __...__
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    // cursiva *...* o _..._ (evitar overlap con negrita ya procesada)
    s = s.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_])_([^_]+)_(?!_)/g, "$1<em>$2</em>");
    // enlaces [texto](url)
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    // <br> literales que a veces mete el modelo
    s = s.replace(/&lt;br\s*\/?&gt;/gi, "<br>");
    return s;
  }

  function isTableSep(line){
    // |---|:---|---:| etc.
    return /^\|?[\s:|-]+\|[\s:|-]*\|?$/.test(line.trim()) && /---/.test(line);
  }

  function isTableRow(line){
    const t = line.trim();
    return t.includes("|") && !isTableSep(t);
  }

  function splitCells(line){
    let s = line.trim();
    if(s.startsWith("|")) s = s.slice(1);
    if(s.endsWith("|")) s = s.slice(0, -1);
    return s.split("|").map((c) => c.trim());
  }

  function buildTable(rows){
    // rows[0] = header, rows[1] = separator (ignorado), rows[2..] = body
    const wrap = document.createElement("div");
    wrap.className = "md-table-wrap";
    const table = document.createElement("table");
    table.className = "md-table";

    if(rows.length){
      const thead = document.createElement("thead");
      const tr = document.createElement("tr");
      splitCells(rows[0]).forEach((cell) => {
        const th = document.createElement("th");
        th.innerHTML = inlineMd(cell);
        tr.appendChild(th);
      });
      thead.appendChild(tr);
      table.appendChild(thead);
    }

    const bodyRows = rows.slice(1).filter((r) => !isTableSep(r));
    if(bodyRows.length){
      const tbody = document.createElement("tbody");
      bodyRows.forEach((row) => {
        const tr = document.createElement("tr");
        splitCells(row).forEach((cell) => {
          const td = document.createElement("td");
          td.innerHTML = inlineMd(cell);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
    }

    wrap.appendChild(table);
    return wrap;
  }

  /** Renderiza un bloque de markdown (sin code fences) a nodos DOM. */
  function renderMarkdownBlock(text){
    const frag = document.createDocumentFragment();
    if(!text || !text.trim()) return frag;

    const lines = text.replace(/\r\n/g, "\n").split("\n");
    let i = 0;
    let paraBuf = [];

    function flushPara(){
      if(!paraBuf.length) return;
      const raw = paraBuf.join("\n").trim();
      paraBuf = [];
      if(!raw) return;
      const p = document.createElement("div");
      p.className = "msg-text";
      p.innerHTML = inlineMd(raw);
      frag.appendChild(p);
    }

    while(i < lines.length){
      const line = lines[i];
      const trimmed = line.trim();

      // línea en blanco → cortar párrafo
      if(!trimmed){
        flushPara();
        i++;
        continue;
      }

      // tabla markdown
      if(isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])){
        flushPara();
        const tableLines = [line, lines[i + 1]];
        i += 2;
        while(i < lines.length && isTableRow(lines[i])){
          tableLines.push(lines[i]);
          i++;
        }
        frag.appendChild(buildTable(tableLines));
        continue;
      }

      // encabezados # ## ###
      const hMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if(hMatch){
        flushPara();
        const level = hMatch[1].length;
        const el = document.createElement("h" + Math.min(level + 1, 5)); // h2..h5
        el.className = "md-h md-h" + level;
        el.innerHTML = inlineMd(hMatch[2]);
        frag.appendChild(el);
        i++;
        continue;
      }

      // separador ---
      if(/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)){
        flushPara();
        const hr = document.createElement("hr");
        hr.className = "md-hr";
        frag.appendChild(hr);
        i++;
        continue;
      }

      // blockquote >
      if(trimmed.startsWith(">")){
        flushPara();
        const quoteLines = [];
        while(i < lines.length && lines[i].trim().startsWith(">")){
          quoteLines.push(lines[i].trim().replace(/^>\s?/, ""));
          i++;
        }
        const bq = document.createElement("blockquote");
        bq.className = "md-quote";
        bq.innerHTML = inlineMd(quoteLines.join("\n"));
        frag.appendChild(bq);
        continue;
      }

      // lista - o * o 1.
      if(/^[-*]\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)){
        flushPara();
        const ordered = /^\d+\.\s+/.test(trimmed);
        const list = document.createElement(ordered ? "ol" : "ul");
        list.className = "md-list";
        while(i < lines.length){
          const t = lines[i].trim();
          const itemMatch = ordered ? t.match(/^\d+\.\s+(.+)$/) : t.match(/^[-*]\s+(.+)$/);
          if(!itemMatch) break;
          const li = document.createElement("li");
          li.innerHTML = inlineMd(itemMatch[1]);
          list.appendChild(li);
          i++;
        }
        frag.appendChild(list);
        continue;
      }

      // párrafo normal
      paraBuf.push(line);
      i++;
    }
    flushPara();
    return frag;
  }

  /** Parsea texto con fences ``` y markdown del resto. */
  function renderRichContent(text){
    const frag = document.createDocumentFragment();
    if(text == null) text = "";
    text = String(text);

    const re = /```([a-zA-Z0-9_+-]*)[ \t]*\n?([\s\S]*?)```/g;
    let last = 0;
    let m;
    while((m = re.exec(text)) !== null){
      if(m.index > last){
        const plain = text.slice(last, m.index);
        if(plain.trim()) frag.appendChild(renderMarkdownBlock(plain));
      }
      frag.appendChild(buildCodeBlock(m[1] || "", m[2].replace(/\n$/, "")));
      last = m.index + m[0].length;
    }
    if(last < text.length){
      const rest = text.slice(last);
      if(rest.trim()) frag.appendChild(renderMarkdownBlock(rest));
    }
    if(!frag.childNodes.length){
      frag.appendChild(renderMarkdownBlock(text));
    }
    return frag;
  }

  function buildCodeBlock(lang, code){
    const wrap = document.createElement("div");
    wrap.className = "code-block";

    const header = document.createElement("div");
    header.className = "code-header";

    const label = document.createElement("span");
    label.className = "code-lang";
    label.textContent = langLabel(lang);
    header.appendChild(label);

    const actions = document.createElement("div");
    actions.className = "code-actions";

    const btnCopy = document.createElement("button");
    btnCopy.type = "button";
    btnCopy.className = "code-btn";
    btnCopy.textContent = "Copiar";
    btnCopy.title = "Copiar al portapapeles";
    btnCopy.addEventListener("click", () => copyText(code, btnCopy));

    const btnDl = document.createElement("button");
    btnDl.type = "button";
    btnDl.className = "code-btn";
    btnDl.textContent = "Descargar";
    btnDl.title = "Descargar archivo";
    btnDl.addEventListener("click", () => {
      downloadText(code, "elipse." + extFor(lang));
    });

    const btnView = document.createElement("button");
    btnView.type = "button";
    btnView.className = "code-btn";
    btnView.textContent = "Ampliar";
    btnView.title = "Ver en pantalla completa";
    btnView.addEventListener("click", () => openCodeViewer(lang, code));

    actions.appendChild(btnCopy);
    actions.appendChild(btnDl);
    actions.appendChild(btnView);
    header.appendChild(actions);

    const pre = document.createElement("pre");
    const codeEl = document.createElement("code");
    codeEl.textContent = code;
    pre.appendChild(codeEl);

    wrap.appendChild(header);
    wrap.appendChild(pre);
    return wrap;
  }

  function openCodeViewer(lang, code){
    const existing = document.getElementById("codeViewerOverlay");
    if(existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.id = "codeViewerOverlay";
    overlay.className = "code-viewer-overlay";

    const panel = document.createElement("div");
    panel.className = "code-viewer-panel";

    const head = document.createElement("div");
    head.className = "code-viewer-head";
    const title = document.createElement("span");
    title.textContent = langLabel(lang);
    head.appendChild(title);

    const headActions = document.createElement("div");
    headActions.className = "code-actions";

    const btnCopy = document.createElement("button");
    btnCopy.type = "button";
    btnCopy.className = "code-btn";
    btnCopy.textContent = "Copiar";
    btnCopy.addEventListener("click", () => copyText(code, btnCopy));

    const btnDl = document.createElement("button");
    btnDl.type = "button";
    btnDl.className = "code-btn";
    btnDl.textContent = "Descargar";
    btnDl.addEventListener("click", () => downloadText(code, "elipse." + extFor(lang)));

    const btnClose = document.createElement("button");
    btnClose.type = "button";
    btnClose.className = "code-btn code-btn-close";
    btnClose.textContent = "Cerrar";
    btnClose.addEventListener("click", () => overlay.remove());

    headActions.appendChild(btnCopy);
    headActions.appendChild(btnDl);
    headActions.appendChild(btnClose);
    head.appendChild(headActions);

    const body = document.createElement("pre");
    body.className = "code-viewer-body";
    const codeEl = document.createElement("code");
    codeEl.textContent = code;
    body.appendChild(codeEl);

    panel.appendChild(head);
    panel.appendChild(body);
    overlay.appendChild(panel);
    overlay.addEventListener("click", (e) => { if(e.target === overlay) overlay.remove(); });
    document.addEventListener("keydown", function onEsc(e){
      if(e.key === "Escape"){ overlay.remove(); document.removeEventListener("keydown", onEsc); }
    });
    document.body.appendChild(overlay);
  }

  function appendMsg(log, role, text, meta){
    clearEmptyState(log);
    const div = document.createElement("div");
    div.className = "msg " + role;
    if(role === "assistant" && text){
      div.appendChild(renderRichContent(text));
    }else{
      const t = document.createElement("div");
      t.className = "msg-text";
      t.textContent = text || "";
      div.appendChild(t);
    }
    if(meta){
      const m = document.createElement("span");
      m.className = "meta";
      m.textContent = meta;
      div.appendChild(m);
    }
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function appendPendingMsg(log, text){
    clearEmptyState(log);
    const div = document.createElement("div");
    div.className = "msg assistant";
    const t = document.createElement("div");
    t.className = "msg-text";
    t.innerHTML = '<span class="spinner"></span>' + escapeHtml(text || "pensando...");
    div.appendChild(t);
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function setMsgContent(div, text, meta){
    div.innerHTML = "";
    div.appendChild(renderRichContent(text || ""));
    if(meta){
      const m = document.createElement("span");
      m.className = "meta";
      m.textContent = meta;
      div.appendChild(m);
    }
  }

  function buildConfirmCard(pendingList, onDone){
    const card = document.createElement("div");
    card.className = "confirm-card";

    const title = document.createElement("div");
    title.className = "confirm-title";
    title.textContent = pendingList.length === 1
      ? "Se necesita tu confirmación"
      : "Se necesitan " + pendingList.length + " confirmaciones";
    card.appendChild(title);

    pendingList.forEach((p) => {
      const item = document.createElement("div");
      item.className = "confirm-item";
      item.dataset.actionId = p.id;

      const detail = document.createElement("div");
      detail.className = "confirm-detail";
      const tool = document.createElement("span");
      tool.className = "confirm-tool";
      tool.textContent = p.tool || "acción";
      detail.appendChild(tool);
      const desc = document.createElement("span");
      desc.className = "confirm-desc";
      desc.textContent = p.detail || ("id " + p.id);
      detail.appendChild(desc);
      item.appendChild(detail);

      const actions = document.createElement("div");
      actions.className = "confirm-actions";

      const btnOk = document.createElement("button");
      btnOk.type = "button";
      btnOk.className = "confirm-btn approve";
      btnOk.textContent = "Aprobar";
      btnOk.addEventListener("click", () => resolveOne(p.id, true, item, card, onDone));

      const btnNo = document.createElement("button");
      btnNo.type = "button";
      btnNo.className = "confirm-btn reject";
      btnNo.textContent = "Rechazar";
      btnNo.addEventListener("click", () => resolveOne(p.id, false, item, card, onDone));

      actions.appendChild(btnOk);
      actions.appendChild(btnNo);
      item.appendChild(actions);
      card.appendChild(item);
    });

    return card;
  }

  async function resolveOne(actionId, approved, itemEl, cardEl, onDone){
    const btns = itemEl.querySelectorAll("button");
    btns.forEach((b) => { b.disabled = true; });
    itemEl.classList.add("resolving");

    try{
      const res = await Elipse.api.apiFetch("/v1/confirm-action/" + actionId, {
        method: "POST",
        headers: Elipse.api.authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ approved: approved }),
      });

      itemEl.classList.remove("resolving");
      itemEl.classList.add(approved ? "approved" : "rejected");
      const status = document.createElement("div");
      status.className = "confirm-status";
      if(approved){
        status.textContent = "Aprobada" + (res && res.result ? " · " + String(res.result).slice(0, 120) : "");
      }else{
        status.textContent = "Rechazada — no se ejecutó nada";
      }
      const actions = itemEl.querySelector(".confirm-actions");
      if(actions) actions.replaceWith(status);

      const still = cardEl.querySelectorAll(".confirm-item:not(.approved):not(.rejected)");
      if(still.length === 0 && typeof onDone === "function"){
        onDone(approved);
      }
    }catch(e){
      itemEl.classList.remove("resolving");
      btns.forEach((b) => { b.disabled = false; });
      const err = document.createElement("div");
      err.className = "confirm-status error";
      err.textContent = e.message || "Error al confirmar";
      itemEl.appendChild(err);
    }
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
    const pending = appendPendingMsg(log, "pensando...");
    sendBtn.disabled = true;

    try{
      const result = await Elipse.api.runTask("/v1/chat", { message: text });

      if(result.status === "error"){
        pending.remove();
        showBanner(banner, result.result?.detail || result.result?.reply || "Ocurrió un error procesando el mensaje.");
      }else if(result.status === "esperando_confirmacion"){
        const r = result.result || {};
        const reply = r.reply || "Esta acción necesita tu confirmación.";
        setMsgContent(pending, reply, (r.provider || "") + " · esperando confirmación");

        const list = r.pending_confirmations || [];
        if(list.length){
          const card = buildConfirmCard(list, (anyApproved) => {
            const note = document.createElement("div");
            note.className = "confirm-done";
            note.textContent = anyApproved
              ? "Listo. Si querés que el agente continúe con el resultado, mandá otro mensaje (ej. «seguí» o «¿quedó bien?»)."
              : "Acción rechazada. Podés pedir otra cosa cuando quieras.";
            pending.appendChild(note);
            log.scrollTop = log.scrollHeight;
          });
          pending.appendChild(card);
        }
      }else{
        const r = result.result || {};
        const meta = (r.provider || "") + (r.used_tools ? " · usó herramientas" : "");
        setMsgContent(pending, r.reply || "", meta);
      }
    }catch(e){
      pending.remove();
      showBanner(banner, e.message);
    }finally{
      sendBtn.disabled = false;
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