/* Workspace navigation only. PDF jobs are owned by pdf-export.js.
 * Self-starts after app.js registers its generic .tab click listeners.
 * Reset hook: window "studio-reset-layout" (no persistence here).
 */
(() => {
  "use strict";

  function initWorkflow() {
    const nav = document.getElementById("tabs");
    if (!nav || nav.dataset.workflowReady) return;
    nav.dataset.workflowReady = "true";
    const toggle = document.getElementById("tools-toggle");
    const menu = document.getElementById("tools-menu");
    const activeLabel = document.getElementById("tools-active-label");
    const items = [...menu.querySelectorAll("[data-tab]")];
    items.forEach(item => { item.tabIndex = -1; });

    function closeMenu(restoreFocus = false) {
      menu.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      if (restoreFocus) toggle.focus();
    }

    function positionMenu() {
      menu.style.left = "0px";
      const bounds = menu.getBoundingClientRect();
      menu.style.left = Math.min(0, window.innerWidth - bounds.right - 8) + "px";
    }

    function openMenu(last = false) {
      menu.hidden = false;
      positionMenu();
      toggle.setAttribute("aria-expanded", "true");
      (last ? items[items.length - 1] : items[0]).focus();
    }

    function syncActiveTool() {
      const selected = items.find(item => item.classList.contains("is-active"));
      const label = selected ? selected.textContent.trim() : "";
      activeLabel.textContent = label;
      toggle.classList.toggle("is-active", Boolean(selected));
      toggle.setAttribute("aria-label", label ? "Tools, " + label : "Tools");
    }

    toggle.addEventListener("click", () => {
      if (menu.hidden) openMenu();
      else closeMenu(true);
    });
    toggle.addEventListener("keydown", event => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openMenu(event.key === "ArrowUp");
      }
    });
    menu.addEventListener("keydown", event => {
      const index = items.indexOf(document.activeElement);
      let next;
      if (event.key === "ArrowDown") next = (index + 1) % items.length;
      if (event.key === "ArrowUp") next = (index - 1 + items.length) % items.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = items.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        items[next].focus();
      }
    });
    document.addEventListener("click", event => {
      if (!event.target.closest(".tools-menu-wrap")) closeMenu();
    });
    nav.addEventListener("focusout", event => {
      if (!event.relatedTarget?.closest(".tools-menu-wrap")) closeMenu();
    });
    // Observe programmatic selection/restoration as well as pointer navigation.
    new MutationObserver(syncActiveTool).observe(nav, {
      subtree: true, attributes: true, attributeFilter: ["aria-current"]
    });
    nav.addEventListener("click", event => {
      if (!event.target.closest(".tab[data-tab]")) return;
      syncActiveTool();
      closeMenu(menu.contains(document.activeElement));
      closeDrawer();
    });

    const showExport = () => nav.querySelector('[data-tab="export"]').click();
    document.getElementById("export-pdf").addEventListener("click", showExport);
    document.getElementById("task-tray-open").addEventListener("click", showExport);
    document.getElementById("reset-layout").addEventListener("click", () => {
      closeDrawer();
      window.dispatchEvent(new CustomEvent("studio-reset-layout"));
    });

    const workspace = document.getElementById("workspace");
    const narrow = window.matchMedia("(max-width: 800px)");
    const drawerToggles = {
      tree: document.getElementById("toggle-tree"),
      details: document.getElementById("toggle-details")
    };
    const backdrop = document.createElement("button");
    backdrop.type = "button";
    backdrop.className = "drawer-backdrop";
    backdrop.setAttribute("aria-label", "Close side panel");
    backdrop.tabIndex = -1;
    backdrop.hidden = true;
    workspace.appendChild(backdrop);

    function syncDrawerButtons() {
      for (const [name, button] of Object.entries(drawerToggles)) {
        const expanded = narrow.matches ? workspace.dataset.drawer === name :
          !workspace.classList.contains(name + "-collapsed");
        button.setAttribute("aria-expanded", String(expanded));
      }
    }

    function closeDrawer(restoreFocus = false) {
      const previous = workspace.dataset.drawer;
      delete workspace.dataset.drawer;
      backdrop.hidden = true;
      syncDrawerButtons();
      if (restoreFocus && previous) drawerToggles[previous].focus();
    }

    for (const [name, button] of Object.entries(drawerToggles)) {
      // On desktop app.js owns collapse state and saved panel widths. On a
      // narrow viewport preserve that state and show just one overlay drawer.
      button.addEventListener("click", event => {
        if (!narrow.matches) return;
        event.stopImmediatePropagation();
        if (workspace.dataset.drawer === name) closeDrawer(true);
        else {
          workspace.dataset.drawer = name;
          backdrop.hidden = false;
          syncDrawerButtons();
          document.querySelector('[data-close-drawer="' + name + '"]').focus();
        }
      }, true);
    }
    document.querySelectorAll("[data-close-drawer]").forEach(button => {
      button.addEventListener("click", () => closeDrawer(true));
    });
    backdrop.addEventListener("click", () => closeDrawer(true));
    narrow.addEventListener("change", () => closeDrawer());
    window.addEventListener("resize", () => { if (!menu.hidden) positionMenu(); });
    window.addEventListener("studio-reset-layout", syncDrawerButtons);
    // Modeless drawers keep navigation available. Close before keyboard focus
    // moves to workspace content that would otherwise sit behind the scrim.
    document.addEventListener("focusin", event => {
      const name = workspace.dataset.drawer;
      if (name && !document.getElementById(name + "-panel").contains(event.target) &&
          !Object.values(drawerToggles).includes(event.target)) closeDrawer();
    });
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      if (!menu.hidden) {
        event.preventDefault();
        closeMenu(true);
      } else if (workspace.dataset.drawer) {
        event.preventDefault();
        closeDrawer(true);
      }
    });
    syncDrawerButtons();
    syncActiveTool();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initWorkflow);
  } else initWorkflow();
})();
