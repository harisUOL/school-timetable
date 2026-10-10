// Runs before the stylesheet paints: marks JS as available and applies a saved theme.
(function () {
  var root = document.documentElement;
  root.classList.remove("no-js");
  root.classList.add("js");
  try {
    var theme = localStorage.getItem("theme");
    if (theme === "dark" || theme === "light") root.setAttribute("data-theme", theme);
  } catch (e) {}
})();
