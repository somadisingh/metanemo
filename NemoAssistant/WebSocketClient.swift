import Foundation
import Combine

/// WebSocket client that speaks to the middleware on the Acer Veriton.
///
/// Protocol:
///   → { setup: { model: "nemotron-3-nano" } }
///   ← { setupComplete: {} }
///   → { realtimeInput: { audio: { mimeType, data } } }
///   → { realtimeInput: { locationPing: { latitude, longitude } } }
///   → { clientContent: { turns: [{ role, parts }] } }
///   ← { serverContent: { modelTurn: { parts: [{ text: "..." }] } } }
///   ← { serverContent: { outputTranscription: { text } } }
///   ← { serverContent: { proactiveAlert: { bullets: [...] } } }
///   ← { serverContent: { turnComplete: true } }
///   ← { serverContent: { inputTranscription: { text } } }
class WebSocketClient: ObservableObject {
  static let shared = WebSocketClient()

  @Published var isConnected = false
  @Published var isReady = false

  /// Callbacks
  var onOutputText: ((String) -> Void)?         // What the AI said (for subtitles / AVSpeechSynthesizer)
  var onInputText: ((String) -> Void)?          // What the user said (for subtitles)
  var onTurnComplete: (() -> Void)?
  var onReady: (() -> Void)?
  var onProactiveAlert: (([String]) -> Void)?   // Proactive hazard bullets from middleware

  private var webSocketTask: URLSessionWebSocketTask?
  private var urlSession: URLSession!
  private let delegate = WSDelegate()
  private let sendQueue = DispatchQueue(label: "ws.send", qos: .userInitiated)

  // Reconnection
  private var shouldReconnect = true
  private var reconnectDelay: TimeInterval = 1.0
  private var pingTimer: Timer?

  init() {
    let config = URLSessionConfiguration.default
    // Default config is fine, removing short timeoutIntervalForRequest so WS doesn't drop
    urlSession = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
  }

  // MARK: - Connect

  func connect() {
    guard !isConnected else { return }
    shouldReconnect = true

    delegate.onOpen = { [weak self] in
      DispatchQueue.main.async {
        self?.isConnected = true
        self?.reconnectDelay = 1.0
        NSLog("[WS] Connected — sending setup")
        self?.sendSetup()
        self?.startReceiving()
        self?.startPingTimer()
      }
    }

    delegate.onClose = { [weak self] code, reason in
      DispatchQueue.main.async {
        self?.isConnected = false
        self?.isReady = false
        self?.pingTimer?.invalidate()
        NSLog("[WS] Closed: %d", code)
        self?.scheduleReconnect()
      }
    }

    delegate.onError = { [weak self] error in
      DispatchQueue.main.async {
        self?.isConnected = false
        self?.isReady = false
        NSLog("[WS] Error: %@", error?.localizedDescription ?? "unknown")
        self?.scheduleReconnect()
      }
    }

    webSocketTask = urlSession.webSocketTask(with: Config.websocketURL)
    webSocketTask?.resume()
  }

  func disconnect() {
    shouldReconnect = false
    pingTimer?.invalidate()
    webSocketTask?.cancel(with: .normalClosure, reason: nil)
    webSocketTask = nil
    isConnected = false
    isReady = false
  }

  // MARK: - Send

  /// Send audio chunk in realtimeInput format
  func sendAudio(pcmData: Data) {
    guard isReady else {
      NSLog("[WS] sendAudio skipped — not ready (isConnected=%d, isReady=%d)", isConnected, isReady)
      return
    }
    sendQueue.async { [weak self] in
      let base64 = pcmData.base64EncodedString()
      let json: [String: Any] = [
        "realtimeInput": [
          "audio": [
            "mimeType": "audio/pcm;rate=16000",
            "data": base64
          ]
        ]
      ]
      self?.sendJSON(json)
    }
  }

  /// Send a text query (e.g., after wake word + transcription)
  func sendText(_ text: String) {
    guard isReady else { return }
    let json: [String: Any] = [
      "clientContent": [
        "turns": [
          ["role": "user", "parts": [["text": text]]]
        ]
      ]
    ]
    sendJSON(json)
  }

  /// Send GPS location update
  func sendLocation(lat: Double, lon: Double) {
    guard isReady else { return }
    let json: [String: Any] = [
      "realtimeInput": [
        "location": ["latitude": lat, "longitude": lon]
      ]
    ]
    sendJSON(json)
  }

  /// Send a camera frame (foreground only)
  func sendImage(base64Jpeg: String) {
    guard isReady else { return }
    sendQueue.async { [weak self] in
      let json: [String: Any] = [
        "realtimeInput": [
          "video": [
            "mimeType": "image/jpeg",
            "data": base64Jpeg
          ]
        ]
      ]
      self?.sendJSON(json)
    }
  }

  /// Send a dedicated location ping for proactive alerts (every 2 min)
  func sendLocationPing(lat: Double, lon: Double) {
    guard isReady else { return }
    let json: [String: Any] = [
      "realtimeInput": [
        "locationPing": ["latitude": lat, "longitude": lon]
      ]
    ]
    sendJSON(json)
  }

  // MARK: - Private

  private func sendSetup() {
    let setup: [String: Any] = [
      "setup": [
        "model": "nemotron-3-nano",
        "generationConfig": [
          "responseModalities": ["TEXT"],
          "temperature": 0.3
        ],
        "systemInstruction": [
          "parts": [
            ["text": "You are Nemo, a real-time urban safety assistant for NYC."]
          ]
        ]
      ]
    ]
    sendJSON(setup)
  }

  private func sendJSON(_ json: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: json),
          let string = String(data: data, encoding: .utf8) else { return }
    webSocketTask?.send(.string(string)) { error in
      if let error { NSLog("[WS] Send error: %@", error.localizedDescription) }
    }
  }

  private func startReceiving() {
    webSocketTask?.receive { [weak self] result in
      guard let self else { return }
      switch result {
      case .success(let message):
        switch message {
        case .string(let text):
          self.handleTextMessage(text)
        case .data:
          break // Binary frames not used; TTS handled by AVSpeechSynthesizer
        @unknown default:
          break
        }
        self.startReceiving() // Continue listening
      case .failure(let error):
        NSLog("[WS] Receive error: %@", error.localizedDescription)
        DispatchQueue.main.async {
          self.isConnected = false
          self.isReady = false
          self.pingTimer?.invalidate()
          self.scheduleReconnect()
        }
      }
    }
  }

  private func handleTextMessage(_ text: String) {
    guard let data = text.data(using: .utf8),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

    // setupComplete
    if json["setupComplete"] != nil {
      DispatchQueue.main.async {
        self.isReady = true
        NSLog("[WS] Setup complete — ready")
        self.onReady?()
      }
      return
    }

    // serverContent
    if let sc = json["serverContent"] as? [String: Any] {

      // Model turn (TEXT modality) — AI response text arrives here
      if let modelTurn = sc["modelTurn"] as? [String: Any],
         let parts = modelTurn["parts"] as? [[String: Any]] {
        for part in parts {
          if let text = part["text"] as? String, !text.isEmpty {
            NSLog("[WS] Model text received: %@", text)
            DispatchQueue.main.async { self.onOutputText?(text) }
          }
        }
      }

      // Output transcription (AI text — fallback / alternative path)
      if let ot = sc["outputTranscription"] as? [String: Any],
         let t = ot["text"] as? String, !t.isEmpty {
        DispatchQueue.main.async { self.onOutputText?(t) }
      }

      // Input transcription (user text)
      if let it = sc["inputTranscription"] as? [String: Any],
         let t = it["text"] as? String, !t.isEmpty {
        DispatchQueue.main.async { self.onInputText?(t) }
      }

      // Proactive alert (hazard bullets from middleware)
      if let pa = sc["proactiveAlert"] as? [String: Any],
         let bullets = pa["bullets"] as? [String], !bullets.isEmpty {
        NSLog("[WS] Proactive alert: %d bullets", bullets.count)
        DispatchQueue.main.async { self.onProactiveAlert?(bullets) }
      }

      // Turn complete
      if let tc = sc["turnComplete"] as? Bool, tc {
        DispatchQueue.main.async { self.onTurnComplete?() }
      }
    }
  }

  private func scheduleReconnect() {
    guard shouldReconnect else { return }
    let delay = reconnectDelay
    reconnectDelay = min(reconnectDelay * 2, 30)
    NSLog("[WS] Reconnecting in %.0fs", delay)
    DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
      guard let self, self.shouldReconnect else { return }
      self.connect()
    }
  }
  
  private func startPingTimer() {
    pingTimer?.invalidate()
    // Send a low-level WebSocket ping to keep the connection alive (for NATs and URLSession timeout)
    pingTimer = Timer.scheduledTimer(withTimeInterval: 15.0, repeats: true) { [weak self] _ in
      self?.webSocketTask?.sendPing { error in
        if let error = error {
          NSLog("[WS] Ping error: %@", error.localizedDescription)
        }
      }
    }
  }
}

// MARK: - WebSocket Delegate

private class WSDelegate: NSObject, URLSessionWebSocketDelegate {
  var onOpen: (() -> Void)?
  var onClose: ((Int, Data?) -> Void)?
  var onError: ((Error?) -> Void)?

  func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                   didOpenWithProtocol proto: String?) {
    onOpen?()
  }

  func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                   didCloseWith code: URLSessionWebSocketTask.CloseCode, reason: Data?) {
    onClose?(code.rawValue, reason)
  }

  func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
    if let error { onError?(error) }
  }
}
