/* Расписание занятий в панели: сетка недели, окно занятия, полоска «сегодня по
   расписанию» в журнале. Подключается после teacher.js и пользуется его state,
   activeSchool, api(), openModal() и т. п. Менять расписание могут учитель
   и администратор; администрация школы только смотрит (сервер проверяет сам). */

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];

let scheduleClass = null;     // фильтр на вкладке «Расписание»: null — все классы
let scheduleTarget = null;    // занятие в окне правки; null — новое
let scheduleDays = new Set();
let scheduleWeek = 0;         // 0 — эта неделя, −1 — прошлая, 1 — следующая
let scheduleSplit = false;    // окно в режиме «новое время с даты»

/** День недели ISO-даты: 1 — понедельник … 7 — воскресенье. */
function isoWeekday(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return ((new Date(y, m - 1, d).getDay() + 6) % 7) + 1;
}

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Даты недели: понедельник … воскресенье. offset — сколько недель от текущей. */
function thisWeekDates(offset = 0) {
  const now = new Date();
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7) + offset * 7);
  return Array.from({ length: 7 }, (_, i) => isoDate(new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i)));
}

const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                    "сентября", "октября", "ноября", "декабря"];

/** «23–29 ноября» или «30 ноября – 6 декабря». */
function weekRangeLabel(dates) {
  const [, m1, d1] = dates[0].split("-").map(Number);
  const [, m2, d2] = dates[6].split("-").map(Number);
  return m1 === m2 ? `${d1}–${d2} ${MONTHS_GEN[m2 - 1]}` : `${d1} ${MONTHS_GEN[m1 - 1]} – ${d2} ${MONTHS_GEN[m2 - 1]}`;
}

function shiftScheduleWeek(step) {
  scheduleWeek = step === 0 ? 0 : scheduleWeek + step;
  renderSchedule();
}

function schoolSchedule() {
  const ids = new Set(sortedClasses(activeSchool).map((c) => c.id));
  return (state.schedule || []).filter((e) => ids.has(e.class_id))
    .sort((a, b) => a.weekday - b.weekday || a.start.localeCompare(b.start));
}

function lessonHeld(classId, day) {
  return (state.lessons || []).some((l) => l.class_id === classId && l.date === day);
}

/* ── Полоска в журнале ────────────────────────────────────── */

function renderTodayStrip() {
  const host = $("todayStrip");
  if (!host) return;
  const day = lessonDay();
  const entries = schoolSchedule().filter((e) => e.weekday === isoWeekday(day) && entryActiveOn(e, day));
  host.hidden = !entries.length || !canEdit();
  if (host.hidden) return;
  host.innerHTML = `<span class="caps">По расписанию на ${escapeHtml(dotted(day))}</span>` + entries.map((e) => {
    const cls = classById(e.class_id);
    const done = lessonHeld(e.class_id, day);
    return `<button class="ts-chip ${e.class_id === activeClass ? "on" : ""} ${done ? "done" : ""}"
        onclick="selectClass(${e.class_id})" title="${done ? "Урок проведён" : "Открыть журнал класса"}">
      <span class="ts-time">${escapeHtml(e.start)}</span><b>${escapeHtml(cls?.name || "")}</b>
      ${e.room ? `<span class="ts-room">каб. ${escapeHtml(e.room)}</span>` : ""}
      ${done ? icon("check", "ic ic-xs") : ""}</button>`;
  }).join("");
}

/* ── Вкладка «Расписание» ─────────────────────────────────── */

function renderSchedule() {
  const grid = $("weekGrid");
  if (!grid) return;
  const classes = sortedClasses(activeSchool);
  if (scheduleClass && !classes.some((c) => c.id === scheduleClass)) scheduleClass = null;
  $("scheduleClasses").innerHTML = [`<button role="tab" aria-selected="${scheduleClass === null}" onclick="pickScheduleClass(null)">Все</button>`]
    .concat(classes.map((c) => `<button role="tab" aria-selected="${c.id === scheduleClass}" onclick="pickScheduleClass(${c.id})">${escapeHtml(c.name)}</button>`))
    .join("");
  $("scheduleClasses").hidden = classes.length < 2;

  if (!activeSchool) {
    grid.innerHTML = `<div class="card empty">${icon("school", "ic ic-xl")}<b>Школ пока нет</b></div>`;
    return;
  }
  const entries = schoolSchedule().filter((e) => scheduleClass === null || e.class_id === scheduleClass);
  const dates = thisWeekDates(scheduleWeek);
  const today = isoDate(new Date());
  const range = weekRangeLabel(dates);
  $("weekLabel").textContent = scheduleWeek === 0 ? `Эта неделя · ${range}` : range;
  $("weekLabel").setAttribute("aria-current", String(scheduleWeek === 0));
  const showSunday = entries.some((e) => e.weekday === 7 && entryActiveOn(e, dates[6]));
  const days = showSunday ? 7 : 6;
  grid.style.setProperty("--days", days);

  grid.innerHTML = Array.from({ length: days }, (_, i) => {
    const weekday = i + 1;
    const date = dates[i];
    const [, m, d] = date.split("-");
    const items = entries.filter((e) => e.weekday === weekday && entryActiveOn(e, date));
    return `<section class="day ${date === today ? "today" : ""}" aria-label="${WEEKDAYS_FULL[i]}">
      <header class="day-head"><b>${WEEKDAYS[i]}</b><span>${d}.${m}</span>${date === today ? `<span class="tag tag-accent">сегодня</span>` : ""}</header>
      ${items.map((e) => scheduleCard(e, date, today)).join("") || `<p class="day-empty">нет занятий</p>`}
    </section>`;
  }).join("");
}

function scheduleCard(entry, date, today) {
  const cls = classById(entry.class_id);
  const done = date <= today && lessonHeld(entry.class_id, date);
  const missed = date < today && !done;
  const teacher = staffName(entry.user_id);
  const tag = done ? `<span class="sch-state ok">${icon("check", "ic ic-xs")}проведён</span>`
    : missed ? `<span class="sch-state">не отмечен</span>` : "";
  const bounds = entryBoundsLabel(entry, today);
  const inner = `
    <span class="sch-time">${escapeHtml(entry.start)}–${escapeHtml(entry.end)}${bounds ? `<span class="sch-valid">${escapeHtml(bounds)}</span>` : ""}</span>
    <span class="sch-class">${escapeHtml(cls?.name || "")}</span>
    <span class="sch-meta">${entry.room ? `каб. ${escapeHtml(entry.room)}` : ""}${entry.room && teacher ? " · " : ""}${escapeHtml(teacher)}</span>
    ${tag}`;
  return canEdit()
    ? `<button class="sch-card ${done ? "done" : ""}" onclick="openScheduleEntry(${entry.id})" title="Изменить занятие">${inner}</button>`
    : `<div class="sch-card ${done ? "done" : ""}">${inner}</div>`;
}

function pickScheduleClass(id) {
  scheduleClass = id;
  renderSchedule();
}

/* ── Окно занятия ─────────────────────────────────────────── */

function scheduleTeachers() {
  const school = activeSchool;
  return staffList().filter((u) => u.role !== "school_admin" &&
    (u.role === "superadmin" || !u.school_ids || u.school_ids.includes(school)));
}

function renderDayPick() {
  $("schDays").innerHTML = WEEKDAYS.map((name, i) => {
    const on = scheduleDays.has(i + 1);
    return `<button type="button" class="${on ? "on" : ""}" aria-pressed="${on}" onclick="toggleScheduleDay(${i + 1})"
      title="${WEEKDAYS_FULL[i]}">${name}</button>`;
  }).join("");
}

function toggleScheduleDay(day) {
  if (scheduleTarget) scheduleDays = new Set([day]);          // у существующего занятия день один
  else if (scheduleDays.has(day)) scheduleDays.delete(day);
  else scheduleDays.add(day);
  renderDayPick();
}

function openScheduleEntry(id = null) {
  if (!activeSchool) return toast("Сначала выберите школу", "error");
  const classes = sortedClasses(activeSchool);
  if (!classes.length) return toast("Сначала добавьте классы", "error");
  scheduleTarget = id;
  scheduleSplit = false;
  const entry = id ? (state.schedule || []).find((e) => e.id === id) : null;
  $("scheduleTitle").textContent = entry ? "Занятие" : "Новое занятие";
  $("scheduleSub").textContent = entry ? "Изменения увидят ученики класса в своём кабинете."
    : "Можно отметить сразу несколько дней недели.";
  $("schClass").innerHTML = classes.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  $("schClass").value = String(entry?.class_id || scheduleClass || activeClass || classes[0].id);
  $("schClass").disabled = Boolean(entry);
  scheduleDays = new Set(entry ? [entry.weekday] : [isoWeekday(lessonDay())]);
  renderDayPick();
  $("schStart").value = entry?.start || "14:00";
  $("schEnd").value = entry?.end || "14:45";
  $("schRoom").value = entry?.room || "";
  const teachers = scheduleTeachers();
  $("schTeacher").innerHTML = `<option value="">не указан</option>` +
    teachers.map((u) => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join("");
  $("schTeacher").value = String(entry ? entry.user_id ?? "" : state.me?.id ?? "");
  $("schFromLabel").textContent = "Действует с";
  $("schFrom").value = entry?.valid_from || "";
  $("schTo").value = entry?.valid_to || "";
  $("schDatesHint").textContent = entry && !entry.valid_from && entry.created_at
    ? `Пусто — с ${dotted(entry.created_at.slice(0, 10))}, когда занятие внесли, и без конца.`
    : "Пусто — с сегодняшнего дня и без конца.";
  $("schDelete").hidden = !entry;
  $("schSplit").hidden = !entry;
  openModal("modalSchedule");
}

/** Ближайший после сегодняшнего день с этим днём недели — «с какого дня» по умолчанию. */
function nextWeekdayDate(weekday) {
  const now = new Date();
  const ahead = ((weekday - (((now.getDay() + 6) % 7) + 1)) + 7) % 7 || 7;
  return isoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + ahead));
}

/** Окно переходит в «новое время с даты»: старое занятие закроется накануне, новое начнётся с даты. */
function startScheduleSplit() {
  const entry = (state.schedule || []).find((e) => e.id === scheduleTarget);
  if (!entry) return;
  scheduleSplit = true;
  $("scheduleTitle").textContent = "Новое время с даты";
  $("scheduleSub").textContent = "Старое занятие будет действовать до этого дня, новое — с него. "
    + "Прошедшие уроки и отметки не изменятся.";
  $("schFromLabel").textContent = "С какого дня";
  $("schFrom").value = nextWeekdayDate(entry.weekday);
  $("schTo").value = "";
  $("schDatesHint").textContent = "«По» — если новое время тоже временное. Пусто — без конца.";
  $("schSplit").hidden = true;
  $("schDelete").hidden = true;
  $("schStart").focus();
}

async function saveScheduleEntry() {
  const start = $("schStart").value;
  const end = $("schEnd").value;
  if (!scheduleDays.size) return toast("Отметьте хотя бы один день недели", "error");
  if (!start || !end || end <= start) return toast("Конец занятия должен быть позже начала", "error");
  const teacher = $("schTeacher").value ? parseInt($("schTeacher").value) : null;
  const validFrom = $("schFrom").value || null;
  const validTo = $("schTo").value || null;
  if (validFrom && validTo && validTo < validFrom) return toast("«По» не может быть раньше «с»", "error");
  const room = $("schRoom").value.trim();
  try {
    if (scheduleSplit) {
      await splitScheduleEntry({ start, end, room, user_id: teacher, valid_from: validFrom, valid_to: validTo });
    } else if (scheduleTarget) {
      await api(`/api/schedule/${scheduleTarget}`, {
        method: "PATCH",
        json: { weekday: [...scheduleDays][0], start, end, room, user_id: teacher,
                valid_from: validFrom, valid_to: validTo },
      });
    } else {
      await api("/api/schedule", {
        method: "POST",
        json: { class_id: parseInt($("schClass").value), weekdays: [...scheduleDays], start, end, room,
                user_id: teacher, valid_from: validFrom, valid_to: validTo },
      });
    }
    closeModal("modalSchedule");
    await loadState();
    if (!scheduleSplit) toast("Расписание сохранено", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/** Смена с даты: старому занятию — «по» накануне, затем новое с даты.
    Не сохранилось новое — старому возвращаем прежний срок, ничего не теряется. */
async function splitScheduleEntry(fields) {
  const old = (state.schedule || []).find((e) => e.id === scheduleTarget);
  if (!old) throw new Error("Занятие не найдено");
  if (!fields.valid_from) throw new Error("Укажите, с какого дня новое время");
  const oldFrom = old.valid_from || (old.created_at || "").slice(0, 10);
  if (oldFrom && fields.valid_from <= oldFrom) {
    throw new Error("Новое время должно начинаться позже, чем действует старое — или просто измените занятие");
  }
  const until = dayBefore(fields.valid_from);
  await api(`/api/schedule/${old.id}`, { method: "PATCH", json: { valid_to: until } });
  try {
    await api("/api/schedule", {
      method: "POST",
      json: { class_id: old.class_id, weekdays: [...scheduleDays], ...fields },
    });
  } catch (error) {
    await api(`/api/schedule/${old.id}`, { method: "PATCH", json: { valid_to: old.valid_to ?? null } }).catch(() => {});
    throw error;
  }
  toast(`Старое время — по ${dotted(until)}, новое — с ${dotted(fields.valid_from)}`, "success");
}

function removeScheduleEntry() {
  const entry = (state.schedule || []).find((e) => e.id === scheduleTarget);
  if (!entry) return;
  closeModal("modalSchedule");
  confirmAction("Удалить занятие?",
    `${escapeHtml(classById(entry.class_id)?.name || "")}, ${WEEKDAYS_FULL[entry.weekday - 1]} ${escapeHtml(entry.start)}. ` +
    "Проведённые уроки и звёзды останутся.",
    async () => {
      await api(`/api/schedule/${entry.id}`, { method: "DELETE" });
      await loadState();
      toast("Занятие удалено", "success");
    }, "Удалить");
}
