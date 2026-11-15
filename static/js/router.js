// Hash routing. Each screen module registers its routes when screens.js imports it:
//
//   register("/p/:id", { label: "Place", render });
//   register("/past", { label: "Past places", nav: 40, render });
//
// `nav` puts the screen in the header's navigation, ordered by the number (Tonight 10, Inbox 20, Lists 30,
// Past places 40, Settings 50); leave it out for screens reached by links. `render(ctx)` draws the screen into
// ctx.view and may be async:
//
//   ctx = { view, params, query, path, alive, rerender }
//
// `view` is a fresh element for this visit, `params` holds the pattern's :names, `query` the ?a=b part of the hash,
// `alive()` turns false once the person has moved on (check it after every await), and `rerender()` draws the same
// address again. A render that throws shows the error with a "Try again" button. No top-level DOM access, so Node can
// import this file (tests/frontend.test.mjs).
import { ApiError } from "./api.js";
import { esc } from "./core.js";

const routes = [];

export function register(pattern, { label, nav = 0, render } = {}) {
  if (typeof pattern !== "string" || !pattern.startsWith("/")) throw new Error(`A route pattern must start with "/": ${pattern}`);
  if (typeof render !== "function") throw new Error(`Route ${pattern} needs a render function`);
  if (routes.some(r => r.pattern === pattern)) throw new Error(`Route ${pattern} is already registered`);
  routes.push({ pattern, label: label ?? "", nav: Number(nav) || 0, render });
}

// "#/p/12/visits?person=3" -> { path: "/p/12/visits", query: { person: "3" } }. A trailing "/" is dropped.
export function parseHash(hash) {
  const [rawPath, qs = ""] = String(hash ?? "").replace(/^#/, "").split("?");
  const parts = rawPath.split("/").filter(Boolean);
  const query = {};
  for (const [k, v] of new URLSearchParams(qs)) query[k] = v;
  return { path: `/${parts.join("/")}`, query };
}

const segments = path => path.split("/").filter(Boolean);

// The params of `path` under `pattern` ("/p/:id" and "/p/12" -> { id: "12" }), or null when it doesn't match.
export function match(pattern, path) {
  const want = segments(pattern);
  const got = segments(path);
  if (want.length !== got.length) return null;
  const params = {};
  for (let i = 0; i < want.length; i++) {
    if (want[i].startsWith(":")) {
      try {
        params[want[i].slice(1)] = decodeURIComponent(got[i]);
      } catch {
        return null;
      }
    } else if (want[i] !== got[i]) {
      return null;
    }
  }
  return params;
}

// The first registered route matching `path`, as { route, params }, or null.
export function routeFor(path) {
  for (const route of routes) {
    const params = match(route.pattern, path);
    if (params) return { route, params };
  }
  return null;
}

// The screens that belong in the navigation, in their order.
export function navRoutes() {
  return routes.filter(r => r.nav > 0 && !r.pattern.includes(":")).sort((a, b) => a.nav - b.nav);
}

const renderNav = list => list.map(renderNavLink).join("");
const renderNavLink = r => `<a href="#${esc(r.pattern)}" data-path="${esc(r.pattern)}">${esc(r.label)}</a>`;
const renderNotFound = () => `<div class="empty"><p>There's nothing at this address.</p><p><a class="btn" href="#/">Go to the start</a></p></div>`;
const renderFailure = message => `<div class="notice error" role="alert"><p>${esc(message)}</p><p><button type="button" data-act="retry">Try again</button></p></div>`;

let visit = 0;

async function show(el, navEl) {
  const mine = ++visit;
  const { path, query } = parseHash(location.hash);
  const view = document.createElement("div");
  view.className = "screen";
  el.replaceChildren(view);
  for (const a of navEl?.querySelectorAll("a[data-path]") ?? []) {
    const on = a.dataset.path === path;
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  window.scrollTo(0, 0);
  const found = routeFor(path);
  const ctx = { view, params: found?.params ?? {}, query, path, alive: () => visit === mine && view.isConnected, rerender: () => show(el, navEl) };
  view.addEventListener("click", e => {
    if (e.target.closest?.("[data-act]")?.dataset.act === "retry") ctx.rerender();
  });
  if (!found) {
    view.innerHTML = renderNotFound();
    return;
  }
  try {
    await found.route.render(ctx);
  } catch (e) {
    if (!ctx.alive()) return;
    view.innerHTML = renderFailure(e instanceof ApiError ? e.error : "Something went wrong. Reload the page.");
    if (!(e instanceof ApiError)) console.error(e);
  }
}

// Draw the navigation into navEl, then the current address into el, and again on every hash change.
export function start(el, navEl = null) {
  if (navEl) navEl.innerHTML = renderNav(navRoutes());
  window.addEventListener("hashchange", () => show(el, navEl));
  return show(el, navEl);
}
