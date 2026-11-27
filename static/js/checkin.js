/* Отметки преподавателей в панели. Подключается после teacher.js, schedule.js
   и stats.js и пользуется их state, api(), openModal(), toast() и т. п.

   Здесь: точка школы на карте, код сотрудника для бота (QR, карточка), Telegram и ставка
   сотрудника, срок действия занятий.
   Права проверяет сервер; панель лишь прячет то, что всё равно недоступно. */

/** Раздел «Отметки» (приход, опоздания, часы, деньги) — только администратор. */
function canManageCheckins() {
  return isAdmin();
}

/* ── Срок действия занятия ────────────────────────────────── */

/** Действует ли занятие в дату ГГГГ-ММ-ДД. Пустое «с» — с того дня, как его внесли. */
function entryActiveOn(entry, iso) {
  const from = entry.valid_from || (entry.created_at || "").slice(0, 10);
  const to = entry.valid_to || "";
  return (!from || from <= iso) && (!to || iso <= to);
}

/** Стоит ли занятие в таблице расписания в этот день — только по явному сроку «с … по …»:
 *  поставленное в среду на понедельник видно и в понедельник этой недели (как на сервере). */
function entryShownOn(entry, iso) {
  return entryActiveOn({ ...entry, created_at: "" }, iso);
}

function shortDate(iso) {
  const [y, m, d] = iso.split("-");
  return y === String(new Date().getFullYear()) ? `${d}.${m}` : `${d}.${m}.${y.slice(2)}`;
}

/** Подпись срока на карточке занятия: «до 31.12», «с 01.12», «01.12–31.12». Без срока — пусто. */
function entryBoundsLabel(entry, today = isoDate(new Date())) {
  const from = entry.valid_from && entry.valid_from > today ? entry.valid_from : "";
  const to = entry.valid_to || "";
  if (from && to) return `${shortDate(from)}–${shortDate(to)}`;
  if (to) return `до ${shortDate(to)}`;
  if (from) return `с ${shortDate(from)}`;
  return "";
}

/** День до даты ГГГГ-ММ-ДД — для «старое занятие действует по…». */
function dayBefore(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d - 1));
}

/* ── Точка школы на карте ─────────────────────────────────── */

/** Координаты из того, что вставили: «41.311, 69.279», «41.311 69.279», ссылка Google Maps.
    Пусто — null (очистить). Непонятное — ошибка с подсказкой. */
function parseCoords(text) {
  const value = String(text || "").trim();
  if (!value) return null;
  const fromLink = value.match(/@(-?\d{1,3}(?:\.\d+)?),\s*(-?\d{1,3}(?:\.\d+)?)/);
  const plain = value.match(/^(-?\d{1,3}(?:\.\d+)?)°?\s*[NnСс]?\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)°?\s*[EeВв]?$/);
  const match = fromLink || plain;
  if (!match) throw new Error("Не получилось прочитать координаты. Вставьте их как в картах: 41.311081, 69.279737");
  const lat = Number(match[1]);
  const lon = Number(match[2]);
  if (!(lat >= -90 && lat <= 90) || !(lon >= -180 && lon <= 180)) {
    throw new Error("Широта — от −90 до 90, долгота — от −180 до 180. Проверьте, не перепутан ли порядок");
  }
  return { lat, lon };
}

function mapLink(lat, lon) {
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=18/${lat}/${lon}`;
}

function formatCoords(lat, lon) {
  return lat === null || lat === undefined ? "" : `${Number(lat).toFixed(6)}, ${Number(lon).toFixed(6)}`;
}

let locationTarget = null;

function openSchoolLocation() {
  closeMenus();
  const school = schoolById(activeSchool);
  if (!school) return;
  locationTarget = school.id;
  $("locationSchool").textContent = school.name;
  $("locationCoords").value = formatCoords(school.lat, school.lon);
  $("locationRadius").value = school.radius_m || 150;
  $("locationAddress").value = school.address || "";
  $("locationHere").hidden = !(navigator.geolocation && window.isSecureContext);
  updateLocationPreview();
  openModal("modalLocation");
}

function updateLocationPreview() {
  const error = $("locationError");
  const link = $("locationMap");
  try {
    const point = parseCoords($("locationCoords").value);
    error.hidden = true;
    link.hidden = !point;
    if (point) link.href = mapLink(point.lat, point.lon);
    return point;
  } catch (exc) {
    error.textContent = exc.message;
    error.hidden = false;
    link.hidden = true;
    return undefined;
  }
}

function useMyLocation() {
  const button = $("locationHere");
  button.disabled = true;
  navigator.geolocation.getCurrentPosition((position) => {
    button.disabled = false;
    $("locationCoords").value = formatCoords(position.coords.latitude, position.coords.longitude);
    updateLocationPreview();
    toast(`Точка определена с точностью около ${fmt(Math.round(position.coords.accuracy))} м`, "success");
  }, () => {
    button.disabled = false;
    toast("Браузер не дал определить место — вставьте координаты из карт", "error");
  }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
}

async function saveSchoolLocation() {
  const point = updateLocationPreview();
  if (point === undefined) return toast($("locationError").textContent, "error");
  const radius = parseInt($("locationRadius").value, 10);
  if (!(radius >= 30 && radius <= 2000)) return toast("Радиус — от 30 до 2000 метров", "error");
  try {
    const result = await api(`/api/checkin/schools/${locationTarget}/location`, {
      method: "PUT",
      json: { address: $("locationAddress").value.trim(), lat: point ? point.lat : null,
              lon: point ? point.lon : null, radius_m: radius },
    });
    Object.assign(schoolById(locationTarget) || {}, result.school);
    closeModal("modalLocation");
    renderSchoolMenu();
    if (typeof renderSchoolsPage === "function") renderSchoolsPage();
    toast(point ? "Точка школы сохранена" : "Координаты школы очищены", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Telegram сотрудника: код для бота, QR и карточка (только администратор) ── */

/** bound — привязан, blocked — остановил бота, none — не привязан (код — на карточке сотрудника). */
function tgState(user) {
  if (user.tg_id) return user.tg_blocked_at ? "blocked" : "bound";
  return "none";
}

/** «ABCD 2345» — код так же, как на карточке. */
function codeText(code) {
  return code ? `${code.slice(0, 4)} ${code.slice(4)}` : "";
}

/** Ссылка сотрудника в бот — туда же ведёт QR карточки. Пусто, пока бот не подключён. */
function staffLink(code, bot = state.bot_username) {
  return bot && code ? `https://t.me/${bot}?start=inv_${code}` : "";
}

/** Метки под именем в списке сотрудников. */
function checkinBadges(user) {
  const tags = [];
  if (user.checkin_tracked) {
    const tg = tgState(user);
    if (tg === "bound") tags.push(`<span class="tag tag-ok">Telegram${user.tg_username ? " @" + escapeHtml(user.tg_username) : ""}</span>`);
    else if (tg === "blocked") tags.push(`<span class="tag">остановил бота</span>`);
    else tags.push(`<span class="tag">Telegram не привязан</span>`);
  }
  if (user.hourly_rate !== null && user.hourly_rate !== undefined) tags.push(`<span class="tag">${fmt(user.hourly_rate)} сум/ч</span>`);
  return tags.join("");
}

/** QR и код для бота — как на карточке сотрудника. src — адрес картинки QR. */
function staffCodeBlock(code, src, name) {
  return `<div class="tg-card">
      <img class="tg-qr" src="${src}" alt="QR-код для бота: ${escapeHtml(name)}" width="120" height="120">
      <div class="tg-info">
        <span class="caps">Код для бота</span>
        <b class="staff-code">${escapeHtml(codeText(code))}</b>
        <span class="tg-hint">Наведите камеру телефона на QR — откроется бот. Или отправьте боту этот код.</span>
      </div>
    </div>`;
}

/** Блок «Вход в Telegram-бот» в окне сотрудника: код, QR, ссылка, карточка, новый код, отвязка. */
function renderUserTelegram(user) {
  const host = $("userTelegram");
  if (!user) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  const tg = tgState(user);
  const status = {
    bound: `Telegram привязан${user.tg_username ? ": @" + escapeHtml(user.tg_username) : ""}.`,
    blocked: "Сотрудник остановил бота — сообщения не доходят. Попросите открыть бот и нажать «Старт».",
    none: "Telegram не привязан: сотрудник сканирует QR с карточки или присылает боту код.",
  }[tg];
  const link = staffLink(user.bot_code);
  const qr = `${BASE}/api/users/${user.id}/qr.png?size=6&v=${encodeURIComponent(user.bot_code || "")}`;
  host.innerHTML = `
    <span class="field-label">Вход в Telegram-бот</span>
    ${staffCodeBlock(user.bot_code, qr, user.name)}
    <p class="tg-status">${status}${link ? "" : " Ссылка в бот появится, когда к сайту подключат бота."}</p>
    <div class="tg-acts">
      <a class="btn btn-ghost btn-sm" href="${BASE}/cards/staff/${user.id}" target="_blank" rel="noopener">${icon("printer", "ic ic-sm")}Карточка</a>
      ${link ? `<button type="button" class="btn btn-ghost btn-sm" data-link="${escapeHtml(link)}" onclick="copyText(this.dataset.link)">${icon("copy", "ic ic-sm")}Ссылка</button>` : ""}
      <button type="button" class="btn btn-ghost btn-sm" onclick="newStaffCode(${user.id})">${icon("refresh", "ic ic-sm")}Новый код</button>
      ${tg === "none" ? "" : `<button type="button" class="btn btn-ghost btn-sm" onclick="unbindTelegram(${user.id})">${icon("logout", "ic ic-sm")}Отвязать</button>`}
    </div>`;
  host.hidden = false;
}

function refreshUserWindow(id) {
  if ($("modalUser").classList.contains("open") && userTarget === id) {
    renderUserTelegram((state.users || []).find((u) => u.id === id));
  }
}

/** Новый код — как «новый QR» у ученика: старая карточка перестаёт работать. */
function newStaffCode(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user) return;
  confirmAction("Выдать новый код?",
    `${escapeHtml(user.name)}: старая карточка сотрудника перестанет работать, Telegram отвяжется.
     Распечатайте новую карточку — сотрудник отсканирует её заново.`,
    async () => {
      await api(`/api/users/${id}/code`, { method: "POST" });
      await loadState();
      refreshUserWindow(id);
      toast("Новый код выдан — распечатайте карточку", "success");
    }, "Выдать новый код");
}

function unbindTelegram(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user) return;
  confirmAction("Отвязать Telegram?",
    `${escapeHtml(user.name)} перестанет отмечаться с этого аккаунта. Код на карточке остаётся прежним:
     с нового телефона сотрудник отсканирует ту же карточку. Карточку потеряли — «Новый код».`,
    async () => {
      await api(`/api/users/${id}/telegram`, { method: "DELETE" });
      await loadState();
      refreshUserWindow(id);
      toast("Telegram отвязан", "success");
    }, "Отвязать");
}

/** Ставка из поля: пусто — без ставки (null), иначе целое число сум. */
function readRate() {
  const raw = $("userRate").value.trim().replace(/\s/g, "");
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0 || value > 10000000) {
    throw new Error("Ставка — целое число сум от 0 до 10 000 000");
  }
  return value;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("Скопировано", "success");
}
