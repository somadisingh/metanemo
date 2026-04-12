import AVFoundation
import UIKit
import Combine

/// Captures a single camera frame when the app is in the foreground.
/// iOS does not allow camera access in the background — this is a hard OS restriction.
/// The camera session is only created when the app is active and destroyed when backgrounded.
class CameraManager: NSObject, ObservableObject, AVCaptureVideoDataOutputSampleBufferDelegate {
  static let shared = CameraManager()

  @Published var isActive = false
  @Published var latestFrame: UIImage?

  private var captureSession: AVCaptureSession?
  private let sessionQueue = DispatchQueue(label: "camera.session")
  private let context = CIContext()

  /// Start the camera session (call only when app is in foreground)
  func start() {
    guard !isActive else { return }
    sessionQueue.async { [weak self] in
      self?.configureSession()
    }
  }

  /// Stop the camera session (call when app goes to background)
  func stop() {
    sessionQueue.async { [weak self] in
      self?.captureSession?.stopRunning()
      self?.captureSession = nil
      DispatchQueue.main.async { self?.isActive = false }
      NSLog("[Camera] Session stopped")
    }
  }

  /// Capture a single frame as a Base64 JPEG string, resized to 640x480
  func captureBase64Frame() -> String? {
    guard let image = latestFrame else { return nil }

    // Resize to target resolution
    let targetSize = CGSize(width: Config.frameWidth, height: Config.frameHeight)
    UIGraphicsBeginImageContextWithOptions(targetSize, true, 1.0)
    image.draw(in: CGRect(origin: .zero, size: targetSize))
    let resized = UIGraphicsGetImageFromCurrentImageContext()
    UIGraphicsEndImageContext()

    guard let jpeg = resized?.jpegData(compressionQuality: Config.jpegQuality) else { return nil }
    return jpeg.base64EncodedString()
  }

  // MARK: - Private

  private func configureSession() {
    let session = AVCaptureSession()
    session.sessionPreset = .medium

    guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
          let input = try? AVCaptureDeviceInput(device: camera),
          session.canAddInput(input) else {
      NSLog("[Camera] Failed to access back camera")
      return
    }
    session.addInput(input)

    let output = AVCaptureVideoDataOutput()
    output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
    output.setSampleBufferDelegate(self, queue: sessionQueue)
    output.alwaysDiscardsLateVideoFrames = true

    guard session.canAddOutput(output) else { return }
    session.addOutput(output)

    // Force portrait orientation
    if let connection = output.connection(with: .video),
       connection.isVideoRotationAngleSupported(90) {
      connection.videoRotationAngle = 90
    }

    session.startRunning()
    captureSession = session
    DispatchQueue.main.async { self.isActive = true }
    NSLog("[Camera] Session started")
  }

  // MARK: - AVCaptureVideoDataOutputSampleBufferDelegate

  func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                     from connection: AVCaptureConnection) {
    guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
    let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
    guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else { return }
    let image = UIImage(cgImage: cgImage)
    DispatchQueue.main.async { self.latestFrame = image }
  }
}
