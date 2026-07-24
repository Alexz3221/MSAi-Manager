    const johnEnabled = window.JOHN_ENABLED;
    const filters = document.querySelector("#filters");
    const companySelect = document.querySelector("#company");
    const serviceSelect = document.querySelector("#service");
    const feed = document.querySelector("#feed");
    const noticeCount = document.querySelector("#notice-count");
    const companyCount = document.querySelector("#company-count");
    const actionCount = document.querySelector("#action-count");
    const toolTabs = document.querySelectorAll("[data-tool-target]");
    const johnForm = document.querySelector("#john-form");
    const johnMessage = document.querySelector("#john-message");
    const johnSend = document.querySelector("#john-send");
    const johnStatus = document.querySelector("#john-status");
    const johnTab = document.querySelector("#john-tab");
    const chatLog = document.querySelector("#chat-log");
    const johnUserId = `web-${crypto.randomUUID ? crypto.randomUUID() : Date.now()}`;
    let johnSessionId = null;

    if (!johnEnabled) {
      johnTab.disabled = true;
      johnTab.textContent = "John (offline)";
      johnTab.setAttribute("aria-label", "John is currently disabled");
      johnMessage.disabled = true;
      johnSend.disabled = true;
      johnStatus.textContent = "John is currently disabled by the administrator.";
      document.querySelectorAll("[data-prompt]").forEach(button => {
        button.disabled = true;
      });
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    function option(value, label) {
      return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
    }

    function selectTool(targetId) {
      document.querySelectorAll(".tool-view").forEach(view => {
        view.hidden = view.id !== targetId;
      });
      toolTabs.forEach(tab => {
        const active = tab.dataset.toolTarget === targetId;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      if (targetId === "john-tool") johnMessage.focus();
    }

    function appendMessage(role, text, tools = []) {
      const article = document.createElement("article");
      article.className = `message ${role}`;
      const toolNote = "";
      const formattedText = escapeHtml(text)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/^### (.+)$/gm, '<strong class="message-heading">$1</strong>')
        .replace(/\n/g, "<br>");
      article.innerHTML = `
        <span class="message-label">${role === "user" ? "You" : "John"}</span>
        ${formattedText}
        ${toolNote}
      `;
      chatLog.appendChild(article);
      article.scrollIntoView({ behavior: "smooth", block: "end" });
    }

    async function askJohn(message) {
      appendMessage("user", message);
      johnSend.disabled = true;
      johnMessage.disabled = true;
      johnStatus.textContent = "John is checking your project context...";

      try {
        const response = await fetch("/api/john", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            user_id: johnUserId,
            session_id: johnSessionId
          })
        });
        const payload = await response.json();
        if (!response.ok) {
          const retry = payload.retry_after_seconds
            ? ` Try again in ${payload.retry_after_seconds} seconds.`
            : "";
          throw new Error((payload.error || "John is unavailable.") + retry);
        }
        johnSessionId = payload.session_id;
        appendMessage("john", payload.reply, payload.tools || []);
        johnStatus.textContent = "John is ready for a follow-up question.";
      } catch (error) {
        appendMessage("john", error.message || "John is temporarily unavailable.");
        johnStatus.textContent = "The request failed. You can try again.";
      } finally {
        johnSend.disabled = false;
        johnMessage.disabled = false;
        johnMessage.focus();
      }
    }

    async function loadFilters() {
      const [companiesResponse, servicesResponse] = await Promise.all([
        fetch("/api/companies"),
        fetch("/api/services")
      ]);
      const companies = await companiesResponse.json();
      const services = await servicesResponse.json();

      companySelect.innerHTML = option("", "All companies") + companies.companies
        .map(company => option(company.id, company.name))
        .join("");
      serviceSelect.innerHTML = option("", "All services") + services.services
        .map(service => option(service, service))
        .join("");
    }

    function paramsFromForm() {
      const data = new FormData(filters);
      const params = new URLSearchParams();
      for (const [key, value] of data.entries()) {
        if (value) params.set(key, value);
      }
      if (document.querySelector("#requires_action").checked) {
        params.set("requires_action", "true");
      }
      return params;
    }

    function statusFromSubject(subject) {
        const match = /^\s*\[(action required|action advised)\]/i.exec(subject || "");
        if (!match) return "status-default";
        const tag = match[1].toLowerCase();
        if (tag === "action required") return "status-required";
        if (tag === "action advised") return "status-advised";
        return "status-default";
      }

    function formatSubject(subject) {
        const match = /^\s*\[([^\]]+)\]\s*/.exec(subject || "");
        if (!match) return escapeHtml(subject || "");
        const tag = match[1].toUpperCase();
        const rest = (subject || "").slice(match[0].length);
        return `<span class="status-tag">${escapeHtml(tag)}:</span> ${escapeHtml(rest)}`;
      }

    function renderFeed(payload) {
      const impacted = new Set();
      let actionRequired = 0;

      payload.items.forEach(item => {
        if (item.requires_customer_action) actionRequired += 1;
        item.impacted_companies.forEach(company => impacted.add(company.company_id));
      });

      noticeCount.textContent = payload.count;
      companyCount.textContent = impacted.size;
      actionCount.textContent = actionRequired;
      document.querySelector("#notice-count-table").textContent = payload.count;

      const radius = 60;
      const circumference = 2 * Math.PI * radius;
      const percent = payload.count ? actionRequired / payload.count : 0;
      const ringProgress = document.querySelector("#ring-progress");
      ringProgress.style.strokeDasharray = `${circumference} ${circumference}`;
      ringProgress.style.strokeDashoffset = `${circumference * (1 - percent)}`;

      if (!payload.items.length) {
        feed.innerHTML = `<article class="feed-card">No MSA notices match the selected filters.</article>`;
        return;
      }

      feed.innerHTML = payload.items.map(item => {
        const services = item.affected_services
          .map(service => `<span class="pill">${escapeHtml(service)}</span>`)
          .join("");
        const companies = item.impacted_companies
          .map(company => {return `<span class="pill warning">${escapeHtml(company.company_name)}</span>`;
          })
          .join("");
        const actions = item.actions
          .map(action => `<li>${escapeHtml(action)}</li>`)
          .join("");

        const statusClass = statusFromSubject(item.subject);

        return `
          <article class="feed-card ${statusClass}">
            <h2><span class="status-dot"></span>${formatSubject(item.subject)}</h2>
            <p><strong>effective:</strong> ${escapeHtml(item.effective_date || "Not listed")}</p>
            <div class="pills">${services}</div>
            <div class="pills">${companies}</div>
            ${actions ? `<ul>${actions}</ul>` : ""}
          </article>
        `;
      }).join("");
    }

    async function loadFeed() {
      feed.innerHTML = `<article class="feed-card">Loading MSA feed...</article>`;
      const response = await fetch(`/api/feed?${paramsFromForm().toString()}`);
      renderFeed(await response.json());
    }

    filters.addEventListener("submit", event => {
      event.preventDefault();
      loadFeed();
    });

    toolTabs.forEach(tab => {
      tab.addEventListener("click", () => selectTool(tab.dataset.toolTarget));
    });

    johnForm.addEventListener("submit", event => {
      event.preventDefault();
      const message = johnMessage.value.trim();
      if (!message || johnSend.disabled) return;
      johnMessage.value = "";
      askJohn(message);
    });

    document.querySelectorAll("[data-prompt]").forEach(button => {
      button.addEventListener("click", () => {
        johnMessage.value = button.dataset.prompt;
        johnForm.requestSubmit();
      });
    });

    loadFilters().then(loadFeed);
