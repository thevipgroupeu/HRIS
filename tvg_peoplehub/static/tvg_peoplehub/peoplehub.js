// THEVIPGROUP PeopleHub — Enterprise Workspace Interactions
(function () {
  "use strict";

  var body = document.body;
  var toggle = document.querySelector("[data-nav-toggle]");
  var backdrop = document.querySelector("[data-nav-backdrop]");

  function setNav(open) {
    body.classList.toggle("nav-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (backdrop) backdrop.hidden = !open;
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      setNav(!body.classList.contains("nav-open"));
    });
  }
  if (backdrop) backdrop.addEventListener("click", function () { setNav(false); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && body.classList.contains("nav-open")) {
      setNav(false);
      if (toggle) toggle.focus();
    }
  });

  // Live Warsaw / CET Digital Clock
  var clockEl = document.querySelector("[data-live-clock]");
  if (clockEl) {
    function updateClock() {
      try {
        var now = new Date();
        var formatter = new Intl.DateTimeFormat("en-GB", {
          timeZone: "Europe/Warsaw",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false
        });
        clockEl.textContent = formatter.format(now) + " CET";
      } catch (err) {
        // fallback
        var d = new Date();
        var h = String(d.getHours()).padStart(2, '0');
        var m = String(d.getMinutes()).padStart(2, '0');
        var s = String(d.getSeconds()).padStart(2, '0');
        clockEl.textContent = h + ":" + m + ":" + s + " CET";
      }
    }
    updateClock();
    setInterval(updateClock, 1000);
  }

  // Dashboard Tab Switcher
  var tabButtons = document.querySelectorAll("[data-tab-target]");
  var sections = document.querySelectorAll("[data-tab-section]");
  if (tabButtons.length > 0) {
    tabButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = btn.getAttribute("data-tab-target");
        tabButtons.forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");

        sections.forEach(function (sec) {
          if (target === "all") {
            sec.style.display = "";
          } else {
            var categories = (sec.getAttribute("data-tab-section") || "").split(" ");
            sec.style.display = categories.indexOf(target) !== -1 ? "" : "none";
          }
        });
      });
    });
  }

  // Live Dashboard Search Filter
  var dashSearchInput = document.querySelector("[data-dash-search]");
  if (dashSearchInput) {
    dashSearchInput.addEventListener("input", function () {
      var query = dashSearchInput.value.trim().toLowerCase();
      var items = document.querySelectorAll("[data-searchable]");
      items.forEach(function (item) {
        var text = item.textContent.toLowerCase();
        var match = !query || text.indexOf(query) !== -1;
        item.style.display = match ? "" : "none";
      });
    });
  }

  // Live filtering for lists marked with data-filter-target (e.g. Directory)
  document.querySelectorAll("[data-filter-input]").forEach(function (input) {
    var target = document.querySelector(input.getAttribute("data-filter-input"));
    var counter = document.querySelector(input.getAttribute("data-filter-count"));
    if (!target) return;
    var items = target.querySelectorAll("[data-filter-item]");
    input.addEventListener("input", function () {
      var query = input.value.trim().toLowerCase();
      var shown = 0;
      items.forEach(function (item) {
        var match = !query || item.textContent.toLowerCase().indexOf(query) !== -1;
        item.hidden = !match;
        if (match) shown += 1;
      });
      if (counter) counter.textContent = shown;
    });
  });
})();
