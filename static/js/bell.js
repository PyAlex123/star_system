/* Звонок и «кто сейчас на работе».

   Звонок — на главном экране учителя («Журнал → Сегодня») и в «Моём кабинете». Если идёт урок,
   виджет показывает, сколько осталось до его конца, если перемена — сколько до следующего урока.
   Время уроков берётся у школы (school.bells), свои занятия учителя — из расписания;
   по ним пишется «у вас 5-А, каб. 12».

   «Сотрудники» (суперадмин): под именем — где человек сейчас по расписанию и есть ли у него
   отметка в боте за сегодня (/api/checkin/day). У тех, кто отмечается, уроки берутся из отметок:
   там учтены отмены и замены.

   Часы — устройства, как у плиток «Сегодня». */

/* ── Чистые функции (проверки — tests/test_frontend.py) ────── */

/** Где мы в ленте уроков дня. slots — [{start, end, n}] по времени, sec — секунды от полуночи.
 *  before — уроки ещё не начались (i — первый урок), lesson — идёт урок i, break — перемена
 *  перед уроком i, after — уроки кончились. Звонок с урока (ровно end) — это уже перемена. */
function bellMoment(slots, sec) {
  if (!slots.length) return { kind: "none" };
  for (let i = 0; i < slots.length; i++) {
    const start = clockMinutes(slots[i].start) * 60;
    const end = clockMinutes(slots[i].end) * 60;
    if (sec < start) {
      if (i === 0) return { kind: "before", i, left: start - sec, total: null };
      const prev = clockMinutes(slots[i - 1].end) * 60;
      return { kind: "break", i, left: start - sec, total: start - prev };
    }
    if (sec < end) return { kind: "lesson", i, left: end - sec, total: end - start };
  }
  return { kind: "after", i: slots.length - 1, left: 0, total: null };
}

/** Сколько осталось: меньше часа — «12:34», больше — «1 ч 05 мин». */
function leftText(sec) {
  const s = Math.max(0, Math.ceil(sec));
  if (s >= 3600) {
    const m = Math.ceil(s / 60);
    return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, "0")} мин`;
  }
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** То же словами — для экранного диктора: меняется раз в минуту, а не каждую секунду. */
function leftWords(sec) {
  const m = Math.max(1, Math.ceil(sec / 60));
  if (m < 60) return `${m} ${plural(m, "минута", "минуты", "минут")}`;
  const h = Math.floor(m / 60), rest = m % 60;
  return `${h} ${plural(h, "час", "часа", "часов")}${rest ? ` ${rest} ${plural(rest, "минута", "минуты", "минут")}` : ""}`;
}

/** Где сотрудник сейчас по своим урокам дня. lessons — [{start, end, …}], minutes — от полуночи.
 *  lesson — идёт урок; break — между уроками (lesson — следующий); before — уроки впереди;
 *  done — все прошли (lesson — последний); none — уроков нет. */
function staffMoment(lessons, minutes) {
  const list = lessons.slice().sort((a, b) => clockMinutes(a.start) - clockMinutes(b.start));
  if (!list.length) return { kind: "none" };
  const now = list.find((l) => clockMinutes(l.start) <= minutes && minutes < clockMinutes(l.end));
  if (now) return { kind: "lesson", lesson: now };
  const next = list.find((l) => clockMinutes(l.start) > minutes);
  if (next) return { kind: next === list[0] ? "before" : "break", lesson: next };
  const last = list.reduce((a, b) => (clockMinutes(b.end) > clockMinutes(a.end) ? b : a));
  return { kind: "done", lesson: last };
}

/** Отметка за сегодня по урокам из «Отметок» ({state, checkin_at, late_minutes}). */
function staffMark(planned) {
  if (!planned.length) return null;
  const late = planned.find((l) => l.state === "late");
  if (late) return { cls: "late", text: `Опоздание ${late.late_minutes} мин`, came: true };
  const ok = planned.filter((l) => l.state === "ok");
  if (ok.length) {
    const first = ok.map((l) => l.checkin_at).filter(Boolean).sort()[0];
    return { cls: "ok", text: first ? `Отметка в ${first}` : "Засчитано", came: true };
  }
  if (planned.some((l) => l.state === "review")) return { cls: "review", text: "На проверке", came: true };
  if (planned.some((l) => l.state === "missing")) return { cls: "missing", text: "Нет отметки", came: false };
  if (planned.some((l) => l.state === "now")) return { cls: "", text: "Ждём отметку", came: false };
  return null;
}

/** Занятия одного времени в одной школе — один урок: объединённые классы вместе. */
function groupLessons(items) {
  const groups = new Map();
  for (const item of items) {
    const key = `${item.start}|${item.end}|${item.school_id}`;
    if (!groups.has(key)) groups.set(key, { ...item, classes: [] });
    const group = groups.get(key);
    if (item.class) group.classes.push(item.class);
    if (!group.room && item.room) group.room = item.room;
  }
  return [...groups.values()]
    .map(({ classes, ...rest }) => ({ ...rest, class: combinedName(classes) }))
    .sort((a, b) => clockMinutes(a.start) - clockMinutes(b.start));
}

function overlaps(a, b) {
  return clockMinutes(a.start) < clockMinutes(b.end) && clockMinutes(b.start) < clockMinutes(a.end);
}

/* ── Звонок ───────────────────────────────────────────────── */

function daySeconds(d) { return d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds(); }

/** Свои занятия на сегодня в этой школе. */
function myEntriesOn(schoolId, day) {
  const ids = new Set(sortedClasses(schoolId).map((c) => c.id));
  const weekday = isoWeekday(day);
  return (state.schedule || []).filter((e) => e.user_id === state.me?.id && ids.has(e.class_id)
    && e.weekday === weekday && entryShownOn(e, day));
}

/** Уроки школы по звонкам и свои занятия в другое время (если не пересекаются со звонками). */
function bellSlots(school, mine) {
  const slots = (school.bells || []).map((b, i) => ({ start: b.start, end: b.end, n: i + 1 }));
  for (const e of mine.slice().sort((a, b) => clockMinutes(a.start) - clockMinutes(b.start))) {
    if (e.lesson || slots.some((s) => overlaps(s, e))) continue;
    slots.push({ start: e.start, end: e.end, n: null });
  }
  return slots.sort((a, b) => clockMinutes(a.start) - clockMinutes(b.start));
}

/** «5-А, каб. 12» — свои классы на этом уроке. */
function ownText(entries) {
  const names = combinedName(entries.map((e) => classById(e.class_id)?.name || "").filter(Boolean));
  const room = entries.find((e) => e.room)?.room;
  return names + (room ? `, каб. ${room}` : "");
}

const slotName = (slot) => (slot.n ? `${slot.n}-й урок` : "урок");
const slotUntil = (slot) => (slot.n ? `до ${slot.n}-го урока` : "до урока");

/** Что показать в звонке сейчас; null — виджета нет (выходной, у школы нет уроков). */
function bellView(school, now = new Date()) {
  if (!school) return null;
  const day = isoDate(now);
  const mine = myEntriesOn(school.id, day);
  if (isoWeekday(day) > (school.days || 5) && !mine.length) return null;
  const slots = bellSlots(school, mine);
  const moment = bellMoment(slots, daySeconds(now));
  if (moment.kind === "none") return null;
  const own = (slot) => mine.filter((e) => overlaps(e, slot));
  const ownFrom = (i) => slots.slice(i).find((s) => own(s).length);
  const slot = slots[moment.i];
  const view = { key: `${school.id}|${day}|${moment.kind}|${moment.i}`, kind: moment.kind, meta: [],
                 left: moment.left, done: moment.total ? 1 - moment.left / moment.total : null };

  if (moment.kind === "lesson") {
    view.title = slot.n ? `Идёт ${slot.n}-й урок` : "Идёт урок";
    view.cap = "до конца урока";
    view.meta.push(`${slot.start}–${slot.end}`);
    const next = ownFrom(moment.i + 1);
    if (own(slot).length) view.meta.push(`у вас ${ownText(own(slot))}`);
    else if (next) view.meta.push(`у вас окно, дальше ${slotName(next)} в ${next.start} — ${ownText(own(next))}`);
  } else if (moment.kind === "after") {
    view.title = "Уроки на сегодня закончились";
    view.meta.push(`последний урок — до ${slots[slots.length - 1].end}`);
  } else {
    // Перемена или утро до уроков: считаем до следующего урока
    view.title = moment.kind === "break" ? "Перемена" : "До начала уроков";
    view.cap = slotUntil(slot);
    view.meta.push(`${slotName(slot)} в ${slot.start}`);
    const next = ownFrom(moment.i);
    if (next === slot) view.meta.push(`у вас ${ownText(own(slot))}`);
    else if (next) view.meta.push(`${moment.kind === "before" ? "ваш первый" : "ваш следующий"} — ${slotName(next)} в ${next.start}, ${ownText(own(next))}`);
  }
  return view;
}

/** Нарисовать звонок в host. school — null, если звонок здесь не нужен (журнал на другую дату). */
function renderBell(host, school) {
  if (!host) return;
  const view = bellView(school);
  host.dataset.school = school ? String(school.id) : "";
  host.dataset.key = view ? view.key : "";
  host.hidden = !view;
  if (!view) { host.innerHTML = ""; return; }
  host.innerHTML = `
    <section class="bell ${view.kind}" aria-label="Звонок">
      <span class="bell-ic">${icon("clock")}</span>
      <div class="bell-text">
        <b class="bell-title">${escapeHtml(view.title)}</b>
        ${view.meta.length ? `<span class="bell-meta">${view.meta.map(escapeHtml).join(" · ")}</span>` : ""}
      </div>
      ${view.cap ? `<div class="bell-count">
        <span class="bell-left" aria-hidden="true">${leftText(view.left)}</span>
        <span class="bell-cap" aria-hidden="true">${escapeHtml(view.cap)}</span>
        <span class="sr-only bell-sr">${escapeHtml(`${view.cap}: ${leftWords(view.left)}`)}</span>
      </div>` : ""}
      ${view.done === null ? "" : `<span class="bell-bar" aria-hidden="true"><span style="--done: ${view.done.toFixed(3)}"></span></span>`}
    </section>`;
}

/** Раз в секунду: цифры и полоска; сменился урок или перемена — звонок (и плитки) заново. */
function tickBells() {
  for (const host of [$("journalBell"), $("meBell")]) {
    if (!host || !host.dataset.school || !host.closest(".page.active")) continue;
    const school = schoolById(Number(host.dataset.school));
    const view = bellView(school);
    if ((view ? view.key : "") !== host.dataset.key) {
      if (host.id === "journalBell" && journalView === "home") renderJournalHome();
      else renderBell(host, school);
      continue;
    }
    if (!view || !view.cap) continue;
    const left = host.querySelector(".bell-left");
    if (left) left.textContent = leftText(view.left);
    const sr = host.querySelector(".bell-sr");
    const words = `${view.cap}: ${leftWords(view.left)}`;
    if (sr && sr.textContent !== words) sr.textContent = words;
    const bar = host.querySelector(".bell-bar > span");
    if (bar && view.done !== null) bar.style.setProperty("--done", view.done.toFixed(3));
  }
}

/* ── «Сотрудники»: кто сейчас на работе и где ─────────────── */

let presenceDay = null;           // /api/checkin/day за сегодня: {today, lessons, at}
let usersFilter = "all";          // all — все; work — кто сейчас на работе
const PRESENCE_STALE_MS = 120000;

async function loadPresence() {
  if (!isAdmin()) return;
  try {
    const data = await api("/api/checkin/day");
    presenceDay = { today: data.today, lessons: data.lessons || [], at: Date.now() };
  } catch {
    presenceDay = { today: "", lessons: [], at: Date.now() };   // без отметок — по расписанию
  }
  if (currentPage() === "users") renderPresence();
}

/** Уроки сотрудника сегодня: у отмечающегося — из «Отметок», у остальных — по расписанию. */
function staffToday(user, day) {
  const planned = presenceDay?.today === day ? presenceDay.lessons.filter((l) => l.user_id === user.id) : [];
  if (user.checkin_tracked && planned.length) {
    const live = planned.filter((l) => l.state !== "cancelled");
    return {
      lessons: groupLessons(live.map((l) => ({ start: l.start, end: l.end, school_id: l.school_id,
                                              school: l.school, class: l.class, room: l.room }))),
      planned: live.map((l) => ({ state: l.state, late_minutes: l.late_minutes, checkin_at: l.checkin?.at || null })),
    };
  }
  const weekday = isoWeekday(day);
  const entries = (state.schedule || []).filter((e) => e.user_id === user.id && e.weekday === weekday && entryShownOn(e, day));
  return {
    lessons: groupLessons(entries.map((e) => {
      const cls = classById(e.class_id);
      return { start: e.start, end: e.end, school_id: cls?.school_id ?? null,
               school: schoolById(cls?.school_id)?.name || "", class: cls?.name || "", room: e.room || "" };
    })),
    planned: [],
  };
}

function presenceOf(user, day, minutes) {
  const { lessons, planned } = staffToday(user, day);
  const moment = staffMoment(lessons, minutes);
  const mark = staffMark(planned);
  const atWork = moment.kind === "lesson" || moment.kind === "break" || (moment.kind === "before" && Boolean(mark?.came));
  return { moment, mark, atWork, tracked: planned.length > 0 };
}

function presenceHtml(user, p) {
  const chip = (cls, text) => `<span class="ck-chip ${cls}">${escapeHtml(text)}</span>`;
  const where = (l) => [l.school, l.class, l.room ? `каб. ${l.room}` : ""].filter(Boolean).map(escapeHtml).join(" · ");
  const l = p.moment.lesson;
  const parts = [];
  // Время не разрываем переносом: «до&nbsp;13:16»
  if (p.moment.kind === "lesson") parts.push(chip("now", "На уроке"), `<span class="un-text">${where(l)} · до&nbsp;${l.end}</span>`);
  else if (p.moment.kind === "break") parts.push(chip("", "Перемена"), `<span class="un-text">дальше в&nbsp;${l.start} · ${where(l)}</span>`);
  else if (p.moment.kind === "before") parts.push(chip("", `Начало в ${l.start}`), `<span class="un-text">${where(l)}</span>`);
  else if (p.moment.kind === "done") parts.push(`<span class="un-text muted">Уроки закончились · последний до&nbsp;${l.end}</span>`);
  else if (Object.values(user.posts || {}).flat().includes("teacher")) parts.push(`<span class="un-text muted">Сегодня уроков нет</span>`);
  if (p.mark) parts.push(chip(p.mark.cls, p.mark.text));
  return parts.join("");
}

/** Строки состояния у сотрудников, сводка над списком и фильтр «Сейчас на работе». */
function renderPresence() {
  const host = $("usersNow"), list = $("userList");
  if (!host || !list || !isAdmin()) return;
  const now = new Date();
  const day = isoDate(now), minutes = now.getHours() * 60 + now.getMinutes();
  const count = { lesson: 0, pause: 0, work: 0, tracked: 0, came: 0, missing: 0 };
  const bySchool = new Map();
  for (const user of state.users || []) {
    const p = presenceOf(user, day, minutes);
    const cell = list.querySelector(`.user-now[data-user="${user.id}"]`);
    if (cell) {
      const html = presenceHtml(user, p);
      if (cell.innerHTML !== html) cell.innerHTML = html;
      cell.hidden = !html;
      cell.closest(".user-row").hidden = usersFilter === "work" && !p.atWork;
    }
    if (p.moment.kind === "lesson") count.lesson++;
    if (p.moment.kind === "break") count.pause++;
    if (p.atWork) {
      count.work++;
      const school = p.moment.lesson?.school;
      if (school) bySchool.set(school, (bySchool.get(school) || 0) + 1);
    }
    if (p.tracked) {
      count.tracked++;
      if (p.mark?.came) count.came++;
      if (p.mark?.cls === "missing") count.missing++;
    }
  }
  const empty = $("usersEmpty");
  if (empty) empty.hidden = !(usersFilter === "work" && !count.work);
  const marks = count.tracked
    ? ` · отметки: ${count.came} из ${count.tracked}${count.missing ? `, нет отметки — ${count.missing}` : ""}` : "";
  const schools = bySchool.size > 1
    ? `<span class="un-schools">${[...bySchool].map(([name, n]) => `${escapeHtml(name)} — ${n}`).join(" · ")}</span>` : "";
  host.innerHTML = `
    <p class="un-sum"><span><b>Сейчас ${clockText(minutes)}</b> — на уроках ${count.lesson}, на перемене ${count.pause}${marks}</span>${schools}</p>
    <div class="seg" role="radiogroup" aria-label="Кого показать">
      <button role="radio" aria-checked="${usersFilter === "all"}" onclick="setUsersFilter('all')">Все</button>
      <button role="radio" aria-checked="${usersFilter === "work"}" onclick="setUsersFilter('work')">Сейчас на работе · ${count.work}</button>
    </div>`;
}

function setUsersFilter(value) {
  usersFilter = value;
  renderPresence();
}

/* ── Часы ─────────────────────────────────────────────────── */

let bellTimer = null;
let bellTicks = 0;

/** Один таймер на всё: звонок — раз в секунду, «Сотрудники» — раз в 30 секунд
 *  (отметки с сервера — не чаще раза в 2 минуты). */
function startBellClock() {
  if (bellTimer) return;
  bellTimer = setInterval(() => {
    if (document.hidden) return;
    tickBells();
    bellTicks += 1;
    if (bellTicks % 30 === 0 && currentPage() === "users") {
      if (!presenceDay || Date.now() - presenceDay.at > PRESENCE_STALE_MS) loadPresence();
      else renderPresence();
    }
  }, 1000);
}
