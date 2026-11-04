/*
 * Recognition kiosk.
 *
 * - The browser owns the camera (getUserMedia) and sends one downscaled JPEG frame at
 *   a time. A new frame is captured only after the previous response arrives, so frames
 *   never queue up when the server is slower than the camera (backpressure).
 * - The server answers with a state. For a "candidate", scanning pauses until the staff
 *   member confirms or rejects; confirmation sends the server-signed token back.
 * - Camera tracks are stopped on Stop, on errors, and when the page is hidden or left.
 */
(function () {
  "use strict";

  const root = document.getElementById("kiosk");
  if (!root) return;

  const MAX_WIDTH = 640;
  const REQUEST_TIMEOUT_MS = 15000;
  const RESUME_AFTER_RESULT_MS = 2500;
  const config = {
    recognizeUrl: root.dataset.recognizeUrl,
    confirmUrl: root.dataset.confirmUrl,
    csrfToken: root.dataset.csrfToken,
    minIntervalMs: Math.max(250, parseInt(root.dataset.minIntervalMs, 10) || 0),
    maxFrameBytes: parseInt(root.dataset.maxFrameBytes, 10) || 1500000,
  };

  const el = {
    video: document.getElementById("kiosk-video"),
    placeholder: document.getElementById("kiosk-placeholder"),
    camera: document.getElementById("kiosk-camera"),
    start: document.getElementById("kiosk-start"),
    stop: document.getElementById("kiosk-stop"),
    status: document.getElementById("kiosk-status"),
    candidate: document.getElementById("kiosk-candidate"),
    photo: document.getElementById("candidate-photo"),
    name: document.getElementById("candidate-name"),
    reg: document.getElementById("candidate-reg"),
    klass: document.getElementById("candidate-class"),
    today: document.getElementById("candidate-today"),
    distance: document.getElementById("candidate-distance"),
    threshold: document.getElementById("candidate-threshold"),
    confirm: document.getElementById("candidate-confirm"),
    reject: document.getElementById("candidate-reject"),
  };
  const canvas = document.createElement("canvas");

  const state = {
    stream: null,
    running: false,
    paused: false,
    inFlight: null, // AbortController of the request in progress
    timer: null,
    token: null,
  };
  // Exposed for automated browser tests only (read-only diagnostics).
  window.__kiosk = { state: state, framesSent: 0 };

  function setStatus(message, tone) {
    el.status.textContent = message;
    el.status.dataset.tone = tone || "";
  }

  function mode() {
    const checked = document.querySelector('input[name="kiosk-mode"]:checked');
    return checked ? checked.value : "check_in";
  }

  function modeLabel(value) {
    return value === "check_out" ? "check-out" : "check-in";
  }

  function cameraErrorMessage(error) {
    if (!window.isSecureContext) return "The camera needs HTTPS (or localhost). Ask an administrator to enable HTTPS.";
    switch (error && error.name) {
      case "NotAllowedError":
      case "SecurityError":
        return "Camera permission was denied. Allow camera access in the browser's site settings, then press Start.";
      case "NotFoundError":
      case "OverconstrainedError":
        return "No camera was found. Connect a camera or choose another one.";
      case "NotReadableError":
      case "AbortError":
        return "The camera is in use by another application or tab.";
      default:
        return "The camera could not be started.";
    }
  }

  async function listCameras(activeDeviceId) {
    if (!navigator.mediaDevices.enumerateDevices) return;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    el.camera.replaceChildren();
    devices.forEach((device, index) => {
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || "Camera " + (index + 1);
      option.selected = device.deviceId === activeDeviceId;
      el.camera.appendChild(option);
    });
    el.camera.disabled = devices.length < 2;
  }

  function releaseCamera() {
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    el.video.srcObject = null;
    el.video.hidden = true;
    el.placeholder.hidden = false;
  }

  function stop(message, tone) {
    state.running = false;
    state.paused = false;
    state.token = null;
    clearTimeout(state.timer);
    if (state.inFlight) state.inFlight.abort();
    state.inFlight = null;
    releaseCamera();
    el.candidate.hidden = true;
    el.start.disabled = false;
    el.stop.disabled = true;
    setStatus(message || "Stopped. The camera is off.", tone);
  }

  async function start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus(window.isSecureContext ? "This browser does not support camera access." : cameraErrorMessage(null), "error");
      return;
    }
    el.start.disabled = true;
    setStatus("Starting camera…");
    const deviceId = el.camera.value;
    const constraints = {
      audio: false,
      video: deviceId ? { deviceId: { exact: deviceId } } : { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
    };
    try {
      state.stream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (error) {
      stop(cameraErrorMessage(error), "error");
      return;
    }
    el.video.srcObject = state.stream;
    el.video.hidden = false;
    el.placeholder.hidden = true;
    try {
      await el.video.play();
    } catch (error) {
      /* autoplay of a muted inline video is allowed; ignore */
    }
    const track = state.stream.getVideoTracks()[0];
    await listCameras(track && track.getSettings ? track.getSettings().deviceId : "");
    state.running = true;
    state.paused = false;
    el.stop.disabled = false;
    setStatus("Looking for a face…");
    schedule(0);
  }

  function schedule(delay) {
    clearTimeout(state.timer);
    if (state.running && !state.paused) {
      state.timer = setTimeout(scan, Math.max(delay, 0));
    }
  }

  function captureFrame() {
    const width = el.video.videoWidth;
    const height = el.video.videoHeight;
    if (!width || !height) return Promise.resolve(null);
    const scale = Math.min(1, MAX_WIDTH / width);
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    canvas.getContext("2d").drawImage(el.video, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  }

  async function post(url, body) {
    const controller = new AbortController();
    state.inFlight = controller;
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { "X-CSRFToken": config.csrfToken, "Accept": "application/json" },
        signal: controller.signal,
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = { status: "server_error" };
      }
      return { http: response.status, data: data };
    } finally {
      clearTimeout(timeout);
      if (state.inFlight === controller) state.inFlight = null;
    }
  }

  async function scan() {
    if (!state.running || state.paused || state.inFlight) return;
    const blob = await captureFrame();
    if (!state.running || state.paused) return;
    if (!blob) {
      schedule(config.minIntervalMs);
      return;
    }
    if (blob.size > config.maxFrameBytes) {
      setStatus("The camera image is too large to send. Choose a lower-resolution camera.", "error");
      schedule(config.minIntervalMs * 4);
      return;
    }
    const form = new FormData();
    form.append("frame", blob, "frame.jpg");
    form.append("mode", mode());
    let result;
    try {
      window.__kiosk.framesSent += 1;
      result = await post(config.recognizeUrl, form);
    } catch (error) {
      if (!state.running) return;
      setStatus("Network problem; retrying…", "warn");
      schedule(config.minIntervalMs * 4);
      return;
    }
    if (!state.running) return;
    handleRecognition(result);
  }

  function handleRecognition(result) {
    const data = result.data || {};
    switch (data.status) {
      case "candidate":
        showCandidate(data);
        return;
      case "unsuitable":
        setStatus(data.message, "warn");
        break;
      case "unknown":
      case "ambiguous":
        setStatus(data.message, "warn");
        break;
      case "throttled":
      case "busy":
        schedule(Math.max(config.minIntervalMs, data.retry_after_ms || 0));
        return;
      case "model_unavailable":
        stop(data.message, "error");
        return;
      case "unauthenticated":
      case "forbidden":
        stop(data.message, "error");
        return;
      case "invalid_frame":
      case "frame_too_large":
        setStatus(data.message || "The camera image could not be read.", "warn");
        break;
      default:
        setStatus("Unexpected server response (" + result.http + "); retrying…", "warn");
        schedule(config.minIntervalMs * 4);
        return;
    }
    schedule(config.minIntervalMs);
  }

  function showCandidate(data) {
    const candidate = data.candidate;
    state.paused = true;
    state.token = candidate.token;
    el.name.textContent = candidate.name;
    el.reg.textContent = candidate.registration_number;
    el.klass.textContent = candidate.branch + ", " + candidate.year;
    const parts = [];
    if (candidate.checked_in_at) parts.push("checked in " + candidate.checked_in_at);
    if (candidate.checked_out_at) parts.push("checked out " + candidate.checked_out_at);
    el.today.textContent = parts.length ? parts.join(", ") : "nothing recorded yet";
    el.distance.textContent = Number(candidate.distance).toFixed(3);
    el.threshold.textContent = Number(candidate.threshold).toFixed(3);
    if (candidate.photo_url) {
      el.photo.src = candidate.photo_url;
      el.photo.hidden = false;
    } else {
      el.photo.removeAttribute("src");
      el.photo.hidden = true;
    }
    el.confirm.textContent = "Confirm " + modeLabel(data.mode);
    el.confirm.disabled = false;
    el.candidate.hidden = false;
    setStatus("Possible match found. Check the person, then confirm or reject.", "ok");
    el.confirm.focus();
  }

  function resume(delay) {
    el.candidate.hidden = true;
    state.paused = false;
    state.token = null;
    if (state.running) {
      setTimeout(() => {
        if (state.running && !state.paused) {
          setStatus("Looking for a face…");
          schedule(0);
        }
      }, delay);
    }
  }

  async function confirmCandidate() {
    if (!state.token) return;
    el.confirm.disabled = true;
    const form = new FormData();
    form.append("token", state.token);
    let result;
    try {
      result = await post(config.confirmUrl, form);
    } catch (error) {
      el.confirm.disabled = false;
      setStatus("Network problem; the attendance was not confirmed. Try again.", "error");
      return;
    }
    const data = result.data || {};
    if (data.status === "recorded") {
      const when = data.mode === "check_out" ? data.checked_out_at : data.checked_in_at;
      setStatus(data.name + ": " + modeLabel(data.mode) + " recorded at " + when + ".", "ok");
    } else if (data.status === "already_recorded") {
      setStatus(data.name + ": " + modeLabel(data.mode) + " was already recorded today. Nothing changed.", "warn");
    } else {
      setStatus(data.message || "The attendance was not recorded.", "error");
    }
    resume(RESUME_AFTER_RESULT_MS);
  }

  el.start.addEventListener("click", start);
  el.stop.addEventListener("click", () => stop());
  el.confirm.addEventListener("click", confirmCandidate);
  el.reject.addEventListener("click", () => {
    setStatus("Rejected. Nothing was recorded.", "warn");
    resume(RESUME_AFTER_RESULT_MS);
  });
  el.camera.addEventListener("change", () => {
    if (state.running) {
      stop("Switching camera…");
      start();
    }
  });
  document.querySelectorAll('input[name="kiosk-mode"]').forEach((input) =>
    input.addEventListener("change", () => {
      if (!el.candidate.hidden) resume(0);
    })
  );
  // Release the camera whenever the page is hidden or left.
  window.addEventListener("pagehide", () => stop());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && state.running) {
      stop("Paused because the page was hidden. Press Start to continue.");
    }
  });
})();
