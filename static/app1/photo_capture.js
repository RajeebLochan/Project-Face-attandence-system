/*
 * Enrolment photo capture. The captured JPEG is placed into the form's normal file
 * input, so the server validates exactly the same upload whether it came from the
 * camera or from a chosen file. Camera tracks are released after capture, on submit
 * and when the page is left.
 */
(function () {
  "use strict";

  const MAX_SIDE = 1280;

  document.querySelectorAll("[data-photo-capture]").forEach((root) => {
    const video = root.querySelector("[data-video]");
    const preview = root.querySelector("[data-preview]");
    const placeholder = root.querySelector("[data-placeholder]");
    const startButton = root.querySelector("[data-start]");
    const captureButton = root.querySelector("[data-capture]");
    const retakeButton = root.querySelector("[data-retake]");
    const status = root.querySelector("[data-camera-status]");
    const input = root.querySelector('input[type="file"]');
    let stream = null;
    let previewUrl = null;

    function say(message) {
      status.textContent = message;
    }

    function release() {
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        stream = null;
      }
      video.srcObject = null;
      video.hidden = true;
      captureButton.hidden = true;
    }

    function showPreview(blob) {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(blob);
      preview.src = previewUrl;
      preview.hidden = false;
      placeholder.hidden = true;
    }

    function errorMessage(error) {
      if (!window.isSecureContext) return "The camera needs HTTPS (or localhost). Choose a file instead.";
      if (error && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
        return "Camera permission was denied. Allow it in the browser's site settings, or choose a file.";
      }
      if (error && error.name === "NotFoundError") return "No camera was found. Choose a file instead.";
      if (error && error.name === "NotReadableError") return "The camera is in use by another application.";
      return "The camera could not be started. Choose a file instead.";
    }

    async function start() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        say(errorMessage(null));
        return;
      }
      startButton.disabled = true;
      say("Starting camera…");
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: "user" } });
      } catch (error) {
        startButton.disabled = false;
        say(errorMessage(error));
        return;
      }
      video.srcObject = stream;
      video.hidden = false;
      preview.hidden = true;
      placeholder.hidden = true;
      try { await video.play(); } catch (error) { /* ignore */ }
      startButton.hidden = true;
      retakeButton.hidden = true;
      captureButton.hidden = false;
      startButton.disabled = false;
      say("Centre the face in the frame, then press Capture.");
    }

    function capture() {
      const width = video.videoWidth;
      const height = video.videoHeight;
      if (!width || !height) {
        say("The camera is not ready yet.");
        return;
      }
      const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        if (!blob) {
          say("Capture failed. Try again.");
          return;
        }
        const transfer = new DataTransfer();
        transfer.items.add(new File([blob], "capture.jpg", { type: "image/jpeg" }));
        input.files = transfer.files;
        release();
        showPreview(blob);
        retakeButton.hidden = false;
        say("Photo captured. Submit the form, or press Retake.");
      }, "image/jpeg", 0.92);
    }

    startButton.addEventListener("click", start);
    retakeButton.addEventListener("click", start);
    captureButton.addEventListener("click", capture);
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) {
        release();
        showPreview(input.files[0]);
        startButton.hidden = false;
        say("File selected.");
      }
    });
    const form = root.closest("form");
    if (form) form.addEventListener("submit", release);
    window.addEventListener("pagehide", release);
  });
})();
