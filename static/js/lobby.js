(() => {
  const $ = (id) => document.getElementById(id);
  const nameInput = $("name");
  const titleInput = $("title");

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

  let loading = false;

  async function loadRooms(manual = false) {
    const host = $("rooms");
    if (loading) return;
    loading = true;
    if (manual) $("refresh").classList.add("spin");
    try {
      const data = await WT.api("/api/rooms");
      if (!data.rooms.length) {
        host.className = "rooms-empty";
        host.textContent = "Пока нет комнат — создайте первую.";
        return;
      }
      host.className = "";
      host.innerHTML = "";
      for (const room of data.rooms) {
        const card = document.createElement("a");
        card.className = "room-card";
        card.href = `/r/${room.id}`;
        const playing = room.status === "playing";
        const preparing = room.source_status === "preparing";
        const watchers =
          room.participants === 1
            ? "1 зритель"
            : room.participants < 5
              ? `${room.participants} зрителя`
              : `${room.participants} зрителей`;
        card.innerHTML = `
          <span class="room-play">${WT.icon(playing || preparing ? "play" : "pause", "fill")}</span>
          <div class="grow" style="min-width: 0">
            <div class="row" style="gap: 7px">
              <strong>${WT.escapeHtml(room.title)}</strong>
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
          <span class="room-code">${room.id}</span>
        `;
        host.appendChild(card);
      }
    } catch (err) {
      host.className = "muted";
      host.textContent = "Не удалось загрузить комнаты: " + err.message;
    } finally {
      loading = false;
      if (manual) $("refresh").classList.remove("spin");
    }
  }

  $("create").onclick = createRoom;
  $("refresh").onclick = () => loadRooms(true);
  function onEnter(handler) {
    return (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      handler();
    };
  }

  nameInput.onkeydown = onEnter(createRoom);
  titleInput.onkeydown = onEnter(createRoom);

  // Сессия и список комнат — независимые запросы, и оба могут зависнуть на
  // медленном туннеле. Ни один из них не должен блокировать интерфейс.
  WT.establishSession();

  // Ни один из фоновых запросов не должен блокировать кнопки лобби.
  loadRooms();
  setInterval(() => loadRooms(), 6000);
})();