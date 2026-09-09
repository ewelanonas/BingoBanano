/* Ang BINGO celebration popup.
 *
 * Isang function lang ang inilalabas nito, `showCelebration(name, word)`, para
 * pareho ang hitsura ng panalo sa player, sa caller, at sa elimination.
 *
 * Hindi ito nagdedesisyon ng panalo. Ang server lang ang nagsasabi kung may
 * nanalo; ito ay nagpapakita lang ng sinabi nito.
 */
(() => {
  const overlay = document.getElementById("celebration");
  if (!overlay) { return; }

  const nameEl = document.getElementById("celebration-name");
  const wordEl = document.getElementById("celebration-word");
  const photoEl = overlay.querySelector(".celebration-photo");
  const closeButton = document.getElementById("celebration-close");

  // Ang dalawang litrato ng party. Kada panalo ay isa sa kanila ang lumalabas,
  // kaya hindi nagsasawa ang mga bisita sa maraming round.
  //
  // `Math.random` ang gamit dito at hindi crypto RNG. Palamuti lang ito: walang
  // card, walang bola, at walang panalong nakasalalay dito. Ang mga iyon ay may
  // sariling RNG sa server na sinasadyang naka-audit.
  const PHOTOS = ["/static/img/photo1.png", "/static/img/photo2.png"];

  function pickPhoto() {
    const chosen = PHOTOS[Math.floor(Math.random() * PHOTOS.length)];
    // Custom property, hindi `background-image` nang tuwiran: sa CSS nakalagay
    // ang gradient sa ilalim, kaya kung wala pa ang PNG ay may makikita pa rin.
    photoEl.style.setProperty("--celebration-photo", `url("${chosen}")`);
  }

  // Kung saan huling nakatuon bago bumukas, para may mababalikan pagsara.
  let previousFocus = null;

  function hide() {
    overlay.hidden = true;
    if (previousFocus && previousFocus.isConnected) { previousFocus.focus(); }
    previousFocus = null;
  }

  window.showCelebration = (name, word) => {
    wordEl.textContent = word || "BINGO!";
    nameEl.textContent = name || "";
    pickPhoto();
    previousFocus = document.activeElement;
    overlay.hidden = false;
    closeButton.focus();
  };

  window.hideCelebration = hide;

  closeButton.addEventListener("click", hide);

  // Kahit saan sa labas ng burst ay puwedeng ipindot pansara. Sa party ay hindi
  // hahanapin ng bisita ang maliit na button.
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) { hide(); }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !overlay.hidden) { hide(); }
  });
})();
