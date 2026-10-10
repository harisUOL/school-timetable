// Shared page behaviour: theme toggle, mobile navigation, toasts and busy buttons.

const root = document.documentElement;

// ---- Theme ----------------------------------------------------------------

const themeToggle = document.getElementById("theme-toggle");
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

function effectiveTheme() {
  return root.getAttribute("data-theme") || (systemDark.matches ? "dark" : "light");
}

function syncThemeButton() {
  if (!themeToggle) return;
  const dark = effectiveTheme() === "dark";
  themeToggle.setAttribute("aria-pressed", String(dark));
  themeToggle.textContent = dark ? "Light theme" : "Dark theme";
}

themeToggle?.addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try {
    localStorage.setItem("theme", next);
  } catch {}
  syncThemeButton();
});
systemDark.addEventListener?.("change", syncThemeButton);
syncThemeButton();

// ---- Mobile navigation ------------------------------------------------------

const navToggle = document.getElementById("nav-toggle");
navToggle?.addEventListener("click", () => {
  const open = navToggle.getAttribute("aria-expanded") !== "true";
  navToggle.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("nav-open", open);
});

// ---- Toasts -------------------------------------------------------------------

const AUTO_DISMISS_MS = 6000;
const regions = {
  polite: document.querySelector("[data-toast-region=polite]"),
  assertive: document.querySelector("[data-toast-region=assertive]"),
};

function dismiss(toast) {
  toast.classList.add("is-leaving");
  setTimeout(() => toast.remove(), 200);
}

function arm(toast) {
  toast.querySelector(".toast-close")?.addEventListener("click", () => dismiss(toast));
  // Errors stay until dismissed; everything else fades after a while.
  if (!toast.classList.contains("toast-error")) {
    let timer = setTimeout(() => dismiss(toast), AUTO_DISMISS_MS);
    toast.addEventListener("mouseenter", () => clearTimeout(timer));
    toast.addEventListener("focusin", () => clearTimeout(timer));
    toast.addEventListener("mouseleave", () => (timer = setTimeout(() => dismiss(toast), AUTO_DISMISS_MS)));
  }
}

export function showToast(message, kind = "info") {
  const region = kind === "error" ? regions.assertive : regions.polite;
  if (!region || !message) return;
  const toast = document.createElement("div");
  toast.className = `toast toast-${kind}`;
  const text = document.createElement("span");
  text.className = "toast-message";
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss");
  close.textContent = "×";
  toast.append(text, close);
  region.append(toast);
  arm(toast);
}

document.querySelectorAll(".toast").forEach(arm);
document.addEventListener("app:toast", (event) => showToast(event.detail.message, event.detail.kind));

// ---- Busy state for ordinary forms ---------------------------------------------
// While a POST form is submitting, its submit buttons are disabled and show a
// spinner, so a second click cannot send it twice.

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== "post" || "noBusy" in form.dataset) return;
  if (form.dataset.submitting) {
    event.preventDefault();
    return;
  }
  // A submit button with data-confirm asks first (e.g. deleting a timing group).
  const question = event.submitter?.dataset.confirm;
  if (question && !window.confirm(question)) {
    event.preventDefault();
    return;
  }
  form.dataset.submitting = "true";
  form.setAttribute("aria-busy", "true");
  const submitter = event.submitter;
  // Disable after the browser has collected the form data (including the submitter).
  setTimeout(() => {
    form.querySelectorAll("button[type=submit], input[type=submit]").forEach((button) => {
      button.disabled = true;
      if (button === submitter) button.classList.add("is-loading");
    });
  });
});

// Restore forms when the page is shown again from the back/forward cache.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  document.querySelectorAll("form[data-submitting]").forEach((form) => {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
    form.querySelectorAll("button, input[type=submit]").forEach((b) => {
      b.disabled = false;
      b.classList.remove("is-loading");
    });
  });
});
