import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/gowun-dodum/400.css";
import "@fontsource/jua/400.css";
import "@fontsource/dongle/400.css";
import "@fontsource/dongle/700.css";
import { App } from "./App";
import { applyTheme, cachedTheme } from "./theme";
import "./styles.css";

applyTheme(cachedTheme()); // last theme of this browser until the profile arrives (no flash of other colors)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Windows desktop web app (installed PWA): the service worker caches build assets and routes clicks on
// desktop notifications to the open window. Development builds do not register it.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
  });
}
