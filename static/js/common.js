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

/** Всплывающее уведомление. Страница задаёт, куда оно уезжает, исчезая:
 *  в кабинете на телефоне — вниз, в панели учителя — вправо. */
function makeToast({ exit, duration }) {
  return function toast(message, kind = "", iconName = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`.trim();
    if (iconName) el.innerHTML = icon(iconName);
    el.appendChild(document.createTextNode(message));   // текст — никогда не как HTML
    $("toastHost").appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .3s, transform .3s";
      el.style.opacity = "0";
      el.style.transform = exit;
      setTimeout(() => el.remove(), 300);
    }, duration);
  };
}

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
