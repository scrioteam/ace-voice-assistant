(function () {
  const transcript = document.getElementById("transcript");
  const productStrip = document.getElementById("product-strip");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const connectionStatus = document.getElementById("connection-status");
  const voiceStatus = document.getElementById("voice-status");
  const micButton = document.getElementById("mic-button");
  const assistantPanel = document.getElementById("assistant-panel");
  const assistantLauncher = document.getElementById("assistant-launcher");
  const assistantMinimize = document.getElementById("assistant-minimize");
  const assistantClose = document.getElementById("assistant-close");
  const sessionId = localStorage.getItem("ace_demo_session") || crypto.randomUUID();
  localStorage.setItem("ace_demo_session", sessionId);

  let pc = null;
  let dc = null;
  let localStream = null;
  let remoteAudio = null;
  let connected = false;
  let assistantBuffer = "";
  let userBuffer = "";
  let speechRecognition = null;
  let recognizing = false;
  let ttsVoice = null;
  let mediaRecorder = null;
  let recordingStream = null;
  let recordedChunks = [];
  let recording = false;
  let recordingTimer = null;
  let realtimeFailedOnce = false;
  const genericVoiceRequestLabel = "בקשה קולית התקבלה בעברית";

  function money(value) {
    const amount = Number(value || 0);
    return new Intl.NumberFormat("he-IL", {
      style: "currency",
      currency: "ILS",
      maximumFractionDigits: amount % 1 ? 2 : 0,
    }).format(amount);
  }

  function setStatus(text, busy) {
    connectionStatus.textContent = text;
    connectionStatus.classList.toggle("busy", Boolean(busy));
  }

  function addMessage(role, text) {
    const value = String(text || "").trim();
    if (!value) return;
    const node = document.createElement("div");
    node.className = "msg " + role;
    node.lang = "he-IL";
    node.dir = "rtl";
    node.textContent = value;
    transcript.appendChild(node);
    while (transcript.children.length > 10) transcript.removeChild(transcript.firstElementChild);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function containsHebrewText(text) {
    return /[\u0590-\u05ff]/.test(String(text || ""));
  }

  function hasStrongForeignScript(text) {
    const value = String(text || "");
    return /[\u0400-\u04ff\u0600-\u06ff]/.test(value);
  }

  function canDisplayUserTranscript(text) {
    const value = String(text || "").trim();
    if (!value) return false;
    if (hasStrongForeignScript(value)) return false;
    if (containsHebrewText(value)) return true;
    return /^[\d\s.,!?'"()₪%+\-/:A-Za-z]+$/.test(value);
  }

  function safeUserTranscript(text) {
    const value = String(text || "").trim();
    return canDisplayUserTranscript(value) ? value : genericVoiceRequestLabel;
  }

  function addUserTranscript(text) {
    const value = String(text || "").trim();
    if (!value) return;
    addMessage("user", safeUserTranscript(value));
  }

  function renderProducts(products) {
    const list = Array.isArray(products) ? products : [];
    productStrip.hidden = list.length === 0;
    productStrip.innerHTML = "";
    list.slice(0, 3).forEach((product) => {
      const card = document.createElement("a");
      card.className = "mini-product";
      card.href = product.url;
      card.target = "_blank";
      card.rel = "noopener noreferrer";
      card.innerHTML = `
        <img alt="" src="${product.image}">
        <div>
          <strong>${escapeHtml(product.title)}</strong>
          <span>${money(product.price)}</span>
        </div>
      `;
      productStrip.appendChild(card);
    });
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
    return data;
  }

  function getSpeechRecognitionConstructor() {
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  function initTTS() {
    if (!window.speechSynthesis) return;
    const voices = window.speechSynthesis.getVoices();
    ttsVoice = voices.find((voice) => voice.lang && voice.lang.toLowerCase().startsWith("he"))
      || voices.find((voice) => /carmit|hebrew|he-il|he_/i.test(voice.name || ""))
      || voices[0]
      || null;
  }

  function speakAssistant(text) {
    if (!window.speechSynthesis) {
      voiceStatus.textContent = "זוהה ונשלח ליועץ";
      setMicState("idle");
      return;
    }
    const value = String(text || "").trim();
    if (!value) return;
    window.speechSynthesis.cancel();
    if (!ttsVoice) initTTS();
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = "he-IL";
    utterance.rate = 1.03;
    utterance.pitch = 1;
    if (ttsVoice) utterance.voice = ttsVoice;
    utterance.onstart = () => {
      voiceStatus.textContent = "עונה בקול";
      setMicState("speaking");
    };
    utterance.onend = () => {
      voiceStatus.textContent = "לחץ שוב ודבר בעברית";
      if (!connected && !recognizing) setMicState("idle");
    };
    utterance.onerror = () => {
      voiceStatus.textContent = "התשובה הוצגה בכתב";
      if (!connected && !recognizing) setMicState("idle");
    };
    window.speechSynthesis.speak(utterance);
  }

  function displayTextForInput(text, options) {
    return options && options.voice ? safeUserTranscript(text) : String(text || "").trim();
  }

  async function sendDemoText(text, options) {
    const value = String(text || "").trim();
    if (!value) return;
    const shouldSpeak = Boolean(options && options.speak);
    addMessage("user", displayTextForInput(value, options));
    chatInput.value = "";
    setStatus("מעבד בקשה", true);
    try {
      const data = await postJson("/api/demo/chat", { session_id: sessionId, text: value });
      addMessage("assistant", data.message);
      renderProducts(data.products);
      if (shouldSpeak) speakAssistant(data.message);
      if (data.screen) {
        setStatus("מוצג: " + data.screen.location_label, false);
      } else {
        setStatus("דמו מוכן", false);
      }
    } catch (error) {
      const message = "לא הצלחתי להשלים את הפעולה: " + error.message;
      addMessage("assistant", message);
      if (shouldSpeak) speakAssistant(message);
      setStatus("נדרשת בדיקה", true);
    }
  }

  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendDemoText(chatInput.value);
  });

  document.querySelectorAll("[data-demo]").forEach((button) => {
    button.addEventListener("click", () => sendDemoText(button.dataset.demo));
  });

  function setAssistantPanelState(state) {
    const value = state === "closed" || state === "minimized" ? state : "open";
    assistantPanel.classList.toggle("is-minimized", value === "minimized");
    assistantPanel.classList.toggle("is-hidden", value === "closed");
    assistantPanel.setAttribute("aria-expanded", value === "open" ? "true" : "false");
    assistantPanel.dataset.panelState = value;
    assistantLauncher.hidden = value !== "closed";
    assistantLauncher.setAttribute(
      "aria-label",
      value === "minimized" ? "פתיחת יועץ המכירות הממוזער" : "פתיחת יועץ המכירות"
    );
    const launcherText = assistantLauncher.querySelector("span");
    if (launcherText) launcherText.textContent = value === "minimized" ? "פתח" : "ACE";
    assistantMinimize.textContent = value === "minimized" ? "פתח" : "מזער";
    assistantMinimize.setAttribute("aria-label", value === "minimized" ? "פתיחת היועץ" : "מזעור היועץ");
    localStorage.setItem("ace_assistant_panel_state", value);
  }

  assistantPanel.querySelector(".panel-header").addEventListener("click", (event) => {
    if (!assistantPanel.classList.contains("is-minimized")) return;
    if (event.target.closest("button")) return;
    setAssistantPanelState("open");
  });

  assistantMinimize.addEventListener("click", () => {
    const next = assistantPanel.classList.contains("is-minimized") ? "open" : "minimized";
    setAssistantPanelState(next);
  });

  assistantClose.addEventListener("click", () => {
    stopVoice();
    if (recording) stopPushToTalkRecording();
    setAssistantPanelState("closed");
  });

  assistantLauncher.addEventListener("click", () => setAssistantPanelState("open"));

  function setMicState(state) {
    micButton.classList.toggle("listening", state === "listening");
    micButton.classList.toggle("speaking", state === "speaking");
  }

  function sendTool(callId, output) {
    if (!dc || dc.readyState !== "open") return;
    dc.send(JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(output),
      },
    }));
    dc.send(JSON.stringify({ type: "response.create" }));
  }

  async function handleFunctionCall(item) {
    const id = item.call_id;
    const name = item.name;
    const args = item.arguments ? JSON.parse(item.arguments) : {};
    try {
      if (name === "search_products") {
        const params = new URLSearchParams();
        if (args.query) params.set("q", args.query);
        if (args.category) params.set("category", args.category);
        if (args.min_price != null) params.set("min_price", args.min_price);
        if (args.max_price != null) params.set("max_price", args.max_price);
        if (args.limit != null) params.set("limit", args.limit);
        if (args.page != null) params.set("page", args.page);
        params.set("include_meta", "true");
        params.set("live_only", "true");
        const data = await fetch("/api/products/search?" + params.toString()).then((r) => r.json());
        const products = Array.isArray(data) ? data : (data.products || []);
        renderProducts(products);
        sendTool(id, {
          products,
          found: products.length,
          source: data.source || "unknown",
          fallback_used: Boolean(data.fallback_used),
          live_only: true,
          page: data.page || (args.page || 1),
        });
        return;
      }
      if (name === "browse_products") {
        const params = new URLSearchParams();
        if (args.query) params.set("q", args.query);
        if (args.limit != null) params.set("limit", args.limit);
        if (args.page != null) params.set("page", args.page);
        params.set("include_meta", "true");
        const data = await fetch("/api/products/browse?" + params.toString()).then((r) => r.json());
        const products = Array.isArray(data) ? data : (data.products || []);
        renderProducts(products);
        sendTool(id, {
          products,
          found: products.length,
          source: data.source || "live_sitemap",
          fallback_used: Boolean(data.fallback_used),
          live_only: true,
          page: data.page || (args.page || 1),
          total: data.total,
        });
        return;
      }
      if (name === "get_catalog_position") {
        const position = args.position || 1;
        const response = await fetch("/api/products/catalog-position/" + encodeURIComponent(position) + "?include_meta=true");
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          sendTool(id, {
            position,
            found: false,
            source: "live_sitemap",
            fallback_used: false,
            live_only: true,
            error: data.detail || response.statusText,
          });
          return;
        }
        const product = data.product || data;
        renderProducts([product]);
        sendTool(id, {
          product,
          found: true,
          source: data.source || "live_sitemap",
          fallback_used: Boolean(data.fallback_used),
          live_only: true,
          position: data.position || position,
          total: data.total,
        });
        return;
      }
      if (name === "get_product_details") {
        const response = await fetch("/api/products/" + encodeURIComponent(args.sku) + "?include_meta=true&live_only=true");
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          sendTool(id, {
            sku: args.sku,
            found: false,
            source: "live",
            fallback_used: false,
            live_only: true,
            error: data.detail || response.statusText,
          });
          return;
        }
        const product = data.product || data;
        renderProducts([product]);
        sendTool(id, {
          ...product,
          source: data.source || "unknown",
          fallback_used: Boolean(data.fallback_used),
          live_only: true,
        });
        return;
      }
      if (name === "get_screen_status") {
        const status = await fetch("/api/screens").then((r) => r.json());
        sendTool(id, status);
        return;
      }
      if (name === "show_on_screen") {
        const result = await postJson("/api/show", {
          session_id: args.session_id || sessionId,
          product_ids: args.product_ids || [],
          headline: args.headline,
          message: args.message,
          mode: args.mode,
          department: args.department,
          live_only: true,
        });
        renderProducts(result.products);
        setStatus("מוצג: " + result.screen.location_label, false);
        sendTool(id, result);
        return;
      }
      if (name === "answer_store_policy") {
        const data = await fetch("/api/policy?topic=" + encodeURIComponent(args.topic || "")).then((r) => r.json());
        sendTool(id, data);
        return;
      }
      sendTool(id, { error: "unknown tool: " + name });
    } catch (error) {
      sendTool(id, { error: error.message });
    }
  }

  function processRealtimeEvent(event) {
    if (["response.output_audio_transcript.delta", "response.output_text.delta", "response.audio_transcript.delta", "response.text.delta"].includes(event.type)) {
      assistantBuffer += event.delta || event.text || "";
      setMicState("speaking");
    }
    if (["response.output_audio_transcript.done", "response.output_text.done", "response.audio_transcript.done", "response.text.done"].includes(event.type)) {
      addMessage("assistant", assistantBuffer);
      assistantBuffer = "";
    }
    if (event.type === "conversation.item.input_audio_transcription.delta") {
      userBuffer += event.delta || "";
    }
    if (event.type === "conversation.item.input_audio_transcription.completed") {
      addUserTranscript(event.transcript || userBuffer);
      userBuffer = "";
    }
    if (event.type === "response.done") {
      if (assistantBuffer.trim()) addMessage("assistant", assistantBuffer);
      assistantBuffer = "";
      setMicState(connected ? "listening" : "idle");
      const outputs = (event.response && event.response.output) || [];
      outputs.filter((output) => output.type === "function_call").forEach(handleFunctionCall);
    }
    if (event.type === "error") {
      voiceStatus.textContent = event.error && event.error.message ? event.error.message : "שגיאת קול";
      setMicState("idle");
    }
  }

  async function startVoice() {
    voiceStatus.textContent = "מתחבר ל-GPT Realtime";
    setMicState("listening");
    try {
      pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
      remoteAudio = document.createElement("audio");
      remoteAudio.autoplay = true;
      remoteAudio.playsInline = true;
      remoteAudio.style.display = "none";
      document.body.appendChild(remoteAudio);
      pc.ontrack = (event) => {
        remoteAudio.srcObject = event.streams[0];
        remoteAudio.play().catch(() => {
          voiceStatus.textContent = "הקול מחובר, אבל הדפדפן חסם השמעה אוטומטית";
        });
      };
      localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));
      dc = pc.createDataChannel("oai-events");
      dc.addEventListener("open", () => {
        connected = true;
        realtimeFailedOnce = false;
        voiceStatus.textContent = "מחובר ל-GPT Realtime";
        setMicState("listening");
        dc.send(JSON.stringify({
          type: "conversation.item.create",
          item: {
            type: "message",
            role: "user",
            content: [{ type: "input_text", text: "session_id לשימוש בכלי show_on_screen: " + sessionId }],
          },
        }));
      });
      dc.addEventListener("message", (event) => processRealtimeEvent(JSON.parse(event.data)));
      const offer = await pc.createOffer();
      if (!offer.sdp || offer.sdp.length < 100) {
        throw new Error("הדפדפן יצר SDP קצר מדי: " + String(offer.sdp || "").length);
      }
      await pc.setLocalDescription(offer);
      const answerSdp = await negotiateRealtime(offer.sdp);
      await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    } catch (error) {
      stopVoice();
      const message = error && error.message ? error.message : String(error);
      if (message.includes("Permission") || message.includes("NotAllowed")) {
        voiceStatus.textContent = "אין הרשאת מיקרופון לדפדפן";
      } else if (message.includes("OPENAI_API_KEY")) {
        voiceStatus.textContent = "אין מפתח OpenAI; השתמש בדמו הכתוב";
      } else {
        realtimeFailedOnce = true;
        if (supportsPushToTalkRecording()) {
          voiceStatus.textContent = "Realtime לא זמין כאן; עובר להקלטה";
          await startPushToTalkRecording();
        } else {
          voiceStatus.textContent = "קול לא זמין: " + message;
        }
      }
    }
  }

  async function negotiateRealtime(offerSdp) {
    const relayResponse = await fetch("/api/realtime/sdp?session_id=" + encodeURIComponent(sessionId), {
      method: "POST",
      body: offerSdp,
      headers: {
        "Content-Type": "application/sdp",
      },
    });
    if (relayResponse.ok) return relayResponse.text();

    const relayError = (await relayResponse.text()).slice(0, 500);
    try {
      const tokenData = await postJson("/api/session", { session_id: sessionId });
      const directResponse = await fetch("https://api.openai.com/v1/realtime/calls", {
        method: "POST",
        body: offerSdp,
        headers: {
          Authorization: "Bearer " + tokenData.token,
          "Content-Type": "application/sdp",
        },
      });
      if (directResponse.ok) return directResponse.text();
      throw new Error("relay failed: " + relayError + " | direct failed: " + (await directResponse.text()).slice(0, 500));
    } catch (directError) {
      if (directError && String(directError.message || directError).includes("relay failed")) {
        throw directError;
      }
      throw new Error("relay failed: " + relayError + " | direct failed: " + String(directError.message || directError).slice(0, 500));
    }
  }

  function stopVoice() {
    if (localStream) localStream.getTracks().forEach((track) => track.stop());
    if (dc) dc.close();
    if (pc) pc.close();
    localStream = null;
    dc = null;
    pc = null;
    if (remoteAudio) remoteAudio.remove();
    remoteAudio = null;
    connected = false;
    setMicState("idle");
  }

  function supportsRealtimeVoice() {
    return Boolean(
      window.RTCPeerConnection
      && navigator.mediaDevices
      && navigator.mediaDevices.getUserMedia
    );
  }

  function supportsPushToTalkRecording() {
    return Boolean(
      typeof navigator !== "undefined"
      && navigator.mediaDevices
      && navigator.mediaDevices.getUserMedia
      && window.MediaRecorder
    );
  }

  function isLikelyMobileOrTouch() {
    const ua = navigator.userAgent || "";
    return /Android|iPhone|iPad|iPod|Mobile/i.test(ua)
      || (navigator.maxTouchPoints && navigator.maxTouchPoints > 1 && Math.min(window.innerWidth, window.innerHeight) < 900);
  }

  function shouldPreferRecorder() {
    return supportsPushToTalkRecording() && isLikelyMobileOrTouch();
  }

  function preferredRecordingMimeType() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    return [
      "audio/mp4;codecs=mp4a.40.2",
      "audio/mp4",
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
    ].find((type) => MediaRecorder.isTypeSupported(type)) || "";
  }

  function cleanupRecording() {
    if (recordingTimer) clearTimeout(recordingTimer);
    recordingTimer = null;
    if (recordingStream) recordingStream.getTracks().forEach((track) => track.stop());
    recordingStream = null;
    mediaRecorder = null;
    recording = false;
    recordedChunks = [];
  }

  async function transcribeRecording(blob) {
    if (!blob || blob.size < 512) {
      voiceStatus.textContent = "ההקלטה קצרה מדי; נסה שוב";
      setMicState("idle");
      return;
    }
    voiceStatus.textContent = "מפענח דיבור בעברית";
    setMicState("speaking");
    try {
      const response = await fetch("/api/transcribe?session_id=" + encodeURIComponent(sessionId), {
        method: "POST",
        body: blob,
        headers: { "Content-Type": blob.type || "audio/webm" },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || response.statusText);
      const text = String(data.text || "").trim();
      if (!text) throw new Error("לא זוהה טקסט ברור");
      voiceStatus.textContent = "זוהה: " + safeUserTranscript(text);
      await sendDemoText(text, { speak: true, voice: true });
    } catch (error) {
      voiceStatus.textContent = "זיהוי הקול נכשל: " + (error.message || String(error));
      setMicState("idle");
    }
  }

  async function startPushToTalkRecording() {
    stopVoice();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    try {
      recordingStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const mimeType = preferredRecordingMimeType();
      mediaRecorder = mimeType
        ? new MediaRecorder(recordingStream, { mimeType })
        : new MediaRecorder(recordingStream);
      recordedChunks = [];
      mediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) recordedChunks.push(event.data);
      };
      mediaRecorder.onstop = () => {
        const blobType = mediaRecorder && mediaRecorder.mimeType ? mediaRecorder.mimeType : (mimeType || "audio/webm");
        const blob = new Blob(recordedChunks, { type: blobType });
        cleanupRecording();
        transcribeRecording(blob);
      };
      mediaRecorder.start(250);
      recording = true;
      voiceStatus.textContent = "מקליט בעברית; לחץ שוב לסיום";
      setMicState("listening");
      recordingTimer = setTimeout(() => {
        if (recording && mediaRecorder && mediaRecorder.state === "recording") {
          voiceStatus.textContent = "מפענח דיבור בעברית";
          if (mediaRecorder.requestData) mediaRecorder.requestData();
          mediaRecorder.stop();
        }
      }, 8000);
    } catch (error) {
      cleanupRecording();
      const message = error && error.message ? error.message : String(error);
      if (message.includes("Permission") || message.includes("NotAllowed")) {
        voiceStatus.textContent = "אין הרשאת מיקרופון לדפדפן";
      } else {
        voiceStatus.textContent = "הקלטה לא זמינה: " + message;
      }
      setMicState("idle");
    }
  }

  function stopPushToTalkRecording() {
    if (!recording || !mediaRecorder) return;
    if (recordingTimer) clearTimeout(recordingTimer);
    recordingTimer = null;
    voiceStatus.textContent = "מפענח דיבור בעברית";
    setMicState("speaking");
    if (mediaRecorder.state === "recording") {
      if (mediaRecorder.requestData) mediaRecorder.requestData();
      mediaRecorder.stop();
    } else {
      cleanupRecording();
      setMicState("idle");
    }
  }

  function startSpeechRecognition() {
    const Recognition = getSpeechRecognitionConstructor();
    if (!Recognition) {
      startVoice();
      return;
    }
    stopVoice();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    let finalText = "";
    let recognitionError = "";
    speechRecognition = new Recognition();
    speechRecognition.lang = "he-IL";
    speechRecognition.interimResults = true;
    speechRecognition.continuous = false;
    speechRecognition.maxAlternatives = 3;
    speechRecognition.onstart = () => {
      recognizing = true;
      voiceStatus.textContent = "מקשיב בעברית";
      setMicState("listening");
    };
    speechRecognition.onresult = (event) => {
      let interimText = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = result[0] && result[0].transcript ? result[0].transcript.trim() : "";
        if (!text) continue;
        if (result.isFinal) {
          finalText += (finalText ? " " : "") + text;
        } else {
          interimText += (interimText ? " " : "") + text;
        }
      }
      const heard = (finalText || interimText).trim();
      if (heard) voiceStatus.textContent = "שמעתי: " + safeUserTranscript(heard);
    };
    speechRecognition.onerror = (event) => {
      recognitionError = event.error || "unknown";
    };
    speechRecognition.onend = () => {
      const value = finalText.trim();
      recognizing = false;
      speechRecognition = null;
      if (value) {
        voiceStatus.textContent = "שולח ליועץ: " + safeUserTranscript(value);
        setMicState("speaking");
        sendDemoText(value, { speak: true, voice: true }).finally(() => {
          if (!connected && !recognizing && !window.speechSynthesis) setMicState("idle");
        });
        return;
      }
      setMicState("idle");
      if (recognitionError === "not-allowed" || recognitionError === "service-not-allowed") {
        voiceStatus.textContent = "אין הרשאת מיקרופון לדפדפן";
      } else if (recognitionError === "no-speech") {
        voiceStatus.textContent = "לא שמעתי מספיק ברור; נסה שוב קרוב יותר למיקרופון";
      } else if (recognitionError) {
        voiceStatus.textContent = "זיהוי דיבור לא זמין: " + recognitionError;
      } else {
        voiceStatus.textContent = "זיהוי הדיבור הופסק";
      }
    };
    try {
      speechRecognition.start();
    } catch (error) {
      recognizing = false;
      speechRecognition = null;
      voiceStatus.textContent = "זיהוי דיבור לא זמין: " + (error.message || String(error));
      setMicState("idle");
    }
  }

  function voiceStatusLabel() {
    if (supportsRealtimeVoice()) return "לחץ להתחברות ל-GPT Realtime";
    if (shouldPreferRecorder()) return "לחץ להקלטה בעברית; לחץ שוב לסיום";
    if (getSpeechRecognitionConstructor()) return "לחץ ודבר בעברית";
    if (supportsPushToTalkRecording()) return "לחץ להקלטה בעברית; לחץ שוב לסיום";
    return "קול מוגבל בדפדפן הזה; הדמו הכתוב זמין";
  }

  function currentVoiceMode() {
    if (supportsRealtimeVoice() && !realtimeFailedOnce) return "gpt-realtime-voice";
    if (shouldPreferRecorder()) return "mobile-push-to-talk-transcription";
    if (getSpeechRecognitionConstructor()) return "browser-speech-recognition";
    if (supportsPushToTalkRecording()) return "push-to-talk-transcription";
    return "realtime-fallback";
  }

  micButton.addEventListener("click", (event) => {
    if (recording) {
      stopPushToTalkRecording();
      return;
    }
    if (recognizing && speechRecognition) {
      speechRecognition.stop();
      voiceStatus.textContent = "זיהוי הופסק";
      return;
    }
    if (connected || pc) {
      stopVoice();
      voiceStatus.textContent = "קול כבוי";
    } else if (supportsRealtimeVoice() && !event.shiftKey) {
      startVoice();
    } else if (shouldPreferRecorder() && !event.shiftKey) {
      startPushToTalkRecording();
    } else if (getSpeechRecognitionConstructor() && !event.shiftKey) {
      startSpeechRecognition();
    } else if (supportsPushToTalkRecording() && !event.shiftKey) {
      startPushToTalkRecording();
    } else {
      startVoice();
    }
  });

  initTTS();
  if (window.speechSynthesis && window.speechSynthesis.onvoiceschanged !== undefined) {
    window.speechSynthesis.onvoiceschanged = initTTS;
  }
  window.__aceVoiceMode = currentVoiceMode();
  document.documentElement.dataset.aceVoiceMode = window.__aceVoiceMode;
  setAssistantPanelState(localStorage.getItem("ace_assistant_panel_state") || "open");
  voiceStatus.textContent = voiceStatusLabel();
  addMessage("assistant", "שלום, אני יועץ המכירות של ACE. אפשר להתחיל בתרחיש הספה או לשאול על מוצר.");
})();
