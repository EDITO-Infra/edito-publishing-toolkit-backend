/*
 * Lightbox player for screencast links (any <a> whose href ends in .mp4).
 * Clicking a 🎬 link opens the video in a fullscreen overlay with native
 * controls; ✕ Close, ← Back to tutorial, a backdrop click, or Esc closes it.
 * Without JS the links degrade to normal navigation to the raw video file.
 */
(function () {
  var overlay = null;

  function close() {
    if (overlay) {
      overlay.remove();
      overlay = null;
    }
  }

  function open(src) {
    close();
    overlay = document.createElement("div");
    overlay.className = "screencast-lightbox";
    overlay.innerHTML =
      '<button type="button" class="screencast-close" aria-label="Close screencast">✕ Close</button>' +
      '<video controls autoplay playsinline src="' + src + '"></video>' +
      '<a class="screencast-back" href="#">← Back to tutorial</a>';
    document.body.appendChild(overlay);
  }

  document.addEventListener("click", function (e) {
    var link = e.target.closest('a[href$=".mp4"]');
    if (link) {
      e.preventDefault();
      open(link.getAttribute("href"));
      return;
    }
    if (overlay) {
      var clicked =
        e.target === overlay ||
        e.target.closest(".screencast-close") ||
        e.target.closest(".screencast-back");
      // clicks on the video itself keep the player open
      if (clicked) close();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });

  var style = document.createElement("style");
  style.textContent =
    ".screencast-lightbox{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.85);" +
    "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:24px}" +
    ".screencast-lightbox video{max-width:min(960px,95vw);max-height:82vh;background:#000}" +
    ".screencast-lightbox .screencast-close,.screencast-lightbox .screencast-back{font:inherit;color:#fff;" +
    "background:transparent;border:1px solid rgba(255,255,255,.4);border-radius:6px;padding:6px 16px;" +
    "cursor:pointer;text-decoration:none}" +
    ".screencast-lightbox .screencast-close:hover,.screencast-lightbox .screencast-back:hover{border-color:#fff}";
  document.head.appendChild(style);
})();
