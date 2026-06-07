(function () {
  const grid = document.getElementById("screens-grid");
  const status = document.getElementById("demo-status");

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
    return data;
  }

  function stateLabel(screen) {
    if (screen.status === "leased") return "בשימוש";
    if (screen.status === "idle") return "פנוי";
    return "עסוק";
  }

  function render(screens) {
    grid.innerHTML = "";
    screens.forEach((screen) => {
      const card = document.createElement("article");
      card.className = "screen-card";
      card.innerHTML = `
        <span class="state ${screen.status}">${stateLabel(screen)}</span>
        <div>
          <h3>${screen.name}</h3>
          <p>${screen.location_label}</p>
        </div>
        <p>מחלקה: ${screen.department} · ${screen.connected ? "מחובר" : "לא פתוח"}</p>
        <div class="quick-actions">
          <a href="/screen/${screen.id}" target="_blank">פתח</a>
          <button type="button" data-availability="${screen.id}" data-value="${screen.available ? "0" : "1"}">
            ${screen.available ? "סמן עסוק" : "סמן פנוי"}
          </button>
          <button type="button" data-release="${screen.id}">שחרר</button>
        </div>
      `;
      grid.appendChild(card);
    });
    grid.querySelectorAll("[data-availability]").forEach((button) => {
      button.addEventListener("click", async () => {
        await postJson(`/api/screens/${button.dataset.availability}/availability`, { available: button.dataset.value === "1" });
        await loadScreens();
      });
    });
    grid.querySelectorAll("[data-release]").forEach((button) => {
      button.addEventListener("click", async () => {
        await postJson(`/api/screens/${button.dataset.release}/release`);
        await loadScreens();
      });
    });
  }

  async function loadScreens() {
    const data = await fetch("/api/screens").then((response) => response.json());
    render(data.screens || []);
  }

  document.getElementById("reset-demo").addEventListener("click", async () => {
    await postJson("/api/demo/reset");
    status.textContent = "הדמו אופס; רק המסך ליד האינסטלציה פנוי.";
    await loadScreens();
  });

  document.getElementById("preload-sofa").addEventListener("click", async () => {
    const data = await postJson("/api/demo/preload-sofa");
    status.textContent = "תרחיש הספה נטען למסך " + data.screen.location_label;
    await loadScreens();
  });

  document.getElementById("refresh-catalog").addEventListener("click", async () => {
    status.textContent = "מרענן נתוני ספות מ-ACE";
    try {
      const data = await postJson("/api/demo/refresh-catalog");
      status.textContent = `עודכנו ${data.added_or_updated} מוצרים; סה"כ ${data.total}.`;
    } catch (error) {
      status.textContent = "הרענון נכשל; הדמו ממשיך עם המטמון המקומי.";
    }
    await loadScreens();
  });

  document.getElementById("open-screens").addEventListener("click", async () => {
    const data = await fetch("/api/screens").then((response) => response.json());
    (data.screens || []).forEach((screen, index) => {
      setTimeout(() => window.open(`/screen/${screen.id}`, "_blank"), index * 180);
    });
  });

  loadScreens();
  setInterval(loadScreens, 2500);
})();
