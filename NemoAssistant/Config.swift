import Foundation

/// Central configuration — edit the Tailscale IP to match your DGX Spark.
enum Config {
  /// Tailscale IP of the DGX Spark middleware WebSocket server
  static let serverHost = "100.105.166.78"
  static let serverPort = 8080
  static var websocketURL: URL {
    URL(string: "ws://\(serverHost):\(serverPort)/ws")!
  }

  /// Wake word — say this to activate the assistant
  static let wakeWord = "nemo"

  /// Mic sample rate matching Parakeet ASR on the DGX
  static let inputSampleRate: Double = 16000

  /// Camera frame settings (foreground only)
  static let jpegQuality: CGFloat = 0.5
  static let frameWidth: Int = 640
  static let frameHeight: Int = 480
}
