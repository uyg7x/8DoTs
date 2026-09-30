    (() => {
      const clock = document.getElementById("site-clock");
      if (clock) {
        const updateClock = () => {
          const now = new Date();
          const time = [now.getHours(), now.getMinutes(), now.getSeconds()]
            .map((value) => String(value).padStart(2, "0"))
            .join(":");
          clock.dateTime = time;
          clock.textContent = time;
        };
        updateClock();
        window.setInterval(updateClock, 1000);
      }

      const links = Array.from(document.querySelectorAll("[data-view-link]"));
      const panels = Array.from(document.querySelectorAll("[data-view-panel]"));
      const validViews = new Set(panels.map((panel) => panel.dataset.viewPanel));

      function showView(name, updateHistory = true) {
        const view = validViews.has(name) ? name : "home";
        for (const panel of panels) panel.hidden = panel.dataset.viewPanel !== view;
        for (const link of links) {
          if (link.dataset.viewLink === view) link.setAttribute("aria-current", "page");
          else link.removeAttribute("aria-current");
        }
        if (updateHistory) history.replaceState(null, "", "#" + view);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }

      for (const link of links) {
        link.addEventListener("click", (event) => {
          const view = link.dataset.viewLink;
          if (!view) return;
          event.preventDefault();
          if (link.dataset.scrollTarget) {
            showView(view, false);
            history.replaceState(null, "", "#" + view);
            requestAnimationFrame(() => {
              const target = document.getElementById(link.dataset.scrollTarget);
              if (target) {
                target.scrollIntoView({ behavior: "smooth", block: "start" });
                target.focus({ preventScroll: true });
              }
            });
          } else {
            showView(view);
          }
        });
      }
      const installTrigger = document.querySelector(".install-trigger");
      const installMenu = document.querySelector(".install-menu");
      installMenu.addEventListener("mouseenter", () => installTrigger.setAttribute("aria-expanded", "true"));
      installMenu.addEventListener("mouseleave", () => installTrigger.setAttribute("aria-expanded", "false"));
      installMenu.addEventListener("focusin", () => installTrigger.setAttribute("aria-expanded", "true"));
      installMenu.addEventListener("focusout", (event) => {
        if (!installMenu.contains(event.relatedTarget)) installTrigger.setAttribute("aria-expanded", "false");
      });
      for (const link of document.querySelectorAll("[data-install-section]")) {
        link.addEventListener("click", (event) => {
          event.preventDefault();
          const target = document.getElementById(link.dataset.installSection);
          showView("install", false);
          history.replaceState(null, "", "#install");
          if (target) {
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            target.focus({ preventScroll: true });
          }
        });
      }
      window.addEventListener("hashchange", () => {
        const hash = location.hash.slice(1);
        if (validViews.has(hash)) showView(hash, false);
      });
      showView(location.hash.slice(1), false);

      for (const block of document.querySelectorAll("pre, .module-map")) {
        let wrapper = block.closest(".code-wrap");
        if (!wrapper) {
          wrapper = document.createElement("div");
          wrapper.className = "code-wrap copy-wrap";
          block.parentNode.insertBefore(wrapper, block);
          wrapper.appendChild(block);
        }
        if (wrapper.querySelector(".copy-button")) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "copy-button";
        button.setAttribute("aria-label", "Copy code block");
        button.dataset.copy = block.textContent.trimEnd();
        button.textContent = "Copy";
        wrapper.appendChild(button);
      }
      const status = document.getElementById("copy-status");
      for (const button of document.querySelectorAll("[data-copy]")) {
        button.addEventListener("click", async () => {
          try {
            const value = button.dataset.copy;
            await navigator.clipboard.writeText(value);
            status.textContent = "Command copied.";
          } catch (_error) {
            status.textContent = "Clipboard access is unavailable. Select the command text to copy it.";
          }
        });
      }
    })();