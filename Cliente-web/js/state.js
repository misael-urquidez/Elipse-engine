window.Elipse = window.Elipse || {};
(function(){
  "use strict";
  const Elipse = window.Elipse;

  Elipse.$ = (sel) => document.querySelector(sel);

  Elipse.state = {
    baseUrl: localStorage.getItem("elipse_base_url") || "http://127.0.0.1:8000",
    apiKey: localStorage.getItem("elipse_api_key") || "",
  };

  Elipse.saveState = function saveState(){
    localStorage.setItem("elipse_base_url", Elipse.state.baseUrl);
    localStorage.setItem("elipse_api_key", Elipse.state.apiKey);
  };
})();
