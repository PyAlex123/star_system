/* Раздел «Отметки преподавателей» в панели: сегодня, очередь решений, отчёт за месяц,
   календарь (праздники, отмены, замены, разовые уроки), журнал правок, настройки.
   Подключается после checkin.js и пользуется state, api(), openModal(), toast() и т. п.

   Видят раздел администратор и учитель с поручением «ведёт отметки» (canManageCheckins);
   права проверяет сервер. Данные раздела — в отдельной базе отметок, поэтому раздел
   грузит их сам, а не из /api/state. Любое ручное решение — с причиной. */

const CK_TABS = ["today", "queue", "report", "calendar", "audit", "settings"];
const CK_STATES = {
  ok: "пришёл", late: "опоздал", missing: "нет отметки", review: "на проверке",
  now: "идёт", ahead: "впереди", cancelled: "отменён",
};
const CK_MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
                   "сентябрь", "октябрь", "ноябрь", "декабрь"];
const CK_KIND_NAMES = { holiday: "Праздник", cancel: "Отмена", substitute: "Замена" };
const CK_KIND_HINTS = {
  holiday: "Уроков в эти дни нет — во всех школах или в одной. Часы за них не считаются.",
  cancel: "Отменить занятие, все уроки преподавателя (болеет) или уроки одной школы. Выберите хотя бы что-то одно.",
  substitute: "Уроки ведёт другой преподаватель: отметится и получит часы он. Выберите, кого заменяют, и кто заменяет.",
};
const CK_ACTIONS = {
  approve: { title: "Засчитать", sub: "Урок войдёт в часы.", button: "Засчитать" },
  reject: { title: "Не засчитывать", sub: "Урок не войдёт в часы.", button: "Не засчитывать" },
  cancel: { title: "Отменить урок", sub: "Урока не было и не будет — часы не считаются.", button: "Отменить урок" },
  reopen: { title: "Вернуть урок", sub: "Отменённый урок снова ждёт отметки или решения.", button: "Вернуть" },
  hours: { title: "Часы вручную", sub: "Ручные часы главнее статуса урока.", button: "Сохранить" },
};

let ckTab = "today";
let ckDay = null;            // ГГГГ-ММ-ДД; null — сегодня по часам школ
let ckMonth = null;          // ГГГГ-ММ;   null — текущий
let ckSchool = "";           // фильтр «Сегодня» по школе
let ckOverview = null;
let ckTeachers = null;
let ckQueue = [];
let ckPicked = new Set();
let ckAudit = [];
let ckAuditMore = false;
let ckLessonId = null;
let ckDecision = null;       // { ids, action }
let ckExKind = "holiday";
let ckTicket = 0;

/* ── Чистые помощники (проверяются тестами в Node) ───────── */

function ckStateLabel(stateName) { return CK_STATES[stateName] || stateName; }

/** Ссылка на сообщение в закрытой супергруппе: t.me/c/<номер без -100>/<сообщение>. */
function tgMessageLink(chatId, messageId) {
  const chat = String(chatId || "");
  if (!messageId || !chat.startsWith("-100")) return "";
  return `https://t.me/c/${chat.slice(4)}/${messageId}`;
}

function shiftMonth(ym, step) {
  const [y, m] = ym.split("-").map(Number);
  const date = new Date(y, m - 1 + step, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function shiftDay(iso, step) {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + step));
}

function monthLabel(ym) {
  const [y, m] = ym.split("-").map(Number);
  return `${CK_MONTHS[m - 1]} ${y}`;
}

function ckHours(value) { return String(Math.round(Number(value || 0) * 100) / 100).replace(".", ","); }
function ckMoney(value) { return value === null || value === undefined ? "—" : `${fmt(value)} сум`; }

const CK_FIELD_NAMES = {
  status: "статус", hours_override: "часы", hourly_rate: "ставка", checkin_tracked: "отмечается",
  checkin_manager: "ведёт отметки", tg_id: "Telegram", tg_username: "Telegram", late_minutes: "опоздание",
  lat: "широта", lon: "долгота", radius_m: "радиус", address: "адрес", report_chat_id: "группа",
  grace_minutes: "не опоздание, мин", checkin_window_before_min: "отметка заранее, мин",
  video_note_deadline_sec: "время на кругляшок, с", review_after_min: "на проверку через, мин",
  merge_gap_min: "перерыв между уроками, мин", lesson_horizon_days: "уроки вперёд, дней",
  notify_teacher_late: "напоминать преподавателю",
};
const CK_STATUS_NAMES = {
  planned: "впереди", checked_in: "отметился", late: "опоздал", no_checkin: "нет отметки",
  needs_review: "на проверке", approved: "засчитан", rejected: "не засчитан", cancelled: "отменён",
};
const CK_WHAT = {
  "lesson:approve": "засчитал урок", "lesson:reject": "не засчитал урок", "lesson:cancel": "отменил урок",
  "lesson:reopen": "вернул урок", "lesson:hours": "изменил часы урока", "lesson:create": "добавил разовый урок",
  "exception:create": "добавил в календарь", "exception:delete": "убрал из календаря",
  "settings:update": "изменил настройки", "settings:report_chat": "подключил группу «Отметки»",
  "school:location": "задал место школы", "user:update": "изменил сотрудника", "user:invite": "выдал приглашение",
  "user:unbind_telegram": "отвязал Telegram", "user:telegram_bound": "привязал Telegram",
};

function ckValue(field, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "status") return CK_STATUS_NAMES[value] || value;
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (field === "hours_override") return `${ckHours(value)} ч`;
  if (field === "hourly_rate") return `${fmt(value)} сум/ч`;
  return String(value);
}

/** Запись журнала правок словами: «засчитал урок», «статус: нет отметки → засчитан». */
function auditSummary(item) {
  const what = CK_WHAT[`${item.entity}:${item.action}`] || `${item.entity}: ${item.action}`;
  const before = item.before || {};
  const after = item.after || {};
  if (item.entity === "exception") {
    const row = item.after || item.before || {};
    const dates = row.date_from && row.date_to && row.date_from !== row.date_to
      ? `${row.date_from} — ${row.date_to}` : (row.date_from || "");
    return { what, changes: row.kind ? [`${(CK_KIND_NAMES[row.kind] || row.kind).toLowerCase()}: ${dates}`] : [] };
  }
  const changes = Object.keys(CK_FIELD_NAMES)
    .filter((field) => field in after && JSON.stringify(before[field]) !== JSON.stringify(after[field]))
    .filter((field) => !(field === "tg_username" && "tg_id" in after))
    .map((field) => [field, ckValue(field, before[field]), ckValue(field, after[field])])
    .filter(([, from, to]) => from !== to)
    .map(([field, from, to]) => `${CK_FIELD_NAMES[field]}: ${from} → ${to}`);
  return { what, changes };
}

/* ── Загрузка и вкладки ───────────────────────────────────── */

async function refreshCheckinBadge() {
  if (!canManageCheckins()) return;
  try {
    ckOverview = await api("/api/checkin/overview");
  } catch {
    return;
  }
  const count = ckOverview.counts.queue;
  for (const id of ["checkinNavCount", "checkinQueueCount"]) {
    $(id).textContent = count > 99 ? "99+" : String(count);
    $(id).hidden = !count;
  }
}

function setCheckinTab(tab) {
  ckTab = CK_TABS.includes(tab) ? tab : "today";
  loadCheckin();
}

async function loadCheckin() {
  if (!canManageCheckins()) return;
  document.querySelectorAll("#checkinTabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === ckTab)));
  const host = $("checkinHost");
  const ticket = ++ckTicket;
  if (!host.innerHTML.trim()) host.innerHTML = `<div class="card empty">Загружаем…</div>`;
  try {
    await refreshCheckinBadge();
    const html = await CK_RENDER[ckTab]();
    if (ticket !== ckTicket) return;
    host.innerHTML = html;
    if (ckTab === "queue") updateCheckinBulk();
  } catch (error) {
    if (ticket !== ckTicket) return;
    host.innerHTML = `<div class="card empty">${icon("warning", "ic ic-xl")}<b>Не загрузилось</b>${escapeHtml(error.message)}</div>`;
  }
}

/** Адрес #checkin-lesson-12 — кнопка «Открыть в панели» из группы «Отметки». */
function openCheckinFromHash() {
  const match = location.hash.match(/^#checkin(?:-lesson-(\d+))?$/);
  if (!match || !canManageCheckins()) return;
  if (currentPage() !== "checkin") switchPage("checkin");
  if (match[1]) openCheckinLesson(parseInt(match[1], 10));
}

/* ── Сегодня ──────────────────────────────────────────────── */

function lessonMeta(lesson) {
  const tags = [];
  if (lesson.late_minutes) tags.push(`<span class="tag tag-star">опоздание ${lesson.late_minutes} мин</span>`);
  for (const flag of lesson.flag_labels || []) tags.push(`<span class="tag">${escapeHtml(flag)}</span>`);
  if (lesson.checkin && lesson.checkin.distance_m !== null && lesson.checkin.distance_m !== undefined) {
    tags.push(`<span class="tag">${fmt(lesson.checkin.distance_m)} м</span>`);
  }
  if (lesson.checkin && !lesson.checkin.video && lesson.status !== "planned") tags.push(`<span class="tag">без кругляшка</span>`);
  if (lesson.stars_given) tags.push(`<span class="tag tag-ok">${icon("star", "ic ic-xs ic-star")}звёзды выставлены</span>`);
  if (lesson.one_off) tags.push(`<span class="tag tag-accent">разовый</span>`);
  if (lesson.hours_override !== null && lesson.hours_override !== undefined) tags.push(`<span class="tag">${ckHours(lesson.hours_override)} ч вручную</span>`);
  return tags.join("");
}

function lessonRow(lesson, { pick = false } = {}) {
  const who = `${escapeHtml(lesson.teacher)}${lesson.original_teacher ? ` <span class="muted">за ${escapeHtml(lesson.original_teacher)}</span>` : ""}`;
  const where = [lesson.school, lesson.class, lesson.room ? `каб. ${lesson.room}` : ""].filter(Boolean).map(escapeHtml).join(" · ");
  const day = pick ? `<span class="ck-date">${escapeHtml(formatDay(lesson.date, { year: false }))}</span>` : "";
  const inner = `
    <span class="ck-time"><span>${lesson.start}–${lesson.end}</span>${day}</span>
    <span class="ck-who"><b>${who}</b><small>${where}</small></span>
    <span class="ck-meta">${lessonMeta(lesson)}</span>
    <span class="ck-chip ${lesson.state}">${escapeHtml(ckStateLabel(lesson.state))}</span>`;
  if (!pick) {
    return `<button type="button" class="ck-row ${lesson.state === "cancelled" ? "is-cancelled" : ""}"
      onclick="openCheckinLesson(${lesson.id})">${inner}</button>`;
  }
  return `<div class="ck-row pick">
    <label class="ck-check" title="Выбрать"><input type="checkbox" ${ckPicked.has(lesson.id) ? "checked" : ""}
      onchange="toggleCheckinPick(${lesson.id}, this.checked)" aria-label="Выбрать урок"></label>
    ${inner}
    <button type="button" class="icon-btn boxed" onclick="openCheckinLesson(${lesson.id})" title="Подробнее" aria-label="Подробнее">${icon("chevron-right", "ic ic-sm")}</button>
  </div>`;
}

function readinessHtml() {
  if (!ckOverview) return "";
  const ready = ckOverview.readiness;
  const items = [];
  if (!ckOverview.bot.enabled) {
    items.push(`<li><span>Бот отметок не подключён — преподаватели пока не могут отмечаться.
      Подключает тот, кто обслуживает сервер.</span></li>`);
  }
  if (!ckOverview.chat_connected) {
    items.push(`<li><span>Группа «Отметки» не подключена: добавьте бота в закрытую группу и отправьте там <b>/setchat</b>.</span>
      <button class="btn btn-ghost btn-sm" onclick="setCheckinTab('settings')">Как подключить</button></li>`);
  }
  for (const school of ready.schools_without_coords) {
    const mine = schoolById(school.id);
    items.push(`<li><span>У школы «${escapeHtml(school.name)}» нет точки на карте — все отметки там уйдут на проверку.</span>
      ${mine ? `<button class="btn btn-ghost btn-sm" onclick="openCheckinSchoolLocation(${school.id})">${icon("pin", "ic ic-sm")}Место на карте</button>` : ""}</li>`);
  }
  for (const user of ready.tracked_without_telegram) {
    const why = { pending: "ссылка выдана, ждём", expired: "ссылка истекла", none: "ссылки нет" }[user.invite] || "";
    items.push(`<li><span>${escapeHtml(user.name)} отмечается, но Telegram не привязан${why ? ` (${why})` : ""}.</span>
      ${isAdmin() ? `<button class="btn btn-ghost btn-sm" onclick="switchPage('users'); openUser(${user.id})">Сотрудник</button>` : ""}</li>`);
  }
  for (const user of ready.tracked_without_lessons || []) {
    items.push(`<li><span>${escapeHtml(user.name)} отмечается, но в расписании нет его занятий.</span>
      <button class="btn btn-ghost btn-sm" onclick="switchPage('schedule')">Расписание</button></li>`);
  }
  for (const user of ready.blocked_bot || []) {
    items.push(`<li><span>${escapeHtml(user.name)} заблокировал бота — сообщения не доходят.</span></li>`);
  }
  const loose = ready.entries_without_tracked_teacher;
  if (loose.length) {
    const sample = loose.slice(0, 3).map((e) => `${e.class} ${WEEKDAYS[e.weekday - 1]} ${e.start}`).join(", ");
    items.push(`<li><span>${loose.length} ${plural(loose.length, "занятие", "занятия", "занятий")} без отмечающегося учителя
      (${escapeHtml(sample)}${loose.length > 3 ? "…" : ""}) — их уроки не проверяются.</span>
      <button class="btn btn-ghost btn-sm" onclick="switchPage('schedule')">Расписание</button></li>`);
  }
  if (!items.length) return "";
  return `<section class="card ck-ready"><h2 class="eyebrow">${icon("warning", "ic ic-sm")}Что мешает отметкам</h2><ul>${items.join("")}</ul></section>`;
}

function openCheckinSchoolLocation(id) {
  if (schoolById(id) && activeSchool !== id) selectSchool(id);
  openSchoolLocation();
}

async function renderCheckinToday() {
  const data = await api(`/api/checkin/day${ckDay ? `?date=${ckDay}` : ""}`);
  ckDay = data.date;
  const schools = [...new Map(data.lessons.map((l) => [l.school_id, l.school])).entries()]
    .sort((a, b) => a[1].localeCompare(b[1], "ru"));
  if (ckSchool && !schools.some(([id]) => String(id) === ckSchool)) ckSchool = "";
  const lessons = data.lessons.filter((l) => !ckSchool || String(l.school_id) === ckSchool);
  const counts = {};
  for (const lesson of lessons) counts[lesson.state] = (counts[lesson.state] || 0) + 1;
  const tiles = ["ok", "late", "missing", "review", "now", "ahead", "cancelled"].filter((s) => counts[s])
    .map((s) => `<div class="tile ck-tile ${s}"><span>${ckStateLabel(s)}</span><b>${counts[s]}</b></div>`).join("");
  const isToday = data.date === data.today;
  const toolbar = `
    <div class="ck-toolbar">
      <div class="week-nav" role="group" aria-label="День">
        <button class="icon-btn boxed" onclick="shiftCheckinDay(-1)" title="Предыдущий день" aria-label="Предыдущий день">${icon("chevron-left", "ic ic-sm")}</button>
        <button class="btn btn-ghost btn-sm" onclick="shiftCheckinDay(0)" title="К сегодняшнему дню">${isToday ? "Сегодня · " : ""}${escapeHtml(formatDay(data.date, { weekday: true }))}</button>
        <button class="icon-btn boxed" onclick="shiftCheckinDay(1)" title="Следующий день" aria-label="Следующий день">${icon("chevron-right", "ic ic-sm")}</button>
      </div>
      ${schools.length > 1 ? `<select class="ck-select" onchange="ckSchool = this.value; loadCheckin()" aria-label="Школа">
        <option value="">Все школы</option>${schools.map(([id, name]) => `<option value="${id}" ${String(id) === ckSchool ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}
      </select>` : ""}
      <span class="grow"></span>
      <button class="btn btn-ghost btn-sm" onclick="openCheckinOneOff()">${icon("plus", "ic ic-sm")}Разовый урок</button>
    </div>`;
  const list = lessons.length
    ? `<section class="card ck-list">${lessons.map((l) => lessonRow(l)).join("")}</section>`
    : `<div class="card empty">${icon("calendar", "ic ic-xl")}<b>Уроков нет</b>
        ${ckOverview && !ckOverview.counts.tracked ? "Пока никто не отмечается: включите сотрудникам «Отмечается в боте» в разделе «Сотрудники»." : "В этот день по расписанию отмечающихся уроков нет."}</div>`;
  return `${readinessHtml()}${toolbar}${tiles ? `<div class="tiles stat-tiles">${tiles}</div>` : ""}${list}`;
}

function shiftCheckinDay(step) {
  ckDay = step === 0 || !ckDay ? null : shiftDay(ckDay, step);
  loadCheckin();
}

/* ── Очередь ──────────────────────────────────────────────── */

async function renderCheckinQueue() {
  ckQueue = (await api("/api/checkin/queue")).lessons;
  const ids = new Set(ckQueue.map((l) => l.id));
  ckPicked = new Set([...ckPicked].filter((id) => ids.has(id)));
  if (!ckQueue.length) {
    return `<div class="card empty">${icon("check-circle", "ic ic-xl")}<b>Очередь пуста</b>
      Сюда попадают уроки без отметки и спорные отметки: далеко от школы, без кругляшка, после конца урока.</div>`;
  }
  return `
    <div class="ck-toolbar ck-bulk">
      <label class="check"><input type="checkbox" id="ckPickAll" onchange="pickAllCheckin(this.checked)"
        ${ckPicked.size === ckQueue.length ? "checked" : ""}>Выбрать все</label>
      <span class="muted" id="ckPickedLabel"></span>
      <span class="grow"></span>
      <button class="btn btn-sm" id="ckBulkApprove" onclick="openCheckinDecide([...ckPicked], 'approve')">${icon("check", "ic ic-sm")}Засчитать</button>
      <button class="btn btn-ghost btn-sm" id="ckBulkReject" onclick="openCheckinDecide([...ckPicked], 'reject')">${icon("close", "ic ic-sm")}Не засчитывать</button>
    </div>
    <section class="card ck-list">${ckQueue.map((l) => lessonRow(l, { pick: true })).join("")}</section>`;
}

function updateCheckinBulk() {
  if (!$("ckPickedLabel")) return;
  $("ckPickedLabel").textContent = ckPicked.size ? `Выбрано: ${ckPicked.size}` : "Отметьте уроки";
  $("ckBulkApprove").disabled = !ckPicked.size;
  $("ckBulkReject").disabled = !ckPicked.size;
  $("ckPickAll").checked = ckPicked.size === ckQueue.length && ckQueue.length > 0;
}

function toggleCheckinPick(id, on) {
  if (on) ckPicked.add(id); else ckPicked.delete(id);
  updateCheckinBulk();
}

function pickAllCheckin(on) {
  ckPicked = on ? new Set(ckQueue.map((l) => l.id)) : new Set();
  document.querySelectorAll(".ck-row.pick input[type=checkbox]").forEach((box) => { box.checked = on; });
  updateCheckinBulk();
}

/* ── Окно урока ───────────────────────────────────────────── */

async function openCheckinLesson(id) {
  ckLessonId = id;
  try {
    const lesson = await api(`/api/checkin/lessons/${id}`);
    renderCheckinLesson(lesson);
    if (!$("modalCheckinLesson").classList.contains("open")) openModal("modalCheckinLesson");
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderCheckinLesson(lesson) {
  $("ckLessonTitle").textContent = `${lesson.start}–${lesson.end} · ${formatDay(lesson.date)}`;
  $("ckLessonSub").innerHTML = `${escapeHtml(lesson.teacher)}${lesson.original_teacher ? ` (замена за ${escapeHtml(lesson.original_teacher)})` : ""}`
    + ` — ${escapeHtml(lesson.school)}, ${escapeHtml(lesson.class)}${lesson.room ? `, каб. ${escapeHtml(lesson.room)}` : ""}`;
  const facts = [
    ["Статус", `<span class="ck-chip ${lesson.state}">${escapeHtml(lesson.status_label)}</span>`],
    ["Часы", `${ckHours(lesson.hours)} ч${lesson.hours_override !== null && lesson.hours_override !== undefined ? " (вручную)" : ` из ${ckHours(lesson.duration_minutes / 60)} ч по расписанию`}`],
  ];
  if (lesson.late_minutes) facts.push(["Опоздание", `${lesson.late_minutes} мин`]);
  if (lesson.flag_labels.length) facts.push(["На проверке", escapeHtml(lesson.flag_labels.join(", "))]);
  const checkin = lesson.checkin;
  if (checkin) {
    const point = checkin.lat !== undefined && checkin.lat !== null
      ? ` · <a href="${mapLink(checkin.lat, checkin.lon)}" target="_blank" rel="noopener">точка на карте</a>` : "";
    facts.push(["Отметка", `в ${checkin.at}${checkin.distance_m !== null && checkin.distance_m !== undefined ? `, ${fmt(checkin.distance_m)} м от школы` : ""}${point}`]);
    if (checkin.accuracy_m) facts.push(["Точность", `±${fmt(Math.round(checkin.accuracy_m))} м`]);
    const link = tgMessageLink(checkin.report_chat_id, checkin.report_message_id);
    facts.push(["Кругляшок", checkin.video
      ? (link ? `<a href="${link}" target="_blank" rel="noopener">в группе «Отметки»</a>` : "получен")
      : "не прислан"]);
  } else if (lesson.status !== "planned" && lesson.status !== "cancelled") {
    facts.push(["Отметка", "нет"]);
  }
  if (lesson.stars_given) facts.push(["Журнал звёзд", "урок с начислениями в этот день есть"]);
  if (lesson.note) facts.push(["Примечание", escapeHtml(lesson.note)]);
  if (lesson.reviewed_by) facts.push(["Решение", escapeHtml(lesson.reviewed_by)]);
  const history = (lesson.history || []).slice().reverse().map((item) => {
    const { what, changes } = auditSummary(item);
    return `<li><span class="muted">${escapeHtml(formatStamp(item.created_at))}</span>
      <b>${escapeHtml(item.user_name || "—")}</b> ${escapeHtml(what)}${changes.length ? `: ${escapeHtml(changes.join("; "))}` : ""}
      ${item.reason ? `<small>«${escapeHtml(item.reason)}»</small>` : ""}</li>`;
  }).join("");
  $("ckLessonBody").innerHTML = `
    <div class="ck-facts">${facts.map(([k, v]) => `<span>${k}</span><div>${v}</div>`).join("")}</div>
    ${history ? `<h3 class="eyebrow ck-h">История правок</h3><ul class="ck-history">${history}</ul>` : ""}`;

  const labels = { approve: "Засчитать", reject: "Не засчитывать", hours: "Часы вручную", cancel: "Отменить урок", reopen: "Вернуть урок" };
  const button = (action, cls) =>
    `<button class="${cls}" onclick="openCheckinDecide([${lesson.id}], '${action}')">${labels[action]}</button>`;
  const parts = [];
  if (lesson.status === "cancelled") {
    parts.push(`<span class="grow"></span>`);
    if (lesson.cancel_source === "admin") parts.push(button("reopen", "btn"));
  } else {
    parts.push(button("cancel", "btn btn-danger-quiet"), `<span class="grow"></span>`, button("hours", "btn btn-ghost"));
    if (lesson.status !== "rejected") parts.push(button("reject", "btn btn-ghost"));
    if (lesson.status !== "approved") parts.push(button("approve", "btn"));
  }
  $("ckLessonActions").innerHTML = parts.join("")
    + `<button class="btn btn-ghost" onclick="closeModal('modalCheckinLesson')">Закрыть</button>`;
}

/** «25 ноября, 14:07» по часам браузера — для журнала правок. */
function formatStamp(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso || "";
  return `${formatDay(isoDate(date), { year: false })}, ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/* ── Решение с причиной ───────────────────────────────────── */

function openCheckinDecide(ids, action) {
  if (!ids.length) return;
  ckDecision = { ids, action };
  const info = CK_ACTIONS[action];
  const many = ids.length > 1 ? ` — ${ids.length} ${plural(ids.length, "урок", "урока", "уроков")}` : "";
  $("ckDecideTitle").textContent = info.title + many;
  $("ckDecideSub").textContent = info.sub;
  $("ckDecideSubmit").textContent = info.button;
  $("ckDecideSubmit").className = action === "cancel" || action === "reject" ? "btn btn-danger" : "btn";
  $("ckDecideHoursField").hidden = action !== "hours";
  $("ckDecideHours").value = "";
  $("ckDecideReason").value = "";
  openModal("modalCheckinDecide");
  (action === "hours" ? $("ckDecideHours") : $("ckDecideReason")).focus();
}

async function submitCheckinDecision() {
  if (!ckDecision) return;
  const reason = $("ckDecideReason").value.trim();
  if (!reason) return toast("Укажите причину — она попадёт в журнал правок", "error");
  const { ids, action } = ckDecision;
  try {
    if (action === "hours") {
      const raw = $("ckDecideHours").value.trim().replace(",", ".");
      const hours = raw === "" ? null : Number(raw);
      if (hours !== null && !(hours >= 0 && hours <= 12 && Math.round(hours * 4) === hours * 4)) {
        return toast("Часы — от 0 до 12, шагом в четверть часа", "error");
      }
      await api(`/api/checkin/lessons/${ids[0]}`, { method: "PATCH", json: { hours, reason } });
    } else {
      await api("/api/checkin/lessons/decide", { method: "POST", json: { lesson_ids: ids, action, reason } });
    }
    closeModal("modalCheckinDecide");
    ids.forEach((id) => ckPicked.delete(id));
    toast(ids.length > 1 ? `Готово: ${ids.length} ${plural(ids.length, "урок", "урока", "уроков")}` : "Сохранено", "success");
    if ($("modalCheckinLesson").classList.contains("open") && ids.includes(ckLessonId)) await openCheckinLesson(ckLessonId);
    await loadCheckin();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Отчёт ────────────────────────────────────────────────── */

async function renderCheckinReport() {
  const report = await api(`/api/checkin/report${ckMonth ? `?month=${ckMonth}` : ""}`);
  ckMonth = report.month;
  const t = report.totals;
  const tiles = [
    ["Уроков по плану", fmt(t.lessons)], ["Подтверждено", fmt(t.confirmed)], ["Опозданий", fmt(t.late)],
    ["Ждут решения", fmt(t.pending)], ["Часы", ckHours(t.hours)], ["К выплате", t.amount === null ? "—" : fmt(t.amount)],
  ].map(([label, value]) => `<div class="tile"><span>${label}</span><b>${value}</b></div>`).join("");
  const teachers = report.teachers.map((row) => `
    <div class="st-row">
      <span>${escapeHtml(row.name)}${row.pending ? ` <span class="tag">ждут решения: ${row.pending}</span>` : ""}</span>
      <span class="r num">${row.confirmed} / ${row.planned}</span>
      <span class="r num">${row.late ? `${row.late} <small>(${row.late_minutes} мин)</small>` : "—"}</span>
      <span class="r num">${row.rejected || "—"}</span>
      <span class="r num"><b>${ckHours(row.hours)}</b></span>
      <span class="r num">${row.rate === null || row.rate === undefined ? "—" : fmt(row.rate)}</span>
      <span class="r num"><b>${ckMoney(row.amount)}</b></span>
    </div>`).join("");
  const schools = report.schools.map((school) => `
    <div class="st-group">${escapeHtml(school.name)} — ${ckHours(school.hours)} ч, ${school.lessons} ${plural(school.lessons, "урок", "урока", "уроков")}</div>
    ${school.teachers.map((tch) => `<div class="st-row"><span>${escapeHtml(tch.name)}</span><span class="r num">${ckHours(tch.hours)} ч</span></div>`).join("")}`).join("");
  return `
    <div class="ck-toolbar">
      <div class="week-nav" role="group" aria-label="Месяц">
        <button class="icon-btn boxed" onclick="shiftCheckinMonth(-1)" title="Предыдущий месяц" aria-label="Предыдущий месяц">${icon("chevron-left", "ic ic-sm")}</button>
        <button class="btn btn-ghost btn-sm" onclick="shiftCheckinMonth(0)" title="К текущему месяцу">${escapeHtml(monthLabel(report.month))}</button>
        <button class="icon-btn boxed" onclick="shiftCheckinMonth(1)" title="Следующий месяц" aria-label="Следующий месяц">${icon("chevron-right", "ic ic-sm")}</button>
      </div>
      <span class="grow"></span>
      <button class="btn btn-sm" onclick="downloadFile('/api/checkin/report.xlsx?month=${report.month}')">${icon("export", "ic ic-sm")}Скачать Excel</button>
    </div>
    <div class="tiles stat-tiles">${tiles}</div>
    ${report.teachers.length ? `
    <section class="card stat-card">
      <h2 class="eyebrow">Преподаватели</h2>
      <div class="st-table ck-report">
        <div class="st-row st-headrow"><span>Преподаватель</span><span class="r">Подтв. / план</span><span class="r">Опоздания</span>
          <span class="r">Не засч.</span><span class="r">Часы</span><span class="r">Ставка</span><span class="r">Сумма</span></div>
        ${teachers}
      </div>
      ${t.pending ? `<p class="muted st-note">Уроки, которые ждут решения, войдут в часы, когда их засчитают.</p>` : ""}
    </section>
    <section class="card stat-card">
      <h2 class="eyebrow">Школы — часы для счёта</h2>
      <div class="st-table">${schools || `<p class="muted">Засчитанных уроков пока нет.</p>`}</div>
    </section>` : `<div class="card empty">${icon("chart", "ic ic-xl")}<b>За ${escapeHtml(monthLabel(report.month))} уроков нет</b></div>`}`;
}

function shiftCheckinMonth(step) {
  ckMonth = step === 0 || !ckMonth ? null : shiftMonth(ckMonth, step);
  loadCheckin();
}

/* ── Календарь ────────────────────────────────────────────── */

async function checkinTeachers() {
  if (!ckTeachers) ckTeachers = (await api("/api/checkin/teachers")).teachers;
  return ckTeachers;
}

function exceptionScope(item) {
  const parts = [];
  parts.push(item.school ? `школа «${item.school}»` : item.kind === "holiday" ? "все школы" : "");
  if (item.teacher) parts.push(item.kind === "substitute" ? `заменяют: ${item.teacher}` : `преподаватель: ${item.teacher}`);
  if (item.entry) parts.push(`занятие: ${item.entry}`);
  if (item.substitute) parts.push(`заменяет: ${item.substitute}`);
  return parts.filter(Boolean).join(" · ");
}

async function renderCheckinCalendar() {
  const items = (await api("/api/checkin/exceptions")).exceptions;
  const rows = items.map((item) => {
    const dates = item.date_from === item.date_to ? formatDay(item.date_from)
      : `${formatDay(item.date_from)} — ${formatDay(item.date_to)}`;
    return `<div class="ck-ex">
      <span class="tag ${item.kind === "substitute" ? "tag-accent" : item.kind === "holiday" ? "tag-star" : ""}">${CK_KIND_NAMES[item.kind]}</span>
      <div class="ck-ex-main"><b>${escapeHtml(dates)}</b><small>${escapeHtml(exceptionScope(item))}</small>
        <small>«${escapeHtml(item.reason)}»${item.author ? ` — ${escapeHtml(item.author)}` : ""}</small></div>
      <button class="icon-btn boxed danger" onclick="removeCheckinException(${item.id})" title="Убрать" aria-label="Убрать">${icon("trash", "ic ic-sm")}</button>
    </div>`;
  }).join("");
  return `
    <div class="ck-toolbar">
      <p class="muted ck-note">Праздники, отмены и замены сразу меняют уроки на эти даты. Уже начавшиеся и решённые уроки не трогаются.</p>
      <span class="grow"></span>
      <button class="btn btn-ghost btn-sm" onclick="openCheckinOneOff()">${icon("plus", "ic ic-sm")}Разовый урок</button>
      <button class="btn btn-sm" onclick="openCheckinException()">${icon("calendar", "ic ic-sm")}Добавить</button>
    </div>
    ${rows ? `<section class="card ck-list">${rows}</section>` : `<div class="card empty">${icon("calendar", "ic ic-xl")}<b>Календарь пуст</b>Добавьте праздник, каникулы, отмену или замену.</div>`}`;
}

function removeCheckinException(id) {
  confirmAction("Убрать из календаря?", "Уроки на эти даты вернутся по расписанию (кроме уже начавшихся).", async () => {
    await api(`/api/checkin/exceptions/${id}`, { method: "DELETE" });
    toast("Убрано", "success");
    await loadCheckin();
  }, "Убрать");
}

async function openCheckinException() {
  try {
    await checkinTeachers();
  } catch (error) {
    return toast(error.message, "error");
  }
  const today = ckOverview?.today || localToday();
  $("ckExFrom").value = today;
  $("ckExTo").value = today;
  $("ckExReason").value = "";
  $("ckExSchool").innerHTML = `<option value="">Все школы</option>` +
    state.schools.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");
  const tracked = ckTeachers.filter((u) => u.checkin_tracked);
  $("ckExTeacher").innerHTML = `<option value="">Любой</option>` +
    tracked.map((u) => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join("");
  $("ckExSub").innerHTML = `<option value="">Выберите</option>` +
    tracked.map((u) => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join("");
  pickCheckinExKind("holiday");
  openModal("modalCheckinException");
}

function pickCheckinExKind(kind) {
  ckExKind = kind;
  document.querySelectorAll("#ckExKind button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.kind === kind)));
  $("ckExHint").textContent = CK_KIND_HINTS[kind];
  $("ckExTeacherField").hidden = kind === "holiday";
  $("ckExEntryField").hidden = kind === "holiday";
  $("ckExSubField").hidden = kind !== "substitute";
  $("ckExTeacherLabel").textContent = kind === "substitute" ? "Кого заменяют" : "Преподаватель";
  renderCheckinEntryOptions();
}

function renderCheckinEntryOptions() {
  const school = parseInt($("ckExSchool").value, 10) || null;
  const teacher = parseInt($("ckExTeacher").value, 10) || null;
  const entries = (state.schedule || []).filter((e) => {
    const cls = classById(e.class_id);
    return cls && (!school || cls.school_id === school) && (!teacher || e.user_id === teacher);
  }).sort((a, b) => a.weekday - b.weekday || a.start.localeCompare(b.start));
  $("ckExEntry").innerHTML = `<option value="">Все занятия</option>` + entries.map((e) => {
    const cls = classById(e.class_id);
    const who = staffName(e.user_id) || (ckTeachers || []).find((u) => u.id === e.user_id)?.name || "";
    return `<option value="${e.id}">${escapeHtml(cls?.name || "")}, ${WEEKDAYS[e.weekday - 1]} ${e.start}–${e.end}${who ? ` — ${escapeHtml(who)}` : ""}</option>`;
  }).join("");
}

async function saveCheckinException() {
  const num = (id) => parseInt($(id).value, 10) || null;
  const body = {
    kind: ckExKind, date_from: $("ckExFrom").value, date_to: $("ckExTo").value || null,
    school_id: num("ckExSchool"), reason: $("ckExReason").value.trim(),
    user_id: ckExKind === "holiday" ? null : num("ckExTeacher"),
    entry_id: ckExKind === "holiday" ? null : num("ckExEntry"),
    substitute_user_id: ckExKind === "substitute" ? num("ckExSub") : null,
  };
  if (!body.date_from) return toast("Укажите дату", "error");
  if (!body.reason) return toast("Укажите причину — она попадёт в журнал правок", "error");
  try {
    await api("/api/checkin/exceptions", { method: "POST", json: body });
    closeModal("modalCheckinException");
    toast("Добавлено в календарь", "success");
    await loadCheckin();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function openCheckinOneOff() {
  try {
    await checkinTeachers();
  } catch (error) {
    return toast(error.message, "error");
  }
  const tracked = ckTeachers.filter((u) => u.checkin_tracked);
  if (!tracked.length) return toast("Пока никто не отмечается — включите сотруднику «Отмечается в боте»", "error");
  $("ckOneTeacher").innerHTML = tracked.map((u) => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join("");
  $("ckOneClass").innerHTML = state.schools.map((s) => `<optgroup label="${escapeHtml(s.name)}">` +
    sortedClasses(s.id).map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("") + `</optgroup>`).join("");
  $("ckOneDate").value = ckDay || ckOverview?.today || localToday();
  $("ckOneRoom").value = "";
  $("ckOneReason").value = "";
  openModal("modalCheckinOneOff");
}

async function saveCheckinOneOff() {
  const body = {
    user_id: parseInt($("ckOneTeacher").value, 10), class_id: parseInt($("ckOneClass").value, 10),
    date: $("ckOneDate").value, start: $("ckOneStart").value, end: $("ckOneEnd").value,
    room: $("ckOneRoom").value.trim(), reason: $("ckOneReason").value.trim(),
  };
  if (!body.class_id) return toast("Выберите класс", "error");
  if (!body.date || !body.start || !body.end || body.end <= body.start) return toast("Проверьте дату и время урока", "error");
  if (!body.reason) return toast("Укажите причину — она попадёт в журнал правок", "error");
  try {
    await api("/api/checkin/lessons", { method: "POST", json: body });
    closeModal("modalCheckinOneOff");
    toast("Урок добавлен", "success");
    ckDay = body.date;
    if (ckTab !== "today") ckTab = "today";
    await loadCheckin();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Журнал правок ────────────────────────────────────────── */

function auditRow(item) {
  const { what, changes } = auditSummary(item);
  return `<div class="ck-audit">
    <span class="muted ck-when">${escapeHtml(formatStamp(item.created_at))}</span>
    <div><b>${escapeHtml(item.user_name || "—")}</b> ${escapeHtml(what)}${item.entity === "lesson" && item.entity_id
      ? ` <button class="link" onclick="openCheckinLesson(${item.entity_id})">урок №${item.entity_id}</button>` : ""}
      ${changes.length ? `<small>${escapeHtml(changes.join("; "))}</small>` : ""}
      ${item.reason ? `<small>«${escapeHtml(item.reason)}»</small>` : ""}</div>
  </div>`;
}

async function renderCheckinAudit() {
  const items = (await api("/api/checkin/audit?limit=50")).items;
  ckAudit = items;
  ckAuditMore = items.length === 50;
  return auditHtml();
}

function auditHtml() {
  if (!ckAudit.length) {
    return `<div class="card empty">${icon("history", "ic ic-xl")}<b>Правок пока нет</b>Здесь появится каждое ручное решение: кто, когда, что и почему.</div>`;
  }
  return `<section class="card ck-list">${ckAudit.map(auditRow).join("")}</section>
    ${ckAuditMore ? `<div class="ck-more"><button class="btn btn-ghost btn-sm" onclick="moreCheckinAudit()">Показать ещё</button></div>` : ""}`;
}

async function moreCheckinAudit() {
  const last = ckAudit[ckAudit.length - 1];
  try {
    const items = (await api(`/api/checkin/audit?limit=50&before_id=${last.id}`)).items;
    ckAudit = ckAudit.concat(items);
    ckAuditMore = items.length === 50;
    $("checkinHost").innerHTML = auditHtml();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Настройки ────────────────────────────────────────────── */

async function renderCheckinSettings() {
  const view = await api("/api/checkin/settings");
  const fields = view.fields.map((field) => field.type === "bool" ? `
    <label class="check ck-setting"><input type="checkbox" data-key="${field.key}" ${view.values[field.key] ? "checked" : ""}>
      <span><b>${escapeHtml(field.label)}</b><small>${escapeHtml(field.description)}</small></span></label>` : `
    <div class="field ck-setting">
      <label for="ckSet_${field.key}">${escapeHtml(field.label)}</label>
      <input type="number" id="ckSet_${field.key}" data-key="${field.key}" min="${field.min}" max="${field.max}" step="1"
        value="${view.values[field.key]}" inputmode="numeric">
      <p class="field-hint">${escapeHtml(field.description)} От ${field.min} до ${field.max}.</p>
    </div>`).join("");
  const bot = ckOverview?.bot || {};
  const botLine = bot.enabled
    ? `Бот отметок подключён${bot.username ? `: <b>@${escapeHtml(bot.username)}</b>` : ""}.`
    : "Бот отметок не подключён — его подключает тот, кто обслуживает сервер. Без бота преподаватели не могут отмечаться.";
  const chatLine = ckOverview?.chat_connected
    ? "Группа «Отметки» подключена: туда приходят копии отметок и карточки уроков с кнопками."
    : `Группа «Отметки» не подключена. Создайте закрытую группу, добавьте в неё бота и отправьте там <b>/setchat</b>.
       Команду примет только администратор или ответственный за отметки с привязанным Telegram.`;
  return `
    <div class="stat-cols">
      <section class="card stat-card">
        <h2 class="eyebrow">Правила</h2>
        <div class="ck-settings" id="ckSettingsForm">${fields}</div>
        <div class="modal-actions"><button class="btn" onclick="saveCheckinSettings()">Сохранить</button></div>
      </section>
      <section class="card stat-card">
        <h2 class="eyebrow">Telegram</h2>
        <p class="ck-note">${botLine}</p>
        <p class="ck-note">${chatLine}</p>
        <p class="ck-note muted">Преподаватель подключается по ссылке-приглашению из раздела «Сотрудники»: выбирает язык,
          соглашается на отметки — и в меню бота появляется кнопка «Я на месте».</p>
      </section>
    </div>`;
}

async function saveCheckinSettings() {
  const values = {};
  for (const input of document.querySelectorAll("#ckSettingsForm [data-key]")) {
    values[input.dataset.key] = input.type === "checkbox" ? input.checked : Number(input.value);
  }
  try {
    await api("/api/checkin/settings", { method: "PUT", json: { values } });
    toast("Настройки сохранены", "success");
    await loadCheckin();
  } catch (error) {
    toast(error.message, "error");
  }
}

const CK_RENDER = {
  today: renderCheckinToday, queue: renderCheckinQueue, report: renderCheckinReport,
  calendar: renderCheckinCalendar, audit: renderCheckinAudit, settings: renderCheckinSettings,
};
