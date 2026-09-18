window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;
  const STORAGE_KEY = "elipse_theme";

  function current(){
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function apply(theme){
    document.documentElement.setAttribute("data-theme", theme);
    const sw = document.getElementById("themeSwitch");
    if(sw){
      sw.setAttribute("aria-checked", theme === "dark" ? "true" : "false");
      sw.title = theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
    }
  }

  function set(theme){
    apply(theme);
    try{ localStorage.setItem(STORAGE_KEY, theme); }catch(e){ /* sin storage: solo dura esta sesión */ }
  }

  function toggle(){
    set(current() === "dark" ? "light" : "dark");
  }

  function init(){
    apply(current());   // sincroniza aria-checked con el tema que ya fijó el script del <head>
    const sw = document.getElementById("themeSwitch");
    if(sw) sw.addEventListener("click", toggle);

    // Si el usuario nunca eligió a mano, seguir el cambio del sistema operativo.
    if(window.matchMedia){
      const mq = matchMedia("(prefers-color-scheme: dark)");
      const onChange = (e) => {
        let saved = null;
        try{ saved = localStorage.getItem(STORAGE_KEY); }catch(_){}
        if(!saved) apply(e.matches ? "dark" : "light");
      };
      if(mq.addEventListener) mq.addEventListener("change", onChange);
    }
  }

  Elipse.theme = { init, set, toggle, current };
  document.addEventListener("DOMContentLoaded", init);
})();