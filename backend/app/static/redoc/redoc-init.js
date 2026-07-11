document.addEventListener("DOMContentLoaded", function () {
  Redoc.init("/api/openapi.json", {}, document.getElementById("redoc-container"));
});
