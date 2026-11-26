/* Отметки преподавателей в панели. Подключается после teacher.js, schedule.js
   и stats.js и пользуется их state, api(), openModal(), toast() и т. п.

   Сейчас здесь подготовка: точка школы на карте, Telegram и ставка сотрудника,
   срок действия занятий. Сам раздел «Отметки» появится здесь же.
   Права проверяет сервер; панель лишь прячет то, что всё равно недоступно. */

const INVITE_DAYS = 7;

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

/* ── Telegram и ставка сотрудника (только администратор) ──── */

/** bound — привязан, blocked — заблокировал бота, pending — ждём по ссылке, expired, none. */
function inviteState(user, now = Date.now()) {
  if (user.tg_id) return user.tg_blocked_at ? "blocked" : "bound";
  if (!user.invite_code || !user.invite_created_at) return "none";
  const age = now - Date.parse(user.invite_created_at);
  return age > INVITE_DAYS * 86400000 ? "expired" : "pending";
}

function inviteUntil(user) {
  const created = Date.parse(user.invite_created_at || "");
  return Number.isNaN(created) ? "" : formatDay(isoDate(new Date(created + INVITE_DAYS * 86400000)));
}

/** Метки под именем в списке сотрудников. */
function checkinBadges(user) {
  const tags = [];
  if (user.checkin_tracked) {
    const tg = inviteState(user);
    if (tg === "bound") tags.push(`<span class="tag tag-ok">Telegram${user.tg_username ? " @" + escapeHtml(user.tg_username) : ""}</span>`);
    else if (tg === "blocked") tags.push(`<span class="tag">заблокировал бота</span>`);
    else if (tg === "pending") tags.push(`<span class="tag tag-accent">ждём по ссылке</span>`);
    else if (tg === "expired") tags.push(`<span class="tag">ссылка истекла</span>`);
    else tags.push(`<span class="tag">Telegram не привязан</span>`);
  }
  if (user.hourly_rate !== null && user.hourly_rate !== undefined) tags.push(`<span class="tag">${fmt(user.hourly_rate)} сум/ч</span>`);
  return tags.join("");
}

/** Строка о Telegram в окне сотрудника и кнопки к ней. */
function renderUserTelegram(user) {
  const host = $("userTelegram");
  if (!user) {
    host.hidden = true;
    return;
  }
  const tg = inviteState(user);
  const text = {
    bound: `Telegram привязан${user.tg_username ? ": @" + escapeHtml(user.tg_username) : ""}.`,
    blocked: "Сотрудник заблокировал бота — сообщения не доходят. Отвяжите и выдайте новую ссылку.",
    pending: `Ссылка выдана, ждём, пока сотрудник её откроет. Действует до ${escapeHtml(inviteUntil(user))}.`,
    expired: "Ссылка истекла — выдайте новую.",
    none: "Telegram не привязан.",
  }[tg];
  const button = tg === "bound" || tg === "blocked"
    ? `<button type="button" class="btn btn-ghost btn-sm" onclick="unbindTelegram(${user.id})">${icon("logout", "ic ic-sm")}Отвязать</button>`
    : `<button type="button" class="btn btn-ghost btn-sm" onclick="inviteUser(${user.id})">${icon("send", "ic ic-sm")}${tg === "none" ? "Ссылка-приглашение" : "Новая ссылка"}</button>`;
  host.innerHTML = `<p>${text}</p>${button}`;
  host.hidden = false;
}

let lastInviteLink = "";

async function inviteUser(id) {
  try {
    const result = await api(`/api/users/${id}/invite`, { method: "POST" });
    await loadState();
    const user = (state.users || []).find((u) => u.id === id) || result.user;
    if ($("modalUser").classList.contains("open") && userTarget === id) {
      // Приглашение включает отметки (кроме администратора) — не выключить их при сохранении окна
      if (user.role !== "superadmin") $("userTracked").checked = true;
      renderUserTelegram(user);
    }
    lastInviteLink = result.link || "";
    const name = escapeHtml(user.name);
    if (!result.link) {
      showInfo("Telegram-бот ещё не подключён",
        `<p>${user.role === "superadmin" ? "" : `${name} теперь отмечается: уроки появятся в отметках. `}
         Ссылка-приглашение будет доступна, когда к сайту подключат Telegram-бота —
         попросите того, кто обслуживает сервер.</p>`);
      return;
    }
    showInfo("Ссылка-приглашение",
      `<p>Отправьте её сотруднику (${name}) в Telegram. Ссылка действует ${result.days}
       ${plural(result.days, "день", "дня", "дней")} и срабатывает один раз.</p>
       <div class="invite-box"><b>${escapeHtml(result.link)}</b>
       <button type="button" class="btn btn-sm" onclick="copyText(lastInviteLink)">${icon("clipboard", "ic ic-sm")}Скопировать</button></div>`);
  } catch (error) {
    toast(error.message, "error");
  }
}

function unbindTelegram(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user) return;
  confirmAction("Отвязать Telegram?",
    `${escapeHtml(user.name)} перестанет отмечаться с этого аккаунта. Для нового телефона выдайте новую ссылку-приглашение.`,
    async () => {
      await api(`/api/users/${id}/telegram`, { method: "DELETE" });
      await loadState();
      if ($("modalUser").classList.contains("open") && userTarget === id) {
        renderUserTelegram((state.users || []).find((u) => u.id === id));
      }
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
