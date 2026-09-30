const ICON_PATHS = {
  "arrow-left": '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
  play: '<path d="M6.5 4.2 20 12 6.5 19.8Z"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  back10:
    '<polyline points="1.5 4.5 1.5 10.5 7.5 10.5"/><path d="M3.7 15a9 9 0 1 0 2-9.4L1.5 10.5"/>' +
    '<text x="12" y="15.6" font-size="7.4" text-anchor="middle" fill="currentColor" stroke="none">10</text>',
  fwd10:
    '<polyline points="22.5 4.5 22.5 10.5 16.5 10.5"/><path d="M20.3 15a9 9 0 1 1-2-9.4l4.2 4.9"/>' +
    '<text x="12" y="15.6" font-size="7.4" text-anchor="middle" fill="currentColor" stroke="none">10</text>',
  volume:
    '<polygon points="11 5 6.5 9 3 9 3 15 6.5 15 11 19"/><path d="M15.2 9.2a4 4 0 0 1 0 5.6"/><path d="M18 6.4a8 8 0 0 1 0 11.2"/>',
  "volume-off":
    '<polygon points="11 5 6.5 9 3 9 3 15 6.5 15 11 19"/><line x1="21" y1="9.5" x2="16" y2="14.5"/>' +
    '<line x1="16" y1="9.5" x2="21" y2="14.5"/>',
  maximize:
    '<path d="M8.5 3.5H5.5a2 2 0 0 0-2 2v3"/><path d="M15.5 3.5h3a2 2 0 0 1 2 2v3"/>' +
    '<path d="M20.5 15.5v3a2 2 0 0 1-2 2h-3"/><path d="M8.5 20.5h-3a2 2 0 0 1-2-2v-3"/>',
  minimize:
    '<path d="M8.5 3.5v3a2 2 0 0 1-2 2h-3"/><path d="M15.5 3.5v3a2 2 0 0 0 2 2h3"/>' +
    '<path d="M20.5 15.5h-3a2 2 0 0 0-2 2v3"/><path d="M8.5 20.5h3a2 2 0 0 0 2-2v-3"/>',
  link: '<path d="M10 13.2a4.6 4.6 0 0 0 7 .5l2.6-2.6a4.6 4.6 0 0 0-6.5-6.5l-1.5 1.5"/>' +
    '<path d="M14 10.8a4.6 4.6 0 0 0-7-.5l-2.6 2.6a4.6 4.6 0 0 0 6.5 6.5l1.5-1.5"/>',
  film:
    '<rect x="2.5" y="4" width="19" height="16" rx="2"/><line x1="7.5" y1="4" x2="7.5" y2="20"/>' +
    '<line x1="16.5" y1="4" x2="16.5" y2="20"/><line x1="2.5" y1="9.5" x2="7.5" y2="9.5"/>' +
    '<line x1="2.5" y1="14.5" x2="7.5" y2="14.5"/><line x1="16.5" y1="9.5" x2="21.5" y2="9.5"/>' +
    '<line x1="16.5" y1="14.5" x2="21.5" y2="14.5"/>',
  trash:
    '<polyline points="3.5 6 20.5 6"/><path d="M18.5 6v13a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2V6m3.5 0V4.5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2V6"/>' +
    '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
  refresh:
    '<polyline points="22 5 22 10.5 16.5 10.5"/><path d="M3.6 9.4a9 9 0 0 1 14.7-3.3L22 10.5"/>' +
    '<polyline points="2 19 2 13.5 7.5 13.5"/><path d="M20.4 14.6a9 9 0 0 1-14.7 3.3L2 13.5"/>',
  send: '<line x1="21.5" y1="2.5" x2="10.5" y2="13.5"/><path d="M21.5 2.5 15 21.5l-4.5-8-8-4.5Z"/>',
  chat: '<path d="M21 14.5a2.5 2.5 0 0 1-2.5 2.5H8l-4.5 4.5V5.5A2.5 2.5 0 0 1 6 3h12.5A2.5 2.5 0 0 1 21 5.5Z"/>',
  users:
    '<path d="M16.5 20.5v-1.8a4 4 0 0 0-4-4h-5a4 4 0 0 0-4 4v1.8"/><circle cx="10" cy="7.5" r="3.5"/>' +
    '<path d="M22 20.5v-1.8a4 4 0 0 0-3-3.8"/><path d="M15.5 4.2a3.5 3.5 0 0 1 0 6.6"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  hold: '<circle cx="12" cy="12" r="9"/><line x1="10" y1="15" x2="10" y2="9"/><line x1="14" y1="15" x2="14" y2="9"/>',
  sync:
    '<polyline points="16.5 2.5 20.5 6.5 16.5 10.5"/><path d="M3.5 11.5V10a3.5 3.5 0 0 1 3.5-3.5h13.5"/>' +
    '<polyline points="7.5 21.5 3.5 17.5 7.5 13.5"/><path d="M20.5 12.5V14a3.5 3.5 0 0 1-3.5 3.5H3.5"/>',
  alert:
    '<path d="M10.3 3.9 2.3 17.6a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>' +
    '<line x1="12" y1="9" x2="12" y2="13.5"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  check: '<polyline points="20 6.5 9.5 17 4 11.5"/>',
  star: '<path d="M12 3.2 14.7 9l6.3.9-4.5 4.4 1 6.3L12 17.6 6.5 20.6l1-6.3L3 9.9 9.3 9Z"/>',
  "play-circle": '<circle cx="12" cy="12" r="9"/><path d="M10 8.2 16 12l-6 3.8Z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><polyline points="12 6.5 12 12 15.5 14"/>',
};

function installIcons() {
  if (document.getElementById("wt-sprite")) return;
  const symbols = Object.entries(ICON_PATHS)
    .map(([name, body]) => `<symbol id="i-${name}" viewBox="0 0 24 24">${body}</symbol>`)
    .join("");
  const host = document.createElement("div");
  host.id = "wt-sprite";
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:absolute;width:0;height:0;overflow:hidden";
  host.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg">${symbols}</svg>`;
  document.body.insertBefore(host, document.body.firstChild);
}

function icon(name, extra = "") {
  return `<svg class="ic ${extra}"><use href="#i-${name}"/></svg>`;
}

function setIcon(element, name) {
  const use = element && element.querySelector("use");
  if (use) use.setAttribute("href", `#i-${name}`);
}

installIcons();

const WT = (() => {
  function uid() {
    let value = localStorage.getItem("wt_uid");
    if (!value) {
      value =
        (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).slice(0, 32);
      localStorage.setItem("wt_uid", value);
    }
    return value;
  }

  function name() {
    return (localStorage.getItem("wt_name") || "").trim();
  }

  function setName(value) {
    localStorage.setItem("wt_name", (value || "").trim().slice(0, 32));
  }

  const DEFAULT_TIMEOUT = 12000;
  const WS_TIMEOUT = 10000;

  async function api(path, options = {}) {
    const { timeout = DEFAULT_TIMEOUT, ...rest } = options;
    const opts = { ...rest, headers: { "X-User-Id": uid(), ...(rest.headers || {}) } };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }

    // Таймаут обязателен: через туннель (ssh, port forwarding) соединение
    // может зависнуть навсегда, и без него интерфейс остаётся мёртвым.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    let response;
    try {
      response = await fetch(path, { ...opts, signal: controller.signal });
    } catch (err) {
      if (err.name === "AbortError") {
        throw new Error("сервер не отвечает — соединение слишком медленное или потеряно");
      }
      throw new Error("нет связи с сервером");
    } finally {
      clearTimeout(timer);
    }

    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (err) {
      data = { error: text || "неизвестная ошибка" };
    }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function establishSession() {
    const current = name();
    try {
      await api("/api/session", { method: "POST", body: { uid: uid(), name: current } });
      return true;
    } catch (err) {
      return false;
    }
  }

  function toast(message, kind = "") {
    const host = document.getElementById("toasts");
    if (!host) return;
    const node = document.createElement("div");
    node.className = "toast " + kind;
    node.textContent = message;
    host.appendChild(node);
    setTimeout(() => node.remove(), 4200);
  }

  function formatTime(ts) {
    const date = new Date(ts * 1000);
    return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  function formatClock(seconds) {
    if (!isFinite(seconds) || seconds < 0) seconds = 0;
    const total = Math.floor(seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  return { uid, name, setName, api, establishSession, toast, formatTime, formatClock, escapeHtml, WS_TIMEOUT };
})();

WT.icon = icon;
WT.setIcon = setIcon;