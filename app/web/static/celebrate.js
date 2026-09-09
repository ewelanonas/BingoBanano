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
  const closeButton = document.getElementById("celebration-close");

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
