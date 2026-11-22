(() => {
  if (!/^https?:$/.test(location.protocol)) return;
  const installButton = document.getElementById("pwa-install-btn");
  const logoutButton = document.getElementById("pwa-logout-btn");
  const connection = document.getElementById("pwa-connection");
  let installPrompt;
  let probing = false;
  function showConnection(connected) {
    if (!connection) return;
    connection.hidden = connected;
    if (!connected) connection.textContent = "连接已断开，后台操作暂不可用";
  }
  async function probe() {
    if (probing) return;
    if (!navigator.onLine) return showConnection(false);
    probing = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch("/health", {cache: "no-store", signal: controller.signal});
      showConnection(response.ok);
    } catch (_) {
      showConnection(false);
    } finally {
      clearTimeout(timer);
      probing = false;
    }
  }
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  }
  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    installPrompt = event;
    if (installButton) installButton.hidden = false;
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    if (installButton) installButton.hidden = true;
  });
  installButton?.addEventListener("click", async () => {
    if (!installPrompt) return;
    const prompt = installPrompt;
    installPrompt = null;
    installButton.hidden = true;
    await prompt.prompt();
    await prompt.userChoice;
  });
  if (document.body.dataset.accessProtected === "true") {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (response.status === 401 &&
          new URL(response.url).origin === location.origin &&
          response.headers.get("Content-Type")?.includes("application/json")) {
        const payload = await response.clone().json().catch(() => ({}));
        if (payload.login === "/login") location.replace("/login");
      }
      return response;
    };
    if (logoutButton) logoutButton.hidden = false;
    logoutButton?.addEventListener("click", async () => {
      logoutButton.disabled = true;
      try {
        const response = await fetch("/logout", {method: "POST"});
        if (response.ok) {
          sessionStorage.clear();
          location.replace("/login");
        } else {
          logoutButton.disabled = false;
        }
      } catch (_) {
        logoutButton.disabled = false;
        showConnection(false);
      }
    });
  }
  window.addEventListener("offline", () => showConnection(false));
  window.addEventListener("online", probe);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) probe();
  });
  setInterval(() => { if (!document.hidden) probe(); }, 30000);
  showConnection(navigator.onLine);
  probe();
})();
