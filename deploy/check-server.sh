#!/usr/bin/env bash
# Разведка сервера перед установкой.
# Ничего не меняет — только смотрит и печатает.
#
# Запуск на сервере:
#   bash deploy/check-server.sh

echo "=============================================="
echo " Проверка сервера для системы наград"
echo "=============================================="
echo

echo "── Система ───────────────────────────────────"
. /etc/os-release 2>/dev/null && echo "  ОС: ${PRETTY_NAME:-неизвестно}"
echo "  Архитектура: $(uname -m)"
echo "  Свободно на диске: $(df -h / | awk 'NR==2 {print $4}')"
echo "  Свободно памяти:   $(free -h 2>/dev/null | awk 'NR==2 {print $7}' || echo '—')"
echo

echo "── Docker ────────────────────────────────────"
if command -v docker >/dev/null 2>&1; then
    echo "  docker: $(docker --version)"
    if docker compose version >/dev/null 2>&1; then
        echo "  compose: $(docker compose version | head -1)   ← команда 'docker compose'"
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "  compose: $(docker-compose --version)   ← команда 'docker-compose' (старая)"
    else
        echo "  compose: НЕ НАЙДЕН — поставьте docker-compose-plugin"
    fi
    if docker ps >/dev/null 2>&1; then
        echo "  запуск без sudo: да"
    else
        echo "  запуск без sudo: НЕТ — команды нужно выполнять через sudo"
    fi
else
    echo "  docker НЕ УСТАНОВЛЕН"
fi
echo

echo "── Где работает nginx ────────────────────────"
HOST_NGINX=no
DOCKER_NGINX=no
if command -v nginx >/dev/null 2>&1; then
    HOST_NGINX=yes
    echo "  На сервере: $(nginx -v 2>&1)"
    echo "  Конфиги сайтов:"
    ls -1 /etc/nginx/sites-enabled/ 2>/dev/null | sed 's/^/    /' || echo "    (каталога sites-enabled нет, смотрите conf.d)"
    ls -1 /etc/nginx/conf.d/*.conf 2>/dev/null | sed 's/^/    /'
fi
if command -v docker >/dev/null 2>&1 && docker ps 2>/dev/null | grep -qiE 'nginx|traefik|caddy'; then
    DOCKER_NGINX=yes
    echo "  В Docker:"
    docker ps --format '    {{.Names}}  ({{.Image}})' 2>/dev/null | grep -iE 'nginx|traefik|caddy'
fi

echo
if [ "$HOST_NGINX" = yes ] && [ "$DOCKER_NGINX" = no ]; then
    echo "  ВЫВОД: берите deploy/nginx-host.conf"
elif [ "$DOCKER_NGINX" = yes ] && [ "$HOST_NGINX" = no ]; then
    echo "  ВЫВОД: берите deploy/nginx-docker.conf"
elif [ "$HOST_NGINX" = yes ] && [ "$DOCKER_NGINX" = yes ]; then
    echo "  ВЫВОД: найдены оба — пришлите этот вывод, разберёмся вместе"
else
    echo "  ВЫВОД: nginx не найден — пришлите этот вывод"
fi
echo

echo "── Порт 8000 ─────────────────────────────────"
if command -v ss >/dev/null 2>&1; then
    if ss -ltnp 2>/dev/null | grep -q ':8000 '; then
        echo "  ЗАНЯТ:"
        ss -ltnp 2>/dev/null | grep ':8000 ' | sed 's/^/    /'
        echo "  Выберите другой порт в docker-compose.yml и в конфиге nginx."
    else
        echo "  свободен"
    fi
else
    echo "  не смог проверить (нет ss)"
fi
echo

echo "── Домены в сертификатах Let's Encrypt ───────"
ls -1 /etc/letsencrypt/live/ 2>/dev/null | grep -v README | sed 's/^/  /' || echo "  (каталог недоступен или certbot не используется)"
echo
echo "Пришлите этот вывод — по нему подберём конфиг."
