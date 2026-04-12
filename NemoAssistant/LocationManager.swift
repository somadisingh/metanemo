import CoreLocation
import Foundation
import Combine

/// Manages GPS location with battery-efficient background tracking.
///
/// Strategy:
/// - Background: significantLocationChanges (cell-tower level, ~500m, near-zero battery)
/// - When wake word triggers: switches to high-accuracy for one fix, then back to significant
///
/// The "Always Allow" location permission + UIBackgroundModes:location keeps the app
/// eligible for background execution even if the audio session is briefly interrupted.
class LocationManager: NSObject, ObservableObject, CLLocationManagerDelegate {
  static let shared = LocationManager()

  @Published var latitude: Double = 40.7128   // Default: NYC
  @Published var longitude: Double = -74.0060
  @Published var isTracking = false

  private let manager = CLLocationManager()
  private var highAccuracyTimer: Timer?

  override init() {
    super.init()
    manager.delegate = self
    manager.allowsBackgroundLocationUpdates = true
    manager.pausesLocationUpdatesAutomatically = false
    manager.showsBackgroundLocationIndicator = true
    // Start with significant changes (battery friendly)
    manager.distanceFilter = 100
    manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
  }

  /// Request "Always" permission and start background tracking
  func startTracking() {
    manager.requestAlwaysAuthorization()
    manager.startMonitoringSignificantLocationChanges()
    manager.startUpdatingLocation()
    isTracking = true
    NSLog("[Location] Background tracking started")
  }

  /// Temporarily switch to high accuracy (called when wake word detected)
  func requestHighAccuracy() {
    manager.desiredAccuracy = kCLLocationAccuracyBest
    manager.distanceFilter = 5
    NSLog("[Location] Switched to high accuracy")

    // Revert to low power after 30 seconds
    highAccuracyTimer?.invalidate()
    highAccuracyTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in
      self?.manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
      self?.manager.distanceFilter = 100
      NSLog("[Location] Reverted to low-power mode")
    }
  }

  func stopTracking() {
    manager.stopUpdatingLocation()
    manager.stopMonitoringSignificantLocationChanges()
    highAccuracyTimer?.invalidate()
    isTracking = false
  }

  // MARK: - CLLocationManagerDelegate

  func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
    guard let loc = locations.last else { return }
    latitude = loc.coordinate.latitude
    longitude = loc.coordinate.longitude
  }

  func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
    switch manager.authorizationStatus {
    case .authorizedAlways:
      NSLog("[Location] Always authorization granted")
      if !isTracking { startTracking() }
    case .authorizedWhenInUse:
      NSLog("[Location] WhenInUse authorization — requesting Always")
      manager.requestAlwaysAuthorization()
    case .denied, .restricted:
      NSLog("[Location] Authorization denied")
    default:
      break
    }
  }

  func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
    NSLog("[Location] Error: %@", error.localizedDescription)
  }
}
