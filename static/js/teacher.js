/* Панель учителя: журнал звёзд, карточки с QR, витрина призов. */

const MAX_LESSON_BANK = 10;
const MAX_STUDENT_STARS = 5;

let state = { classes: [], students: [], prizes: [], lessons: [] };
let activeClass = "";
let draft = {};          // { student_id: звёзды за текущий урок }
let deductTarget = null;
let prizeTarget = null;  // null = создаём новый
let photoTarget = null;

const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
let prizePhotoFile = null;        // выбран в форме, но ещё не отправлен
let prizePhotoCleared = false;    // нажали «убрать фото»
let prizePhotoPreviewUrl = null;  // временная ссылка на локальное превью

const $ = (id) => document.getElementById(id);

// Подпуть, на котором развёрнута система ("" в корне, "/stars" на подпути).
// Значение подставляет сервер при отдаче страницы.
const BASE = window.APP_BASE || "";

/** Путь к картинке с учётом подпути; внешние ссылки не трогаем. */
function assetUrl(path) {
  if (!path) return "";
  return /^https?:\/\//.test(path) ? path : BASE + path;
}

/* ── Помощники ────────────────────────────────────────────── */

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`.trim();
  el.textContent = message;
  $("toastHost").appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity .3s, transform .3s";
    el.style.opacity = "0";
    el.style.transform = "translateX(16px)";
    setTimeout(() => el.remove(), 300);
  }, 3000);
}

async function api(path, options = {}) {
  if (options.json !== undefined) {
    options.headers = { ...(options.headers || {}), "Content-Type": "application/json" };
    options.body = JSON.stringify(options.json);
  }
  const response = await fetch(BASE + path, options);
  if (response.status === 401) {
    location.href = BASE + "/login";
    throw new Error("Сессия истекла");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Не удалось выполнить действие");
  return payload;
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
    await onConfirm();
  };
  openModal("modalConfirm");
}

function avatarUrl(student) {
  const avatar = student.avatar || {};
  if (avatar.type === "preset") return `${BASE}/static/img/avatars/${avatar.value}.svg`;
  if (avatar.value) return assetUrl(avatar.value);
  return `${BASE}/static/img/avatars/fox.svg`;
}

/* ── Загрузка состояния ───────────────────────────────────── */

async function loadState() {
  state = await api("/api/state");
  if (!activeClass || !state.classes.includes(activeClass)) {
    activeClass = state.classes[0] || "";
  }
  renderAll();
}

function renderAll() {
  renderClassTabs();
  renderTable();
  renderPrizes();
  renderLessons();
}

function classStudents() {
  return state.students.filter((s) => s.class_name === activeClass);
}

/* ── Классы и таблица ─────────────────────────────────────── */

function renderClassTabs() {
  $("classTabs").innerHTML = state.classes.map((name) => `
    <button class="class-tab ${name === activeClass ? "active" : ""}"
            onclick="selectClass('${escapeHtml(name).replace(/'/g, "\\'")}')">${escapeHtml(name)}</button>
  `).join("");
}

function selectClass(name) {
  activeClass = name;
  draft = {};
  renderAll();
}

function usedBank() {
  return classStudents().reduce((sum, s) => sum + (draft[s.id] || 0), 0);
}

function renderBank() {
  const left = MAX_LESSON_BANK - usedBank();
  $("bankText").textContent = `Банк урока: ${left} ⭐`;
  $("bank").className = "bank" + (left === 0 ? " empty" : "");
  $("bankDots").innerHTML = Array.from({ length: MAX_LESSON_BANK },
    (_, i) => `<span class="bank-dot ${i < left ? "on" : ""}"></span>`).join("");
}

function renderTable() {
  renderBank();
  const host = $("tableHost");

  if (!activeClass) {
    host.innerHTML = `<div class="empty-note">Создайте первый класс — поле и кнопка «➕ Класс» справа вверху.</div>`;
    return;
  }

  const list = classStudents();
  if (!list.length) {
    host.innerHTML = `<div class="empty-note">
      В классе <b>${escapeHtml(activeClass)}</b> пока нет учеников.<br>
      Добавьте их ниже или вставьте список из Excel.
    </div>`;
    return;
  }

  const rows = list.map((student, index) => {
    const current = draft[student.id] || 0;
    const starButtons = Array.from({ length: MAX_STUDENT_STARS }, (_, i) => {
      const value = i + 1;
      return `<button class="star-btn ${value <= current ? "on" : ""}"
                      onclick="setDraft(${student.id}, ${value === current ? value - 1 : value})"
                      aria-label="${value} звёзд">★</button>`;
    }).join("");

    const linked = student.tg_id
      ? `<span class="dot-linked"></span> в Telegram`
      : `<span class="dot-waiting"></span> ждёт входа`;

    return `
      <tr>
        <td class="muted">${index + 1}</td>
        <td>
          <div class="student-cell">
            <div class="avatar-sm"><img src="${escapeHtml(avatarUrl(student))}" alt=""></div>
            <div>
              <div class="student-name">${escapeHtml(student.name)}</div>
              <div class="student-meta">${linked}</div>
            </div>
          </div>
        </td>
        <td>
          <div class="stars-cell">
            ${starButtons}
            <input type="number" class="star-num" min="0" max="${MAX_STUDENT_STARS}" value="${current}"
                   onchange="setDraft(${student.id}, parseInt(this.value) || 0)">
          </div>
        </td>
        <td><span class="balance-badge">${student.total_stars + current} ⭐</span></td>
        <td><span class="code-chip">${escapeHtml(student.token || "—")}</span></td>
        <td>
          <div class="actions">
            <button class="icon-btn" title="Показать QR-код" onclick="showQr(${student.id})">🔳</button>
            <button class="icon-btn" title="Посмотреть кабинет глазами ученика"
                    onclick="previewCabinet(${student.id})">👀</button>
            <button class="icon-btn" title="Выдать приз / списать звёзды" onclick="openDeduct(${student.id})">🎁</button>
            <button class="icon-btn" title="Новый код (старая карточка перестанет работать)"
                    onclick="resetToken(${student.id})">♻️</button>
            <button class="icon-btn" title="Сбросить аватар" onclick="resetAvatar(${student.id})">🙈</button>
            <button class="icon-btn danger" title="Удалить ученика" onclick="removeStudent(${student.id})">✖</button>
          </div>
        </td>
      </tr>`;
  }).join("");

  host.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:44px;">№</th>
            <th>Ученик</th>
            <th style="width:230px;">Звёзды за урок (0–${MAX_STUDENT_STARS})</th>
            <th style="width:110px;">Баланс</th>
            <th style="width:110px;">Код</th>
            <th style="width:250px;">Действия</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function setDraft(studentId, value) {
  value = Math.max(0, Math.min(MAX_STUDENT_STARS, value));
  const others = classStudents().reduce(
    (sum, s) => (s.id === studentId ? sum : sum + (draft[s.id] || 0)), 0);
  const available = MAX_LESSON_BANK - others;

  if (value > available) {
    toast(`В банке урока осталось только ${available} ⭐`, "error");
    value = available;
  }
  draft[studentId] = value;
  renderTable();
}

/* ── Действия с учениками ─────────────────────────────────── */

async function createClass() {
  const input = $("newClassName");
  const name = input.value.trim();
  if (!name) return toast("Введите название класса", "error");

  await api("/api/classes", { method: "POST", json: { name } });
  input.value = "";
  activeClass = name;
  await loadState();
  toast(`Класс «${name}» создан`, "success");
}

function removeClass() {
  if (!activeClass) return;
  confirmAction("Удалить класс?",
    `Класс «${activeClass}» и все его ученики будут удалены без возможности восстановления.`,
    async () => {
      await api(`/api/classes/${encodeURIComponent(activeClass)}`, { method: "DELETE" });
      activeClass = "";
      await loadState();
      toast("Класс удалён", "success");
    });
}

async function addStudent() {
  if (!activeClass) return toast("Сначала создайте класс", "error");
  const nameInput = $("newStudentName");
  const starsInput = $("newStudentStars");
  const name = nameInput.value.trim();
  if (!name) return toast("Введите фамилию и имя", "error");

  await api("/api/students", {
    method: "POST",
    json: { class_name: activeClass, name, total_stars: parseInt(starsInput.value) || 0 },
  });
  nameInput.value = "";
  starsInput.value = "0";
  await loadState();
  toast(`${name} добавлен в журнал`, "success");
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
  await api(`/api/students/${id}/reset-avatar`, { method: "POST" });
  await loadState();
  toast("Аватар сброшен", "success");
}

function showQr(id) {
  const student = state.students.find((s) => s.id === id);
  if (!student) return;
  $("qrName").textContent = student.name;
  $("qrSub").textContent = `Класс ${student.class_name} · код ${student.token}`;
  $("qrImage").src = `${BASE}/api/students/${id}/qr.png?size=10&t=${Date.now()}`;
  openModal("modalQr");
}

function previewCabinet(id) {
  // Открывает настоящий кабинет ученика в новой вкладке — ровно то, что видит ребёнок
  window.open(`${BASE}/app?preview=${id}`, "_blank");
}

function openCards() {
  if (!activeClass) return toast("Сначала выберите класс", "error");
  window.open(`${BASE}/cards/${encodeURIComponent(activeClass)}`, "_blank");
}

/* ── Урок ─────────────────────────────────────────────────── */

async function submitLesson() {
  const list = classStudents();
  if (!list.length) return toast("В классе нет учеников", "error");

  const awards = list.map((s) => ({ student_id: s.id, stars: draft[s.id] || 0 }));
  const total = awards.reduce((sum, a) => sum + a.stars, 0);

  const send = async () => {
    const date = $("lessonDate").value || new Date().toISOString().split("T")[0];
    await api("/api/lessons/complete", {
      method: "POST",
      json: { class_name: activeClass, date, awards },
    });
    draft = {};
    await loadState();
    document.querySelectorAll(".balance-badge").forEach((el) => el.classList.add("bumped"));
    toast(`Урок завершён, начислено ${total} ⭐`, "success");
  };

  if (total === 0) {
    confirmAction("Ни одной звезды?", "Вы не поставили звёзды никому. Всё равно завершить урок?", send);
    return;
  }
  await send();
}

function renderLessons() {
  const host = $("lessonList");
  if (!state.lessons?.length) {
    host.innerHTML = `<div class="empty-note">Пока нет завершённых уроков.</div>`;
    return;
  }
  host.innerHTML = state.lessons.slice(0, 30).map((lesson) => `
    <div class="lesson-row">
      <span><b>${escapeHtml(lesson.date)}</b> — класс <b>${escapeHtml(lesson.class_name)}</b>
        <span class="muted">· награждено: ${lesson.students_rewarded}</span></span>
      <span class="lesson-plus">+${lesson.total_stars} ⭐</span>
    </div>`).join("");
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
  $("deductPreset").innerHTML = `<option value="">— вручную / штраф —</option>` +
    state.prizes.map((p) => `<option value="${p.id}">${escapeHtml(p.name)} (${p.cost} ⭐)</option>`).join("");

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
    toast(`Списано ${amount} ⭐`, "success");
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

  grid.innerHTML = state.prizes.map((prize) => {
    const photo = prize.image
      ? `<img src="${escapeHtml(assetUrl(prize.image))}" alt=""
              onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'no-photo',textContent:'🎁 нет фото'}))">`
      : `<div class="no-photo">🎁 нет фото</div>`;

    return `
      <article class="prize-admin ${prize.active === false ? "hidden-prize" : ""}">
        <div class="prize-admin-photo">
          ${photo}
          <div class="photo-overlay">
            <button class="btn btn-sm" onclick="pickPhoto(${prize.id})">📷 Фото</button>
          </div>
        </div>
        <div class="prize-admin-body">
          <div class="prize-admin-name">${escapeHtml(prize.name)}</div>
          <div class="prize-admin-desc">${escapeHtml(prize.desc || "")}</div>
          <div class="prize-admin-foot">
            <span class="pill pill-gold">${prize.cost} ⭐</span>
            <div class="actions">
              <button class="icon-btn" title="Изменить" onclick="openPrize(${prize.id})">✏️</button>
              <button class="icon-btn" title="${prize.active === false ? "Показать детям" : "Скрыть от детей"}"
                      onclick="togglePrize(${prize.id})">${prize.active === false ? "🙈" : "👁"}</button>
              <button class="icon-btn danger" title="Удалить" onclick="removePrize(${prize.id})">✖</button>
            </div>
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
    box.innerHTML = "🎁<br>нет фото";
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
  await api(`/api/prizes/${id}`, { method: "PATCH", json: { active: prize.active === false } });
  await loadState();
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
  $("pasteArea").value = "";
  openModal("modalPaste");
}

function parseLines(text) {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const items = [];

  lines.forEach((line, index) => {
    const separator = line.includes("\t") ? "\t" : line.includes(";") ? ";" : ",";
    const cols = line.split(separator).map((c) => c.trim().replace(/^"|"$/g, ""));
    if (!cols.length) return;

    const first = cols[0].toLowerCase();
    if (index === 0 && (first.includes("класс") || first.includes("группа") || first.includes("фио"))) {
      return;   // строка заголовков
    }

    let className = activeClass || "5-А";
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

    if (name) items.push({ class_name: className, name, total_stars: stars });
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
  await sendBulk(items, "Добавлено учеников");
}

function importCsv(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;

  if (/\.(xlsx?|ods)$/i.test(file.name)) {
    toast("Сохраните таблицу как CSV или используйте «Вставить из Excel»", "error");
    return;
  }

  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      await sendBulk(parseLines(e.target.result), "Импортировано учеников");
    } catch (error) {
      toast(error.message, "error");
    }
  };
  reader.readAsText(file, "UTF-8");
}

function exportCsv() {
  let csv = "\uFEFFКласс;ФИО;Всего звёзд;Код карточки;Telegram\n";
  state.students.forEach((s) => {
    csv += `${s.class_name};${s.name};${s.total_stars};${s.token || ""};${s.tg_id ? "привязан" : ""}\n`;
  });

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `stars_report_${$("lessonDate").value || "today"}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
  toast("Файл выгружен", "success");
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
  $("lessonDate").value = new Date().toISOString().split("T")[0];
  $("newStudentName").addEventListener("keydown", (e) => { if (e.key === "Enter") addStudent(); });
  $("newClassName").addEventListener("keydown", (e) => { if (e.key === "Enter") createClass(); });

  try {
    await loadState();
    const health = await fetch(BASE + "/healthz").then((r) => r.json());
    $("brandSub").textContent = health.bot_enabled
      ? `бот @${health.bot_username || "…"} · ${health.base_url}`
      : "бот не настроен — заполните BOT_TOKEN в .env";
  } catch (error) {
    toast(error.message, "error");
  }
});
