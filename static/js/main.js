// Entry point. screens.js imports every screen module, and each registers its routes; then the router draws the
// navigation and the current address into <main id="view">. The service worker only makes the app installable.
import "./screens.js";
import { start } from "./router.js";

start(document.getElementById("view"), document.getElementById("nav"));

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
