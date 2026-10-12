/* Make Daily Activity's configuration discoverable in HA's entity dialog. */
(() => {
  const observed = new WeakSet();
  let pending = false;

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      visit(document);
    });
  }

  function enhance(settings) {
    const root = settings.shadowRoot;
    const editor = root?.querySelector("entity-registry-settings-editor");
    const container = root?.querySelector(".form.container");
    if (!editor || !container) return;
    const entry = editor.helperConfigEntry;
    let panel = container.querySelector("[data-daily-activity-editor]");
    if (entry?.domain !== "daily_activity") {
      panel?.remove();
      return;
    }
    if (!panel) {
      panel = document.createElement("section");
      panel.dataset.dailyActivityEditor = "";
      panel.style.cssText = "padding:16px;margin-bottom:16px;border:1px solid var(--divider-color,#ddd);border-radius:12px";
      const heading = document.createElement("strong");
      heading.dataset.heading = "";
      heading.textContent = "Monitored sensors";
      const sources = document.createElement("div");
      sources.dataset.sources = "";
      sources.style.cssText = "margin:8px 0 12px;overflow-wrap:anywhere;font-size:14px";
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.action = "";
      button.textContent = "Edit monitored sensors";
      button.style.cssText = "font:inherit;font-weight:500;padding:10px 16px;min-height:44px;border:0;border-radius:8px;cursor:pointer;background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff)";
      const error = document.createElement("div");
      error.setAttribute("role", "alert");
      error.style.cssText = "color:var(--error-color,#db4437);margin-top:8px";
      button.addEventListener("click", async () => {
        error.textContent = "";
        button.disabled = true;
        try {
          const current = root.querySelector("entity-registry-settings-editor");
          if (current?.helperConfigEntry?.domain !== "daily_activity" ||
              typeof current._showOptionsFlow !== "function") {
            throw new Error("This HA version cannot open the editor here. Use Daily Activity options below.");
          }
          await current._showOptionsFlow();
        } catch (err) {
          error.textContent = err.message || "Unable to open Daily Activity settings.";
        } finally {
          button.disabled = false;
        }
      });
      panel.append(heading, sources, button, error);
      container.prepend(panel);
    }
    const attributes = editor.hass?.states[editor.entry.entity_id]?.attributes;
    const reported = attributes?.source_mode === "reported_intervals";
    panel.querySelector("[data-heading]").textContent = reported ? "Reported interval source" : "Monitored sensors";
    panel.querySelector("[data-action]").textContent = reported ? "Edit source settings / token" : "Edit monitored sensors";
    const sourceEntities = attributes?.source_entities;
    const sources = Array.isArray(sourceEntities) ? sourceEntities : sourceEntities ? [sourceEntities] : [];
    const text = reported ? (attributes.source_id || "Source ID unavailable.") : sources.length ? sources.join("\n") : "No source sensors reported.";
    const label = panel.querySelector("[data-sources]");
    label.style.whiteSpace = "pre-line";
    if (label.textContent !== text) label.textContent = text;
  }

  function visit(root) {
    if (!observed.has(root)) {
      observed.add(root);
      new MutationObserver(schedule).observe(root, { childList: true, subtree: true });
    }
    for (const element of root.querySelectorAll("*")) {
      if (element.localName === "entity-registry-settings") {
        enhance(element);
        // Properties can arrive after the element's DOM is first attached.
        element.updateComplete?.then(() => enhance(element));
      }
      if (element.shadowRoot) visit(element.shadowRoot);
    }
  }

  document.addEventListener("hass-more-info", () => {
    schedule();
    setTimeout(schedule, 250);
    setTimeout(schedule, 1000);
  });
  schedule();
})();
