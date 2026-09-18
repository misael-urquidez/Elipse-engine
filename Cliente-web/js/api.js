window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;

  const POLL_INTERVAL_MS = 1400;
  const POLL_TIMEOUT_MS = 5 * 60 * 1000;   // una tarea con modelo local puede tardar
  const MAX_POLL_FAILURES = 3;              // fallos de red seguidos tolerados mientras se espera

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // Headers con la API key. 'extra' permite sumar otros (ej. Content-Type).
  function authHeaders(extra){
    const headers = Object.assign({}, extra || {});
    if(Elipse.state.apiKey){
      headers["Authorization"] = "Bearer " + Elipse.state.apiKey;
    }
    return headers;
  }

  // fetch contra la baseUrl guardada. Devuelve el JSON, o lanza Error con un
  // mensaje legible (sin conexión, 401, detail de FastAPI, etc).
  async function apiFetch(path, opts){
    let res;
    try{
      res = await fetch(Elipse.state.baseUrl + path, opts || {});
    }catch(e){
      throw new Error("No se pudo conectar con ELIPSE en " + Elipse.state.baseUrl + ". ¿Está corriendo el servidor?");
    }

    let data = null;
    try{ data = await res.json(); }catch(_){ /* respuesta sin JSON */ }

    if(!res.ok){
      let detail = data && data.detail;
      if(detail && typeof detail !== "string") detail = JSON.stringify(detail);
      throw new Error(detail || ("Error " + res.status + " del servidor."));
    }
    return data;
  }

  // Para endpoints que corren en background (/v1/chat, /v1/research):
  // dispara la tarea y hace polling a /v1/task/{id} hasta que deje de estar
  // "en_progreso". Devuelve {status, result}, tal como lo entrega el backend.
  async function runTask(path, body){
    const started = await apiFetch(path, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });

    if(!started || !started.task_id){
      throw new Error("El servidor no devolvió un task_id.");
    }

    const pollPath = started.poll_url || ("/v1/task/" + started.task_id);
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    let failures = 0;

    while(Date.now() < deadline){
      await sleep(POLL_INTERVAL_MS);

      let task;
      try{
        task = await apiFetch(pollPath, { headers: authHeaders() });
        failures = 0;
      }catch(e){
        failures += 1;
        if(failures >= MAX_POLL_FAILURES) throw e;
        continue;
      }

      if(task.status === "en_progreso") continue;

      // Caso "no existe esa tarea": el backend responde {status:"error", detail:...} sin 'result'.
      if(task.status === "error" && !task.result){
        throw new Error(task.detail || "La tarea falló.");
      }
      return task;
    }

    throw new Error("La tarea tardó demasiado y se dejó de esperar.");
  }

  Elipse.api = { authHeaders, apiFetch, runTask };
})();