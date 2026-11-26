/* Расписание занятий в панели: таблица «дни недели × уроки школы», окошко ячейки,
   окно «Время уроков», занятия в другое время и полоска «сегодня по расписанию»
   в журнале. Подключается после teacher.js и пользуется его state, activeSchool,
   api(), openModal() и т. п. Менять расписание могут учитель и администратор;
   администрация школы только смотрит (сервер проверяет сам).

   Занятие стоит в ячейке таблицы, если его время совпало с уроком школы:
   entry.lesson — номер урока (с 1), иначе null — «в другое время».
   В ячейке один урок; объединённые классы — несколько занятий в одной ячейке
   (по одному на класс) с общими учителем и кабинетом. */

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];
const MAX_BELLS = 12;

let scheduleClass = null;     // фильтр на вкладке «Расписание»: null — все классы
let scheduleTarget = null;    // занятие в окне правки; null — новое
let scheduleDays = new Set();
let cellPick = null;          // открытая ячейка: { school, weekday, lesson, date, combine, draft }
                              // date — день открытой недели, combine — выбор нескольких классов,
                              // draft — отмеченные id
let bellsDraft = [];          // время уроков в окне «Время уроков»
let scheduleWeek = 0;         // 0 — эта неделя, −1 — прошлая, 1 — следующая
let scheduleSplit = false;    // окно занятия в режиме «новое время с даты»

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
  closeCellPop();
  renderSchedule();
}

/* ── Время уроков: чистые функции (проверяются через Node) ───── */

function clockMinutes(text) {
  const [h, m] = String(text).split(":").map(Number);
  return h * 60 + m;
}

function clockText(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/** Уроки по порядку: первый в first, каждый length минут, перемены pause минут.
 *  За полночь не уходим — лишние уроки отбрасываются. */
function makeBells(first, length, pause, count) {
  const bells = [];
  let start = clockMinutes(first);
  for (let i = 0; i < count; i++) {
    const end = start + length;
    if (end > 24 * 60 - 1) break;
    bells.push({ start: clockText(start), end: clockText(end) });
    start = end + pause;
  }
  return bells;
}

/** Что не так со временем уроков — текст ошибки или "" . */
function bellsError(bells) {
  if (!bells.length) return "Нужен хотя бы один урок";
  for (let i = 0; i < bells.length; i++) {
    const { start, end } = bells[i];
    const n = i + 1;
    if (!start || !end) return `Заполните время ${n}-го урока`;
    if (end <= start) return `${n}-й урок должен заканчиваться позже, чем начинается`;
    if (i > 0 && start < bells[i - 1].end) return `${n}-й урок начинается раньше, чем закончится ${n - 1}-й`;
  }
  return "";
}

/** Короткое имя объединённых классов: «7-А», «8-АБВ», «7-АВ + 9-Б».
 *  Классы вида «число-буква» одной параллели сливаются, остальные — как есть. */
function combinedName(names) {
  const parts = [];
  const grades = new Map();
  for (const name of names) {
    const m = /^(\d{1,2})\s*-?\s*([А-ЯЁA-Z])$/i.exec(String(name).trim());
    if (!m) { parts.push(name); continue; }
    if (!grades.has(m[1])) {
      grades.set(m[1], []);
      parts.push(grades.get(m[1]));
    }
    grades.get(m[1]).push(m[2]);
    grades.get(m[1]).grade = m[1];
  }
  return parts.map((p) => (typeof p === "string" ? p : `${p.grade}-${p.join("")}`)).join(" + ");
}

/** Сколько дней показать: учебные дни школы, но не меньше дня, на котором уже есть занятия. */
function visibleDays(days, entries) {
  return Math.max(days || 5, ...entries.map((e) => e.weekday));
}

/* ── Данные школы ─────────────────────────────────────────── */

function schoolSchedule() {
  const ids = new Set(sortedClasses(activeSchool).map((c) => c.id));
  return (state.schedule || []).filter((e) => ids.has(e.class_id))
    .sort((a, b) => a.weekday - b.weekday || a.start.localeCompare(b.start));
}

/** Занятия открытой в таблице недели: каждое — если действует в свой день этой недели. */
function weekSchedule(dates = thisWeekDates(scheduleWeek)) {
  return schoolSchedule().filter((e) => entryShownOn(e, dates[e.weekday - 1]));
}

function schoolBells() {
  return schoolById(activeSchool)?.bells || [];
}

/** Занятия ячейки — классы урока в порядке списка классов. */
function cellEntries(weekday, lesson, all = weekSchedule()) {
  const order = new Map(sortedClasses(activeSchool).map((c, i) => [c.id, i]));
  return all.filter((e) => e.weekday === weekday && e.lesson === lesson)
    .sort((a, b) => (order.get(a.class_id) ?? 0) - (order.get(b.class_id) ?? 0));
}

function entriesName(entries) {
  return combinedName(entries.map((e) => classById(e.class_id)?.name || ""));
}

function lessonHeld(classId, day) {
  return (state.lessons || []).some((l) => l.class_id === classId && l.date === day);
}

/** Проведено ли занятие в эту дату; «не отмечен» — только за прошедшие дни, когда занятие
 *  уже было в расписании (добавленное в среду не «пропущено» в понедельник). */
function entryState(entry, date, today) {
  const done = date <= today && lessonHeld(entry.class_id, date);
  const missed = !done && date < today && date >= String(entry.created_at || "").slice(0, 10);
  return { done, missed };
}

/* ── Кто изменил ──────────────────────────────────────────── */

/** Расписание меняют все сотрудники школы: учитель, администратор, администрация школы. */
function scheduleEditable() { return Boolean(state.me); }

const AUTHOR_ROLES = { superadmin: "администратор", teacher: "учитель", school_admin: "администрация школы" };

/** «26.11, 14:05» — по часам этого компьютера (на сервере время с часовым поясом). */
function changedAt(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `сегодня, ${time}`;
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}, ${time}`;
}

/** «Изменил: Каримова (администрация школы), сегодня, 14:05» — у занятия, если его меняли. */
function entryAuthor(entry) {
  if (!entry?.updated_by_name) return "";
  const role = AUTHOR_ROLES[entry.updated_by_role] || "";
  return `Изменено: ${entry.updated_by_name}${role ? ` (${role})` : ""}, ${changedAt(entry.updated_at)}`;
}

function openScheduleLog() {
  const school = schoolById(activeSchool);
  const rows = (state.schedule_log || []).filter((r) => r.school_id === activeSchool);
  $("schedLogSub").textContent = `${school?.name || ""} · последние изменения, новые сверху. Кто, когда и что было → что стало.`;
  $("schedLogList").innerHTML = rows.length ? rows.map((r) => `
    <div class="sched-log-row">
      <span class="sl-when">${escapeHtml(changedAt(r.at))}</span>
      <span class="sl-text">${escapeHtml(r.text)}</span>
      <span class="sl-who">${escapeHtml(r.user_name)}${AUTHOR_ROLES[r.role] ? ` · ${AUTHOR_ROLES[r.role]}` : ""}</span>
    </div>`).join("")
    : `<p class="muted">Изменений пока нет. Здесь появится каждая правка: кто поставил, заменил или убрал урок.</p>`;
  openModal("modalScheduleLog");
}

/* ── Полоска в журнале ────────────────────────────────────── */

function renderTodayStrip() {
  const host = $("todayStrip");
  if (!host) return;
  const day = lessonDay();
  const entries = schoolSchedule().filter((e) => e.weekday === isoWeekday(day) && entryShownOn(e, day));
  host.hidden = !entries.length || !canEdit();
  if (host.hidden) return;
  const joint = typeof jointClasses !== "undefined" ? jointClasses : null;
  host.innerHTML = `<span class="caps">По расписанию на ${escapeHtml(dotted(day))}</span>` + todayLessons(entries).map((group) => {
    const ids = group.map((e) => e.class_id);
    const done = ids.every((id) => lessonHeld(id, day));
    const room = group.find((e) => e.room)?.room || "";
    const together = ids.length > 1;
    const inClass = typeof journalView === "undefined" || journalView === "class";
    const on = inClass && (together ? joint?.join() === ids.join() : !joint && ids[0] === activeClass);
    const names = group.map((e) => classById(e.class_id)?.name || "").join(", ");
    return `<button class="ts-chip ${on ? "on" : ""} ${done ? "done" : ""}"
        onclick="${together ? `openJoint([${ids.join(",")}])` : `selectClass(${ids[0]})`}"
        title="${done ? "Урок проведён" : together ? `Открыть общий журнал: ${escapeHtml(names)}` : "Открыть журнал класса"}">
      <span class="ts-time">${escapeHtml(group[0].start)}</span>${together ? icon("users", "ic ic-xs") : ""}<b>${escapeHtml(entriesName(group))}</b>
      ${room ? `<span class="ts-room">каб. ${escapeHtml(room)}</span>` : ""}
      ${done ? icon("check", "ic ic-xs") : ""}</button>`;
  }).join("");
}

/** Состояние плитки урока на дату date: done — проведён; past — время прошло, урок
 *  не отмечен; now — идёт сейчас; later — впереди. nowMinutes — минуты от полуночи. */
function lessonTileState(start, end, date, today, nowMinutes, held) {
  if (held) return "done";
  if (date < today) return "past";
  if (date > today) return "later";
  if (nowMinutes >= clockMinutes(end)) return "past";
  return nowMinutes >= clockMinutes(start) ? "now" : "later";
}

/** Занятия дня по урокам: объединённые классы одной клетки таблицы — вместе. */
function todayLessons(entries) {
  const groups = [];
  for (const e of entries) {
    const same = e.lesson ? groups.find((g) => g[0].lesson === e.lesson) : null;
    if (same) same.push(e);
    else groups.push([e]);
  }
  return groups.map((g) => (g.length > 1 ? cellEntries(g[0].weekday, g[0].lesson, g) : g));
}

/* ── Вкладка «Расписание» ─────────────────────────────────── */

function renderSchedule() {
  const grid = $("weekGrid");
  if (!grid) return;
  const classes = sortedClasses(activeSchool);
  if (scheduleClass && !classes.some((c) => c.id === scheduleClass)) scheduleClass = null;
  if (cellPick && cellPick.school !== activeSchool) cellPick = null;
  $("scheduleClasses").innerHTML = [`<button role="tab" aria-selected="${scheduleClass === null}" onclick="pickScheduleClass(null)">Все</button>`]
    .concat(classes.map((c) => `<button role="tab" aria-selected="${c.id === scheduleClass}" onclick="pickScheduleClass(${c.id})">${escapeHtml(c.name)}</button>`))
    .join("");
  $("scheduleClasses").hidden = classes.length < 2;
  $("scheduleHint").textContent = scheduleEditable()
    ? "Нажмите на клетку, чтобы поставить класс на урок. Журнал подсказывает, какие уроки сегодня, а ученик видит свой следующий урок."
    : "Уроки школы по дням недели.";

  if (!activeSchool) {
    grid.innerHTML = `<div class="empty">${icon("school", "ic ic-xl")}<b>Школ пока нет</b></div>`;
    $("schedExtra").hidden = true;
    closeCellPop();
    return;
  }
  const dates = thisWeekDates(scheduleWeek);
  const all = weekSchedule(dates);
  const bells = schoolBells();
  const days = visibleDays(schoolById(activeSchool)?.days, all);
  const today = isoDate(new Date());
  const range = weekRangeLabel(dates);
  $("weekLabel").textContent = scheduleWeek === 0 ? `Эта неделя · ${range}` : range;
  $("weekLabel").setAttribute("aria-current", String(scheduleWeek === 0));
  grid.style.setProperty("--days", days);

  const head = `<div class="sg-row sg-head" role="row">
      <div class="sg-corner" role="columnheader"><span class="caps">Урок</span></div>
      ${Array.from({ length: days }, (_, i) => {
        const [, m, d] = dates[i].split("-");
        return `<div class="sg-day ${dates[i] === today ? "today" : ""}" role="columnheader" aria-label="${WEEKDAYS_FULL[i]}">
          <b>${WEEKDAYS[i]}</b><span>${d}.${m}</span></div>`;
      }).join("")}
    </div>`;
  const rows = bells.map((bell, i) => {
    const n = i + 1;
    return `<div class="sg-row" role="row">
      <div class="sg-num" role="rowheader"><b>${n}</b><span>${escapeHtml(bell.start)}<span class="sg-end">–${escapeHtml(bell.end)}</span></span></div>
      ${Array.from({ length: days }, (_, d) => scheduleCell(d + 1, n, all, dates[d], today)).join("")}
    </div>`;
  }).join("");
  grid.innerHTML = head + rows;

  const extra = all.filter((e) => !e.lesson && (scheduleClass === null || e.class_id === scheduleClass));
  $("schedExtra").hidden = !extra.length && !scheduleEditable();
  $("extraList").innerHTML = extra.length
    ? extra.map((e) => scheduleCard(e, dates[e.weekday - 1], today)).join("")
    : `<p class="sched-extra-empty">Если занятие не совпадает со временем уроков школы, добавьте его здесь.</p>`;

  if (cellPick) renderCellPop();
}

function scheduleCell(weekday, lesson, all, date, today) {
  const entries = cellEntries(weekday, lesson, all);
  const where = `${WEEKDAYS_FULL[weekday - 1]}, ${lesson}-й урок`;
  const open = cellPick?.weekday === weekday && cellPick?.lesson === lesson;
  const dim = scheduleClass !== null && !entries.some((e) => e.class_id === scheduleClass);
  let inner;
  let label;
  if (entries.length) {
    const name = entriesName(entries);
    const full = entries.map((e) => classById(e.class_id)?.name || "").join(", ");
    // Объединённый урок проведён, когда отмечены все его классы
    const states = entries.map((e) => entryState(e, date, today));
    const done = states.every((s) => s.done);
    const missed = !done && states.some((s) => s.missed);
    const room = entries.find((e) => e.room)?.room || "";
    const teachers = [...new Set(entries.map((e) => staffName(e.user_id)).filter(Boolean))].join(", ");
    const meta = [room ? `каб. ${escapeHtml(room)}` : "", escapeHtml(teachers)].filter(Boolean).join(" · ");
    const bounds = entries.map((e) => entryBoundsLabel(e, today)).find(Boolean) || "";
    inner = `<span class="sg-fill ${done ? "done" : ""}">
      <b class="sg-class ${entries.length > 1 ? "joint" : ""}" title="${escapeHtml(full)}">${escapeHtml(name)}</b>
      ${meta ? `<span class="sg-meta">${meta}</span>` : ""}
      ${bounds ? `<span class="sg-valid">${escapeHtml(bounds)}</span>` : ""}
      ${done ? `<span class="sg-state ok">${icon("check", "ic ic-xs")}проведён</span>`
        : missed ? `<span class="sg-state">не отмечен</span>` : ""}</span>`;
    label = `${where}: ${entries.length > 1 ? "вместе " : ""}${full}${bounds ? `, ${bounds}` : ""}${done ? ", проведён" : ""}`;
  } else {
    inner = scheduleEditable() ? `<span class="sg-add">${icon("plus", "ic ic-sm")}</span>` : "";
    label = `${where}: свободно`;
  }
  const cls = `sg-cell ${dim ? "dim" : ""}`;
  const body = scheduleEditable()
    ? `<button type="button" class="${cls}" data-cell="${weekday}-${lesson}" aria-label="${escapeHtml(label)}"
         aria-haspopup="dialog" aria-expanded="${open}" onclick="openCell(event, ${weekday}, ${lesson})">${inner}</button>`
    : `<div class="${cls}" aria-label="${escapeHtml(label)}">${inner}</div>`;
  return `<div class="sg-slot ${date === today ? "today" : ""}" role="cell">${body}</div>`;
}

function scheduleCard(entry, date, today) {
  const cls = classById(entry.class_id);
  const { done, missed } = entryState(entry, date, today);
  const teacher = staffName(entry.user_id);
  const tag = done ? `<span class="sch-state ok">${icon("check", "ic ic-xs")}проведён</span>`
    : missed ? `<span class="sch-state">не отмечен</span>` : "";
  const bounds = entryBoundsLabel(entry, today);
  const inner = `
    <span class="sch-time">${WEEKDAYS[entry.weekday - 1]} · ${escapeHtml(entry.start)}–${escapeHtml(entry.end)}${bounds ? `<span class="sch-valid">${escapeHtml(bounds)}</span>` : ""}</span>
    <span class="sch-class">${escapeHtml(cls?.name || "")}</span>
    <span class="sch-meta">${entry.room ? `каб. ${escapeHtml(entry.room)}` : ""}${entry.room && teacher ? " · " : ""}${escapeHtml(teacher)}</span>
    ${tag}`;
  return scheduleEditable()
    ? `<button class="sch-card ${done ? "done" : ""}" onclick="openScheduleEntry(${entry.id})" title="Изменить занятие">${inner}</button>`
    : `<div class="sch-card ${done ? "done" : ""}">${inner}</div>`;
}

function pickScheduleClass(id) {
  scheduleClass = id;
  renderSchedule();
}

/* ── Окошко ячейки: класс и учитель ───────────────────────── */

function cellPopOpen() { return Boolean(cellPick); }

function openCell(event, weekday, lesson) {
  event?.stopPropagation();
  if (cellPick?.weekday === weekday && cellPick?.lesson === lesson) return closeCellPop();
  closeMenus();
  if (!sortedClasses(activeSchool).length) return toast("Сначала добавьте классы", "error");
  const ids = cellEntries(weekday, lesson).map((e) => e.class_id);
  const date = thisWeekDates(scheduleWeek)[weekday - 1];
  cellPick = { school: activeSchool, weekday, lesson, date, combine: ids.length > 1, draft: new Set(ids) };
  document.querySelectorAll("#weekGrid .sg-cell[aria-expanded]").forEach((b) =>
    b.setAttribute("aria-expanded", String(b.dataset.cell === `${weekday}-${lesson}`)));
  renderCellPop();
  $("cellPop").querySelector('[aria-checked="true"], .cp-classes button')?.focus({ preventScroll: true });
}

function closeCellPop() {
  if (!cellPick) return;
  const anchor = document.querySelector(`#weekGrid [data-cell="${cellPick.weekday}-${cellPick.lesson}"]`);
  cellPick = null;
  $("cellPop").hidden = true;
  document.querySelectorAll('#weekGrid .sg-cell[aria-expanded="true"]').forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (anchor && document.activeElement?.closest?.("#cellPop")) anchor.focus({ preventScroll: true });
}

function renderCellPop() {
  const pop = $("cellPop");
  const { weekday, lesson, combine, draft } = cellPick;
  const bell = schoolBells()[lesson - 1];
  if (!bell) return closeCellPop();
  const entries = cellEntries(weekday, lesson);
  const entry = entries[0] || null;
  const teachers = scheduleTeachers();
  const mine = teachers.some((u) => u.id === state.me?.id) ? state.me.id : null;
  const teacherId = entry ? entry.user_id : mine;
  const classes = sortedClasses(activeSchool);
  const buttons = combine
    ? classes.map((c) => `<button type="button" role="checkbox" aria-checked="${draft.has(c.id)}" data-class="${c.id}"
          title="${escapeHtml(c.name)}" onclick="toggleCellClass(${c.id})">${escapeHtml(c.name)}</button>`)
    : classes.map((c) => `<button type="button" role="radio" aria-checked="${entry?.class_id === c.id}" data-class="${c.id}"
          title="${escapeHtml(c.name)}" onclick="pickCellClass(${c.id})">${escapeHtml(c.name)}</button>`);
  const picked = classes.filter((c) => draft.has(c.id)).map((c) => c.name);
  const changed = combine && !sameIds(draft, entries.map((e) => e.class_id));
  const comboFoot = `<div class="cp-combo">
      <span class="cp-hint">${picked.length > 1 ? `Вместе: <b>${escapeHtml(combinedName(picked))}</b>`
        : picked.length ? "Отметьте ещё класс, который занимается вместе с этим." : "Отметьте классы, которые занимаются вместе."}</span>
      <button type="button" class="btn btn-sm" onclick="saveCellCombo()" ${changed ? "" : "disabled"}>${icon("check", "ic ic-sm")}Сохранить</button>
    </div>`;
  pop.innerHTML = `
    <div class="cp-head"><b id="cellPopTitle">${WEEKDAYS[weekday - 1]} · ${lesson}-й урок</b>
      <span>${escapeHtml(bell.start)}–${escapeHtml(bell.end)}</span></div>
    ${cellPick.date > isoDate(new Date()) && scheduleWeek > 0
      ? `<p class="cp-hint">Правка действует с ${escapeHtml(dotted(cellPick.date))}; прошлые недели не меняются.</p>` : ""}
    <div class="cp-classes" role="${combine ? "group" : "radiogroup"}" aria-label="${combine ? "Классы урока" : "Класс"}">
      ${buttons.join("")}
    </div>
    ${combine ? comboFoot : classes.length > 1
      ? `<button type="button" class="btn btn-link btn-sm cp-join" onclick="startCombine()">${icon("users", "ic ic-sm")}Объединить классы</button>` : ""}
    <div class="field">
      <label for="cpTeacher">Учитель${entries.length > 1 ? " (общий)" : ""}</label>
      <select id="cpTeacher" onchange="pickCellTeacher()">
        <option value="">не указан</option>
        ${teachers.map((u) => `<option value="${u.id}" ${u.id === teacherId ? "selected" : ""}>${escapeHtml(u.name)}</option>`).join("")}
      </select>
    </div>
    ${entry && entryAuthor(entry) ? `<p class="cp-author">${icon("history", "ic ic-xs")}${escapeHtml(entryAuthor(entry))}</p>` : ""}
    ${entry ? `<div class="cp-foot">
      <button type="button" class="btn btn-link btn-sm" onclick="editCellEntry(${entry.id})">${icon("pencil", "ic ic-sm")}Кабинет и другое…</button>
      <button type="button" class="btn btn-danger-quiet btn-sm" onclick="pickCellClass(null)">${icon("trash", "ic ic-sm")}Убрать</button>
    </div>` : combine ? "" : `<p class="cp-hint">Выберите класс — урок сохранится сразу.</p>`}`;
  pop.hidden = false;
  placeCellPop();
}

function sameIds(set, ids) {
  return set.size === new Set(ids).size && ids.every((id) => set.has(id));
}

/** Режим «несколько классов»: отмечаем классы, потом «Сохранить». */
function startCombine() {
  if (!cellPick) return;
  cellPick.combine = true;
  renderCellPop();
  $("cellPop").querySelector(".cp-classes button")?.focus({ preventScroll: true });
}

function toggleCellClass(classId) {
  if (!cellPick) return;
  const { draft } = cellPick;
  if (draft.has(classId)) draft.delete(classId);
  else draft.add(classId);
  renderCellPop();
  $("cellPop").querySelector(`.cp-classes [data-class="${classId}"]`)?.focus({ preventScroll: true });
}

async function saveCellCombo() {
  if (!cellPick) return;
  const { weekday, lesson, draft } = cellPick;
  const classes = sortedClasses(activeSchool).filter((c) => draft.has(c.id));
  const value = $("cpTeacher")?.value;
  try {
    await saveCell(classes.map((c) => c.id), value ? parseInt(value) : null);
    closeCellPop();
    await loadState();
    toast(classes.length ? `${combinedName(classes.map((c) => c.name))}: ${WEEKDAYS_FULL[weekday - 1]}, ${lesson}-й урок`
      : `Урок убран: ${WEEKDAYS_FULL[weekday - 1]}, ${lesson}-й`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/** Окошко — у нажатой ячейки: снизу, а если не помещается — сверху; в пределах экрана. */
function placeCellPop() {
  const pop = $("cellPop");
  const anchor = document.querySelector(`#weekGrid [data-cell="${cellPick.weekday}-${cellPick.lesson}"]`);
  const a = anchor?.getBoundingClientRect();
  if (!a || !a.width) { closeCellPop(); return; }
  const gap = 6;
  const top0 = popTopEdge() + gap;
  const h = pop.offsetHeight;
  const w = pop.offsetWidth;
  let top = a.bottom + gap;
  if (top + h > window.innerHeight - gap && a.top - gap - h >= top0) top = a.top - gap - h;
  top = Math.max(top0, Math.min(top, window.innerHeight - gap - h));
  const left = Math.max(gap, Math.min(a.left, window.innerWidth - gap - w));
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
}

/** Классы урока целиком: [] — убрать урок. */
async function saveCell(classIds, teacherId) {
  const { weekday, lesson, date } = cellPick;
  await api("/api/schedule/cell", {
    method: "PUT",
    json: { school_id: activeSchool, weekday, lesson, date, class_ids: classIds, user_id: teacherId },
  });
}

async function pickCellClass(classId) {
  if (!cellPick) return;
  const { weekday, lesson } = cellPick;
  const entries = cellEntries(weekday, lesson);
  if (entries.length === 1 && entries[0].class_id === classId) return closeCellPop();
  const value = $("cpTeacher")?.value;
  try {
    await saveCell(classId ? [classId] : [], value ? parseInt(value) : null);
    closeCellPop();
    await loadState();
    toast(classId ? `${classById(classId)?.name || "Класс"}: ${WEEKDAYS_FULL[weekday - 1]}, ${lesson}-й урок`
      : `Урок убран: ${WEEKDAYS_FULL[weekday - 1]}, ${lesson}-й`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function pickCellTeacher() {
  if (!cellPick) return;
  const entries = cellEntries(cellPick.weekday, cellPick.lesson);
  if (!entries.length) return;              // ячейка пустая — учитель запомнится с выбором класса
  const value = $("cpTeacher").value;
  try {
    await saveCell(entries.map((e) => e.class_id), value ? parseInt(value) : null);
    await loadState();
    toast("Учитель сохранён", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function editCellEntry(id) {
  closeCellPop();
  openScheduleEntry(id);
}

/* ── Окно «Время уроков» ──────────────────────────────────── */

function lessonsInUse() {
  const today = isoDate(new Date());
  return new Set(schoolSchedule().filter((e) => e.lesson && !(e.valid_to && e.valid_to < today)).map((e) => e.lesson));
}

function openBells() {
  const school = schoolById(activeSchool);
  if (!school) return toast("Сначала выберите школу", "error");
  bellsDraft = (school.bells || []).map((b) => ({ ...b }));
  $("bellsSchool").textContent = school.name;
  $("bellsSaturday").checked = (school.days || 5) >= 6;
  if (bellsDraft.length) {
    $("bqFirst").value = bellsDraft[0].start;
    $("bqLength").value = clockMinutes(bellsDraft[0].end) - clockMinutes(bellsDraft[0].start);
    if (bellsDraft.length > 1) $("bqPause").value = Math.max(0, clockMinutes(bellsDraft[1].start) - clockMinutes(bellsDraft[0].end));
    $("bqCount").value = bellsDraft.length;
  }
  renderBells();
  openModal("modalBells");
}

function renderBells() {
  const used = lessonsInUse();
  const last = bellsDraft.length - 1;
  $("bellsList").innerHTML = bellsDraft.map((b, i) => `
    <div class="bell-row">
      <b class="bell-n">${i + 1}</b>
      <input type="time" value="${escapeHtml(b.start)}" aria-label="${i + 1}-й урок: начало" onchange="setBell(${i}, 'start', this.value)">
      <span class="bell-dash">–</span>
      <input type="time" value="${escapeHtml(b.end)}" aria-label="${i + 1}-й урок: конец" onchange="setBell(${i}, 'end', this.value)">
      ${i === last && last > 0
        ? `<button type="button" class="icon-btn" onclick="removeBell()"
             title="${used.has(i + 1) ? "На этом уроке стоят занятия" : "Убрать урок"}" aria-label="Убрать ${i + 1}-й урок">${icon("trash", "ic ic-sm")}</button>`
        : "<span></span>"}
    </div>`).join("");
  $("bellsAdd").disabled = bellsDraft.length >= MAX_BELLS;
}

function setBell(index, field, value) {
  bellsDraft[index][field] = value;
}

function addBell() {
  if (bellsDraft.length >= MAX_BELLS) return;
  const last = bellsDraft[bellsDraft.length - 1];
  const prev = bellsDraft[bellsDraft.length - 2];
  const length = last ? clockMinutes(last.end) - clockMinutes(last.start) : 45;
  const pause = last && prev ? clockMinutes(last.start) - clockMinutes(prev.end) : 10;
  const next = makeBells(last ? clockText(clockMinutes(last.end) + Math.max(0, pause)) : "08:00", length > 0 ? length : 45, 0, 1)[0];
  if (!next) return toast("Следующий урок уже не помещается в сутки", "error");
  bellsDraft.push(next);
  renderBells();
}

function removeBell() {
  if (bellsDraft.length < 2) return;
  if (lessonsInUse().has(bellsDraft.length)) {
    return toast(`На ${bellsDraft.length}-м уроке стоят занятия — сначала уберите их из таблицы`, "error");
  }
  bellsDraft.pop();
  renderBells();
}

function fillBells() {
  const first = $("bqFirst").value;
  const length = parseInt($("bqLength").value, 10);
  const pause = parseInt($("bqPause").value, 10);
  const count = parseInt($("bqCount").value, 10);
  if (!first) return toast("Укажите начало первого урока", "error");
  if (!(length >= 10 && length <= 180)) return toast("Длина урока — от 10 до 180 минут", "error");
  if (!(pause >= 0 && pause <= 120)) return toast("Перемена — от 0 до 120 минут", "error");
  if (!(count >= 1 && count <= MAX_BELLS)) return toast(`Уроков — от 1 до ${MAX_BELLS}`, "error");
  const busy = Math.max(0, ...lessonsInUse());
  if (count < busy) return toast(`На ${busy}-м уроке стоят занятия — уроков нужно не меньше ${busy}`, "error");
  const bells = makeBells(first, length, pause, count);
  if (bells.length < count) return toast("Столько уроков не помещается до полуночи", "error");
  bellsDraft = bells;
  renderBells();
}

async function saveBells() {
  const error = bellsError(bellsDraft);
  if (error) return toast(error, "error");
  try {
    await api(`/api/schedule/bells/${activeSchool}`, {
      method: "PUT",
      json: { bells: bellsDraft, days: $("bellsSaturday").checked ? 6 : 5 },
    });
    closeModal("modalBells");
    await loadState();
    toast("Время уроков сохранено", "success");
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ── Окно занятия ─────────────────────────────────────────── */

function scheduleTeachers() {
  const school = activeSchool;
  return staffList().filter((u) => u.role === "superadmin" || postsOf(u, school).includes("teacher"));
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
  $("scheduleTitle").textContent = entry ? "Занятие" : "Занятие в другое время";
  const together = entry?.lesson ? cellEntries(entry.weekday, entry.lesson) : [];
  $("scheduleAuthor").textContent = entryAuthor(entry);
  $("scheduleAuthor").hidden = !entryAuthor(entry);
  $("scheduleSub").textContent = together.length > 1
    ? `Объединённый урок: ${together.map((e) => classById(e.class_id)?.name || "").join(", ")}. Кабинет и учитель у классов общие.`
    : entry ? "Изменения увидят ученики класса в своём кабинете."
    : "Для уроков не по звонкам. Если время совпадёт с уроком школы, занятие встанет в таблицу.";
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
