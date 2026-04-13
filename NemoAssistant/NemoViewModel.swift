import SwiftUI
import AVFoundation
import Combine

enum NemoState: String {
  case idle = "Connecting..."
  case passive = "Say \"Nemo\" to start"
  case active = "Listening..."
  case processing = "Processing..."
  case responding = "Speaking..."
}

@MainActor
class NemoViewModel: ObservableObject {
  @Published var state: NemoState = .idle
  @Published var userText: String = ""
  @Published var aiText: String = ""
  @Published var isAppForeground = true
  @Published var isGlassesConnected = false

  private let audio = AudioManager.shared
  private let location = LocationManager.shared
  private let camera = CameraManager.shared
  private let ws = WebSocketClient.shared
  private let proactiveAlerts = ProactiveAlertManager.shared

  // AVSpeechSynthesizer for reading AI responses aloud
  private let speechSynthesizer = AVSpeechSynthesizer()
  private let speechDelegate = SpeechDelegate()

  /// Accumulated AI text for the current turn (outputTranscription may arrive in chunks)
  private var accumulatedAIText = ""

  func start() {
    audio.setup()
    location.startTracking()
    ws.connect()
    speechSynthesizer.delegate = speechDelegate
    proactiveAlerts.requestNotificationPermission()
    wireCallbacks()
    audio.startListening()
    state = .passive
  }

  func stop() {
    speechSynthesizer.stopSpeaking(at: .immediate)
    proactiveAlerts.stopPingTimer()
    audio.stopListening()
    location.stopTracking()
    camera.stop()
    ws.disconnect()
    state = .idle
  }

  func appBecameActive() {
    isAppForeground = true
    camera.start()
  }

  func appEnteredBackground() {
    isAppForeground = false
    camera.stop()
  }

  private func wireCallbacks() {
    // Wake word detected → show "Listening..."
    audio.onWakeWordDetected = { [weak self] in
      guard let self else { return }
      self.speechSynthesizer.stopSpeaking(at: .immediate)
      self.state = .active
      self.userText = ""
      self.aiText = ""
      self.accumulatedAIText = ""
      self.location.requestHighAccuracy()
    }
    
    // Bind audio manager's glasses state to view model
    audio.$isGlassesConnected
      .receive(on: DispatchQueue.main)
      .assign(to: &$isGlassesConnected)

    // Live speech text while user is speaking → show as subtitles
    audio.onLiveSpeechText = { [weak self] text in
      DispatchQueue.main.async {
        self?.userText = text
      }
    }

    // Full prompt collected after silence → send ONCE to DGX
    audio.onPromptReady = { [weak self] prompt in
      guard let self else { return }
      NSLog("[Nemo] Sending prompt to DGX: %@", prompt)
      self.state = .processing
      self.userText = prompt
      self.accumulatedAIText = ""

      // Send location
      self.ws.sendLocation(lat: self.location.latitude, lon: self.location.longitude)

      // If foreground, send a camera frame too
      if self.isAppForeground, let frame = self.camera.captureBase64Frame() {
        self.ws.sendImage(base64Jpeg: frame)
      }

      // Send the text prompt — one single request
      self.ws.sendText(prompt)
    }

    // Server sent AI text → accumulate, display, and speak via AVSpeechSynthesizer
    ws.onOutputText = { [weak self] text in
      DispatchQueue.main.async {
        guard let self else { return }
        self.accumulatedAIText += text
        self.aiText = self.accumulatedAIText
        self.state = .responding
        self.speakText(text)
      }
    }

    // Server sent user text (from ASR — backup to local recognition)
    ws.onInputText = { [weak self] text in
      DispatchQueue.main.async { self?.userText = text }
    }

    // Turn complete → transition to passive after speech finishes
    ws.onTurnComplete = { [weak self] in
      DispatchQueue.main.async {
        guard let self else { return }
        // If synthesizer is still speaking, let the delegate handle the transition
        if !self.speechSynthesizer.isSpeaking {
          self.state = .passive
        }
        // Otherwise speechDelegate.onFinished will set state = .passive
      }
    }

    // Speech finished callback
    speechDelegate.onFinished = { [weak self] in
      DispatchQueue.main.async {
        guard let self else { return }
        NSLog("[Nemo] AVSpeechSynthesizer finished speaking")
        if self.state == .responding {
          self.state = .passive
        }
      }
    }

    // WebSocket ready → start proactive ping timer
    ws.onReady = { [weak self] in
      DispatchQueue.main.async {
        if self?.state == .idle { self?.state = .passive }
        self?.proactiveAlerts.startPingTimer()
      }
    }

    // Proactive alert from middleware → fire local notification
    ws.onProactiveAlert = { [weak self] bullets in
      guard let self else { return }
      NSLog("[Nemo] Proactive alert received: %d bullets", bullets.count)
      self.proactiveAlerts.handleAlert(bullets: bullets)
    }
  }

  // MARK: - TTS via AVSpeechSynthesizer

  private func speakText(_ text: String) {
    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
    utterance.rate = AVSpeechUtteranceDefaultSpeechRate
    utterance.pitchMultiplier = 1.0
    utterance.volume = 1.0
    NSLog("[Nemo] Speaking: %@", text)
    speechSynthesizer.speak(utterance)
  }
  
  func connectToGlasses() {
    audio.connectToGlasses()
  }
}

// MARK: - AVSpeechSynthesizerDelegate

private class SpeechDelegate: NSObject, AVSpeechSynthesizerDelegate {
  var onFinished: (() -> Void)?

  func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                          didFinish utterance: AVSpeechUtterance) {
    // Only fire when the synthesizer is truly done (no more queued utterances)
    if !synthesizer.isSpeaking {
      onFinished?()
    }
  }
}

