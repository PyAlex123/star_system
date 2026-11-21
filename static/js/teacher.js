/* Панель учителя: школы, классы, журнал звёзд, карточки с QR, витрина призов.
   Роли: суперадмин видит всё и управляет школами, учителями и витриной;
   учитель — только свои школы. Сервер проверяет права сам, панель лишь прячет
   то, что пользователю всё равно недоступно. */

const MAX_LESSON_BANK = 10;
const MAX_STUDENT_STARS = 5;
const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

let state = { schools: [], classes: [], students: [], prizes: [], lessons: [] };
let activeSchool = null;   // id школы
let activeClass = null;    // id класса
let draft = {};            // { student_id: звёзды за текущий урок }

let deductTarget = null;
let accessTarget = null;   // ученик, открытый в окне «Данные для входа»
let showPasswords = false; // пароли в журнале скрыты: экран учителя часто выводят на доску
let schoolTarget = null;   // null = создаём новую школу
let prizeTarget = null;    // null = создаём новый приз
let photoTarget = null;

let userTarget = null;     // null = создаём нового учителя
let pickedClasses = new Set();

let prizePhotoFile = null;
let prizePhotoCleared = false;
let prizePhotoPreviewUrl = null;

/* ── Помощники ────────────────────────────────────────────── */

// В панели уведомление уезжает вправо, к краю экрана
const toast = makeToast({ exit: "translateX(12px)", duration: 3000 });

/** Запрос от имени учителя: сессия в cookie. Истекла — на страницу входа. */
async function api(path, options = {}) {
  try {
    return await requestJson(path, options, "Не удалось выполнить действие");
  } catch (error) {
    if (error.status === 401) {
      location.href = BASE + "/login";
      throw new Error("Сессия истекла");
    }
    throw error;
  }
}

function openModal(id) { $(id).classList.add("open"); }
function closeModal(id) { $(id).classList.remove("open"); }

function confirmAction(title, text, onConfirm) {
  $("confirmTitle").textContent = title;
  $("confirmText").textContent = text;
  const button = $("confirmBtn");
  const fresh = button.cloneNode(true);   // снимаем прошлые обработчики
  button.replaceWith(fresh);
  fresh.onclick = async () => {
    closeModal("modalConfirm");
    try {
      await onConfirm();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  openModal("modalConfirm");
}

function avatarUrl(student) {
  const avatar = student.avatar || {};
  if (avatar.type === "preset") return `${BASE}/static/img/avatars/${avatar.value}.svg`;
  if (avatar.value) return assetUrl(avatar.value);
  return `${BASE}/static/img/avatars/fox.svg`;
}

/* ── Школы и классы ───────────────────────────────────────── */

function schoolById(id) { return state.schools.find((s) => s.id === id) || null; }
function classById(id) { return state.classes.find((c) => c.id === id) || null; }
function schoolClasses(schoolId) { return state.classes.filter((c) => c.school_id === schoolId); }
function classStudents() { return state.students.filter((s) => s.class_id === activeClass); }

async function loadState() {
  state = await api("/api/state");

  if (!schoolById(activeSchool)) {
    activeSchool = state.schools[0]?.id ?? null;
  }
  const classes = schoolClasses(activeSchool);
  if (!classes.some((c) => c.id === activeClass)) {
    activeClass = classes[0]?.id ?? null;
    draft = {};
  }
  renderAll();
}

function isAdmin() { return state.me?.role === "superadmin"; }

/** Кнопки «.admin-only» видит только администратор, «.teacher-only» — только учитель. */
function applyRole() {
  document.body.classList.toggle("is-admin", isAdmin());
  document.body.classList.toggle("is-teacher", !isAdmin());
  const me = state.me;
  // «Иванова М. С. · учитель»; если имя и есть роль («Учитель») — без повтора
  const role = isAdmin() ? "администратор" : "учитель";
  $("brandSub").textContent = !me ? ""
    : me.name.trim().toLowerCase() === role ? me.name : `${me.name} · ${role}`;
  if (!isAdmin() && document.querySelector(".page.active")?.id === "page-users") switchPage("journal");
}

function renderAll() {
  applyRole();
  renderSchoolSelect();
  renderClassSelect();
  renderTable();
  renderPrizes();
  renderLessons();
  if (isAdmin()) renderUsers();
}

function renderSchoolSelect() {
  $("schoolSelect").innerHTML = state.schools.map((s) => {
    const count = schoolClasses(s.id).length;
    return `<option value="${s.id}" ${s.id === activeSchool ? "selected" : ""}>` +
           `${escapeHtml(s.name)} — ${count} ${plural(count, "класс", "класса", "классов")}</option>`;
  }).join("");
  $("pasteSchoolName").textContent = schoolById(activeSchool)?.name || "—";
}

function selectSchool(id) {
  activeSchool = id;
  activeClass = schoolClasses(id)[0]?.id ?? null;
  draft = {};
  renderAll();
}

const ADD_CLASSES = "__add";

function renderClassSelect() {
  const classes = schoolClasses(activeSchool)
    .slice().sort((a, b) => a.name.localeCompare(b.name, "ru", { numeric: true }));
  const options = classes.map((c) => {
    const count = state.students.filter((s) => s.class_id === c.id).length;
    return `<option value="${c.id}" ${c.id === activeClass ? "selected" : ""}>` +
           `${escapeHtml(c.name)} — ${count} ${plural(count, "ученик", "ученика", "учеников")}</option>`;
  });
  if (!classes.length) options.push(`<option value="" selected disabled>классов пока нет</option>`);
  if (activeSchool) options.push(`<option value="${ADD_CLASSES}">＋ Добавить классы…</option>`);
  $("classSelect").innerHTML = options.join("");
  $("classSelect").disabled = !activeSchool;
}

function onClassSelect(value) {
  if (value === ADD_CLASSES) {
    renderClassSelect();          // вернуть в списке выбранный класс
    openClassGrid();
    return;
  }
  selectClass(parseInt(value));
}

/** «5-А», «5а», «5 А» — один и тот же класс. */
function classKey(name) { return String(name).toLowerCase().replace(/[\s-]/g, ""); }

const GRID_LETTERS = ["А", "Б", "В", "Г", "Д"];

function openClassGrid() {
  if (!activeSchool) return toast("Сначала выберите школу", "error");
  const existing = new Set(schoolClasses(activeSchool).map((c) => classKey(c.name)));
  pickedClasses = new Set();
  $("classGridSchool").textContent = schoolById(activeSchool)?.name || "—";
  $("classOther").value = "";

  let html = `<span></span>` + GRID_LETTERS.map((l) => `<span class="grid-head">${l}</span>`).join("");
  for (let grade = 1; grade <= 11; grade++) {
    html += `<span class="grid-head">${grade}</span>`;
    for (const letter of GRID_LETTERS) {
      const name = `${grade}-${letter}`;
      const has = existing.has(classKey(name));
      html += `<button type="button" class="grid-cell ${has ? "exists" : ""}" ${has ? "disabled" : ""}
                       data-name="${name}" onclick="toggleGridClass(this)"
                       title="${has ? "Уже есть" : name}">${name}</button>`;
    }
  }
  $("classGrid").innerHTML = html;
  updateClassGridButton();
  openModal("modalClasses");
}

function toggleGridClass(button) {
  const name = button.dataset.name;
  if (pickedClasses.has(name)) pickedClasses.delete(name);
  else pickedClasses.add(name);
  button.classList.toggle("picked", pickedClasses.has(name));
  updateClassGridButton();
}

function classGridNames() {
  const other = $("classOther").value.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
  return [...pickedClasses, ...other];
}

function updateClassGridButton() {
  const count = classGridNames().length;
  const button = $("classGridSubmit");
  button.disabled = count === 0;
  button.textContent = count
    ? `Создать ${count} ${plural(count, "класс", "класса", "классов")}`
    : "Создать";
}

async function submitClassGrid() {
  const names = classGridNames();
  if (!names.length) return;
  try {
    const result = await api("/api/classes/bulk", {
      method: "POST", json: { school_id: activeSchool, names },
    });
    closeModal("modalClasses");
    if (result.created.length) {
      activeClass = result.created[0].id;
      draft = {};
    }
    await loadState();
    const made = result.created.length;
    toast(made
      ? `${plural(made, "Создан", "Создано", "Создано")} ${made} ${plural(made, "класс", "класса", "классов")}`
      : "Такие классы уже есть", made ? "success" : "", made ? "check" : "");
  } catch (error) {
    toast(error.message, "error");
  }
}

function selectClass(id) {
  activeClass = id;
  draft = {};
  renderAll();
}

function openSchool(id = null) {
  schoolTarget = id;
  const school = id ? schoolById(id) : null;
  $("schoolModalTitle").textContent = school ? "Переименовать школу" : "Новая школа";
  $("schoolName").value = school?.name || "";
  openModal("modalSchool");
  setTimeout(() => $("schoolName").focus(), 50);
}

async function saveSchool() {
  const name = $("schoolName").value.trim();
  if (!name) return toast("Введите название школы", "error");

  try {
    if (schoolTarget) {
      await api(`/api/schools/${schoolTarget}`, { method: "PATCH", json: { name } });
    } else {
      const created = await api("/api/schools", { method: "POST", json: { name } });
      activeSchool = created.school.id;
      activeClass = null;
    }
    closeModal("modalSchool");
    await loadState();
    toast(schoolTarget ? "Название школы изменено" : "Школа добавлена", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function removeSchool() {
  const school = schoolById(activeSchool);
  if (!school) return;
  if (state.schools.length === 1) {
    return toast("Нельзя удалить единственную школу", "error");
  }
  const classes = schoolClasses(activeSchool).length;
  const kids = state.students.filter(
    (s) => schoolClasses(activeSchool).some((c) => c.id === s.class_id)).length;

  confirmAction("Удалить школу?",
    `«${school.name}» будет удалена вместе с классами (${classes}) и учениками (${kids}). ` +
    "Звёзды и история тоже пропадут.",
    async () => {
      await api(`/api/schools/${activeSchool}`, { method: "DELETE" });
      activeSchool = null;
      activeClass = null;
      await loadState();
      toast("Школа удалена", "success");
    });
}

function renameClass() {
  const cls = classById(activeClass);
  if (!cls) return toast("Сначала выберите класс", "error");
  $("classNameInput").value = cls.name;
  openModal("modalClass");
  setTimeout(() => $("classNameInput").focus(), 50);
}

async function saveClassName() {
  const name = $("classNameInput").value.trim();
  if (!name) return toast("Введите название класса", "error");
  try {
    await api(`/api/classes/${activeClass}`, { method: "PATCH", json: { name } });
    closeModal("modalClass");
    await loadState();
    toast("Класс переименован", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function removeClass() {
  const cls = classById(activeClass);
  if (!cls) return;
  const kids = classStudents().length;
  confirmAction("Удалить класс?",
    `Класс «${cls.name}» и его ученики (${kids}) будут удалены без возможности восстановления.`,
    async () => {
      await api(`/api/classes/${activeClass}`, { method: "DELETE" });
      activeClass = null;
      await loadState();
      toast("Класс удалён", "success");
    });
}

/* ── Банк урока и таблица ─────────────────────────────────── */

/** Сегодня по часам компьютера учителя. toISOString() дал бы дату по Гринвичу:
 *  в Ташкенте до пяти утра это ещё вчера. */
function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function lessonDay() { return $("lessonDate").value || localToday(); }

/* Лимиты действуют на дату, а не на одно нажатие «Завершить урок»: классу — 10 звёзд
   за день, ученику — 5. Решает сервер, а панель показывает остаток, чтобы учитель
   не упирался в ошибку. Правило подсчёта то же, что в backend/routers/lessons.py. */
function classGotOn(day) {
  return (state.lessons || [])
    .filter((l) => l.class_id === activeClass && l.date === day)
    .reduce((sum, l) => sum + (l.total_stars || 0), 0);
}

function studentGotOn(student, day) {
  const reason = `Урок (${day})`;
  return (student.history || [])
    .filter((h) => h.date === day && h.reason === reason && h.delta > 0)
    .reduce((sum, h) => sum + h.delta, 0);
}

function usedBank() {
  return classStudents().reduce((sum, s) => sum + (draft[s.id] || 0), 0);
}

function bankLeft() {
  return Math.max(0, MAX_LESSON_BANK - classGotOn(lessonDay()) - usedBank());
}

function renderBank() {
  const given = classGotOn(lessonDay());
  const left = bankLeft();
  $("bankText").textContent = given
    ? `Банк дня: ${left} (уже начислено ${given})`
    : `Банк дня: ${left}`;
  $("bank").title = `За одну дату класс получает не больше ${MAX_LESSON_BANK} звёзд, ` +
                    `ученик — не больше ${MAX_STUDENT_STARS}, сколько бы уроков ни завершили`;
  $("bank").className = "bank" + (left === 0 ? " empty" : "");
  $("bankDots").innerHTML = Array.from({ length: MAX_LESSON_BANK }, (_, i) => {
    const cls = i < given ? "given" : i < given + usedBank() ? "" : "on";
    return `<span class="bank-dot ${cls}"></span>`;
  }).join("");
}

/** Выбрали другую дату — у неё свой банк. Черновик подрезаем под новые остатки. */
function onLessonDateChange() {
  const day = lessonDay();
  let room = MAX_LESSON_BANK - classGotOn(day);
  for (const student of classStudents()) {
    const cap = Math.max(0, MAX_STUDENT_STARS - studentGotOn(student, day));
    const value = Math.max(0, Math.min(draft[student.id] || 0, cap, room));
    draft[student.id] = value;
    room -= value;
  }
  renderTable();
}

/** Пароль в строке журнала: скрыт точками. Открыть можно все сразу кнопкой
 *  «Показать пароли» или один — глазом в строке. Если ребёнок сменил пароль сам,
 *  его не знает никто, кроме него. */
let flippedPasswords = new Set();   // строки, где глаз нажат вопреки общей кнопке

function passwordVisible(student) {
  return showPasswords !== flippedPasswords.has(student.id);
}

function passwordCell(student) {
  if (!student.password_shown) return `<i>свой пароль</i>`;
  const visible = passwordVisible(student);
  const text = visible
    ? `<b>${escapeHtml(student.password_shown)}</b>`
    : `<b class="masked">••••••</b>`;
  return `${text}<button class="cred-eye" type="button"
            title="${visible ? "Скрыть пароль" : "Показать пароль"}"
            aria-label="${visible ? "Скрыть пароль" : "Показать пароль"}"
            onclick="togglePassword(${student.id})">${icon(visible ? "eye-off" : "eye")}</button>`;
}

function togglePassword(studentId) {
  if (flippedPasswords.has(studentId)) flippedPasswords.delete(studentId);
  else flippedPasswords.add(studentId);
  renderTable();
}

function togglePasswords() {
  showPasswords = !showPasswords;
  flippedPasswords = new Set();
  const button = $("passwordsToggle");
  button.querySelector("span").textContent = showPasswords ? "Скрыть пароли" : "Показать пароли";
  button.querySelector("use").setAttribute("href", showPasswords ? "#i-eye-off" : "#i-eye");
  renderTable();
}

function renderTable() {
  renderBank();
  const host = $("tableHost");

  if (!activeSchool) {
    host.innerHTML = isAdmin()
      ? `<div class="empty-note">Добавьте первую школу — кнопка с плюсом вверху.</div>`
      : `<div class="empty-note">Вам пока не открыта ни одна школа.<br>Обратитесь к администратору.</div>`;
    return;
  }
  if (!activeClass) {
    host.innerHTML = `<div class="empty-note">
      В школе «${escapeHtml(schoolById(activeSchool)?.name || "")}» пока нет классов.<br>
      <button class="btn btn-sm" style="margin-top:10px;" onclick="openClassGrid()">
        ${icon("plus", "ic ic-sm")} Добавить классы</button>
    </div>`;
    return;
  }

  const list = classStudents();
  if (!list.length) {
    host.innerHTML = `<div class="empty-note">
      В классе <b>${escapeHtml(classById(activeClass)?.name || "")}</b> пока нет учеников.<br>
      Добавьте их ниже или вставьте список из Excel.
    </div>`;
    return;
  }

  const day = lessonDay();
  const rows = list.map((student, index) => {
    const current = draft[student.id] || 0;
    // Звёзды показывают весь день: сначала уже начисленные раньше (их не изменить),
    // потом звёзды этого урока.
    const got = Math.min(MAX_STUDENT_STARS, studentGotOn(student, day));
    const starButtons = Array.from({ length: MAX_STUDENT_STARS }, (_, i) => {
      const value = i + 1;
      if (value <= got) {
        return `<button class="star-btn given" disabled
                        title="Уже начислено за ${escapeHtml(day)}">${icon("star")}</button>`;
      }
      const lessonValue = value - got;
      return `<button class="star-btn ${lessonValue <= current ? "on" : ""}"
                      onclick="setDraft(${student.id}, ${lessonValue === current ? lessonValue - 1 : lessonValue})"
                      aria-label="${lessonValue} звёзд">${icon("star")}</button>`;
    }).join("");

    const linked = student.tg_id
      ? `<span class="student-meta linked">${icon("check")} в Telegram</span>`
      : `<span class="student-meta">${icon("minus-circle")} ждёт входа</span>`;

    return `
      <tr>
        <td class="col-num">${index + 1}</td>
        <td>
          <div class="student-cell">
            <div class="avatar-sm"><img src="${escapeHtml(avatarUrl(student))}" alt=""></div>
            <div>
              <div class="student-name">${escapeHtml(student.name)}</div>
              ${linked}
            </div>
          </div>
        </td>
        <td>
          <div class="stars-cell">
            ${starButtons}
            <input type="number" class="star-num" min="0" max="${MAX_STUDENT_STARS - got}" value="${current}"
                   title="${got ? `За этот урок. Раньше за ${escapeHtml(day)} уже ${got}` : "За этот урок"}"
                   onchange="setDraft(${student.id}, parseInt(this.value) || 0)">
          </div>
        </td>
        <td><span class="balance-badge">${icon("star")} ${student.total_stars + current}</span></td>
        <td>
          <span class="code-chip" title="Код карточки: для QR-кода и бота. Это не пароль">${escapeHtml(student.token || "—")}</span>
          <div class="cred-line" title="Логин для входа через сайт">
            <span>логин</span><b>${escapeHtml(student.login || "—")}</b>
          </div>
          <div class="cred-line" title="Пароль для входа через сайт">
            <span>пароль</span>${passwordCell(student)}
          </div>
        </td>
        <td>
          <div class="actions">
            <button class="icon-btn" title="Данные для входа: QR, логин, пароль" onclick="showQr(${student.id})">${icon("qr")}</button>
            <button class="icon-btn" title="Посмотреть кабинет глазами ученика"
                    onclick="previewCabinet(${student.id})">${icon("eye")}</button>
            <button class="icon-btn" title="Выдать приз или списать звёзды"
                    onclick="openDeduct(${student.id})">${icon("gift")}</button>
            <button class="icon-btn" title="Новый код: старая карточка перестанет работать"
                    onclick="resetToken(${student.id})">${icon("refresh")}</button>
            <button class="icon-btn" title="Сбросить аватар"
                    onclick="resetAvatar(${student.id})">${icon("eye-off")}</button>
            <button class="icon-btn danger" title="Удалить ученика"
                    onclick="removeStudent(${student.id})">${icon("trash")}</button>
          </div>
        </td>
      </tr>`;
  }).join("");

  host.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="col-num">№</th>
            <th>Ученик</th>
            <th style="width:220px;" title="Серые звёзды уже начислены за эту дату">Звёзды за день (до ${MAX_STUDENT_STARS})</th>
            <th style="width:110px;">Баланс</th>
            <th style="width:215px;">Вход</th>
            <th style="width:230px;">Действия</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function setDraft(studentId, value) {
  const day = lessonDay();
  const student = state.students.find((s) => s.id === studentId);
  const cap = Math.max(0, MAX_STUDENT_STARS - (student ? studentGotOn(student, day) : 0));
  if (value > cap) {
    toast(cap ? `${student.name}: за этот день можно добавить ещё не больше ${cap}`
              : `${student.name} уже получил(а) ${MAX_STUDENT_STARS} звёзд за этот день`, "error");
  }
  value = Math.max(0, Math.min(cap, value));
  const others = classStudents().reduce(
    (sum, s) => (s.id === studentId ? sum : sum + (draft[s.id] || 0)), 0);
  const available = Math.max(0, MAX_LESSON_BANK - classGotOn(day) - others);

  if (value > available) {
    toast(available ? `В банке дня осталось только ${available}`
                    : `Банк дня исчерпан: за ${day} классу уже начислено ${MAX_LESSON_BANK} звёзд`, "error");
    value = available;
  }
  draft[studentId] = value;
  renderTable();
}

/* ── Действия с учениками ─────────────────────────────────── */

async function addStudent() {
  if (!activeClass) return toast("Сначала выберите класс", "error");
  const nameInput = $("newStudentName");
  const starsInput = $("newStudentStars");
  const name = nameInput.value.trim();
  if (!name) return toast("Введите фамилию и имя", "error");

  try {
    await api("/api/students", {
      method: "POST",
      json: { class_id: activeClass, name, total_stars: parseInt(starsInput.value) || 0 },
    });
    nameInput.value = "";
    starsInput.value = "0";
    await loadState();
    toast(`${name} добавлен в журнал`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function removeStudent(id) {
  const student = state.students.find((s) => s.id === id);
  confirmAction("Удалить ученика?",
    `${student?.name || "Ученик"} и вся его история звёзд будут удалены.`,
    async () => {
      await api(`/api/students/${id}`, { method: "DELETE" });
      delete draft[id];
      await loadState();
      toast("Ученик удалён", "success");
    });
}

function resetToken(id) {
  const student = state.students.find((s) => s.id === id);
  confirmAction("Выпустить новый код?",
    `Старая карточка ${student?.name || ""} перестанет работать, а вход через Telegram будет отвязан. ` +
    "Понадобится распечатать карточку заново.",
    async () => {
      await api(`/api/students/${id}/reset-token`, { method: "POST" });
      await loadState();
      toast("Новый код выпущен — распечатайте карточку", "success");
    });
}

async function resetAvatar(id) {
  try {
    await api(`/api/students/${id}/reset-avatar`, { method: "POST" });
    await loadState();
    toast("Аватар сброшен", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Данные для входа ─────────────────────────────────────── */

function renderAccessPassword(student) {
  const box = $("accessPassword");
  if (student?.password_shown) {
    box.textContent = student.password_shown;
    box.classList.remove("hidden-pass");
    $("accessNote").textContent = "Этот пароль напечатан на карточке ученика.";
  } else {
    box.textContent = "ученик сменил пароль сам";
    box.classList.add("hidden-pass");
    $("accessNote").textContent =
      "Посмотреть его нельзя — пароль знает только ребёнок. Забыл? Выдайте новый.";
  }
}

function showQr(id) {
  const student = state.students.find((s) => s.id === id);
  if (!student) return;
  accessTarget = id;

  $("qrName").textContent = student.name;
  $("qrSub").textContent = classById(student.class_id)?.name || "—";
  $("qrCode").textContent = student.token || "—";
  $("qrImage").src = `${BASE}/api/students/${id}/qr.png?size=10&t=${Date.now()}`;
  $("accessSite").textContent = `${location.origin}${BASE}/enter`;
  $("accessLogin").value = student.login || "";
  renderAccessPassword(student);

  openModal("modalQr");
}

async function saveLogin() {
  const login = $("accessLogin").value.trim().toLowerCase();
  if (!login) return toast("Логин не может быть пустым", "error");
  try {
    await api(`/api/students/${accessTarget}`, { method: "PATCH", json: { login } });
    await loadState();
    $("accessLogin").value = login;
    toast("Логин сохранён", "success", "check");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function resetPassword() {
  try {
    const result = await api(`/api/students/${accessTarget}/password`,
                             { method: "POST", json: {} });
    await loadState();
    renderAccessPassword(state.students.find((s) => s.id === accessTarget));
    toast(`Новый пароль: ${result.password}`, "success", "key");
  } catch (error) {
    toast(error.message, "error");
  }
}

function resetTokenFromAccess() {
  // Окно подтверждения в разметке идёт раньше этого, поэтому оказалось бы
  // под ним. Закрываем текущее и отдаём работу обычному сбросу кода.
  const id = accessTarget;
  closeModal("modalQr");
  resetToken(id);
}

function previewCabinet(id) {
  window.open(`${BASE}/app?preview=${id}`, "_blank");
}

function openCards() {
  if (!activeClass) return toast("Сначала выберите класс", "error");
  window.open(`${BASE}/cards/${activeClass}`, "_blank");
}

/* ── Урок ─────────────────────────────────────────────────── */

async function submitLesson() {
  if (!activeClass) return toast("Сначала выберите класс", "error");
  const list = classStudents();
  if (!list.length) return toast("В классе нет учеников", "error");

  const awards = list.map((s) => ({ student_id: s.id, stars: draft[s.id] || 0 }));
  const total = awards.reduce((sum, a) => sum + a.stars, 0);
  const date = lessonDay();

  if (total === 0 && classGotOn(date) >= MAX_LESSON_BANK) {
    return toast(`За ${date} классу уже начислено ${MAX_LESSON_BANK} звёзд — банк дня исчерпан`, "error");
  }

  const send = async () => {
    await api("/api/lessons/complete", {
      method: "POST",
      json: { class_id: activeClass, date, awards },
    });
    draft = {};
    await loadState();
    document.querySelectorAll(".balance-badge").forEach((el) => el.classList.add("bumped"));
    toast(`Урок завершён, начислено ${total} ${plural(total, "звезда", "звезды", "звёзд")}`,
          "success", "check");
  };

  if (total === 0) {
    confirmAction("Ни одной звезды?", "Вы не поставили звёзды никому. Всё равно завершить урок?", send);
    return;
  }
  try {
    await send();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderLessons() {
  const host = $("lessonList");
  if (!state.lessons?.length) {
    host.innerHTML = `<div class="empty-note">Пока нет завершённых уроков.</div>`;
    return;
  }
  host.innerHTML = state.lessons.slice(0, 40).map((lesson) => {
    const where = lesson.school_name
      ? `${escapeHtml(lesson.school_name)}, ${escapeHtml(lesson.class_name || "")}`
      : escapeHtml(lesson.class_name || "");
    return `
      <div class="lesson-row">
        <span><b>${escapeHtml(lesson.date)}</b> · ${where}
          <span class="muted">· награждено: ${lesson.students_rewarded}</span></span>
        <span class="lesson-plus">+${lesson.total_stars} ${icon("star")}</span>
      </div>`;
  }).join("");
}

/* ── Списание звёзд ───────────────────────────────────────── */

function openDeduct(id) {
  const student = state.students.find((s) => s.id === id);
  if (!student) return;
  deductTarget = id;

  $("deductName").textContent = student.name;
  $("deductMax").textContent = student.total_stars;
  $("deductAmount").value = Math.min(10, student.total_stars) || 1;
  $("deductReason").value = "";
  $("deductPreset").innerHTML = `<option value="">— вручную или штраф —</option>` +
    state.prizes.map((p) => `<option value="${p.id}">${escapeHtml(p.name)} — ${p.cost}</option>`).join("");

  openModal("modalDeduct");
}

function applyPreset() {
  const id = parseInt($("deductPreset").value);
  const prize = state.prizes.find((p) => p.id === id);
  if (!prize) return;
  $("deductAmount").value = prize.cost;
  $("deductReason").value = `Приз: ${prize.name}`;
}

async function confirmDeduct() {
  const amount = parseInt($("deductAmount").value) || 0;
  const reason = $("deductReason").value.trim() || "Списание учителем";
  if (amount <= 0) return toast("Укажите количество звёзд", "error");

  try {
    await api("/api/students/deduct", {
      method: "POST",
      json: { student_id: deductTarget, amount, reason },
    });
    closeModal("modalDeduct");
    await loadState();
    toast(`Списано ${amount} ${plural(amount, "звезда", "звезды", "звёзд")}`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Призы ────────────────────────────────────────────────── */

function renderPrizes() {
  const grid = $("prizeGrid");
  if (!state.prizes?.length) {
    grid.innerHTML = `<div class="empty-note" style="grid-column:1/-1;">Призов пока нет.</div>`;
    return;
  }

  const blank = `<div class="no-photo">${icon("gift", "ic ic-xl")}</div>`;

  grid.innerHTML = state.prizes.map((prize) => {
    const photo = prize.image
      ? `<img src="${escapeHtml(assetUrl(prize.image))}" alt="" loading="lazy" decoding="async"
              data-blank="${escapeHtml(blank)}" onerror="this.outerHTML = this.dataset.blank">`
      : blank;

    const hidden = prize.active === false;
    return `
      <article class="prize-admin ${hidden ? "hidden-prize" : ""}">
        <div class="prize-admin-photo">
          ${photo}
          ${isAdmin() ? `<div class="photo-overlay">
            <button class="btn btn-sm" onclick="pickPhoto(${prize.id})">
              ${icon("camera", "ic ic-sm")} Фото
            </button>
          </div>` : ""}
        </div>
        <div class="prize-admin-body">
          <div class="prize-admin-name">${escapeHtml(prize.name)}</div>
          <div class="prize-admin-desc">${escapeHtml(prize.desc || "")}</div>
          <div class="prize-admin-foot">
            <span class="tag tag-gold">${icon("star", "ic ic-sm")} ${prize.cost}</span>
            ${isAdmin() ? `<div class="actions">
              <button class="icon-btn" title="Изменить" onclick="openPrize(${prize.id})">${icon("pencil")}</button>
              <button class="icon-btn" title="${hidden ? "Показать детям" : "Скрыть от детей"}"
                      onclick="togglePrize(${prize.id})">${icon(hidden ? "eye-off" : "eye")}</button>
              <button class="icon-btn danger" title="Удалить" onclick="removePrize(${prize.id})">${icon("trash")}</button>
            </div>` : (hidden ? `<span class="muted" style="font-size:12px;">скрыт</span>` : "")}
          </div>
        </div>
      </article>`;
  }).join("");
}

/* Фотография в форме приза: файл выбирают сразу, а отправляем его
   уже после того, как приз создан и у него появился id. */

function renderPrizePhotoPreview(src) {
  const box = $("prizePhotoPreview");
  if (src) {
    box.className = "photo-preview filled";
    box.innerHTML = `<img src="${escapeHtml(src)}" alt="">`;
  } else {
    box.className = "photo-preview";
    box.innerHTML = `${icon("camera", "ic ic-lg")}<br>нет фото`;
  }
}

function releasePrizePhotoPreview() {
  if (prizePhotoPreviewUrl) {
    URL.revokeObjectURL(prizePhotoPreviewUrl);
    prizePhotoPreviewUrl = null;
  }
}

function stagePrizePhoto(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;

  if (file.size > MAX_PHOTO_BYTES) {
    return toast("Файл больше 5 МБ — выберите фото поменьше", "error");
  }

  releasePrizePhotoPreview();
  prizePhotoFile = file;
  prizePhotoCleared = false;
  prizePhotoPreviewUrl = URL.createObjectURL(file);
  renderPrizePhotoPreview(prizePhotoPreviewUrl);
}

function clearPrizePhoto() {
  releasePrizePhotoPreview();
  prizePhotoFile = null;
  prizePhotoCleared = true;
  renderPrizePhotoPreview("");
}

async function sendPrizePhoto(prizeId, file) {
  const form = new FormData();
  form.append("file", file);
  await api(`/api/prizes/${prizeId}/photo`, { method: "POST", body: form });
}

function openPrize(id = null) {
  prizeTarget = id;
  const prize = id ? state.prizes.find((p) => p.id === id) : null;
  $("prizeModalTitle").textContent = prize ? "Изменить приз" : "Новый приз";
  $("prizeName").value = prize?.name || "";
  $("prizeCost").value = prize?.cost || 30;
  $("prizeDesc").value = prize?.desc || "";
  $("prizeCategory").value = prize?.category || "small";

  releasePrizePhotoPreview();
  prizePhotoFile = null;
  prizePhotoCleared = false;
  renderPrizePhotoPreview(assetUrl(prize?.image || ""));

  openModal("modalPrize");
}

async function savePrize() {
  const payload = {
    name: $("prizeName").value.trim(),
    cost: parseInt($("prizeCost").value) || 1,
    desc: $("prizeDesc").value.trim(),
    category: $("prizeCategory").value,
  };
  if (!payload.name) return toast("Введите название приза", "error");

  try {
    let prizeId = prizeTarget;
    if (prizeTarget) {
      await api(`/api/prizes/${prizeTarget}`, { method: "PATCH", json: payload });
    } else {
      const created = await api("/api/prizes", { method: "POST", json: { ...payload, active: true } });
      prizeId = created.prize.id;
    }

    if (prizePhotoFile) {
      toast("Загружаем фотографию…");
      await sendPrizePhoto(prizeId, prizePhotoFile);
    } else if (prizePhotoCleared && prizeTarget) {
      await api(`/api/prizes/${prizeTarget}/photo`, { method: "DELETE" });
    }

    releasePrizePhotoPreview();
    prizePhotoFile = null;
    prizePhotoCleared = false;

    closeModal("modalPrize");
    await loadState();
    toast(prizeTarget ? "Приз обновлён" : "Приз добавлен", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function togglePrize(id) {
  const prize = state.prizes.find((p) => p.id === id);
  try {
    await api(`/api/prizes/${id}`, { method: "PATCH", json: { active: prize.active === false } });
    await loadState();
  } catch (error) {
    toast(error.message, "error");
  }
}

function removePrize(id) {
  const prize = state.prizes.find((p) => p.id === id);
  confirmAction("Удалить приз?", `«${prize?.name}» исчезнет из витрины у всех детей.`, async () => {
    await api(`/api/prizes/${id}`, { method: "DELETE" });
    await loadState();
    toast("Приз удалён", "success");
  });
}

function pickPhoto(id) {
  photoTarget = id;
  $("prizePhotoInput").click();
}

async function uploadPrizePhoto(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || !photoTarget) return;

  if (file.size > MAX_PHOTO_BYTES) {
    return toast("Файл больше 5 МБ — выберите фото поменьше", "error");
  }

  toast("Загружаем фотографию…");
  try {
    await sendPrizePhoto(photoTarget, file);
    await loadState();
    toast("Фотография обновлена", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Импорт и экспорт ─────────────────────────────────────── */

function openPaste() {
  if (!activeSchool) return toast("Сначала добавьте школу", "error");
  $("pasteArea").value = "";
  $("pasteSchoolName").textContent = schoolById(activeSchool)?.name || "—";
  openModal("modalPaste");
}

function parseLines(text) {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const fallbackClass = classById(activeClass)?.name || "5-А";
  const items = [];

  lines.forEach((line, index) => {
    const separator = line.includes("\t") ? "\t" : line.includes(";") ? ";" : ",";
    const cols = line.split(separator).map((c) => c.trim().replace(/^"|"$/g, ""));
    if (!cols.length) return;

    const first = cols[0].toLowerCase();
    if (index === 0 && (first.includes("класс") || first.includes("группа") || first.includes("фио"))) {
      return;   // строка заголовков
    }

    let className = fallbackClass;
    let name = "";
    let stars = 0;

    if (cols.length >= 3) {
      className = cols[0] || className;
      name = cols[1];
      stars = parseInt(cols[2]) || 0;
    } else if (cols.length === 2) {
      if (!isNaN(parseInt(cols[1]))) {
        name = cols[0];
        stars = parseInt(cols[1]) || 0;
      } else {
        className = cols[0] || className;
        name = cols[1];
      }
    } else {
      name = cols[0];
    }

    if (name) {
      items.push({ school_id: activeSchool, class_name: className, name, total_stars: stars });
    }
  });

  return items;
}

async function sendBulk(items, label) {
  if (!items.length) return toast("Не удалось распознать строки", "error");
  const result = await api("/api/students/bulk", { method: "POST", json: items });
  await loadState();
  toast(`${label}: ${result.added}`, "success");
}

async function processPaste() {
  const items = parseLines($("pasteArea").value);
  if (!items.length) return toast("Не удалось распознать строки", "error");
  closeModal("modalPaste");
  try {
    await sendBulk(items, "Добавлено учеников");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Excel ────────────────────────────────────────────────── */

function toggleMenu(id, event) {
  event?.stopPropagation();
  const wrap = $(id);
  const open = !wrap.classList.contains("open");
  closeMenus();
  wrap.classList.toggle("open", open);
  wrap.querySelector("button")?.setAttribute("aria-expanded", String(open));
}

function closeMenus() {
  document.querySelectorAll(".menu-wrap.open").forEach((wrap) => {
    wrap.classList.remove("open");
    wrap.querySelector("button")?.setAttribute("aria-expanded", "false");
  });
}

/** Скачивание файла с сервера: cookie сессии уходит сама, как у обычной ссылки. */
function downloadFile(path) {
  closeMenus();
  const link = document.createElement("a");
  link.href = BASE + path;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function downloadTemplate() { downloadFile("/api/excel/template"); }

function exportExcel() {
  if (!activeSchool) return toast("Сначала выберите школу", "error");
  downloadFile(`/api/excel/export?school_id=${activeSchool}`);
}

async function uploadExcel(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  closeMenus();
  if (!file) return;
  if (!activeSchool) return toast("Сначала выберите школу", "error");
  if (!/\.xlsx$/i.test(file.name)) {
    return toast("Нужен файл Excel .xlsx — скачайте шаблон и заполните его", "error");
  }

  const form = new FormData();
  form.append("file", file);
  const className = encodeURIComponent(classById(activeClass)?.name || "");
  try {
    const result = await api(`/api/excel/import?school_id=${activeSchool}&class_name=${className}`,
                             { method: "POST", body: form });
    await loadState();
    const added = `${plural(result.added, "Добавлен", "Добавлено", "Добавлено")} ` +
                  `${result.added} ${plural(result.added, "ученик", "ученика", "учеников")}`;
    if (result.skipped.length) {
      showInfo("Список загружен не целиком",
        `<p>${added}. Эти строки пропущены — исправьте их в файле и загрузите только их:</p>
         <ul>${result.skipped.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>`);
    } else {
      toast(added, "success", "check");
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

/** Окно с сообщением. html собирается только из экранированных частей. */
function showInfo(title, html) {
  $("infoTitle").textContent = title;
  $("infoBody").innerHTML = html;
  openModal("modalInfo");
}

/* ── Учителя (только администратор) ──────────────────────── */

function renderUsers() {
  const host = $("userList");
  const users = (state.users || []).slice()
    .sort((a, b) => (a.role === b.role ? a.name.localeCompare(b.name, "ru") : a.role === "superadmin" ? -1 : 1));
  const rows = users.map((user) => {
    const admin = user.role === "superadmin";
    const self = user.id === state.me?.id;
    const schools = admin
      ? `<span class="muted">все школы</span>`
      : (user.school_ids || []).map((id) => schoolById(id)?.name).filter(Boolean)
          .map((name) => `<span class="tag">${escapeHtml(name)}</span>`).join(" ")
        || `<span class="muted">нет школ</span>`;
    return `
      <tr>
        <td><div class="student-name">${escapeHtml(user.name)}${self ? ` <span class="muted">(вы)</span>` : ""}</div></td>
        <td><span class="code-chip">${escapeHtml(user.login)}</span></td>
        <td>${admin ? `<span class="tag tag-gold">администратор</span>` : `<span class="tag">учитель</span>`}</td>
        <td><div class="tag-row">${schools}</div></td>
        <td>
          <div class="actions">
            <button class="icon-btn" title="${admin ? "Имя" : "Имя и школы"}" onclick="openUser(${user.id})">${icon("pencil")}</button>
            ${self ? "" : `<button class="icon-btn" title="Выдать новый пароль" onclick="resetUserPassword(${user.id})">${icon("key")}</button>`}
            ${self ? "" : `<button class="icon-btn danger" title="Удалить" onclick="removeUser(${user.id})">${icon("trash")}</button>`}
          </div>
        </td>
      </tr>`;
  }).join("");
  host.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Имя</th><th style="width:170px;">Логин</th><th style="width:140px;">Роль</th>
                   <th>Школы</th><th style="width:140px;">Действия</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function openUser(id = null) {
  userTarget = id;
  const user = id ? (state.users || []).find((u) => u.id === id) : null;
  $("userModalTitle").textContent = !user ? "Новый учитель"
    : user.role === "superadmin" ? "Администратор" : "Учитель";
  $("userName").value = user?.name || "";
  $("userLogin").value = user?.login || "";
  $("userLogin").disabled = Boolean(user);
  $("userPassword").value = "";
  $("userPasswordField").hidden = Boolean(user);
  // Администратор видит все школы — галочки ему не нужны
  $("userSchools").closest(".field").hidden = user?.role === "superadmin";
  const chosen = new Set(user?.school_ids || (activeSchool ? [activeSchool] : []));
  $("userSchools").innerHTML = state.schools.map((s) => `
    <label class="check"><input type="checkbox" value="${s.id}" ${chosen.has(s.id) ? "checked" : ""}>
      ${escapeHtml(s.name)}</label>`).join("") || `<span class="muted">Сначала добавьте школу</span>`;
  openModal("modalUser");
  setTimeout(() => (user ? $("userName") : $("userName")).focus(), 50);
}

async function saveUser() {
  const name = $("userName").value.trim();
  const login = $("userLogin").value.trim().toLowerCase();
  const school_ids = [...$("userSchools").querySelectorAll("input:checked")].map((i) => parseInt(i.value));
  if (!name) return toast("Введите имя учителя", "error");
  try {
    if (userTarget) {
      const target = (state.users || []).find((u) => u.id === userTarget);
      const json = target?.role === "superadmin" ? { name } : { name, school_ids };
      await api(`/api/users/${userTarget}`, { method: "PATCH", json });
      closeModal("modalUser");
      await loadState();
      toast("Сохранено", "success", "check");
    } else {
      if (!login) return toast("Введите логин", "error");
      const result = await api("/api/users", {
        method: "POST", json: { name, login, school_ids, password: $("userPassword").value || null },
      });
      closeModal("modalUser");
      await loadState();
      showCredentials(result.user, result.password);
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

function showCredentials(user, password) {
  showInfo("Данные для входа", `
    <p>Передайте учителю. Пароль больше не покажется — если его забудут, выдайте новый.</p>
    <div class="cred-box">
      <div><span>сайт</span><b>${escapeHtml(location.host + BASE)}/login</b></div>
      <div><span>логин</span><b>${escapeHtml(user.login)}</b></div>
      <div><span>пароль</span><b>${escapeHtml(password)}</b></div>
    </div>`);
}

function resetUserPassword(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user) return;
  confirmAction("Выдать новый пароль?",
    `${user.name}: старый пароль перестанет работать, все входы завершатся.`,
    async () => {
      const result = await api(`/api/users/${id}/password`, { method: "POST", json: {} });
      showCredentials(user, result.password);
    });
}

function removeUser(id) {
  const user = (state.users || []).find((u) => u.id === id);
  if (!user) return;
  confirmAction("Удалить учителя?",
    `${user.name} больше не сможет войти. Классы и ученики останутся.`,
    async () => {
      await api(`/api/users/${id}`, { method: "DELETE" });
      await loadState();
      toast("Учитель удалён", "success");
    });
}

/* ── Свой пароль ──────────────────────────────────────────── */

function openMyPassword() {
  ["myCurrent", "myNew", "myRepeat"].forEach((id) => { $(id).value = ""; });
  openModal("modalMyPassword");
  setTimeout(() => $("myCurrent").focus(), 50);
}

async function saveMyPassword() {
  const current = $("myCurrent").value;
  const fresh = $("myNew").value;
  if (!current || !fresh) return toast("Заполните все поля", "error");
  if (fresh !== $("myRepeat").value) return toast("Новые пароли не совпадают", "error");
  try {
    await api("/api/auth/teacher/password", { method: "POST", json: { current, new: fresh } });
    closeModal("modalMyPassword");
    toast("Пароль изменён", "success", "check");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ── Навигация и старт ────────────────────────────────────── */

function switchPage(name) {
  document.querySelectorAll(".page").forEach((page) => {
    page.classList.toggle("active", page.id === `page-${name}`);
  });
  document.querySelectorAll(".nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === name);
  });
}

async function logout() {
  await fetch(BASE + "/api/auth/teacher/logout", { method: "POST" });
  location.href = BASE + "/login";
}

document.addEventListener("DOMContentLoaded", async () => {
  $("lessonDate").value = localToday();
  $("newStudentName").addEventListener("keydown", (e) => { if (e.key === "Enter") addStudent(); });
  document.addEventListener("click", (e) => { if (!e.target.closest(".menu-wrap")) closeMenus(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });
  $("schoolName").addEventListener("keydown", (e) => { if (e.key === "Enter") saveSchool(); });
  $("classNameInput").addEventListener("keydown", (e) => { if (e.key === "Enter") saveClassName(); });

  try {
    await loadState();
  } catch (error) {
    toast(error.message, "error");
  }
});
