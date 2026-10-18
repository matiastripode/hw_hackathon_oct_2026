const dialog = document.querySelector("#video-dialog");
const container = document.querySelector("#video-embed");
const videoTemplate = document.querySelector("#video-template");

function openVideo() {
  dialog.showModal();
  container.replaceChildren(videoTemplate.content.cloneNode(true));

  if (window.twttr?.widgets) {
    window.twttr.widgets.load(container);
  } else if (!document.querySelector("#x-video-widgets")) {
    const script = document.createElement("script");
    script.id = "x-video-widgets";
    script.src = "https://platform.twitter.com/widgets.js";
    script.onload = () => {
      if (dialog.open) window.twttr?.widgets?.load(container);
    };
    document.head.append(script);
  }
}

document.querySelector("#close-video").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});
dialog.addEventListener("close", () => container.replaceChildren());

if (dialog.dataset.autoOpen === "true") openVideo();
