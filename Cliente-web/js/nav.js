window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;

  function init(){
    document.querySelectorAll("nav button").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        btn.classList.add("active");
        document.querySelector("#view-" + btn.dataset.view).classList.add("active");
        if(btn.dataset.view === "keys" && Elipse.keys) Elipse.keys.load();
      });
    });
  }

  Elipse.nav = { init };
})();
