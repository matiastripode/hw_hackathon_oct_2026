const caregiver = document.querySelector("#caregiver");
const password = document.querySelector("#password");
const openBoard = document.querySelector("#open-board");

function updateButton() {
  openBoard.disabled = !caregiver.value || password.value.length < 6;
}

caregiver.addEventListener("change", updateButton);
password.addEventListener("input", updateButton);
window.addEventListener("pageshow", updateButton);
updateButton();
