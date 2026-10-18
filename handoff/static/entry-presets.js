document.querySelectorAll(".log-action .preset-select").forEach((preset) => {
  const title = preset.closest("form").querySelector('[name="title"]');
  preset.addEventListener("change", () => {
    if (preset.value) {
      title.value = preset.value;
      title.focus();
    }
  });
});
