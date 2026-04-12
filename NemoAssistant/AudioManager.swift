import AVFoundation
import Foundation
import Speech
import Combine

class AudioManager: ObservableObject {
  static let shared = AudioManager()

  @Published var isListening = false
  @Published var isWakeWordDetected = false

  var onPromptReady: ((String) -> Void)?
  var onWakeWordDetected: (() -> Void)?
  var onLiveSpeechText: ((String) -> Void)?

  private let audioEngine = AVAudioEngine()
  private var speechRecognizer: SFSpeechRecognizer?
  private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
  private var recognitionTask: SFSpeechRecognitionTask?

  private var keepAliveTimer: Timer?
  private var silenceTimer: Timer?
  private var restartTimer: Timer?   // debounce recognition restarts
  private var isCapturing = false

  enum Mode { case passive, collecting }
  private var mode: Mode = .passive
  private var lastPartialText = ""

  init() {
    speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    speechRecognizer?.delegate = nil
  }

  func setup() {
    SFSpeechRecognizer.requestAuthorization { status in
      NSLog("[Audio] Speech recognition auth: %d", status.rawValue)
    }
  }

  // MARK: - Start

  func startListening() {
    guard !isCapturing else { return }

    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playAndRecord, mode: .voiceChat,
                              options: [.mixWithOthers, .allowBluetooth, .defaultToSpeaker])
      try session.setPreferredSampleRate(16000)
      try session.setPreferredIOBufferDuration(0.064)
      try session.setActive(true)
    } catch {
      NSLog("[Audio] Session setup failed: %@", error.localizedDescription)
      return
    }

    let inputNode = audioEngine.inputNode
    let nativeFormat = inputNode.outputFormat(forBus: 0)

    inputNode.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) { [weak self] buffer, _ in
      // Feed audio to current recognition request
      self?.recognitionRequest?.append(buffer)
    }

    do {
      try audioEngine.start()
      isCapturing = true
      isListening = true
    } catch {
      NSLog("[Audio] Engine start failed: %@", error.localizedDescription)
      return
    }

    startRecognition()
    startKeepAlive()
  }

  // MARK: - Recognition

  private func startRecognition() {
    // Debounce: don't restart more than once per second
    restartTimer?.invalidate()
    restartTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: false) { [weak self] _ in
      self?.doStartRecognition()
    }
  }

  private func doStartRecognition() {
    guard isCapturing else { return }
    guard let recognizer = speechRecognizer, recognizer.isAvailable else {
      NSLog("[Audio] Recognizer unavailable — will retry")
      DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
        self?.startRecognition()
      }
      return
    }

    // End old task gracefully instead of cancelling
    recognitionTask?.finish()
    recognitionTask = nil

    let request = SFSpeechAudioBufferRecognitionRequest()
    request.shouldReportPartialResults = true
    request.requiresOnDeviceRecognition = true
    recognitionRequest = request

    NSLog("[Audio] Recognition started (mode: %@)", mode == .passive ? "passive" : "collecting")

    recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
      guard let self else { return }

      if let result {
        let text = result.bestTranscription.formattedString
        self.handleRecognizedText(text)
      }

      if let error = error {
        let nsErr = error as NSError
        // Code 1110 = no speech detected — normal, just restart
        // Code 203 = cancelled — don't restart
        if nsErr.code != 203 {
          DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            guard let self, self.isCapturing, self.mode == .passive else { return }
            self.startRecognition()
          }
        }
      } else if result?.isFinal ?? false {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
          guard let self, self.isCapturing, self.mode == .passive else { return }
          self.startRecognition()
        }
      }
    }
  }

  private func handleRecognizedText(_ fullText: String) {
    let text = fullText.lowercased()

    switch mode {
    case .passive:
      let words = text.split(separator: " ").suffix(5).joined(separator: " ")
      if words.contains(Config.wakeWord) {
        DispatchQueue.main.async {
          self.mode = .collecting
          self.lastPartialText = ""
          self.isWakeWordDetected = true
          NSLog("[Audio] Wake word detected")
          self.playChime()
          self.onWakeWordDetected?()
          // Fresh recognition session for the prompt
          self.recognitionTask?.finish()
          self.recognitionTask = nil
          self.recognitionRequest = nil
          DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            self.doStartRecognition()
          }
        }
      }

    case .collecting:
      let promptText = fullText.trimmingCharacters(in: .whitespacesAndNewlines)
      if !promptText.isEmpty && promptText != lastPartialText {
        lastPartialText = promptText
        DispatchQueue.main.async { self.onLiveSpeechText?(promptText) }
        resetSilenceTimer()
      }

    }
  }

  private func resetSilenceTimer() {
    silenceTimer?.invalidate()
    silenceTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: false) { [weak self] _ in
      guard let self, self.mode == .collecting else { return }
      let finalText = self.lastPartialText.trimmingCharacters(in: .whitespacesAndNewlines)
      NSLog("[Audio] Silence — prompt ready: %@", finalText)

      self.mode = .passive
      self.isWakeWordDetected = false

      if !finalText.isEmpty {
        DispatchQueue.main.async { self.onPromptReady?(finalText) }
      }

      // Restart recognition for next wake word
      self.recognitionTask?.finish()
      self.recognitionTask = nil
      self.recognitionRequest = nil
      DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
        self.doStartRecognition()
      }
    }
  }


  // MARK: - Chime

  private var chimePlayer: AVAudioPlayer?

  private func playChime() {
    // Generate a short two-tone chime as an in-memory WAV, played via AVAudioPlayer
    // (AVAudioPlayerNode crashes if attached to a running engine without a proper output chain)
    let sampleRate: Double = 16000
    let duration = 0.15
    let frameCount = Int(sampleRate * duration)
    var samples = [Int16](repeating: 0, count: frameCount)
    for i in 0..<frameCount {
      let t = Double(i) / sampleRate
      let freq = t < duration / 2 ? 880.0 : 1320.0
      let envelope = 1.0 - (t / duration)
      samples[i] = Int16(clamping: Int(sin(2.0 * .pi * freq * t) * envelope * 0.3 * Double(Int16.max)))
    }

    // Build a minimal WAV file in memory
    let dataSize = UInt32(frameCount * 2)
    var wav = Data()
    wav.append(contentsOf: "RIFF".utf8)
    wav.append(withUnsafeBytes(of: (36 + dataSize).littleEndian) { Data($0) })
    wav.append(contentsOf: "WAVE".utf8)
    wav.append(contentsOf: "fmt ".utf8)
    wav.append(withUnsafeBytes(of: UInt32(16).littleEndian) { Data($0) })   // chunk size
    wav.append(withUnsafeBytes(of: UInt16(1).littleEndian) { Data($0) })    // PCM
    wav.append(withUnsafeBytes(of: UInt16(1).littleEndian) { Data($0) })    // mono
    wav.append(withUnsafeBytes(of: UInt32(sampleRate).littleEndian) { Data($0) }) // sample rate
    wav.append(withUnsafeBytes(of: UInt32(sampleRate * 2).littleEndian) { Data($0) }) // byte rate
    wav.append(withUnsafeBytes(of: UInt16(2).littleEndian) { Data($0) })    // block align
    wav.append(withUnsafeBytes(of: UInt16(16).littleEndian) { Data($0) })   // bits per sample
    wav.append(contentsOf: "data".utf8)
    wav.append(withUnsafeBytes(of: dataSize.littleEndian) { Data($0) })
    samples.withUnsafeBufferPointer { wav.append(UnsafeBufferPointer(start: UnsafeRawPointer($0.baseAddress!).assumingMemoryBound(to: UInt8.self), count: Int(dataSize))) }

    do {
      chimePlayer = try AVAudioPlayer(data: wav)
      chimePlayer?.volume = 0.5
      chimePlayer?.play()
    } catch {
      NSLog("[Audio] Chime playback failed: %@", error.localizedDescription)
    }
  }

  // MARK: - Keep Alive

  private func startKeepAlive() {
    // Keep the recognition session alive with periodic pings
    keepAliveTimer?.invalidate()
    keepAliveTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
      guard let self, self.isCapturing else { return }
      // No-op tick — recognition task handles its own keep-alive
      NSLog("[Audio] Keep-alive tick")
    }
  }

  // MARK: - Stop

  func stopListening() {
    keepAliveTimer?.invalidate()
    silenceTimer?.invalidate()
    restartTimer?.invalidate()
    recognitionTask?.cancel()
    recognitionRequest = nil
    audioEngine.inputNode.removeTap(onBus: 0)
    audioEngine.stop()
    isCapturing = false
    isListening = false
    mode = .passive
  }
}
