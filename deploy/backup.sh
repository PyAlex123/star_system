#!/usr/bin/env bash
# Резервная копия системы наград: база звёзд, отметки преподавателей и загруженные фотографии.
#
# Запуск вручную:
#   bash deploy/backup.sh
#
# Каждую ночь в 3:30 (crontab -e):
#   30 3 * * * cd /opt/school-stars && bash deploy/backup.sh >> backups/backup.log 2>&1

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${PROJECT_DIR}/data"
BACKUP_DIR="${PROJECT_DIR}/backups"
KEEP=14          # сколько копий хранить

if [ ! -d "$DATA_DIR" ]; then
    echo "Нет каталога с данными: $DATA_DIR"
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y-%m-%d_%H-%M)"
ARCHIVE="${BACKUP_DIR}/stars_${STAMP}.tar.gz"

# База отметок (SQLite): живой файл посреди записи может попасть в архив несогласованным,
# поэтому кладём согласованный снимок. Его делает приложение — по команде ниже и само каждую ночь.
# При восстановлении приложение само сделает снимок базой (backend/checkin/store.py).
if command -v docker >/dev/null 2>&1 \
        && docker compose -f "${PROJECT_DIR}/docker-compose.yml" ps --status running -q stars 2>/dev/null | grep -q .; then
    if docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T stars \
            python -m backend.checkin.store snapshot >/dev/null; then
        echo "Снимок базы отметок обновлён"
    else
        echo "Снимок базы отметок не обновился — в архив пойдёт ночной"
    fi
else
    echo "Контейнер не запущен — в архив пойдёт ночной снимок базы отметок"
fi

tar -czf "$ARCHIVE" -C "$PROJECT_DIR" \
    --exclude=data/checkin.sqlite --exclude=data/checkin.sqlite-journal data

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "$(date '+%Y-%m-%d %H:%M')  создана копия: $(basename "$ARCHIVE") ($SIZE)"

# Чистим старые копии, оставляя последние $KEEP
COUNT="$(ls -1 "$BACKUP_DIR"/stars_*.tar.gz 2>/dev/null | wc -l)"
if [ "$COUNT" -gt "$KEEP" ]; then
    ls -1t "$BACKUP_DIR"/stars_*.tar.gz | tail -n +$((KEEP + 1)) | while read -r old; do
        rm -f "$old"
        echo "  удалена старая копия: $(basename "$old")"
    done
fi

echo "Всего копий: $(ls -1 "$BACKUP_DIR"/stars_*.tar.gz 2>/dev/null | wc -l)"
echo
echo "Восстановление:"
echo "  docker compose down"
echo "  rm -rf data && tar -xzf $ARCHIVE -C $PROJECT_DIR"
echo "  docker compose up -d"
