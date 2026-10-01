#!/bin/sh
# Готовит каталог данных и только потом отбрасывает привилегии.
#
# Каталог данных часто приходит из bind-mount и принадлежит root, поэтому
# непривилегированный пользователь не может создать кэш и SQLite-базу.
# Здесь мы стартуем от root, гарантируем права и уходим в обычного пользователя.
set -e

APP_USER="${WT_APP_USER:-wt}"
DATA_DIR="${WT_DATA_DIR:-/app/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR" "$DATA_DIR/cache"

    # Рекурсивный chown нужен редко (том только что создан или раньше писал
    # root), поэтому не тратим на него время при каждом старте.
    if [ "$(stat -c '%u' "$DATA_DIR")" != "$(id -u "$APP_USER")" ]; then
        echo "watch-together: выдаю права на $DATA_DIR пользователю $APP_USER"
        chown -R "$APP_USER":"$APP_USER" "$DATA_DIR"
    fi

    # Каталог может быть смонтирован read-only или принадлежать другому
    # пользователю: тогда предупреждаем, но запускаемся.
    if [ ! -w "$DATA_DIR" ]; then
        echo "watch-together: ВНИМАНИЕ — $DATA_DIR недоступен на запись." >&2
        echo "watch-together: проверьте права: chown -R 1000:1000 data" >&2
    fi

    # setpriv вместо su-exec: su-exec удалили из Debian trixie, а setpriv есть
    # в util-linux и не требует ни пароля, ни PAM. --reuid/--regid нужны,
    # потому что login-шелл у wt не задан.
    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid "$APP_USER" --regid "$APP_USER" --init-groups -- "$@"
    fi
    if command -v su-exec >/dev/null 2>&1; then
        exec su-exec "$APP_USER" "$@"
    fi
    if command -v gosu >/dev/null 2>&1; then
        exec gosu "$APP_USER" "$@"
    fi

    echo "watch-together: ВНИМАНИЕ — нет setpriv/su-exec/gosu, запускаюсь от root." >&2
    exec "$@"
fi

exec "$@"
