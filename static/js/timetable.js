// Timetable builder: one delegated click handler on the grid and one modal.
//
// Rules are enforced by the server. This module only shows the options the
// server returns and sends back exactly what the person chose: the subject
// and teacher ids always come from an explicit selection (or from the lesson
// being edited), never from a default.

const grid = document.getElementById("timetable-grid");
const dialog = document.getElementById("slot-dialog");
const csrfInput = document.querySelector("input[name=csrfmiddlewaretoken]");

if (grid && dialog && csrfInput) {
  const urls = {
    options: grid.dataset.optionsUrl,
    assign: grid.dataset.assignUrl,
    clear: grid.dataset.clearUrl,
    lock: grid.dataset.lockUrl,
    addActivity: grid.dataset.addActivityUrl,
    removeActivity: grid.dataset.removeActivityUrl,
  };
  const classId = Number(grid.dataset.classId);
  const regions = {
    title: dialog.querySelector("#slot-dialog-title"),
    body: dialog.querySelector("[data-region=body]"),
    errors: dialog.querySelector("[data-region=errors]"),
    footer: dialog.querySelector("[data-region=footer]"),
  };

  const state = {
    slot: null, // {day, period}
    options: null, // server response for the slot
    mode: "view", // "view" a lesson, "edit" (choose subject + teacher), "activity" (add one), "activity-view"
    subjectId: null,
    teacherId: null,
    busy: false,
    busyButton: null, // the button whose request is in flight (shows a spinner)
    opener: null,
  };

  // ---- Small DOM helper (text is always set with textContent) ------------

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === false || value === null || value === undefined) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  // Footer buttons; optional ones may be null and are skipped (replaceChildren would print "null").
  function setFooter(...nodes) {
    regions.footer.replaceChildren(...nodes.filter(Boolean));
  }

  // ---- Server calls -----------------------------------------------------

  async function request(url, options = {}) {
    let response;
    try {
      response = await fetch(url, {
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": csrfInput.value,
        },
        ...options,
      });
    } catch {
      return failure("NETWORK", "Could not reach the server. Check your connection and try again.");
    }
    if (response.redirected) {
      return failure("SESSION", "Your session has expired. Reload the page and sign in again.");
    }
    try {
      return await response.json();
    } catch {
      return failure("SERVER", `The server returned an unexpected error (${response.status}).`);
    }
  }

  function failure(code, message) {
    return { ok: false, violations: [{ code, message, context: {} }] };
  }

  function post(url, payload) {
    return request(url, { method: "POST", body: JSON.stringify({ class_id: classId, ...state.slot, ...payload }) });
  }

  function expectedVersion() {
    const entry = state.options && state.options.entry;
    return entry ? { id: entry.id, updated_at: entry.updated_at } : null;
  }

  // ---- Opening and closing ---------------------------------------------

  grid.addEventListener("click", (event) => {
    const button = event.target.closest("button.slot");
    if (!button || !grid.contains(button) || state.busy) return;
    openSlot(button);
  });

  async function openSlot(button) {
    state.opener = button;
    state.slot = { day: Number(button.dataset.day), period: Number(button.dataset.period) };
    state.options = null;
    state.subjectId = null;
    state.teacherId = null;
    regions.title.textContent = "Loading…";
    regions.body.replaceChildren(el("p", { class: "muted", text: "Loading options…" }));
    regions.footer.replaceChildren(cancelButton());
    showErrors([]);
    if (!dialog.open) dialog.showModal();
    await loadOptions();
  }

  async function loadOptions() {
    const params = new URLSearchParams({ class: classId, day: state.slot.day, period: state.slot.period });
    setBusy(true);
    const data = await request(`${urls.options}?${params}`, { method: "GET" });
    setBusy(false);
    if (!data.ok) {
      showErrors(data.violations);
      return;
    }
    state.options = data;
    state.mode = data.activity ? "activity-view" : data.entry ? "view" : "edit";
    if (data.entry) {
      state.subjectId = data.entry.subject_id;
      state.teacherId = data.entry.teacher_id;
    }
    render();
  }

  function close() {
    if (dialog.open) dialog.close();
  }

  dialog.addEventListener("close", () => {
    const { day, period } = state.slot || {};
    const target =
      (day !== undefined && grid.querySelector(`td[data-slot="${day}-${period}"] button.slot`)) || state.opener;
    if (target) target.focus();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target.closest("[data-action=cancel]")) close();
  });

  // Keep Tab inside the modal.
  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button, input, select, [href], [tabindex]:not([tabindex='-1'])")].filter(
      (node) => !node.disabled && node.offsetParent !== null
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      last.focus();
      event.preventDefault();
    } else if (!event.shiftKey && document.activeElement === last) {
      first.focus();
      event.preventDefault();
    }
  });

  // ---- Rendering ---------------------------------------------------------

  function render() {
    const o = state.options;
    const time = o.period.time ? ` (${o.period.time})` : "";
    regions.title.textContent = `${o.class.label} · ${o.day.label}, period ${o.period.roman}${time}`;
    showErrors([]);
    if (state.mode === "activity-view") renderActivity();
    else if (state.mode === "activity") renderActivityForm();
    else if (state.mode === "view") renderLesson();
    else renderPicker();
  }

  function renderLesson() {
    const entry = state.options.entry;
    regions.body.replaceChildren(
      el(
        "dl",
        { class: "lesson-summary" },
        el("dt", { text: "Subject" }),
        el("dd", { text: entry.subject }),
        el("dt", { text: "Teacher" }),
        el("dd", { text: entry.teacher }),
        el("dt", { text: "Status" }),
        el("dd", { text: entry.is_locked ? "Locked (kept by the generator)" : "Not locked" })
      )
    );
    setFooter(
      el("button", { type: "button", class: "btn btn-danger", onclick: removeLesson, text: "Remove" }),
      el("button", {
        type: "button",
        class: "btn",
        onclick: toggleLock,
        text: entry.is_locked ? "Unlock" : "Lock",
      }),
      activityButton("Replace with an activity"),
      el("span", { class: "spacer" }),
      cancelButton(),
      el("button", {
        type: "button",
        class: "btn btn-primary",
        onclick: () => {
          state.mode = "edit";
          render();
          focusFirst("input[name=subject]:checked, input[name=subject]:not(:disabled)");
        },
        text: "Change",
      })
    );
    regions.footer.querySelector(".btn-primary").focus();
  }

  function renderPicker() {
    const o = state.options;
    const subjects = o.subjects;
    if (!subjects.length) {
      regions.body.replaceChildren(
        el("p", { text: `${o.class.label} has no subject requirements. Add them in the admin first.` })
      );
      setFooter(activityButton("Add an activity instead"), el("span", { class: "spacer" }), cancelButton());
      return;
    }

    const subjectList = el(
      "fieldset",
      { class: "choice-list" },
      el("legend", { text: "Subject" }),
      subjects.map((subject) => {
        const isCurrent = o.entry && o.entry.subject_id === subject.id;
        const disabled = subject.met && !isCurrent;
        const id = `subject-${subject.id}`;
        return el(
          "label",
          { class: `choice${disabled ? " is-disabled" : ""}`, for: id },
          el("input", {
            type: "radio",
            name: "subject",
            id,
            value: subject.id,
            checked: state.subjectId === subject.id,
            disabled,
            onchange: () => chooseSubject(subject.id),
          }),
          el("span", { class: "choice-main", text: subject.name }),
          el("span", { class: "choice-meta", text: `${subject.assigned}/${subject.required}` }),
          disabled ? el("span", { class: "choice-reason", text: subject.disabled_reason }) : null
        );
      })
    );

    regions.body.replaceChildren(
      el("div", { class: "picker" }, subjectList, el("div", { class: "teacher-region", "data-region": "teachers" }))
    );
    renderTeachers();

    const confirmLabel = o.entry ? "Save change" : "Assign lesson";
    setFooter(
      o.entry
        ? el("button", {
            type: "button",
            class: "btn",
            onclick: () => {
              state.mode = "view";
              state.subjectId = o.entry.subject_id;
              state.teacherId = o.entry.teacher_id;
              render();
            },
            text: "Back",
          })
        : activityButton("Add an activity instead"),
      el("span", { class: "spacer" }),
      cancelButton(),
      el("button", { type: "button", class: "btn btn-primary", "data-action": "confirm", onclick: assignLesson, text: confirmLabel })
    );
    updateConfirm();
  }

  function currentSubject() {
    if (!state.options) return null;
    return state.options.subjects.find((s) => s.id === state.subjectId) || null;
  }

  function chooseSubject(subjectId) {
    state.subjectId = subjectId;
    const subject = currentSubject();
    const entry = state.options.entry;
    // Pre-select the lesson's own teacher when editing it, otherwise the designated teacher if available.
    const keep = entry && entry.subject_id === subjectId ? entry.teacher_id : subject.designated_teacher_id;
    const option = subject.teachers.find((t) => t.id === keep && t.status === "available");
    state.teacherId = option ? option.id : null;
    renderTeachers();
    showErrors([]);
    updateConfirm();
  }

  function renderTeachers() {
    const region = regions.body.querySelector("[data-region=teachers]");
    const subject = currentSubject();
    if (!subject) {
      region.replaceChildren(el("p", { class: "muted", text: "Choose a subject to see its teachers." }));
      return;
    }
    if (!subject.teachers.length) {
      region.replaceChildren(
        el("p", { class: "alert alert-warning", text: `No active teacher is qualified to teach ${subject.name}.` })
      );
      return;
    }
    const list = el(
      "fieldset",
      { class: "choice-list" },
      el("legend", { text: `Teacher for ${subject.name}` }),
      subject.teachers.map((teacher) => {
        const blocked = teacher.status !== "available";
        const id = `teacher-${teacher.id}`;
        const day = state.options.day.label.slice(0, 3);
        return el(
          "label",
          { class: `choice${blocked ? " is-disabled" : ""}`, for: id },
          el("input", {
            type: "radio",
            name: "teacher",
            id,
            value: teacher.id,
            checked: state.teacherId === teacher.id,
            disabled: blocked,
            onchange: () => {
              state.teacherId = teacher.id;
              updateConfirm();
            },
          }),
          el(
            "span",
            { class: "choice-main" },
            teacher.name,
            teacher.designated ? el("span", { class: "badge badge-info", text: "Designated" }) : null,
            teacher.is_cover ? el("span", { class: "badge", text: "Cover" }) : null
          ),
          el(
            "span",
            { class: "choice-meta" },
            el("span", { title: "Lessons that day / daily maximum", text: `${day} ${teacher.day_load.assigned}/${teacher.day_load.max}` }),
            " · ",
            el("span", { title: "Lessons this week / weekly maximum", text: `Week ${teacher.week_load.assigned}/${teacher.week_load.max}` })
          ),
          blocked
            ? el("span", { class: "choice-reason" }, teacher.reasons.map((r) => r.message).join(" "))
            : el("span", { class: "badge badge-ok", text: "Available" })
        );
      })
    );
    region.replaceChildren(list, el("div", { class: "picker-warning", "data-region": "warning", "aria-live": "polite" }));
  }

  function updateConfirm() {
    const confirm = regions.footer.querySelector("[data-action=confirm]");
    const subject = currentSubject();
    if (confirm) confirm.disabled = state.busy || !subject || !state.teacherId;
    const warning = regions.body.querySelector("[data-region=warning]");
    if (!warning) return;
    warning.replaceChildren();
    if (subject && state.teacherId && subject.designated_teacher_id && subject.designated_teacher_id !== state.teacherId) {
      warning.append(
        el("p", {
          class: "alert alert-warning",
          text: `${subject.designated_teacher} is the designated ${subject.name} teacher for ${state.options.class.label}. You can still choose someone else.`,
        })
      );
    }
  }

  // ---- Activities (assembly, activity session: not a lesson) -------------

  function activityButton(text) {
    return el("button", {
      type: "button",
      class: "btn",
      onclick: () => {
        state.mode = "activity";
        render();
        focusFirst("input[name=activity-label]");
      },
      text,
    });
  }

  function renderActivity() {
    const a = state.options.activity;
    regions.body.replaceChildren(
      el(
        "dl",
        { class: "lesson-summary" },
        el("dt", { text: "Activity" }),
        el("dd", { text: a.label }),
        el("dt", { text: "Takes" }),
        el("dd", { text: `${state.options.day.label}, ${a.span}` }),
        el("dt", { text: "Added for" }),
        el("dd", { text: a.classes === 1 ? state.options.class.label : `${a.classes} classes` })
      ),
      el("p", { class: "muted", text: "No lesson can be placed here while the activity is in the timetable." })
    );
    setFooter(
      el("button", {
        type: "button",
        class: "btn btn-danger",
        onclick: (event) => submit(urls.removeActivity, { everywhere: false }, event),
        text: a.classes === 1 ? "Remove" : `Remove from ${state.options.class.label}`,
      }),
      a.classes > 1
        ? el("button", {
            type: "button",
            class: "btn btn-danger",
            onclick: (event) => submit(urls.removeActivity, { everywhere: true }, event),
            text: `Remove from all ${a.classes} classes`,
          })
        : null,
      el("span", { class: "spacer" }),
      cancelButton()
    );
  }

  function renderActivityForm() {
    const o = state.options;
    const form = o.activity_form;
    const roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];
    const lengths = Array.from({ length: form.max_length }, (_, i) => {
      const last = o.period.number + i;
      const span = i ? `periods ${roman[o.period.number - 1]}–${roman[last - 1]}` : `period ${roman[last - 1]}`;
      return el("option", { value: i + 1, text: `${i + 1} period${i ? "s" : ""} (${span})` });
    });
    regions.body.replaceChildren(
      el(
        "div",
        { class: "activity-form" },
        el("p", {
          class: "muted",
          text: "For something that is not a lesson, such as an assembly or an activity session. It takes the periods you choose, and no lesson can be placed there.",
        }),
        el(
          "div",
          { class: "field" },
          el("label", { for: "activity-label", text: "Name" }),
          el("input", { type: "text", id: "activity-label", name: "activity-label", maxlength: 80, placeholder: "e.g. Activity session", required: true })
        ),
        el("div", { class: "field" }, el("label", { for: "activity-length", text: "Length" }), el("select", { id: "activity-length" }, lengths)),
        el(
          "fieldset",
          { class: "choice-list" },
          el("legend", { text: "For" }),
          form.scopes.map((scope, i) =>
            el(
              "label",
              { class: "choice", for: `activity-scope-${scope.value}` },
              el("input", { type: "radio", name: "activity-scope", id: `activity-scope-${scope.value}`, value: scope.value, checked: i === 0 }),
              el("span", { class: "choice-main", text: scope.label })
            )
          )
        ),
        el(
          "label",
          { class: "check", for: "activity-clear" },
          el("input", { type: "checkbox", id: "activity-clear", checked: Boolean(o.entry) }),
          el("span", { text: "Remove the lessons in those periods. They become unplaced again, and the generator can put them somewhere else." })
        )
      )
    );
    setFooter(
      el("button", {
        type: "button",
        class: "btn",
        onclick: () => {
          state.mode = o.entry ? "view" : "edit";
          render();
        },
        text: "Back",
      }),
      el("span", { class: "spacer" }),
      cancelButton(),
      el("button", { type: "button", class: "btn btn-primary", onclick: addActivity, text: "Add activity" })
    );
  }

  function addActivity(event) {
    const body = regions.body;
    const label = body.querySelector("#activity-label").value.trim();
    if (!label) {
      showErrors([{ code: "ACTIVITY_NAME", message: "Give the activity a name." }]);
      body.querySelector("#activity-label").focus();
      return;
    }
    return submit(
      urls.addActivity,
      {
        label,
        length: Number(body.querySelector("#activity-length").value),
        scope: body.querySelector("input[name=activity-scope]:checked").value,
        clear_lessons: body.querySelector("#activity-clear").checked,
      },
      event
    );
  }

  function cancelButton() {
    return el("button", { type: "button", class: "btn", "data-action": "cancel", text: "Cancel" });
  }

  function focusFirst(selector) {
    const node = regions.body.querySelector(selector);
    if (node) node.focus();
  }

  function setBusy(busy) {
    state.busy = busy;
    dialog.setAttribute("aria-busy", busy ? "true" : "false");
    grid.setAttribute("aria-busy", busy ? "true" : "false");
    for (const button of regions.footer.querySelectorAll("button")) {
      if (button.dataset.action === "cancel") continue;
      button.disabled = busy;
      button.classList.toggle("is-loading", busy && button === state.busyButton);
    }
    if (!busy) state.busyButton = null;
    if (!busy) updateConfirm();
  }

  function showErrors(violations) {
    regions.errors.replaceChildren(
      ...violations.map((violation) => el("p", { class: "alert alert-error", text: violation.message }))
    );
    if (violations.some((v) => v.code === "STALE_SLOT")) {
      regions.errors.append(
        el("button", {
          type: "button",
          class: "btn btn-small",
          onclick: async () => {
            await loadOptions();
          },
          text: "Reload this slot",
        })
      );
    }
  }

  // ---- Actions -----------------------------------------------------------

  async function submit(url, payload, event) {
    state.busyButton = event ? event.currentTarget : null;
    setBusy(true);
    const data = await post(url, payload);
    setBusy(false);
    if (!data.ok) {
      showErrors(data.violations);
      return;
    }
    if (data.reload) {
      // An activity can change many cells and classes: show the page afresh (its message comes with it).
      window.location.reload();
      return;
    }
    applyResult(data);
    close();
  }

  function assignLesson(event) {
    if (!state.subjectId || !state.teacherId) return;
    return submit(
      urls.assign,
      { subject_id: state.subjectId, teacher_id: state.teacherId, expected: expectedVersion() },
      event
    );
  }

  function removeLesson(event) {
    return submit(urls.clear, { expected: expectedVersion() }, event);
  }

  function toggleLock(event) {
    return submit(urls.lock, { locked: !state.options.entry.is_locked, expected: expectedVersion() }, event);
  }

  function applyResult(data) {
    const { day, period } = state.slot;
    const cell = grid.querySelector(`td[data-slot="${day}-${period}"]`);
    if (cell) cell.outerHTML = data.cell_html;
    const panel = document.getElementById("progress-panel");
    if (panel) panel.innerHTML = data.progress_html;
    for (const [key, value] of Object.entries(data.stats)) {
      const node = document.querySelector(`[data-stat="${key}"]`);
      if (node) node.textContent = value;
    }
    toast(data.message, "success");
    for (const warning of data.warnings) toast(warning.message, "warning");
  }

  // Toasts are shown (and announced through aria-live) by app.js.
  function toast(message, kind) {
    document.dispatchEvent(new CustomEvent("app:toast", { detail: { message, kind } }));
  }
}
