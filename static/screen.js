(function () {
  const root = document.getElementById("screen-root");
  const screenId = window.SCREEN_ID;
  const IDLE_ROTATION_MS = 6500;
  let idleShowcase = null;
  let idleShowcaseDepartment = "";
  let idleIndex = 0;
  let lastIdleAdvance = 0;
  let lastIdleRenderKey = "";

  function money(value) {
    const amount = Number(value || 0);
    return new Intl.NumberFormat("he-IL", {
      style: "currency",
      currency: "ILS",
      maximumFractionDigits: amount % 1 ? 2 : 0,
    }).format(amount);
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  function badges(product) {
    const items = [];
    items.push(`<span class="badge good">${escapeHtml(product.availability_label || "זמין אונליין")}</span>`);
    if (product.promo) items.push(`<span class="badge">${escapeHtml(product.promo)}</span>`);
    if (product.discount_percent) items.push(`<span class="badge">${product.discount_percent}% חסכון</span>`);
    return items.join("");
  }

  function specs(product, limit) {
    return Object.entries(product.specs || {}).slice(0, limit).map(([key, value]) =>
      `<span class="badge">${escapeHtml(key)}: ${escapeHtml(value)}</span>`
    ).join("");
  }

  function productNotes(product) {
    const notes = (product.sales_notes || []).slice(0, 2);
    if (!notes.length) return "";
    return `
      <ul class="idle-notes">
        ${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}
      </ul>
    `;
  }

  async function loadIdleShowcase(screen) {
    const department = screen.department || "";
    if (idleShowcase && idleShowcaseDepartment === department) return idleShowcase;
    const params = new URLSearchParams({ department, limit: "6" });
    const response = await fetch("/api/idle-showcase?" + params.toString());
    idleShowcase = await response.json();
    idleShowcaseDepartment = department;
    idleIndex = 0;
    lastIdleAdvance = 0;
    lastIdleRenderKey = "";
    return idleShowcase;
  }

  function renderIdle(screen) {
    root.innerHTML = `
      <div class="screen-id">ACE</div>
      <div class="screen-empty">
        <h1>${screen.available ? "מסך פנוי" : "מסך עסוק"}</h1>
        <p>${escapeHtml(screen.location_label)}</p>
      </div>
    `;
  }

  function renderBusy(screen) {
    lastIdleRenderKey = "";
    root.innerHTML = `
      <div class="screen-id">ACE</div>
      <div class="screen-empty">
        <h1>מסך לא זמין</h1>
        <p>${escapeHtml(screen.location_label)}</p>
      </div>
    `;
  }

  function renderIdleShowcase(screen, showcase) {
    const products = (showcase && showcase.products) || [];
    if (!products.length) {
      renderIdle(screen);
      return;
    }
    const time = Date.now();
    if (!lastIdleAdvance) {
      lastIdleAdvance = time;
    } else if (time - lastIdleAdvance > IDLE_ROTATION_MS) {
      idleIndex = (idleIndex + 1) % products.length;
      lastIdleAdvance = time;
    }
    const hero = products[idleIndex % products.length];
    const others = products.filter((product) => product.sku !== hero.sku).slice(0, 4);
    const renderKey = `${screen.status}:${screen.id}:${hero.sku}:${products.length}`;
    if (renderKey === lastIdleRenderKey) return;
    lastIdleRenderKey = renderKey;
    const websiteUrl = new URL((showcase && showcase.website_path) || "/", window.PUBLIC_BASE_URL || window.location.origin).toString();
    root.innerHTML = `
      <div class="screen-id">ACE</div>
      <header class="screen-top idle-top">
        <div>
          <h1>${escapeHtml(showcase.headline || "מוצרים חמים באתר ACE")}</h1>
          <p>${escapeHtml(showcase.message || screen.location_label)}</p>
        </div>
        <span class="status-pill">פנוי ליועץ</span>
      </header>
      <section class="idle-showcase">
        <article class="idle-hero">
          <div class="idle-image">
            <img alt="" src="${hero.image}">
            ${hero.promo ? `<span class="idle-ribbon">${escapeHtml(hero.promo)}</span>` : ""}
          </div>
          <div class="idle-info">
            <p class="idle-kicker">מומלץ עכשיו באתר ACE</p>
            <h2>${escapeHtml(hero.title)}</h2>
            <div class="price-line">
              <strong>${money(hero.price)}</strong>
              ${hero.regular_price ? `<del>${money(hero.regular_price)}</del>` : ""}
            </div>
            <div class="badges">${badges(hero)}${specs(hero, 2)}</div>
            ${productNotes(hero)}
            <div class="idle-sales-footer">
              <strong>מסך פנוי ליועץ</strong>
              <span>כאשר לקוח יבקש לראות מוצר, היועץ יחליף את התצוגה אוטומטית במסך הזמין.</span>
            </div>
          </div>
        </article>
        <aside class="idle-side">
          <section class="idle-assistant-promo">
            <div class="assistant-promo-copy">
              <p class="idle-kicker">חדש באתר ACE</p>
              <h3>יועץ מכירות קולי</h3>
              <p>לקוחות יכולים לפתוח את האתר, לדבר עם היועץ, ולקבל המלצות שיופיעו במסך זמין בחנות.</p>
            </div>
            <div class="assistant-qr">
              <img alt="" src="/api/qr?data=${encodeURIComponent(websiteUrl)}">
              <span>פתיחת האתר עם היועץ</span>
            </div>
          </section>
          <section class="idle-next">
            <h3>עוד מוצרים מהאתר</h3>
            ${others.map((product) => `
              <article class="idle-next-item">
                <img alt="" src="${product.image}">
                <div>
                  <h4>${escapeHtml(product.title)}</h4>
                  <p>${money(product.price)} · ${escapeHtml(product.availability_label || "זמין אונליין")}</p>
                </div>
              </article>
            `).join("")}
          </section>
        </aside>
      </section>
    `;
  }

  function renderProducts(screen) {
    lastIdleRenderKey = "";
    const display = screen.display || {};
    const products = display.products || [];
    if (!products.length) {
      renderIdle(screen);
      return;
    }
    const hero = products[0];
    const others = products.slice(1);
    const specs = Object.entries(hero.specs || {}).slice(0, 4).map(([key, value]) =>
      `<span class="badge">${escapeHtml(key)}: ${escapeHtml(value)}</span>`
    ).join("");
    root.innerHTML = `
      <div class="screen-id">ACE</div>
      <header class="screen-top">
        <div>
          <h1>${escapeHtml(display.headline || "המלצת ACE")}</h1>
          <p>${escapeHtml(display.message || screen.location_label)}</p>
        </div>
        <span class="status-pill">מוצג עכשיו</span>
      </header>
      <section class="screen-products">
        <article class="hero-product">
          <img alt="" src="${hero.image}">
          <div class="hero-info">
            <h2>${escapeHtml(hero.title)}</h2>
            <div class="price-line">
              <strong>${money(hero.price)}</strong>
              ${hero.regular_price ? `<del>${money(hero.regular_price)}</del>` : ""}
            </div>
            <div class="badges">${badges(hero)}${specs}</div>
          </div>
        </article>
        <aside>
          <div class="compare-list">
            ${others.map((product) => `
              <article class="compare-item">
                <img alt="" src="${product.image}">
                <div>
                  <h3>${escapeHtml(product.title)}</h3>
                  <p>${money(product.price)} · ${escapeHtml(product.availability_label || "")}</p>
                </div>
              </article>
            `).join("")}
          </div>
          <div class="screen-qr">
            <img alt="" src="/api/qr?data=${encodeURIComponent(hero.url)}">
            <p>סריקה או פתיחה מהטלפון תוביל לדף המוצר באתר ACE. המחיר והזמינות המחייבים הם באתר.</p>
          </div>
        </aside>
      </section>
    `;
  }

  async function tick() {
    try {
      const response = await fetch(`/api/screens/${encodeURIComponent(screenId)}/heartbeat`, { method: "POST" });
      const screen = await response.json();
      if (screen.display && ["products", "comparison", "hero"].includes(screen.display.mode)) {
        renderProducts(screen);
      } else if (screen.status === "idle" && screen.available) {
        const showcase = await loadIdleShowcase(screen);
        renderIdleShowcase(screen, showcase);
      } else {
        renderBusy(screen);
      }
    } catch (error) {
      root.innerHTML = `
        <div class="screen-id">ACE</div>
        <div class="screen-empty">
          <h1>אין חיבור לשרת</h1>
          <p>${escapeHtml(screenId)}</p>
        </div>
      `;
    }
  }

  tick();
  setInterval(tick, 1500);
})();
