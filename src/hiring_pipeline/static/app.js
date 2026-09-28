(() => {
  const STAGES = ["Applied", "Screening", "Interview", "Offer", "Hired", "Rejected"];
  const NEXT_STAGE = {
    Applied: "Screening",
    Screening: "Interview",
    Interview: "Offer",
    Offer: "Hired",
  };
  const app = document.getElementById("app");
  const toastRegion = document.getElementById("toast-region");
  const state = {
    recruiter: null,
    groups: Object.fromEntries(STAGES.map((stage) => [stage, []])),
    loading: false,
    loadError: "",
    query: "",
    searchActive: false,
    searchResults: null,
    searchError: null,
    searching: false,
    selectedCandidate: null,
    selectedLoading: false,
    selectedError: "",
    modal: null,
    busyCandidateId: null,
  };

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.status = status;
      this.data = data;
    }
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
    });
    if (response.status === 204) return null;
    let data;
    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }
    if (!response.ok) {
      const error = data.error || {};
      throw new ApiError(error.message || `Request failed (${response.status}).`, response.status, data);
    }
    return data;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function button(label, className, onClick, disabled = false, type = "button") {
    const node = element("button", `button ${className || ""}`, label);
    node.type = type;
    node.disabled = disabled;
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }

  function toast(message, isError = false) {
    const node = element("div", `toast${isError ? " error" : ""}`, message);
    toastRegion.append(node);
    window.setTimeout(() => node.remove(), 4200);
  }

  function formatElapsed(seconds) {
    const totalHours = Math.floor(Math.max(0, seconds || 0) / 3600);
    const days = Math.floor(totalHours / 24);
    const hours = totalHours % 24;
    if (days >= 30) {
      const months = Math.floor(days / 30);
      const remainingDays = days % 30;
      return `${months}mo${remainingDays ? ` ${remainingDays}d` : ""}`;
    }
    if (days > 0) return `${days}d ${hours}h`;
    if (totalHours > 0) return `${totalHours}h`;
    const minutes = Math.floor(Math.max(0, seconds || 0) / 60);
    return `${minutes}m`;
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value || "Date unavailable";
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  }

  function initials(name) {
    return (name || "R").split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
  }

  function renderLogin(message = "") {
    app.replaceChildren();
    const wrap = element("section", "login-wrap");
    const card = element("form", "login-card");
    card.noValidate = false;
    const brand = element("div", "login-brand");
    brand.append(element("span", "brand-mark", "H"), element("span", "", "Hiring pipeline"));
    card.append(brand, element("h1", "", "Welcome back"), element("p", "", "Sign in to manage your candidates and keep every decision in one place."));
    const emailField = field("Work email", "email", "email", "you@company.com");
    emailField.input.autocomplete = "username";
    emailField.input.required = true;
    const passwordField = field("Password", "password", "current-password", "Your password");
    passwordField.input.required = true;
    const error = element("div", "form-error", message);
    const submit = button("Sign in", "button-primary", null, false, "submit");
    submit.style.width = "100%";
    card.append(emailField.wrap, passwordField.wrap, error, submit);
    card.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.textContent = "";
      submit.disabled = true;
      submit.textContent = "Signing in…";
      try {
        const result = await api("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email: emailField.input.value, password: passwordField.input.value }),
        });
        state.recruiter = result.recruiter;
        await loadCandidates();
      } catch (err) {
        error.textContent = err.message;
        submit.disabled = false;
        submit.textContent = "Sign in";
      }
    });
    wrap.append(card);
    app.append(wrap);
    emailField.input.focus();
  }

  function field(label, name, autocomplete, placeholder, type = "text") {
    const wrap = element("div", "field");
    const labelNode = element("label", "", label);
    labelNode.htmlFor = name;
    const input = document.createElement(type === "textarea" ? "textarea" : "input");
    input.id = name;
    input.name = name;
    input.placeholder = placeholder;
    if (type !== "textarea") input.type = type;
    if (autocomplete) input.autocomplete = autocomplete;
    wrap.append(labelNode, input);
    return { wrap, input };
  }

  async function checkSession() {
    try {
      const result = await api("/api/auth/me");
      state.recruiter = result.recruiter;
      await loadCandidates();
    } catch (err) {
      if (err.status !== 401) toast(err.message, true);
      renderLogin();
    }
  }

  async function loadCandidates() {
    state.loading = true;
    state.loadError = "";
    renderApp();
    try {
      const result = await api("/api/candidates");
      state.groups = Object.fromEntries(STAGES.map((stage) => [stage, []]));
      for (const group of result.groups || []) state.groups[group.stage] = group.candidates || [];
      state.loading = false;
      state.loadError = "";
    } catch (err) {
      state.loading = false;
      state.loadError = err.message;
      if (err.status === 401) {
        state.recruiter = null;
        renderLogin("Your session expired. Sign in again to continue.");
        return;
      }
    }
    renderApp();
  }

  function renderApp() {
    if (!state.recruiter) return renderLogin();
    const searchWasFocused = document.activeElement?.classList.contains("search-input");
    const searchSelection = searchWasFocused ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
    app.replaceChildren();
    app.append(renderTopbar());
    const workspace = element("section", "workspace");
    const heading = element("div", "page-heading");
    const title = element("div");
    title.append(element("p", "eyebrow", "Your workspace"), element("h1", "", "Hiring pipeline"), element("p", "", "A clear view of every candidate, from first look to final decision."));
    heading.append(title);
    workspace.append(heading, renderToolbar());
    if (state.loadError) {
      const notice = element("div", "notice");
      notice.append(element("div", "notice-title", "Couldn't load candidates"), element("div", "", state.loadError));
      notice.append(button("Try again", "button-secondary button-small", loadCandidates));
      workspace.append(notice);
    }
    if (state.searchActive) {
      workspace.append(renderSearchResults());
    } else {
      const allCandidates = STAGES.flatMap((stage) => state.groups[stage] || []);
      const summary = element("div", "board-summary");
      summary.append(element("span", "summary-dot"), element("span", "", `${allCandidates.length} ${allCandidates.length === 1 ? "candidate" : "candidates"} across the pipeline`));
      workspace.append(summary);
      workspace.append(state.loading ? renderLoading("Loading candidates…") : renderBoard());
    }
    app.append(workspace);
    if (state.modal) app.append(renderModal());
    if (state.selectedCandidate || state.selectedLoading) app.append(renderDrawer());
    if (searchWasFocused) {
      const newSearchInput = app.querySelector(".search-input");
      newSearchInput?.focus();
      if (searchSelection && newSearchInput) newSearchInput.setSelectionRange(...searchSelection);
    }
  }

  function renderTopbar() {
    const bar = element("header", "topbar");
    const brand = element("div", "topbar-brand");
    brand.append(element("span", "brand-mark", "H"), element("span", "", "Mini Hiring Pipeline"));
    const right = element("div", "topbar-right");
    const avatar = element("span", "avatar", initials(state.recruiter.email));
    avatar.setAttribute("aria-hidden", "true");
    right.append(element("span", "", state.recruiter.email), avatar, button("Log out", "button-quiet button-small", logout));
    bar.append(brand, right);
    return bar;
  }

  function renderToolbar() {
    const toolbar = element("form", "toolbar");
    const searchWrap = element("div", "search-wrap");
    searchWrap.append(element("span", "search-icon", "⌕"));
    const input = document.createElement("input");
    input.className = "search-input";
    input.type = "search";
    input.placeholder = "Search names, stages, time in stage, or movement…";
    input.setAttribute("aria-label", "Search candidates");
    input.maxLength = 500;
    input.value = state.query;
    input.addEventListener("input", () => {
      state.query = input.value;
      state.searchActive = false;
      state.searchResults = null;
      state.searchError = null;
      renderApp();
    });
    searchWrap.append(input, element("span", "search-hint", "Try ‘sharam’ or ‘in Interview’"));
    toolbar.append(searchWrap, button(state.searching ? "Searching…" : "Search", "button-secondary", null, state.searching, "submit"));
    const add = button("＋ Add candidate", "button-primary", () => openModal("create"));
    toolbar.append(add);
    toolbar.addEventListener("submit", (event) => {
      event.preventDefault();
      runSearch();
    });
    return toolbar;
  }

  async function runSearch() {
    state.searchActive = true;
    if (!state.query.trim()) {
      state.searchActive = false;
      state.searchResults = null;
      state.searchError = null;
      renderApp();
      return;
    }
    state.searching = true;
    state.searchError = null;
    renderApp();
    try {
      state.searchResults = await api("/api/search", {
        method: "POST",
        body: JSON.stringify({ q: state.query.trim() }),
      });
    } catch (err) {
      state.searchResults = null;
      state.searchError = { message: err.message, examples: err.data?.error?.examples || [], status: err.status };
    } finally {
      state.searching = false;
      renderApp();
    }
  }

  function renderSearchResults() {
    if (state.searching) return renderLoading("Searching candidates…");
    const panel = element("section", "results-panel");
    if (state.searchError) {
      const notice = element("div", "notice info");
      notice.append(element("div", "notice-title", state.searchError.status === 503 ? "Search is temporarily unavailable" : "I couldn't understand that search"), element("div", "", state.searchError.message));
      if (state.searchError.examples.length) {
        const suggestions = element("div", "suggestions");
        for (const example of state.searchError.examples) {
          const chip = button(example, "suggestion-chip", () => {
            state.query = example;
            renderApp();
            runSearch();
          });
          suggestions.append(chip);
        }
        notice.append(suggestions);
      }
      panel.append(notice);
      return panel;
    }
    if (!state.searchResults) {
      panel.append(renderLoading("Preparing search…"));
      return panel;
    }
    const result = state.searchResults;
    const heading = element("div", "results-heading");
    heading.append(element("h2", "", `${result.count} ${result.count === 1 ? "match" : "matches"}`), element("span", "", result.interpretation_source === "ai" ? "Interpreted with AI" : "Understood by search rules"));
    panel.append(heading, element("p", "search-explanation", result.explanation));
    if (!result.results.length) {
      const empty = element("div", "search-empty");
      empty.append(element("strong", "", "No candidates match this search"), element("span", "", "The search made sense, but there are no matching candidates. Try another name or filter."));
      panel.append(empty);
      return panel;
    }
    const list = element("div", "results-list");
    for (const candidate of result.results) {
      const row = element("div", "result-row");
      const main = element("div", "result-main");
      main.append(element("strong", "", candidate.full_name), element("small", "", `${candidate.email || "No email"} · ${formatElapsed(candidate.time_in_current_stage_seconds)} in ${candidate.current_stage}`));
      row.append(main, element("span", "stage-pill", candidate.current_stage));
      row.append(button("View history", "button-secondary button-small", () => openCandidate(candidate.id)));
      list.append(row);
    }
    panel.append(list);
    return panel;
  }

  function renderLoading(message) {
    const loading = element("div", "loading-inline");
    loading.append(element("span", "spinner"), element("span", "", message));
    return loading;
  }

  function renderBoard() {
    const board = element("div", "board");
    for (const stage of STAGES) {
      const candidates = state.groups[stage] || [];
      const column = element("section", "stage-column");
      column.dataset.stage = stage;
      const head = element("div", "stage-head");
      const label = element("div", "stage-label");
      label.append(element("span", "stage-indicator"), element("span", "", stage));
      head.append(label, element("span", "stage-count", String(candidates.length)));
      const list = element("div", "candidate-list");
      if (!candidates.length && !state.loading) list.append(element("div", "empty-column", "No candidates here yet"));
      for (const candidate of candidates) list.append(renderCandidateCard(candidate, stage));
      column.append(head, list);
      board.append(column);
    }
    return board;
  }

  function renderCandidateCard(candidate, stage) {
    const card = element("article", "candidate-card");
    const name = button(candidate.full_name, "candidate-name", () => openCandidate(candidate.id));
    card.append(name, element("p", "candidate-email", candidate.email || candidate.phone || "Contact details not added"));
    const duration = element("span", "time-chip", `◷ ${formatElapsed(candidate.time_in_current_stage_seconds)} in stage`);
    card.append(duration);
    if (NEXT_STAGE[stage]) {
      const actions = element("div", "card-actions");
      const isBusy = state.busyCandidateId === candidate.id;
      const advance = button(isBusy ? "Moving…" : "Advance →", "button-primary button-small", () => moveCandidate(candidate), isBusy);
      advance.title = `Advance to ${NEXT_STAGE[stage]}`;
      advance.setAttribute("aria-label", isBusy ? "Moving candidate" : `Advance to ${NEXT_STAGE[stage]}`);
      actions.append(advance);
      actions.append(button("Reject", "button-danger button-small", () => openModal("reject", candidate), isBusy));
      card.append(actions);
    } else {
      card.append(element("div", "terminal-note", stage === "Hired" ? "Final outcome · Hired" : "Final outcome · Rejected"));
    }
    return card;
  }

  async function openCandidate(candidateId) {
    state.selectedCandidate = null;
    state.selectedLoading = true;
    state.selectedError = "";
    renderApp();
    try {
      state.selectedCandidate = await api(`/api/candidates/${candidateId}`);
    } catch (err) {
      state.selectedError = err.message;
    } finally {
      state.selectedLoading = false;
      renderApp();
    }
  }

  function renderDrawer() {
    const overlay = element("div", "overlay");
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeDrawer();
    });
    const drawer = element("aside", "drawer");
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-label", "Candidate history");
    if (state.selectedLoading) {
      drawer.append(renderLoading("Loading candidate history…"));
      overlay.append(drawer);
      return overlay;
    }
    if (state.selectedError || !state.selectedCandidate) {
      const top = element("div", "drawer-top");
      top.append(element("h2", "", "History unavailable"), button("×", "button-secondary icon-button", closeDrawer));
      drawer.append(top, element("div", "drawer-error", state.selectedError || "Candidate details are unavailable."), button("Close", "button-secondary", closeDrawer));
      overlay.append(drawer);
      return overlay;
    }
    const candidate = state.selectedCandidate;
    const currentStage = candidate.current_stage;
    const top = element("div", "drawer-top");
    const title = element("div");
    title.append(element("p", "eyebrow", "Candidate record"), element("h2", "", candidate.full_name), element("p", "drawer-subtitle", `Currently in ${currentStage}`));
    top.append(title, button("×", "button-secondary icon-button", closeDrawer));
    drawer.append(top);
    const facts = element("div", "detail-facts");
    facts.append(fact("Email", candidate.email || "Not provided"), fact("Phone", candidate.phone || "Not provided"), fact("Time in stage", formatElapsed(candidate.time_in_current_stage_seconds)), fact("Stage entered", formatDate(candidate.stage_entered_at)));
    drawer.append(facts);
    if (NEXT_STAGE[currentStage]) {
      const actions = element("div", "card-actions");
      const busy = state.busyCandidateId === candidate.id;
      actions.append(button(busy ? "Moving…" : `Advance to ${NEXT_STAGE[currentStage]} →`, "button-primary", () => moveCandidate(candidate), busy));
      actions.append(button("Reject", "button-danger", () => openModal("reject", candidate), busy));
      drawer.append(actions);
    }
    const history = candidate.history || [];
    const heading = element("div", "timeline-heading");
    heading.append(element("h3", "", "Complete stage history"), element("span", "", `${history.length} ${history.length === 1 ? "event" : "events"}`));
    drawer.append(heading);
    if (!history.length) {
      drawer.append(element("p", "event-meta", "No history events are available."));
    } else {
      const timeline = element("div", "timeline");
      for (const event of history) {
        const row = element("div", "timeline-event");
        const titleText = event.from_stage ? `${event.from_stage} → ${event.to_stage}` : `Added to ${event.to_stage}`;
        row.append(element("p", "event-title", titleText));
        const actor = event.actor_id ? ` · recruiter #${event.actor_id}` : "";
        row.append(element("p", "event-meta", `${formatDate(event.created_at)}${actor}`));
        if (event.reason) row.append(element("p", "event-reason", event.reason));
        timeline.append(row);
      }
      drawer.append(timeline);
    }
    overlay.append(drawer);
    return overlay;
  }

  function fact(label, value) {
    const item = element("div", "fact");
    item.append(element("span", "fact-label", label), element("span", "fact-value", value));
    return item;
  }

  function closeDrawer() {
    state.selectedCandidate = null;
    state.selectedError = "";
    state.selectedLoading = false;
    renderApp();
  }

  function openModal(kind, candidate = null) {
    state.modal = { kind, candidate };
    renderApp();
    const first = app.querySelector(".modal input, .modal textarea");
    if (first) first.focus();
  }

  function renderModal() {
    const modalState = state.modal;
    const overlay = element("div", "modal-overlay");
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeModal();
    });
    const modal = element("form", "modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    const isCreate = modalState.kind === "create";
    const head = element("div", "modal-head");
    const title = element("div");
    title.append(element("h2", "", isCreate ? "Add a candidate" : "Reject candidate"), element("p", "", isCreate ? "Start their journey in Applied." : `This will move ${modalState.candidate.full_name} to a final Rejected outcome.`));
    head.append(title, button("×", "button-secondary icon-button", closeModal));
    modal.append(head);
    let fields;
    if (isCreate) {
      const name = field("Full name", "candidate-name", "name", "e.g. Priya Sharma");
      name.input.required = true;
      name.input.maxLength = 200;
      const email = field("Email (optional)", "candidate-email", "email", "name@example.com", "email");
      email.input.maxLength = 320;
      const phone = field("Phone (optional)", "candidate-phone", "tel", "+1 555 0100", "tel");
      phone.input.maxLength = 50;
      fields = { name, email, phone };
      modal.append(name.wrap, email.wrap, phone.wrap);
    } else {
      const reason = field("Reason (optional)", "reject-reason", "off", "Add a short note to the audit history", "textarea");
      reason.input.maxLength = 2000;
      fields = { reason };
      modal.append(reason.wrap);
    }
    const error = element("div", "form-error");
    const actions = element("div", "modal-actions");
    const cancel = button("Cancel", "button-secondary", closeModal);
    const submit = button(isCreate ? "Add candidate" : "Confirm rejection", isCreate ? "button-primary" : "button-danger", null, false, "submit");
    actions.append(cancel, submit);
    modal.append(error, actions);
    modal.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      submit.textContent = isCreate ? "Adding…" : "Rejecting…";
      error.textContent = "";
      try {
        if (isCreate) {
          await api("/api/candidates", {
            method: "POST",
            body: JSON.stringify({
              full_name: fields.name.input.value,
              email: fields.email.input.value,
              phone: fields.phone.input.value,
            }),
          });
          closeModal();
          state.query = "";
          state.searchActive = false;
          state.searchResults = null;
          state.searchError = null;
          await loadCandidates();
          toast("Candidate added to Applied.");
        } else {
          const candidate = modalState.candidate;
          await api(`/api/candidates/${candidate.id}/reject`, {
            method: "POST",
            body: JSON.stringify({ expected_stage: candidate.current_stage, reason: fields.reason.input.value }),
          });
          state.modal = null;
          await refreshAfterChange();
          if (state.selectedCandidate?.id === candidate.id) await openCandidate(candidate.id);
          toast(`${candidate.full_name} moved to Rejected.`);
        }
      } catch (err) {
        if (err.status === 409 && !isCreate) {
          state.modal = null;
          toast(err.message, true);
          await refreshAfterChange();
          if (state.selectedCandidate?.id === modalState.candidate.id) await openCandidate(modalState.candidate.id);
        } else {
          error.textContent = err.message;
          submit.disabled = false;
          submit.textContent = isCreate ? "Add candidate" : "Confirm rejection";
        }
      }
    });
    overlay.append(modal);
    return overlay;
  }

  async function moveCandidate(candidate) {
    state.busyCandidateId = candidate.id;
    renderApp();
    try {
      await api(`/api/candidates/${candidate.id}/advance`, {
        method: "POST",
        body: JSON.stringify({ expected_stage: candidate.current_stage }),
      });
      await refreshAfterChange();
      if (state.selectedCandidate?.id === candidate.id) await openCandidate(candidate.id);
      toast(`${candidate.full_name} moved to ${NEXT_STAGE[candidate.current_stage]}.`);
    } catch (err) {
      toast(err.message, true);
      await refreshAfterChange();
      if (state.selectedCandidate?.id === candidate.id) await openCandidate(candidate.id);
    } finally {
      state.busyCandidateId = null;
      renderApp();
    }
  }

  function closeModal() {
    state.modal = null;
    renderApp();
  }

  async function refreshAfterChange() {
    await loadCandidates();
    if (state.searchActive && state.query.trim()) await runSearch();
  }

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch (err) {
      toast(err.message, true);
    } finally {
      state.recruiter = null;
      state.query = "";
      state.searchActive = false;
      state.searchResults = null;
      renderLogin();
    }
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (state.modal) closeModal();
      else if (state.selectedCandidate || state.selectedLoading) closeDrawer();
    }
  });

  checkSession();
})();
