(() => {
  const $ = (id) => document.getElementById(id);
  const video = $("video");
  const roomId = location.pathname.split("/").filter(Boolean)[1]?.toLowerCase() || "";
  if (!roomId) location.replace("/");

  const params = new URLSearchParams();
  params.set("uid", WT.uid());
  params.set("name", WT.name() || prompt("Как вас зовут?") || "Гость");

  let socket = null;
  let reconnectDelay = 800;
  let state = null;
  let me = { id: WT.uid(), name: params.get("name"), host: false };
  let hls = null;
  let dragging = false;
  let bufferingSent = null;
  let lastSourceStatus = null;
  let correction = 1;
  let typingTimer = null;
  let roomGone = false;

  const clock = { offset: 0, rtt: Infinity };
  let limits = { hard: 1.9, soft: 0.35, rateMin: 0.92, rateMax: 1.08 };

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const serverNow = () => Date.now() / 1000 + clock.offset;

  /* Высота окна для комнаты берётся из visualViewport — это единственная
     величина, которая честно учитывает открытую клавиатуру. Через 100dvh
     приходилось гадать, и страницу подкручивало к полю ввода: экран дёргался.
     Плюс страница зафиксирована, поэтому автоскролл фокуса ей не мешает. */
  const viewport = window.visualViewport;
  const applyViewportHeight = () => {
    const height = Math.round(viewport ? viewport.height : window.innerHeight);
    document.body.style.setProperty("--app-height", `${height}px`);
  };
  applyViewportHeight();
  viewport?.addEventListener("resize", applyViewportHeight);
  viewport?.addEventListener("scroll", applyViewportHeight);
  window.addEventListener("orientationchange", () => setTimeout(applyViewportHeight, 250));

  function targetPosition() {
    if (!state) return 0;
    if (state.status !== "playing") return state.position;
    return Math.max(0, state.position + (serverNow() - state.anchor) * state.rate);
  }

  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  function leaveRoom(reason) {
    if (roomGone) return;
    roomGone = true;
    stopPing();
    socket?.close();
    WT.toast(reason || "Комната закрыта");
    setTimeout(() => location.replace("/"), 1200);
  }

  let connectFailures = 0;

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/${roomId}?${params}`);

    const openTimer = setTimeout(() => {
      if (socket && socket.readyState !== WebSocket.OPEN) socket.close();
    }, WT.WS_TIMEOUT || 10000);

    socket.onopen = () => {
      clearTimeout(openTimer);
      connectFailures = 0;
      reconnectDelay = 800;
      $("sync-badge").textContent = "на связи";
      startPing();
    };
    socket.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      handle(data);
    };
    socket.onclose = () => {
      clearTimeout(openTimer);
      stopPing();
      if (roomGone) return;
      connectFailures += 1;
      $("sync-badge").textContent = "переподключение…";
      // Многие туннели (ssh, port forwarding в редакторах) не пропускают
      // WebSocket или рвут соединение. После нескольких попыток честно
      // говорим об этом, вместо бесконечного «переподключение…».
      if (connectFailures === 4) {
        WT.toast(
          "Соединение с комнатой не устанавливается. Туннель может блокировать WebSocket — откройте адрес напрямую или через SSH-туннель на localhost.",
          "err"
        );
      }
      reconnectDelay = Math.min(reconnectDelay * 1.6, 8000);
      setTimeout(connect, reconnectDelay);
    };
    socket.onerror = () => socket.close();
  }

  const pingTimer = { id: null };
  function startPing() {
    stopPing();
    pingTimer.id = setInterval(() => send({ type: "ping", t: Date.now() }), 4000);
    send({ type: "ping", t: Date.now() });
  }
  function stopPing() {
    if (pingTimer.id) clearInterval(pingTimer.id);
    pingTimer.id = null;
  }

  function handle(data) {
    switch (data.type) {
      case "welcome":
        me = data.you;
        renderHistory(data.history || []);
        renderPeers(data.peers || []);
        applyState(data.room, false);
        break;
      case "pb.state":
        applyState(data, false);
        break;
      case "pb.sync":
        applyState(data, true);
        break;
      case "peers":
        renderPeers(data.users || []);
        break;
      case "chat.msg":
        appendChat(data);
        break;
      case "chat.seen": {
        // Сообщение прочитано: отмечаем у себя и снимаем счётчик.
        const mid = Number(data.id);
        for (const item of $("chat").querySelectorAll(`.msg.mine[data-msg-id='${mid}']`)) {
          item.classList.remove("unseen");
          const seen = item.querySelector(".seen");
          if (seen) seen.textContent = "прочитано";
        }
        break;
      }
      case "host":
        me.host = data.host_id === me.id;
        renderPeers(lastPeers);
        renderUnread();
        break;
      case "typing":
        showTyping(data);
        break;
      case "room.closed":
        leaveRoom("Комнату удалил хост");
        break;
      case "pong": {
        const rtt = Date.now() - (data.t || Date.now());
        if (rtt < clock.rtt) {
          clock.rtt = rtt;
          clock.offset = data.server_time + rtt / 2000 - Date.now() / 1000;
        }
        break;
      }
      case "error":
        WT.toast(data.message, "err");
        break;
    }
  }

  function applyState(msg, force) {
    const previousUrl = state?.source?.url || null;
    state = msg;
    limits = {
      hard: msg.hard_skew,
      soft: msg.soft_skew,
      rateMin: msg.rate_min,
      rateMax: msg.rate_max,
    };
    if ((msg.source?.url || null) !== previousUrl) {
      loadSource(msg.source);
    } else if (force) {
      hardSeek(targetPosition());
    }
    renderState();
    sendReport();
  }

  function cleanupHls() {
    if (hls) {
      try {
        hls.destroy();
      } catch (err) {
        /* ignore */
      }
      hls = null;
    }
  }

  function loadSource(src) {
    cleanupHls();
    correction = 1;
    video.pause();
    video.removeAttribute("src");
    if (!src) {
      video.load();
      return;
    }
    const onMeta = () => {
      const target = targetPosition();
      if (target > 0.25 && isFinite(video.duration)) {
        video.currentTime = Math.min(target, Math.max(0, video.duration - 0.2));
      }
      reconcile();
    };
    video.addEventListener("loadedmetadata", onMeta, { once: true });

    if (src.hls) {
      if (window.Hls && window.Hls.isSupported()) {
        hls = new window.Hls({ enableWorker: true, lowLatencyMode: false, maxBufferLength: 30 });
        hls.on(window.Hls.Events.ERROR, (_evt, details) => {
          if (details?.fatal) {
            WT.toast("Ошибка HLS-потока: " + (details.details || details.reason), "err");
          }
        });
        hls.loadSource(src.url);
        hls.attachMedia(video);
        return;
      }
      if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = src.url;
        video.load();
        return;
      }
      WT.toast("Браузер не умеет HLS. Скачайте hls.js или откройте в Safari.", "err");
      return;
    }
    video.src = src.url;
    video.load();
  }

  function applyRate() {
    const base = state?.rate || 1;
    const wanted = clamp(base * correction, 0.25, 4);
    if (Math.abs(video.playbackRate - wanted) > 0.01) {
      video.playbackRate = wanted;
    }
  }

  function hardSeek(target) {
    if (!state?.source || video.readyState === 0) return;
    const limit = isFinite(video.duration) ? Math.max(0, video.duration - 0.2) : Infinity;
    video.currentTime = clamp(target, 0, limit);
    correction = 1;
    applyRate();
  }

  function reconcile() {
    if (!state?.source || state.source_status !== "ready") return;
    if (document.activeElement === $("chat-input")) return;
    if (state.status === "playing") {
      if (video.paused) {
        video.play().catch(() => {
          $("sync-badge").textContent = "нажмите «Пуск»";
        });
      }
    } else if (!video.paused) {
      video.pause();
    }
  }

  function tick() {
    if (!state) return;
    const target = targetPosition();
    const duration = video.duration;

    if (isFinite(duration) && duration > 0) {
      $("t-end").textContent = WT.formatClock(duration);
      if (!dragging) $("seek").value = String(clamp((video.currentTime / duration) * 1000, 0, 1000));
    }
    if (!dragging) $("t-now").textContent = WT.formatClock(video.currentTime);

    const drift = video.currentTime - target;
    const badge = $("sync-badge");

    if (video.readyState === 0 || dragging) {
      correction = 1;
      applyRate();
      badge.textContent = "синхронизация ждёт видео";
      return;
    }
    if (Math.abs(drift) <= limits.soft) {
      correction = 1;
      badge.textContent = "синхронно";
      badge.style.color = "";
    } else if (Math.abs(drift) > limits.hard) {
      hardSeek(target);
      badge.textContent = "перемотка по комнате";
      badge.style.color = "var(--warn)";
    } else {
      correction = clamp(1 - drift * 0.3, limits.rateMin, limits.rateMax);
      badge.textContent = `${drift > 0 ? "+" : ""}${drift.toFixed(2)}с`;
      badge.style.color = "var(--warn)";
    }
    applyRate();
    reconcile();
  }

  function sendReport() {
    send({
      type: "pb.report",
      position: video.currentTime,
      playing: !video.paused && !video.ended,
      ready: video.readyState >= 2,
    });
  }

  function setBuffering(value) {
    if (bufferingSent === value) return;
    bufferingSent = value;
    send({ type: "pb.report", position: video.currentTime, buffering: value });
  }

  function control(action, extra = {}) {
    if (!send({ type: "pb.control", action, ...extra })) {
      WT.toast("Нет связи с комнатой", "err");
    }
  }

  function renderState() {
    if (!state) return;
    const playing = state.status === "playing";
    const hasSource = !!state.source;
    $("room-title").textContent = state.title || `Комната ${state.room_id}`;
    // Панель управления скрыта, пока нет источника: иначе она лежит поверх
    // пустого экрана и выглядит как поломка.
    $("player").hidden = !hasSource;
    WT.setIcon($("play"), playing ? "pause" : "play");
    WT.setIcon($("play-center-icon"), playing ? "pause" : "play");
    $("play").setAttribute("aria-label", playing ? "Пауза для всех" : "Пуск для всех");
    $("play-center").setAttribute("aria-label", playing ? "Пауза для всех" : "Пуск для всех");
    me.host = state.host_id === me.id;
    $("delete-room").hidden = !me.host;
    renderUnread();

    if (state.source_status !== lastSourceStatus) {
      lastSourceStatus = state.source_status;
      if (state.source_error) {
        WT.toast(state.source_error, state.source_status === "error" ? "err" : "");
      }
    }

    $("empty").hidden = hasSource;
    if (!hasSource) {
      const preparing = state.source_status === "preparing";
      $("empty-title").textContent = preparing ? "Готовим видео…" : "Видео не выбрано";
      $("empty-text").textContent = preparing
        ? state.source_error || "Скачиваем файл и собираем дорожки через ffmpeg. Это может занять минуту."
        : "Нажмите «Источник» и вставьте ссылку: прямой файл (.mp4, .webm), HLS-поток (.m3u8) или страницу с видео. Загружать файл на сервер не нужно.";
      $("empty-source").hidden = preparing;
    }
  }

  let lastPeers = [];
  function renderPeers(peers) {
    lastPeers = peers;
    const host = $("people");
    const count = peers.length;
    $("people-count").textContent = String(count);
    if ($("people").hidden) return;
    host.innerHTML = "";
    for (const peer of peers) {
      const row = document.createElement("div");
      row.className = "person";
      const driftText =
        peer.drift === null || peer.drift === undefined
          ? ""
          : `${peer.drift > 0 ? "+" : ""}${Number(peer.drift).toFixed(1)}с`;
      const color = Math.abs(peer.drift || 0) < 1 ? "var(--ok)" : "var(--warn)";
      row.innerHTML = `
        <span class="dot ${peer.ready ? "on" : "off"}"></span>
        <span class="name">${WT.escapeHtml(peer.name)}${peer.id === me.id ? " · вы" : ""}</span>
        ${peer.host ? '<svg class="ic fill star"><use href="#i-star"/></svg>' : ""}
        <span class="drift" style="color:${color}">${peer.buffering ? "буфер…" : driftText}</span>
      `;
      host.appendChild(row);
    }
  }

  function appendChat(message, { silent = false } = {}) {
    const log = $("chat");
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
    const node = document.createElement("div");
    const isSystem = message.kind === "system";
    if (isSystem) {
      node.className = "msg system";
      node.innerHTML = `<div class="bubble">${WT.escapeHtml(message.text)}</div>`;
    } else {
      const mine = message.author_id === me.id;
      node.className = "msg" + (mine ? " mine" : "");
      const seenAt = message.seen_at ? WT.formatTime(message.seen_at) : "";
      node.innerHTML = `
        <div class="meta">
          <span>${WT.escapeHtml(message.author)}</span>
          <span>${WT.formatTime(message.ts)}</span>
        </div>
        <div class="bubble">${WT.escapeHtml(message.text)}</div>
        ${mine ? `<div class="seen" data-id="${message.id}">${seenAt || ""}</div>` : ""}
      `;
      node.__message = message;
      if (message.id) node.dataset.msgId = message.id;
    }
    log.appendChild(node);
    while (log.children.length > 300) log.firstChild.remove();

    // Сообщение от другого считается прочитанным, если вкладка чата активна,
    // окно видно и лог пролистан вниз. Иначе — счётчик на вкладке и звук.
    // Своё сообщение ждёт подтверждения от других.
    const mine = !isSystem && message.author_id === me.id;
    if (isSystem) {
      node.classList.remove("unseen");
    } else if (mine) {
      if (message.seen_at) {
        const seen = node.querySelector(".seen");
        if (seen) seen.textContent = "прочитано";
      } else {
        node.classList.add("pending");
      }
    } else if (chatTabActive() && !document.hidden && nearBottom) {
      markSeen(node, message);
    } else {
      node.classList.add("unseen");
      unread += 1;
      renderUnread();
      if (!silent && soundOn) WT.ping("msg");
    }
    if (nearBottom) log.scrollTop = log.scrollHeight;
  }

  function chatTabActive() {
    return document.querySelector('.side-tabs button[data-tab="chat"]').classList.contains("active");
  }

  function renderUnread() {
    const badge = $("chat-unread");
    badge.hidden = unread === 0;
    badge.textContent = unread > 99 ? "99+" : String(unread);
    // Счётчик в заголовке вкладки — про неё пользователь узнаёт, свернув окно.
    const base = state?.source?.title || "Watch Together";
    document.title = unread > 0 ? `(${unread}) ${base}` : (me.host ? "★ " : "") + base;
  }

  let unread = 0;
  function clearUnread() {
    if (unread === 0) return;
    unread = 0;
    renderUnread();
  }

  // Отмечаем прочитанными все сообщения, которые уже видны в логе.
  function markSeenVisible() {
    const log = $("chat");
    if (!chatTabActive() || document.hidden) return;
    if (log.scrollHeight - log.scrollTop - log.clientHeight > 24) return;
    for (const node of log.querySelectorAll(".msg.unseen")) {
      if (node.__message?.author_id === me.id) continue;
      node.classList.remove("unseen");
      markSeen(node, node.__message);
    }
    clearUnread();
  }

  function markSeen(node, message) {
    if (!message || !message.id) return;
    send({ type: "chat.seen", id: message.id });
  }

  // Прочитанным считаем сообщение, которое реально долистано до конца лога.
  $("chat").addEventListener("scroll", markSeenVisible, { passive: true });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) markSeenVisible();
  });
  window.addEventListener("focus", markSeenVisible);

  function renderHistory(messages) {
    $("chat").innerHTML = "";
    for (const message of messages) appendChat(message, { silent: true });
  }

  function showTyping(data) {
    if (data.user_id === me.id) return;
    const badge = $("sync-badge");
    const previous = badge.dataset.typing;
    badge.dataset.typing = data.name;
    badge.textContent = `${data.name} печатает…`;
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => {
      badge.dataset.typing = "";
      tick();
    }, 1500);
  }

  /* Панель управления прячется, когда смотрим и не трогаем экран.
     На паузе остаётся видимой: без неё непонятно, чем продолжить.
     На тач-устройствах наведения нет, поэтому скрываем только по бездействию. */
  let idleTimer = null;
  const canHover = window.matchMedia("(hover: hover)").matches;
  function pokePlayer() {
    $("player").classList.remove("idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (state?.status === "playing" && !dragging) $("player").classList.add("idle");
    }, 2600);
  }
  /* Слушаем на обёртке видео, а не на самом <video>: кнопки панели лежат
     поверх кадра отдельным слоем, и на самом видео они не потомки. Слушатели
     на <video> гасили панель ровно в момент, когда курсор доходил до кнопки
     паузы, — нажать было нельзя. */
  const wrap = $("video-wrap");
  wrap.addEventListener("pointermove", pokePlayer);
  wrap.addEventListener("pointerdown", pokePlayer);
  // У курсора есть уход за пределы кадра, у пальца — нет: там панель
  // возвращается тапом, поэтому убирать её по уходу нельзя.
  if (canHover) {
    wrap.addEventListener("pointerleave", () => {
      clearTimeout(idleTimer);
      if (state?.status === "playing" && !dragging) $("player").classList.add("idle");
    });
  }
  $("seek").addEventListener("pointerdown", pokePlayer);
  $("seek").addEventListener("input", pokePlayer);
  pokePlayer();

  $("play").onclick = () => {
    control("toggle");
    pokePlayer();
  };
  $("play-center").onclick = () => {
    control("toggle");
    pokePlayer();
  };
  $("back").onclick = () => control("seek", { position: Math.max(0, targetPosition() - 10) });
  $("fwd").onclick = () => control("seek", { position: targetPosition() + 10 });
  $("mute").onclick = () => {
    video.muted = !video.muted;
    WT.setIcon($("mute"), video.muted ? "volume-off" : "volume");
    $("mute").setAttribute("aria-pressed", video.muted ? "true" : "false");
    $("mute").title = video.muted ? "Включить звук" : "Звук только у вас";
  };

  // Звук новых сообщений. Настройка общая для всех комнат и хранится локально.
  let soundOn = localStorage.getItem("wt_sound") !== "0";
  const soundButton = $("sound-toggle");
  const paintSound = () => {
    WT.setIcon(soundButton, soundOn ? "bell-ring" : "bell-off");
    soundButton.setAttribute("aria-pressed", soundOn ? "true" : "false");
    soundButton.title = soundOn ? "Выключить звук сообщений" : "Включить звук сообщений";
  };
  paintSound();
  soundButton.onclick = () => {
    soundOn = !soundOn;
    localStorage.setItem("wt_sound", soundOn ? "1" : "0");
    paintSound();
    if (soundOn) WT.ping("msg");
  };
  $("fs").onclick = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else video.requestFullscreen?.();
  };
  document.addEventListener("fullscreenchange", () => {
    WT.setIcon($("fs"), document.fullscreenElement ? "minimize" : "maximize");
    $("fs").title = document.fullscreenElement ? "Выйти из полноэкранного режима" : "Во весь экран";
  });
  $("copy-link").onclick = async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      WT.toast("Ссылка скопирована", "ok");
    } catch (err) {
      WT.toast(location.href);
    }
  };

  const seekBar = $("seek");
  seekBar.addEventListener("pointerdown", () => (dragging = true));
  window.addEventListener("pointerup", () => {
    if (!dragging) return;
    dragging = false;
    const duration = video.duration;
    if (!isFinite(duration)) return;
    control("seek", { position: (Number(seekBar.value) / 1000) * duration });
  });
  seekBar.addEventListener("input", () => {
    dragging = true;
    const duration = video.duration;
    if (isFinite(duration)) {
      $("t-now").textContent = WT.formatClock((Number(seekBar.value) / 1000) * duration);
    }
  });

  video.addEventListener("waiting", () => setBuffering(true));
  video.addEventListener("stalled", () => setBuffering(true));
  video.addEventListener("playing", () => {
    setBuffering(false);
    reconcile();
  });
  video.addEventListener("canplay", () => setBuffering(false));
  video.addEventListener("seeking", () => {
    correction = 1;
    applyRate();
  });
  video.addEventListener("ended", () => {
    if (state && state.status === "playing") control("pause", { position: video.duration });
  });
  video.addEventListener("error", () => {
    const code = video.error?.code;
    if (!code) return;
    const hints = {
      1: "загрузка прервана",
      2: "сетевая ошибка",
      3: "не удалось декодировать (кодек не поддерживается)",
      4: "формат или адрес не поддерживается",
    };
    if (code === 4 || code === 2) {
      WT.toast(`Видео: ${hints[code]}. Проверьте ссылку и права доступа.`, "err");
    }
  });

  const chatForm = $("chat-form");
  const chatInput = $("chat-input");
  chatForm.onsubmit = (event) => {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    send({ type: "chat", text });
    chatInput.value = "";
    $("chat").scrollTop = $("chat").scrollHeight;
  };
  let lastTypingSent = 0;
  chatInput.oninput = () => {
    const now = Date.now();
    if (now - lastTypingSent > 1500) {
      lastTypingSent = now;
      send({ type: "typing" });
    }
  };

  document.querySelectorAll(".side-tabs button").forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll(".side-tabs button").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      const isChat = button.dataset.tab === "chat";
      $("chat").hidden = !isChat;
      $("chat-form").hidden = !isChat;
      $("people").hidden = isChat;
      if (isChat) markSeenVisible();
      else renderPeers(lastPeers);
    };
  });

  const modal = $("modal");
  const openModal = () => {
    modal.hidden = false;
    $("src-url").value = state?.source?.input_url || "";
    $("src-title").value = state?.source?.title || "";
    $("src-bypass").checked = false;
    $("src-url").focus();
    loadCapabilities();
  };
  const closeModal = () => {
    modal.hidden = true;
  };
  $("set-source").onclick = openModal;
  $("empty-source").onclick = openModal;
  $("src-close").onclick = closeModal;
  $("src-cancel").onclick = closeModal;
  modal.onclick = (event) => {
    if (event.target === modal) closeModal();
  };

  const confirmBox = $("confirm");
  const closeConfirm = () => {
    confirmBox.hidden = true;
  };
  $("delete-room").onclick = () => {
    $("confirm-id").textContent = roomId;
    confirmBox.hidden = false;
    $("confirm-cancel").focus();
  };
  $("confirm-cancel").onclick = closeConfirm;
  confirmBox.onclick = (event) => {
    if (event.target === confirmBox) closeConfirm();
  };
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!confirmBox.hidden) closeConfirm();
    else if (!modal.hidden) closeModal();
  });
  $("confirm-ok").onclick = async () => {
    const button = $("confirm-ok");
    button.disabled = true;
    try {
      await WT.api(`/api/rooms/${roomId}`, { method: "DELETE" });
      roomGone = true;
      socket?.close();
      WT.toast("Комната удалена");
      setTimeout(() => location.replace("/"), 600);
    } catch (err) {
      button.disabled = false;
      closeConfirm();
      WT.toast(err.message, "err");
    }
  };

  async function loadCapabilities() {
    try {
      const caps = await WT.api("/api/capabilities");
      let hint = "";
      if (caps.yt_dlp) {
        hint = caps.ffmpeg
          ? "Можно вставлять ссылку на страницу с видео — поток будет извлечён автоматически."
          : "Страницы поддерживаются, но для некоторых источников нужен ffmpeg (ffmpeg не найден).";
      } else {
        hint = "yt-dlp не установлен: работают только прямые ссылки на файл или .m3u8.";
      }
      if (!me.host) hint += " Источник может задать только хост комнаты.";
      $("src-hint").textContent = hint;
    } catch (err) {
      $("src-hint").textContent = "";
    }
  }

  $("src-save").onclick = async () => {
    const url = $("src-url").value.trim();
    if (!url) return;
    $("src-save").disabled = true;
    try {
      const result = await WT.api(`/api/rooms/${roomId}/source`, {
        method: "POST",
        body: { url, title: $("src-title").value.trim(), bypass: $("src-bypass").checked },
      });
      closeModal();
      if (result.note) WT.toast(result.note);
      if (result.status === "preparing") WT.toast("Готовим видео, это займёт некоторое время…");
      else WT.toast("Источник задан", "ok");
    } catch (err) {
      WT.toast(err.message, "err");
    } finally {
      $("src-save").disabled = false;
    }
  };

  window.addEventListener("beforeunload", () => socket?.close());

  if (!WT.name()) WT.setName(params.get("name"));
  WT.establishSession().then(connect);
  setInterval(tick, 500);
  setInterval(sendReport, 1000);
})();