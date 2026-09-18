# ELIPSE — Cliente web

Consola de control para hablar con tu instancia de ELIPSE Core desde el navegador: chat, pipeline de investigación, búsqueda en memoria semántica y gestión de API keys. Sin build, sin dependencias de Node — abrís `index.html` y ya.

---

## Estructura

```
cliente-web/
├── index.html          # estructura de la página, sin lógica
├── css/
│   ├── tokens.css       # paleta, tipografía, espaciado (variables :root)
│   ├── layout.css       # esqueleto: sidebar, main, vistas, responsive
│   └── components.css   # botones, chat, cards, tabla, badges, spinner
├── js/
│   ├── state.js          # baseUrl + apiKey, persistidos en localStorage
│   ├── api.js             # fetch autenticado + polling de tareas en background
│   ├── nav.js             # cambio entre vistas de la sidebar
│   ├── chat.js            # vista Chat
│   ├── research.js        # vista Investigar
│   ├── memory.js          # vista Memoria
│   ├── keys.js            # vista Llaves (API keys)
│   └── app.js             # arranque: conecta todo lo anterior al cargar la página
└── src/                  # reservada para assets futuros (logo exportado, capturas)
```

Todo se comunica a través de un objeto global `window.Elipse`, con una sección por módulo (`Elipse.state`, `Elipse.api`, `Elipse.chat`, etc.). Los scripts son globales normales (no `<script type="module">`) a propósito: así `index.html` funciona abierto directo con doble click (`file://`), sin que el navegador bloquee nada por CORS de módulos.

---

## Cómo usarlo

1. Abre `index.html` en el navegador (doble click alcanza).
2. En la sidebar, abre **Conexión** y completa:
   - **URL de la API**: donde corre tu backend de ELIPSE (ej. `http://127.0.0.1:8000`).
   - **API key**: una llave generada por CLI en el backend (`python -m app.manage_keys create <nombre>`) — la primera llave no se puede crear desde aquí, tiene que existir antes.
3. Click en **Guardar y probar**. El punto de estado en la sidebar se pone verde si conecta.
4. Con eso ya podés usar las 4 vistas: Chat, Investigar, Memoria y Llaves.

El backend (Ollama + FastAPI) tiene que estar corriendo aparte — este cliente es solo la interfaz, no lanza nada por su cuenta. Si usas el `elipse_control_panel.py`, arráncalo desde ahí primero.

---

## Diseño

Paleta de dos acentos con significado, no un color de "IA" genérico:

| Token | Valor | Uso |
|---|---|---|
| `--color-bg` | `#14110D` | fondo |
| `--color-panel` | `#1C1712` | sidebar, cards |
| `--color-text` | `#EDE7DA` | texto principal |
| `--color-ember` | `#D9A15C` | acento primario — acción, energía |
| `--color-moss` | `#7C9473` | acento secundario — memoria, estado bueno |
| `--color-rust` | `#C1614A` | error, estado malo |

Tipografía: **Fraunces** (serif, marca y títulos) + **Work Sans** (UI) + **IBM Plex Mono** (solo datos/logs, nunca mezclado con texto de interfaz). Cambiar cualquiera de estos valores es editar `css/tokens.css` — el resto de los archivos CSS solo consumen las variables, no tienen colores ni tamaños sueltos.

El detalle distintivo: las respuestas de ELIPSE en el chat no son burbujas, son anotaciones al margen (un filo `--color-ember` a la izquierda) — para diferenciarlas visualmente de tus propios mensajes sin caer en el patrón genérico de dos burbujas iguales.

---

## Extender el cliente

- **Nueva vista**: agrega el `<section class="view" id="view-nombre">` en `index.html`, el botón correspondiente en `<nav>`, y un `js/nombre.js` con su propio `init()` — regístralo en `js/app.js` igual que los demás.
- **Nuevo endpoint del backend**: si es una tarea en background (como `/v1/chat` o `/v1/research`), usa `Elipse.api.runTask(path, body)`; si es una llamada directa, usa `Elipse.api.apiFetch(path, opts)` con `Elipse.api.authHeaders()`.
- **Cambiar el look**: todo el sistema de diseño vive en `css/tokens.css`. No hay valores de color/tipografía hardcodeados en `layout.css` ni `components.css`.

---

## Notas conocidas

- Sin autenticación de sesión propia del cliente — la API key vive en `localStorage` del navegador donde la guardaste. No compartas el mismo navegador/perfil para varios dispositivos si querés mantener las llaves separadas.
- El polling de tareas (`runTask`) revisa cada 1.4s vía HTTP; no usa el WebSocket de progreso (`/v1/ws/{task_id}`) todavía — es la próxima mejora obvia si quieres ver el paso a paso del Agent Loop en vivo en vez de solo el resultado final.