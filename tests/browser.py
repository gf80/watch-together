"""Полный сценарий в браузере на ЛОКАЛЬНОМ медиапотоке.

Интернет в тестовой среде может быть недоступен, а ffmpeg нет, поэтому в
качестве «фильма» отдаётся WAV-файл (генерируется стандартной библиотекой):
<video> умеет играть аудио, поэтому вся механика плеера — длительность,
currentTime, play/pause, буферизация — проверяется по-настоящему.

Запуск (сервер поднимается скриптом, WT_ALLOW_PRIVATE_PROXY=1):
    python tests/browser.py
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys

import httpx
from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(__file__))
from media_server import make_tone, serve_media

BASE_URL = os.getenv("WT_BASE_URL", "http://127.0.0.1:8123")
CHANNEL = os.getenv("WT_BROWSER_CHANNEL", "msedge")
MEDIA_PORT = int(os.getenv("WT_MEDIA_PORT", "8124"))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else f"{name} ({extra})")
    print(f"  {'ok  ' if ok else 'FAIL'} {name} {'' if ok else extra}")


def collect(page, label: str, sink: list[str]) -> None:
    page.on(
        "console",
        lambda m: sink.append(f"{label} console.{m.type}: {m.text}")
        if m.type == "error"
        else None,
    )
    page.on("pageerror", lambda e: sink.append(f"{label} pageerror: {e}"))


async def main() -> int:
    media_dir = os.path.join(os.path.dirname(__file__), "_media")
    os.makedirs(media_dir, exist_ok=True)
    make_tone(os.path.join(media_dir, "tone.wav"))
    server = serve_media(media_dir, MEDIA_PORT)
    media_url = f"http://127.0.0.1:{MEDIA_PORT}/tone.wav"

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as http:
        created = await http.post(
            "/api/rooms", json={"title": "Браузерный тест"}, headers={"X-User-Id": "br-host"}
        )
        if created.status_code != 201:
            print("не удалось создать комнату:", created.status_code, created.text)
            return 1
        room_id = created.json()["id"]
    url = f"{BASE_URL}/r/{room_id}"
    print(f"комната {room_id}: {url}\nмедиа: {media_url}")

    errors: list[str] = []
    headless = os.getenv("WT_HEADLESS", "0") in {"1", "true", "yes"}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel=CHANNEL,
            headless=headless,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        host_ctx = await browser.new_context()
        guest_ctx = await browser.new_context()
        await host_ctx.add_init_script(
            "localStorage.setItem('wt_uid','br-host'); localStorage.setItem('wt_name','Хост');"
        )
        await guest_ctx.add_init_script(
            "localStorage.setItem('wt_uid','br-guest'); localStorage.setItem('wt_name','Гость');"
        )
        host = await host_ctx.new_page()
        guest = await guest_ctx.new_page()
        collect(host, "host", errors)
        collect(guest, "guest", errors)

        await host.goto(url)
        await guest.goto(url)
        await host.wait_for_timeout(1500)
        await guest.wait_for_timeout(1500)

        print("\n== подключение ==")
        for page, label in ((host, "host"), (guest, "guest")):
            badge = await page.text_content("#sync-badge")
            check(f"{label}: сокет на связи", "переподключение" not in (badge or ""), f"badge={badge!r}")
        check(
            "хост определён",
            await host.evaluate("document.getElementById('play').title") is not None
            and await host.evaluate("!document.getElementById('src-save').disabled"),
        )
        hit = await host.evaluate(
            "(() => { const el = document.getElementById('chat-input');"
            "const r = el.getBoundingClientRect();"
            "return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === el; })()"
        )
        check("поле чата кликабельно (слой не перекрыт)", hit is True)

        print("\n== чат ==")
        await host.fill("#chat-input", "Привет из теста")
        await host.press("#chat-input", "Enter")
        await host.wait_for_timeout(500)
        check("сообщение видно отправителю", (await host.locator(".msg.mine .bubble").count()) >= 1)
        check(
            "сообщение доставлено гостю",
            "Привет из теста" in await guest.locator("#chat").inner_text(),
        )
        await guest.click("#chat-input")
        await guest.type("#chat-input", "Hello, И тебе привет")
        check(
            "ввод с клавиатуры работает",
            (await guest.input_value("#chat-input")) == "Hello, И тебе привет",
            repr(await guest.input_value("#chat-input")),
        )
        await guest.press("#chat-input", "Enter")
        await guest.wait_for_timeout(500)
        check("ответ доставлен хосту", "И тебе привет" in await host.locator("#chat").inner_text())

        await guest.click(".side-tabs button[data-tab='people']")
        await guest.wait_for_timeout(300)
        check("список зрителей отрисован", (await guest.locator(".person").count()) == 2)
        await guest.click(".side-tabs button[data-tab='chat']")
        await guest.wait_for_timeout(200)

        print("\n== источник ==")
        await host.click("#set-source")
        await host.wait_for_timeout(300)
        check(
            "модалка открылась",
            (await host.evaluate("getComputedStyle(document.getElementById('modal')).display")) == "flex",
        )
        await host.click("#src-url")
        await host.keyboard.type(media_url, delay=10)
        check(
            "URL вводится с клавиатуры",
            (await host.input_value("#src-url")) == media_url,
            repr(await host.input_value("#src-url")),
        )
        await host.fill("#src-title", "Тестовый трек")
        await host.click("#src-save")
        await host.wait_for_timeout(3000)
        for page, label in ((host, "host"), (guest, "guest")):
            info = await page.evaluate(
                "({hidden: document.getElementById('empty').hidden,"
                " dur: document.getElementById('video').duration,"
                " title: document.title})"
            )
            check(f"{label}: источник подхватился", info["hidden"] is True, str(info))
            check(
                f"{label}: метаданные загружены",
                isinstance(info["dur"], (int, float)) and info["dur"] > 100,
                str(info["dur"]),
            )
        check(
            "прокси отдаёт поток с Range",
            (await host.evaluate(
                "fetch(document.querySelector('video').src, {headers:{Range:'bytes=0-1023'}})"
                ".then(r => r.status === 206)"
            ))
            is True,
        )

        print("\n== общая пауза ==")
        await host.click("#play")
        host_pos = guest_pos = 0.0
        drift = 99.0
        for _ in range(30):
            await host.wait_for_timeout(500)
            host_pos = await host.evaluate("document.getElementById('video').currentTime")
            guest_pos = await guest.evaluate("document.getElementById('video').currentTime")
            drift = abs(host_pos - guest_pos)
            if host_pos > 1.0 and drift < 0.35:
                break
        check("host: плеер идёт", host_pos > 1.0, f"pos={host_pos}")
        check("guest: плеер идёт", guest_pos > 1.0, f"pos={guest_pos}")
        check(
            "дрейф ушёл в допуск синхронизации (±0.35с)",
            drift < 0.35,
            f"drift={drift:.2f} host={host_pos:.2f} guest={guest_pos:.2f}",
        )
        for _ in range(40):
            texts = [(await host.text_content("#sync-badge")) or "", (await guest.text_content("#sync-badge")) or ""]
            if all("синхронно" in value.lower() for value in texts):
                break
            await host.wait_for_timeout(500)
        badges = {
            "host": await host.text_content("#sync-badge"),
            "guest": await guest.text_content("#sync-badge"),
        }
        check(
            "индикатор показывает «синхронно»",
            all("синхронно" in (v or "") for v in badges.values()),
            str(badges),
        )

        await guest.click("#play")
        await guest.wait_for_timeout(1000)
        states = {
            "host": await host.evaluate("document.getElementById('video').paused"),
            "guest": await guest.evaluate("document.getElementById('video').paused"),
        }
        check("гость остановил общий плеер", states["host"] and states["guest"], str(states))

        print("\n== перемотка ==")
        await host.evaluate(
            "const s = document.getElementById('seek');"
            "s.value = 500;"
            "s.dispatchEvent(new Event('input'));"
            "window.dispatchEvent(new Event('pointerup'));"
        )
        await host.wait_for_timeout(2000)
        host_pos = await host.evaluate("document.getElementById('video').currentTime")
        guest_pos = await guest.evaluate("document.getElementById('video').currentTime")
        check(
            "перемотка ушла всем",
            abs(host_pos - guest_pos) < 1.5 and host_pos > 30,
            f"host={host_pos:.2f} guest={guest_pos:.2f}",
        )

        print("\n== рассинхронизация и самопочинка ==")
        await host.click("#play")
        await host.wait_for_timeout(1200)
        await guest.evaluate("document.getElementById('video').currentTime += 25")
        await guest.wait_for_timeout(2500)
        drift_after = abs(
            await host.evaluate("document.getElementById('video').currentTime")
            - await guest.evaluate("document.getElementById('video').currentTime")
        )
        check("госта вернуло в общий ритм", drift_after < 1.5, f"drift={drift_after:.2f}")

        print("\n== иконки и доступность ==")
        sprite_ok = await host.evaluate("!!document.getElementById('i-trash')")
        check("спрайт иконок загружен", sprite_ok is True)
        broken = await host.evaluate(
            "Array.from(document.querySelectorAll('svg.ic use'))"
            ".filter(u => !document.querySelector(u.getAttribute('href'))).length"
        )
        check("все ссылки на иконки существуют", broken == 0, f"битых={broken}")
        for control in ("play", "back", "fwd", "mute", "fs", "set-source", "copy-link"):
            info = await host.evaluate(
                f"(() => {{ const el = document.getElementById({control!r});"
                "return {title: el.title, hasSvg: !!el.querySelector('svg.ic'),"
                " w: Math.round(el.getBoundingClientRect().width)}; })()"
            )
            check(
                f"#{control}: иконка с подсказкой",
                bool(info["title"]) and info["hasSvg"],
                str(info),
            )
        check(
            "иконка пуска отрисована",
            (await host.evaluate(
                "document.querySelector('#play use').getAttribute('href')"
            ))
            in {"#i-play", "#i-pause"},
        )
        for control in ("play-center", "sound-toggle"):
            info = await host.evaluate(
                f"(() => {{ const el = document.getElementById({control!r});"
                "return {title: el.title, hasSvg: !!el.querySelector('svg.ic')}; })()"
            )
            check(f"#{control}: иконка с подсказкой", bool(info["title"]) and info["hasSvg"], str(info))

        print("\n== управление поверх видео ==")
        overlay = await host.evaluate(
            "(() => { const v = document.getElementById('video').getBoundingClientRect();"
            " const p = document.getElementById('player').getBoundingClientRect();"
            " const btn = document.getElementById('play').getBoundingClientRect();"
            " return {same: Math.abs(v.top - p.top) < 2 && Math.abs(v.height - p.height) < 2,"
            "  btnInside: btn.top >= v.top && btn.bottom <= v.bottom + 1,"
            "  barVisible: p.height > 40}; })()"
        )
        check("панель лежит поверх видео, а не под ним", overlay["same"], str(overlay))
        check("кнопки панели внутри кадра", overlay["btnInside"] is True, str(overlay))
        check("панель занимает заметную высоту", overlay["barVisible"] is True, str(overlay))
        # Панель прячется во время просмотра и возвращается по наведению.
        await host.evaluate("document.getElementById('video').dispatchEvent(new PointerEvent('pointermove'))")
        await host.wait_for_timeout(200)
        shown = await host.evaluate("!document.getElementById('player').classList.contains('idle')")
        check("панель появляется по наведению", shown is True)
        await host.wait_for_timeout(3200)
        idle = await host.evaluate("document.getElementById('player').classList.contains('idle')")
        check("панель прячется при просмотре", idle is True)
        # Скрытая панель не должна перехватывать клики по самому кадру.
        check(
            "скрытая панель не перехватывает события",
            (await host.evaluate(
                "(() => { const p = document.getElementById('player');"
                " const v = document.getElementById('video');"
                " const r = v.getBoundingClientRect();"
                " const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
                " return !!(hit && p.contains(hit)); })()"
            ))
            is False,
        )
        await host.mouse.move(6, 6)
        await host.wait_for_timeout(200)
        await host.evaluate("document.getElementById('video').dispatchEvent(new PointerEvent('pointermove'))")
        await host.wait_for_timeout(300)
        check(
            "панель вернулась после наведения",
            (await host.evaluate("!document.getElementById('player').classList.contains('idle')"))
            is True,
        )

        print("\n== прочитано и звук ==")
        check(
            "счётчик непрочитанных скрыт, пока всё прочитано",
            (await guest.evaluate("document.getElementById('chat-unread').hidden")) is True,
        )
        # Уводим гостя на вкладку зрителей: чат становится неактивной вкладкой,
        # и входящее сообщение должно засчитаться как непрочитанное.
        await guest.evaluate("document.querySelector('.side-tabs button[data-tab=people]').click()")
        await guest.wait_for_timeout(200)
        await host.fill("#chat-input", "Кто прочитал?")
        await host.press("#chat-input", "Enter")
        await guest.wait_for_timeout(1200)
        unread = await guest.evaluate(
            "(() => { const b = document.getElementById('chat-unread');"
            " return {hidden: b.hidden, text: b.textContent, title: document.title}; })()"
        )
        check("непрочитанное помечено счётчиком", unread["hidden"] is False, str(unread))
        check("счётчик содержит число", unread["text"].strip().isdigit(), str(unread))
        check("счётчик попал в заголовок вкладки", unread["title"].startswith("("), str(unread))
        # Возврат на чат должен снять отметку.
        await guest.evaluate("document.querySelector('.side-tabs button[data-tab=chat]').click()")
        await guest.wait_for_timeout(500)
        check(
            "счётчик сброшен после возврата в чат",
            (await guest.evaluate("document.getElementById('chat-unread').hidden")) is True,
        )
        check(
            "сообщение отмечено прочитанным у отправителя",
            (await host.evaluate(
                "!!Array.from(document.querySelectorAll('.msg.mine .seen'))"
                ".find(n => n.textContent.trim() === 'прочитано')"
            ))
            is True,
        )
        check(
            "у прочитанного сообщения нет класса unseen",
            (await guest.evaluate(
                "document.querySelectorAll('.msg.unseen').length"
            ))
            == 0,
        )
        sound = await guest.evaluate(
            "(() => { const b = document.getElementById('sound-toggle');"
            " return {pressed: b.getAttribute('aria-pressed'),"
            "  href: b.querySelector('use').getAttribute('href'),"
            "  stored: localStorage.getItem('wt_sound')}; })()"
        )
        check("звук сообщений включён по умолчанию", sound["pressed"] == "true", str(sound))
        check("иконка звука соответствует состоянию", sound["href"] == "#i-bell-ring", str(sound))
        check(
            "звук выключается и запоминается",
            (await guest.evaluate(
                "(() => { document.getElementById('sound-toggle').click();"
                " return localStorage.getItem('wt_sound'); })()"
            ))
            == "0",
        )
        guest_del = await guest.evaluate("document.getElementById('delete-room').hidden")
        host_del = await host.evaluate("!document.getElementById('delete-room').hidden")
        check("у гостя нет кнопки удаления", guest_del is True)
        check("у хоста есть кнопка удаления", host_del is True)

        print("\n== мобильный вид (видео + чат) ==")
        await host.set_viewport_size({"width": 390, "height": 844})
        await host.wait_for_timeout(600)
        layout = await host.evaluate(
            "(() => { const v = document.getElementById('video').getBoundingClientRect();"
            " const c = document.getElementById('chat').getBoundingClientRect();"
            " const input = document.getElementById('chat-input').getBoundingClientRect();"
            " return {vTop: v.top, vH: v.height, vW: v.width,"
            " cTop: c.top, cH: c.height, inputTop: input.top,"
            " scroll: document.documentElement.scrollHeight, client: document.documentElement.clientHeight,"
            " logH: Math.round(document.querySelector('.chat-log').getBoundingClientRect().height)}; })()"
        )
        check(
            "видео сверху и не шире экрана",
            layout["vW"] <= 391 and layout["vH"] < 844 * 0.5,
            str({k: round(v, 1) for k, v in layout.items()}),
        )
        # Главный симптом: при открытой клавиатуре dvh уменьшается, и видео
        # раньше схлопывалось. Проверяем, что высота не пропала.
        keyboard = await host.evaluate(
            "(() => { window.innerHeight = 420; window.dispatchEvent(new Event('resize'));"
            " const v = document.getElementById('video').getBoundingClientRect();"
            " return {h: v.height, w: v.width, top: v.top}; })()"
        )
        check(
            "видео не схлопывается при малом экране (клавиатура)",
            keyboard["h"] > 100 and keyboard["w"] > 100,
            f"h={keyboard['h']:.0f} w={keyboard['w']:.0f}",
        )
        check(
            "видео осталось в верхней части экрана",
            keyboard["top"] < 200,
            f"top={keyboard['top']:.0f}",
        )
        await host.evaluate(
            "(() => { window.innerHeight = 844; window.dispatchEvent(new Event('resize')); })()"
        )
        await host.wait_for_timeout(200)
        check(
            "чат виден ниже видео",
            layout["cTop"] > layout["vTop"] and layout["cH"] > 100,
            str({k: round(v, 1) for k, v in layout.items()}),
        )
        check(
            "поле ввода чата в экране",
            0 < layout["inputTop"] < 844,
            f"inputTop={layout['inputTop']:.0f}",
        )
        check(
            "страница не прокручивается по вертикали",
            layout["scroll"] <= layout["client"] + 2,
            f"scroll={layout['scroll']} client={layout['client']}",
        )
        check(
            "лог чата не схлопнут",
            layout["logH"] > 80,
            f"logH={layout['logH']}",
        )
        await host.screenshot(path="tests/screenshot-mobile.png")
        await guest.set_viewport_size({"width": 390, "height": 844})
        await guest.wait_for_timeout(500)
        await guest.screenshot(path="tests/screenshot-mobile-guest.png")
        await host.set_viewport_size({"width": 1280, "height": 800})
        await guest.set_viewport_size({"width": 1280, "height": 800})
        await host.wait_for_timeout(500)
        await host.screenshot(path="tests/screenshot-host.png")
        await guest.screenshot(path="tests/screenshot-guest.png")
        await guest.evaluate("document.querySelector('.side-tabs button[data-tab=people]').click()")
        await host.wait_for_timeout(300)
        await guest.screenshot(path="tests/screenshot-people.png")
        await guest.evaluate("document.querySelector('.side-tabs button[data-tab=chat]').click()")
        await host.wait_for_timeout(200)

        print("\n== удаление комнаты ==")
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as api:

            async def room_alive() -> bool:
                return (await api.get(f"/api/rooms/{room_id}")).status_code == 200

            await host.click("#delete-room")
            await host.wait_for_timeout(300)
            check(
                "подтверждение удаления открылось",
                (await host.evaluate("!document.getElementById('confirm').hidden")) is True,
            )
            check("комната жива до подтверждения", await room_alive())
            await host.click("#confirm-cancel")
            await host.wait_for_timeout(200)
            check(
                "отмена закрывает окно и оставляет комнату",
                (await host.evaluate("document.getElementById('confirm').hidden")) is True
                and await room_alive(),
            )

            await host.click("#delete-room")
            await host.wait_for_timeout(200)
            await host.click("#confirm-ok")
            try:
                await guest.wait_for_url("**/", timeout=15000)
                check("гость вернулся в лобби после удаления", True)
            except Exception as err:
                check("гость вернулся в лобби после удаления", False, str(err)[:80])
            try:
                await host.wait_for_url("**/", timeout=15000)
                check("хост тоже покинул комнату", True)
            except Exception as err:
                check("хост тоже покинул комнату", False, str(err)[:80])
            check("комната удалена на сервере", not await room_alive())

        print("\n== вход в лобби (реальные клавиши) ==")
        lobby_ctx = await browser.new_context()
        await lobby_ctx.add_init_script("localStorage.setItem('wt_uid','br-lobby');")
        lobby = await lobby_ctx.new_page()
        collect(lobby, "lobby", errors)

        # Проверка устойчивости к медленному соединению: интерфейс лобби
        # обязан работать, даже если сервер не отвечает на фоновые запросы.
        await lobby.route("**/api/capabilities", lambda route: asyncio.sleep(30) or route.abort())
        await lobby.route("**/api/rooms", lambda route: asyncio.sleep(30) or route.abort())
        await lobby.goto(BASE_URL)
        await lobby.wait_for_timeout(1500)
        check(
            "лобби живо при недоступном сервере (кнопки на месте)",
            (await lobby.evaluate("typeof document.getElementById('create').onclick")) == "function",
        )
        await lobby.fill("#name", "Sasha")
        await lobby.wait_for_timeout(300)
        enabled = await lobby.evaluate("!document.getElementById('create').disabled")
        check("кнопка создания не заблокирована зависшим запросом", enabled is True)
        check(
            "поле ввода отвечает при зависшем сервере",
            (await lobby.input_value("#name")) == "Sasha",
        )
        await lobby.unroute("**/api/capabilities")
        await lobby.unroute("**/api/rooms")
        await lobby.reload()
        await lobby.wait_for_timeout(1200)
        for field, text in (
            ("name", "Sasha"),
            ("title", "Friday Movie"),
        ):
            await lobby.click(f"#{field}")
            await lobby.keyboard.type(text, delay=25)
            got = await lobby.input_value(f"#{field}")
            check(f"лобби: ввод в #{field} с клавиатуры", got == text, f"получено {got!r}")
        check(
            "в лобби нет поля ввода кода",
            (await lobby.evaluate("!document.getElementById('join-id')")) is True,
        )
        check(
            "кнопка обновления на месте и подписана",
            (await lobby.evaluate(
                "(() => { const b = document.getElementById('refresh');"
                " return !!b && b.textContent.trim().length > 0; })()"
            ))
            is True,
        )
        await lobby.screenshot(path="tests/screenshot-lobby.png")
        await lobby.click("#name")
        await lobby.keyboard.type("Host", delay=25)
        await lobby.keyboard.press("Enter")
        try:
            await lobby.wait_for_url("**/r/**", timeout=8000)
            check("лобби: Enter создаёт комнату", True)
        except Exception as err:
            check("лобби: Enter создаёт комнату", False, str(err)[:80])
        await lobby_ctx.close()

        await browser.close()

    server.shutdown()

    print("\n== консоль браузера ==")
    interesting = [e for e in errors if "favicon" not in e]
    for item in interesting:
        print(f"    {item}")
    check("нет ошибок в консоли", not interesting, "; ".join(interesting[:3]))

    print(f"\n{'=' * 46}\nуспешно: {len(PASSED)}   провалено: {len(FAILED)}")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(asyncio.run(main()))