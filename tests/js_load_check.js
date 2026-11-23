// Загружает common.js и скрипты страницы в один контекст, как это делает браузер,
// и проверяет, что все нужные функции объявлены и ни одна не «съедена» комментарием.
// Аргументы: common.js скрипт1 [скрипт2 …] -- имя1 имя2 …
const fs = require("fs"), vm = require("vm");
const [, , common, ...rest] = process.argv;
const split = rest.indexOf("--");
const pages = rest.slice(0, split), names = rest.slice(split + 1);
const stub = new Proxy(function () { return stub; }, { get: () => stub, apply: () => stub });
// window: APP_BASE — пустой подпуть, всё остальное (addEventListener и т. п.) — заглушка
const win = new Proxy({ APP_BASE: "" }, { get: (target, key) => (key in target ? target[key] : stub) });
const ctx = vm.createContext({ window: win, document: stub, location: {}, sessionStorage: stub,
  setInterval: () => 0, setTimeout: () => 0, URLSearchParams, console, fetch: () => Promise.resolve(stub) });
vm.runInContext(fs.readFileSync(common, "utf8"), ctx);
pages.slice(0, -1).forEach((page) => vm.runInContext(fs.readFileSync(page, "utf8"), ctx));
vm.runInContext(fs.readFileSync(pages[pages.length - 1], "utf8") + "\n;globalThis.__probe = (n) => { try { return typeof eval(n); } catch (e) { return 'нет'; } };", ctx);
const missing = names.filter((n) => ctx.__probe(n) !== "function");
console.log(missing.length ? "НЕ НАЙДЕНЫ: " + missing.join(", ") : `все ${names.length} функций на месте`);
process.exit(missing.length ? 1 : 0);
