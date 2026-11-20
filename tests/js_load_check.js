// Загружает common.js и страницу в один контекст, как это делает браузер,
// и проверяет, что все нужные функции объявлены и ни одна не «съедена» комментарием.
const fs = require("fs"), vm = require("vm");
const [, , common, page, ...names] = process.argv;
const stub = new Proxy(function () { return stub; }, { get: () => stub, apply: () => stub });
const ctx = vm.createContext({ window: { APP_BASE: "" }, document: stub, location: {}, sessionStorage: stub,
  setInterval: () => 0, setTimeout: () => 0, URLSearchParams, console, fetch: () => Promise.resolve(stub) });
vm.runInContext(fs.readFileSync(common, "utf8"), ctx);
vm.runInContext(fs.readFileSync(page, "utf8") + "\n;globalThis.__probe = (n) => { try { return typeof eval(n); } catch (e) { return 'нет'; } };", ctx);
const missing = names.filter((n) => ctx.__probe(n) !== "function");
console.log(missing.length ? "НЕ НАЙДЕНЫ: " + missing.join(", ") : `все ${names.length} функций на месте`);
process.exit(missing.length ? 1 : 0);
