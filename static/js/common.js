/* Общие помощники кабинета ученика и панели учителя.

   Обычный скрипт без сборки и модулей: подключается раньше app.js и teacher.js
   и объявляет глобальные функции, которыми пользуются оба. Раньше эти функции
   жили в двух копиях и потихоньку расходились. */

// Подпуть, на котором развёрнута система ("" в корне, "/stars" на подпути).
// Значение подставляет сервер при отдаче страницы.
const BASE = window.APP_BASE || "";

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/** SVG-иконка из набора, вшитого в страницу. Вместо эмодзи: они выглядят
 *  по-разному на Android, iOS и в Windows, и ломают единый вид интерфейса. */
function icon(name, cls = "ic") {
  return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

/** Путь к картинке с учётом подпути. Внешние ссылки (фото профиля Telegram)
 *  оставляем как есть. */
function assetUrl(path) {
  if (!path) return "";
  return /^https?:\/\//.test(path) ? path : BASE + path;
}

/** Русские формы: plural(5, "звезда", "звезды", "звёзд") → "звёзд". */
function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

/** Всплывающее уведомление в духе макета: тёмная плашка внизу по центру,
 *  слева круглый значок (звезда, галочка или восклицательный знак). */
function makeToast({ duration = 3200 } = {}) {
  return function toast(message, kind = "", iconName = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`.trim();
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    const mark = iconName || (kind === "error" ? "warning" : kind === "success" ? "check" : "star");
    el.innerHTML = `<span class="toast-mark">${icon(mark)}</span>`;
    el.appendChild(document.createTextNode(message));   // текст — никогда не как HTML
    $("toastHost").appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .25s, transform .25s";
      el.style.opacity = "0";
      el.style.transform = "translateY(8px)";
      setTimeout(() => el.remove(), 260);
    }, duration);
  };
}

/* ── Тема оформления ─────────────────────────────────────────
   Тему рисует сервер (<html data-theme>), здесь — смена без перезагрузки
   и запоминание на устройстве. Cookie только про внешний вид. */

const THEMES = ["notebook", "quest", "sky", "clear", "sticker"];
const THEME_NAMES = {
  notebook: "Тетрадь отличника", quest: "Звёздный квест", sky: "Созвездие",
  clear: "Ясно и быстро", sticker: "Стикербук",
};

function setPrefCookie(name, value) {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=${BASE || "/"}; max-age=31536000; samesite=lax`;
}

function currentTheme() { return document.documentElement.dataset.theme || "notebook"; }

function applyTheme(theme) {
  if (!THEMES.includes(theme)) return;
  document.documentElement.dataset.theme = theme;
  setPrefCookie("zv_theme", theme);
  document.dispatchEvent(new CustomEvent("themechange", { detail: theme }));
}

/** Светлая / тёмная / авто — только у «Ясно и быстро». */
function applyMode(mode) {
  if (!["auto", "light", "dark"].includes(mode)) return;
  document.documentElement.dataset.mode = mode;
  setPrefCookie("zv_mode", mode);
  document.dispatchEvent(new CustomEvent("themechange", { detail: currentTheme() }));
}

/** Значение токена темы на элементе (по умолчанию — на <body>). */
function token(name, el = document.body) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

/** Дата «2026-11-21» → «21 ноября 2026», с днём недели по желанию. */
function formatDay(iso, { weekday = false, year = true } = {}) {
  const [y, m, d] = String(iso).split("-").map(Number);
  if (!y || !m || !d) return String(iso || "");
  const date = new Date(y, m - 1, d);
  const text = date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", ...(year ? { year: "numeric" } : {}) })
    .replace(/\s*г\.$/, "");
  return weekday ? `${text}, ${date.toLocaleDateString("ru-RU", { weekday: "long" })}` : text;
}

/** Число с неразрывным пробелом в тысячах: 1 250. */
function fmt(n) { return Number(n || 0).toLocaleString("ru-RU").replace(/\s/g, "\u00A0"); }

/** «Название» в ёлочках; внутренние кавычки — „лапки“. */
function quoted(name) { return "«" + String(name).replace(/«/g, "„").replace(/»/g, "“") + "»"; }

/** Текст ошибки из ответа сервера. Обычно это строка, но на всякий случай
 *  разбираем и список — иначе на экране оказался бы «[object Object]». */
function errorText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d?.msg).filter(Boolean).join("; ");
  return "";
}

/** JSON-запрос к серверу: собирает тело, разбирает ответ, превращает ошибку
 *  в понятный текст. Вход (токен или cookie) добавляет обёртка api() страницы. */
async function requestJson(path, options = {}, fallbackError = "Что-то пошло не так") {
  const headers = { ...(options.headers || {}) };
  const init = { ...options, headers };
  delete init.json;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  }

  const response = await fetch(BASE + path, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(errorText(payload.detail) || fallbackError);
    error.status = response.status;
    throw error;
  }
  return payload;
}
