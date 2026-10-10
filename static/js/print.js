// Print pages: the toolbar's Print button opens the browser's print dialog.
document.querySelector("[data-print]")?.addEventListener("click", () => window.print());
