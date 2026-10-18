(() => {
  const caregiverSelect = document.querySelector("#caregiver");
  if (caregiverSelect) {
    const customField = document.querySelector("#custom-name-field");
    const nameInput = document.querySelector("#name");
    const toggleName = () => {
      const custom = caregiverSelect.value === "other";
      customField.hidden = !custom;
      nameInput.required = custom;
    };
    caregiverSelect.addEventListener("change", toggleName);
    toggleName();
  }

  function localizeTimes(root) {
    root.querySelectorAll("time[data-local-time]").forEach((element) => {
      const date = new Date(element.dateTime);
      if (!Number.isNaN(date.getTime())) {
        element.textContent = new Intl.DateTimeFormat(undefined, {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(date);
      }
    });
  }

  const board = document.querySelector("[data-board-content]");
  if (!board) return;
  localizeTimes(board);

  let refreshing = false;
  let editing = false;
  let editVersion = 0;
  let appliedRevision = Number(board.querySelector("[data-board-revision]").dataset.boardRevision);
  let pendingRevision = appliedRevision;
  const updates = document.querySelector("#board-updates");
  const applyButton = document.querySelector("#apply-board-updates");

  function paused() {
    return editing || board.querySelector("details[open]") ||
      (board.contains(document.activeElement) && document.activeElement.closest("form"));
  }

  function showUpdates() {
    updates.hidden = pendingRevision <= appliedRevision;
  }

  function markEditing(event) {
    if (event.target.closest("form")) {
      editing = true;
      editVersion += 1;
    }
  }

  board.addEventListener("input", markEditing);
  board.addEventListener("change", markEditing);
  board.addEventListener("toggle", () => {
    if (pendingRevision > appliedRevision && !paused()) refreshBoard();
  }, true);
  board.addEventListener("focusout", () => {
    window.setTimeout(() => {
      if (pendingRevision > appliedRevision && !paused()) refreshBoard();
    }, 0);
  });

  const connection = document.querySelector("#connection-status");
  const connectionWrap = connection.parentElement;
  function setConnection(text, offline) {
    connection.textContent = text;
    connectionWrap.classList.toggle("is-offline", offline);
  }

  async function refreshBoard(force = false) {
    if (refreshing || (force && editing &&
        !window.confirm("Discard your unsaved changes and load the latest board?"))) return;
    refreshing = true;
    applyButton.disabled = true;
    const startedAtEdit = editVersion;
    try {
      const response = await fetch("/board/fragment", { cache: "no-store" });
      if (response.redirected && response.url.endsWith("/login")) {
        window.location.assign("/login");
        return;
      }
      if (!response.ok) throw new Error("Refresh failed");
      const html = await response.text();
      const nextBoard = new DOMParser().parseFromString(html, "text/html")
        .querySelector("[data-board-revision]");
      const revision = Number(nextBoard?.getAttribute("data-board-revision"));
      if (!nextBoard || !Number.isSafeInteger(revision)) throw new Error("Invalid board revision");
      pendingRevision = Math.max(pendingRevision, revision);
      if (revision > appliedRevision) {
        // Don't replace a form while someone is writing their shift update.
        if (startedAtEdit === editVersion && (force || !paused())) {
          board.innerHTML = html;
          localizeTimes(board);
          appliedRevision = revision;
          editing = false;
        }
      }
      showUpdates();
    } catch {
      setConnection("Reconnecting - checking every 5 seconds", true);
    } finally {
      refreshing = false;
      applyButton.disabled = false;
    }
  }

  applyButton.addEventListener("click", () => refreshBoard(true));
  window.setInterval(() => refreshBoard(), 5000);
  if (window.EventSource) {
    const stream = new EventSource("/events");
    stream.addEventListener("board", (event) => {
      const revision = Number(event.data);
      if (!Number.isSafeInteger(revision) || revision <= pendingRevision) return;
      pendingRevision = revision;
      if (paused()) showUpdates();
      else refreshBoard();
    });
    stream.onopen = () => setConnection("Board is live", false);
    stream.onerror = () => setConnection("Reconnecting - checking every 5 seconds", true);
  } else {
    setConnection("Checking for updates every 5 seconds", true);
  }
})();
