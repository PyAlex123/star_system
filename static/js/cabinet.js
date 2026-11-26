/* «Мой кабинет» (#me) — страница сотрудника: фото, школы и должности, отметки через бота,
   уроки сегодня и за месяц со статусом отметки, часы, опоздания и журнал за месяц.
   Денег (ставки и сумм) здесь нет — их видит только администратор в «Отметках».
   Подключается после teacher.js и checkin-page.js (ckHours, ckStateLabel, shiftMonth, monthLabel). */

let cabinetMonth = null;        // «ГГГГ-ММ» — открытый месяц
let cabinetData = null;         // ответ /api/checkin/my или { error }

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Блок профиля в меню открывает кабинет. */
function openProfile() {
  closeMenus();
  switchPage("me");
}

function shiftCabinetMonth(step) {
  cabinetMonth = step === 0 ? thisMonth() : shiftMonth(cabinetMonth || thisMonth(), step);
  loadCabinet();
}

async function loadCabinet() {
  cabinetMonth = cabinetMonth || thisMonth();
  const want = cabinetMonth;
  if (cabinetData?.month_key !== want) cabinetData = null;
  renderCabinet();
  try {
    const data = await api(`/api/checkin/my?month=${want}`);
    if (want !== cabinetMonth) return;
    cabinetData = { ...data, month_key: want };
  } catch (error) {
    cabinetData = { error: error.message, month_key: want };
  }
  renderCabinet();
}

function renderCabinet() {
  if (!state.me || !$("page-me")) return;
  renderCabinetHead();
  renderCabinetSchools();
  renderCabinetBot();
  renderCabinetToday();
  renderCabinetMonth();
}

function renderCabinetHead() {
  const me = state.me;
  const photo = me.avatar?.type === "photo";
  $("profileAvatar").innerHTML = staffAvatar(me, "xl");
  $("profileName").textContent = me.name;
  const all = isAdmin() ? ["администратор системы"]
    : Object.keys(POSTS).filter((p) => Object.values(me.posts || {}).some((list) => list.includes(p))).map((p) => POSTS[p]);
  $("profileRole").textContent = all.join(" · ") || "Должностей пока нет";
  $("profileLogin").textContent = me.login || "—";
  $("profilePhotoLabel").textContent = photo ? "Другое фото" : "Загрузить фото";
  $("profileDrop").hidden = !photo;
}

function renderCabinetSchools() {
  const me = state.me;
  if (isAdmin()) {
    $("meSchools").innerHTML = `<p class="me-p">Все школы — вы администратор системы.</p>`;
    return;
  }
  const rows = Object.entries(me.posts || {}).map(([id, posts]) => {
    const school = schoolById(Number(id));
    if (!school) return "";
    return `
      <div class="me-school">
        <b>${escapeHtml(school.name)}</b>
        <span class="tag-row">${posts.map((p) =>
          `<span class="tag ${ADMIN_POSTS.includes(p) ? "tag-accent" : ""}">${POSTS[p]}</span>`).join("")}</span>
      </div>`;
  }).join("");
  $("meSchools").innerHTML = rows || `<p class="me-p muted">Вам пока не открыта ни одна школа — обратитесь к администратору.</p>`;
}

function renderCabinetBot() {
  const block = $("meBotBlock");
  block.hidden = isAdmin();
  if (isAdmin()) return;
  const data = cabinetData && !cabinetData.error ? cabinetData : null;
  if (!data) {
    $("meBot").innerHTML = `<p class="me-p muted">${cabinetData?.error ? escapeHtml(cabinetData.error) : "Загружаем…"}</p>`;
    return;
  }
  const tg = data.telegram;
  let html;
  if (!data.tracked) {
    html = `<p class="me-p">Отметки для вас не включены — их включает администратор.
      Когда включит, здесь появится кнопка «Подключить Telegram».</p>`;
  } else if (tg.bound && tg.blocked) {
    html = `<p class="me-p sc-warn">${icon("warning", "ic ic-xs")}Вы остановили бота — отметки не приходят.
      Откройте бот в Telegram и нажмите «Старт».</p>`;
  } else if (tg.bound) {
    html = `<p class="me-p"><span class="tag tag-ok">${icon("check", "ic ic-xs")}Telegram привязан${tg.username ? ` · @${escapeHtml(tg.username)}` : ""}</span></p>
      <p class="me-p">Приехав на урок, откройте бот${data.bot.username ? ` <b>@${escapeHtml(data.bot.username)}</b>` : ""},
        нажмите «Я на месте» и запишите кругляшок 5 секунд.${tg.consent ? "" : " Сначала бот попросит согласие."}</p>`;
  } else if (data.bot.ready) {
    html = `<p class="me-p">Привяжите свой Telegram — в нём вы будете отмечаться на уроках.</p>
      <button class="btn btn-sm" onclick="myInvite()">${icon("send", "ic ic-sm")}Подключить Telegram</button>`;
  } else {
    html = `<p class="me-p">Telegram-бот пока не подключён к сайту. Когда подключат, здесь появится кнопка «Подключить Telegram».</p>`;
  }
  $("meBot").innerHTML = html;
}

async function myInvite() {
  try {
    const result = await api("/api/checkin/my/invite", { method: "POST" });
    if (!result.link) return toast("Telegram-бот пока не подключён к сайту", "error");
    lastInviteLink = result.link;
    showInfo("Ссылка в бот", `
      <p>Откройте её на телефоне, где ваш Telegram. Ссылка действует ${result.days}
        ${plural(result.days, "день", "дня", "дней")} и срабатывает один раз.</p>
      <div class="invite-box"><b>${escapeHtml(result.link)}</b>
        <a class="btn btn-sm" href="${escapeHtml(result.link)}" target="_blank" rel="noopener">${icon("send", "ic ic-sm")}Открыть в Telegram</a></div>`);
  } catch (error) {
    toast(error.message, "error");
  }
}

/** Подпись состояния урока для самого преподавателя. */
function cabinetState(lesson, tracked) {
  if (lesson.state === "late") return `опоздал на ${lesson.late_minutes} мин`;
  if (lesson.state === "ok") return lesson.checkin_at ? `пришёл в ${lesson.checkin_at}` : lesson.status_label || "засчитан";
  if (lesson.state === "missing") return lesson.status_label || "нет отметки";
  if (lesson.state === "now") return tracked ? "идёт — отметьтесь" : "идёт";
  return ckStateLabel(lesson.state);
}

function cabinetRow(lesson, tracked, withDate = false) {
  const where = [lesson.class, lesson.school, lesson.room ? `каб. ${lesson.room}` : ""].filter(Boolean).map(escapeHtml).join(" · ");
  const meta = [
    lesson.minutes ? `<span class="tag">${ckHours(lesson.hours)} ч</span>` : "",
    ...(lesson.flag_labels || []).map((f) => `<span class="tag tag-star">${escapeHtml(f)}</span>`),
  ].join("");
  return `
    <div class="ck-row ${lesson.state === "cancelled" ? "is-cancelled" : ""}">
      <span class="ck-time"><span>${lesson.start}–${lesson.end}</span>${withDate ? `<span class="ck-date">${escapeHtml(formatDay(lesson.date, { year: false }))}</span>` : ""}</span>
      <span class="ck-who"><b>${where}</b></span>
      <span class="ck-meta">${meta}</span>
      <span class="ck-chip ${lesson.state}">${escapeHtml(cabinetState(lesson, tracked))}</span>
    </div>`;
}

function renderCabinetToday() {
  const data = cabinetData && !cabinetData.error ? cabinetData : null;
  const lessons = data?.today_lessons || [];
  $("meTodayBlock").hidden = !lessons.length;
  $("meToday").innerHTML = lessons.map((l) => cabinetRow(l, data.tracked)).join("");
}

function renderCabinetMonth() {
  const month = cabinetMonth || thisMonth();
  const [y, m] = month.split("-").map(Number);
  $("meMonthLabel").textContent = monthLabel(month);
  const work = monthWork(state.me.id, new Date(y, m - 1, 1));
  $("meJournal").innerHTML = `${icon("book", "ic ic-sm")}<span>Журнал: <b>${fmt(work.lessons)}</b> ${plural(work.lessons, "урок", "урока", "уроков")},
    выдано <b class="nw">${fmt(work.stars)}&nbsp;${icon("star", "ic ic-xs me-star")}</b>, <b>${fmt(work.classes)}</b> ${plural(work.classes, "класс", "класса", "классов")}</span>`;
  const data = cabinetData && !cabinetData.error ? cabinetData : null;
  if (!data) {
    $("meTiles").innerHTML = "";
    $("meRules").textContent = cabinetData?.error || "Загружаем…";
    $("meLessons").innerHTML = "";
    $("meLessons").hidden = true;
    return;
  }
  const s = data.summary;
  const showCheckins = data.tracked || s.planned > 0;
  if (!showCheckins) {
    $("meTiles").innerHTML = "";
    $("meRules").innerHTML = isAdmin()
      ? `Часы и опоздания всех преподавателей — в разделе «Отметки».`
      : `Часы по отметкам появятся здесь, когда администратор включит вам отметки в боте.`;
    $("meLessons").hidden = true;
    return;
  }
  $("meTiles").innerHTML = [
    ["По расписанию", fmt(s.planned), ""],
    ["Засчитано", fmt(s.confirmed), "ok"],
    ["Часы", ckHours(s.hours), "ok"],
    ["Опоздания", s.late ? `${fmt(s.late)}<small>${fmt(s.late_minutes)} мин</small>` : "0", s.late ? "late" : ""],
    ["Ждут решения", fmt(s.pending), s.pending ? "review" : ""],
    ["Не засчитано", fmt(s.rejected), s.rejected ? "missing" : ""],
  ].map(([label, value, kind]) => `<div class="tile ck-tile ${kind}"><span>${label}</span><b>${value}</b></div>`).join("");
  const r = data.rules;
  $("meRules").innerHTML = `Приход засчитывается за ${r.before} мин до начала урока, опоздание — позже ${r.grace} мин
    после начала. Одна отметка — на уроки подряд (перерыв до ${r.merge_gap} мин). Часы — по расписанию.`;
  const days = new Map();
  for (const lesson of data.lessons) {
    if (!days.has(lesson.date)) days.set(lesson.date, []);
    days.get(lesson.date).push(lesson);
  }
  $("meLessons").hidden = false;
  $("meLessons").innerHTML = days.size ? [...days].map(([day, list]) => `
      <div class="me-day">${escapeHtml(formatDay(day, { weekday: true, year: false }))}</div>
      ${list.map((l) => cabinetRow(l, data.tracked)).join("")}`).join("")
    : `<p class="me-p muted me-empty">За ${escapeHtml(monthLabel(month))} уроков с отметками нет.</p>`;
}
