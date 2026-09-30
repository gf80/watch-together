(() => {
  const $ = (id) => document.getElementById(id);
  const nameInput = $("name");
  const titleInput = $("title");
  const joinInput = $("join-id");

  nameInput.value = WT.name();

  function goTo(roomId) {
    location.href = `/r/${roomId.toLowerCase()}`;
  }

  async function createRoom() {
    const chosen = nameInput.value.trim();
    if (!chosen) {
      WT.toast("Введите имя", "err");
      nameInput.focus();
      return;
    }
    WT.setName(chosen);
    $("create").disabled = true;
    try {
      const room = await WT.api("/api/rooms", {
        method: "POST",
        body: { title: titleInput.value.trim() },
        timeout: 20000,
      });
      goTo(room.id);
    } catch (err) {
      WT.toast(err.message, "err");
      $("create").disabled = false;
    }
  }

  function enterByCode() {
    const code = joinInput.value.trim().toLowerCase();
    if (!code) return;
    goTo(code);
  }

  let loading = false;

  async function loadRooms() {
    const host = $("rooms");
    if (loading) return;
    loading = true;
    try {
      const data = await WT.api("/api/rooms");
      if (!data.rooms.length) {
        host.className = "card";
        host.style.cssText = "padding: 20px; text-align: center; font-size: 14px";
        host.textContent = "Пока нет комнат — создайте первую.";
        return;
      }
      host.className = "";
      host.innerHTML = "";
      for (const room of data.rooms) {
        const card = document.createElement("div");
        card.className = "room-card";
        const playing = room.status === "playing";
        const preparing = room.source_status === "preparing";
        const watchers =
          room.participants === 1
            ? "1 зритель"
            : room.participants < 5
              ? `${room.participants} зрителя`
              : `${room.participants} зрителей`;
        card.innerHTML = `
          <div class="grow" style="min-width: 0">
            <div class="row" style="gap: 7px">
              <strong>${WT.escapeHtml(room.title)}</strong>
              <span class="tag">${room.id}</span>
              ${room.is_host ? '<span class="tag host">вы хост</span>' : ""}
              ${
                preparing
                  ? '<span class="tag">готовим видео…</span>'
                  : `<span class="tag ${playing ? "live" : ""}">${playing ? "идёт" : "пауза"}</span>`
              }
            </div>
            <div class="muted" style="font-size: 13px; margin-top: 4px">
              ${WT.escapeHtml(room.source_title || "источник не выбран")} · ${watchers}
            </div>
          </div>
          <button class="btn ${room.is_host ? "" : "primary"}">${
            room.is_host ? "Открыть" : "Смотреть"
          }</button>
        `;
        card.querySelector("button").onclick = () => goTo(room.id);
        host.appendChild(card);
      }
    } catch (err) {
      host.className = "muted";
      host.textContent = "Не удалось загрузить комнаты: " + err.message;
    } finally {
      loading = false;
    }
  }

  $("create").onclick = createRoom;
  $("join").onclick = enterByCode;
  $("refresh").onclick = loadRooms;
  function onEnter(handler) {
    return (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      handler();
    };
  }

  nameInput.onkeydown = onEnter(createRoom);
  joinInput.onkeydown = onEnter(enterByCode);
  titleInput.onkeydown = onEnter(createRoom);

  // Сессия и список комнат — независимые запросы, и оба могут зависнуть на
  // медленном туннеле. Ни один из них не должен блокировать интерфейс.
  WT.establishSession();

  async function loadCapabilities() {
    try {
      const caps = await WT.api("/api/capabilities");
      const parts = [];
      parts.push(caps.yt_dlp ? "yt-dlp ✓" : "yt-dlp ✗");
      parts.push(caps.ffmpeg ? "ffmpeg ✓" : "ffmpeg ✗");
      $("caps").textContent = parts.join(" · ");
      const tip =
        caps.yt_dlp && caps.ffmpeg
          ? "Можно вставлять ссылки на любые страницы с видео"
          : "Без yt-dlp/ffmpeg работают только прямые ссылки на файл или .m3u8";
      $("caps").title = caps.ffmpeg_path ? `${tip}\nffmpeg: ${caps.ffmpeg_path}` : tip;
    } catch (err) {
      $("caps").textContent = "сервер недоступен";
    }
  }

  // Ни один из фоновых запросов не должен блокировать кнопки лобби.
  loadCapabilities();
  loadRooms();
  setInterval(loadRooms, 6000);
})();