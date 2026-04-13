import SwiftUI

struct ContentView: View {
  @StateObject private var viewModel = NemoViewModel()
  @Environment(\.scenePhase) private var scenePhase
  @State private var showingSettings = false

  var body: some View {
    ZStack {
      // Background gradient
      LinearGradient(
        colors: backgroundColors,
        startPoint: .topLeading,
        endPoint: .bottomTrailing
      )
      .ignoresSafeArea()

      VStack(spacing: 0) {
        HStack {
            // Meta Glasses connect button
            Button(action: {
              viewModel.connectToGlasses()
            }) {
              HStack(spacing: 8) {
                Image(systemName: "glasses")
                  .font(.system(size: 14, weight: .medium))
                  .foregroundColor(viewModel.isGlassesConnected ? .green : .white.opacity(0.9))
                Text(viewModel.isGlassesConnected ? "Glasses Connected" : "Connect to Meta Glasses")
                  .font(.system(size: 13, weight: .semibold))
                  .foregroundColor(viewModel.isGlassesConnected ? .green : .white.opacity(0.9))
              }
              .padding(.horizontal, 16)
              .padding(.vertical, 10)
              .background(
                RoundedRectangle(cornerRadius: 20)
                  .fill(Color.white.opacity(0.1))
                  .overlay(
                    RoundedRectangle(cornerRadius: 20)
                      .stroke(viewModel.isGlassesConnected ? Color.green.opacity(0.5) : Color.white.opacity(0.2), lineWidth: 1)
                  )
              )
            }
            
            Spacer()
            
            // Settings button
            Button(action: {
                showingSettings = true
            }) {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(.white.opacity(0.9))
                    .padding(10)
                    .background(Circle().fill(Color.white.opacity(0.1)))
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 16)

        Spacer()

        // Status ring
        ZStack {
          // Outer pulse ring (when active/responding)
          if viewModel.state == .active || viewModel.state == .responding {
            Circle()
              .stroke(ringColor.opacity(0.3), lineWidth: 2)
              .frame(width: 200, height: 200)
              .scaleEffect(viewModel.state == .responding ? 1.3 : 1.1)
              .animation(.easeInOut(duration: 1.0).repeatForever(autoreverses: true), value: viewModel.state)
          }

          // Main circle
          Circle()
            .fill(ringColor.opacity(0.15))
            .frame(width: 160, height: 160)

          Circle()
            .stroke(ringColor, lineWidth: 3)
            .frame(width: 160, height: 160)

          // Icon
          VStack(spacing: 8) {
            Image(systemName: stateIcon)
              .font(.system(size: 44, weight: .light))
              .foregroundColor(ringColor)

            Text(viewModel.state.rawValue)
              .font(.system(size: 13, weight: .medium))
              .foregroundColor(.white.opacity(0.8))
          }
        }

        Spacer().frame(height: 40)

        // Transcripts
        VStack(spacing: 12) {
          if !viewModel.userText.isEmpty {
            HStack {
              Image(systemName: "person.fill")
                .font(.system(size: 12))
                .foregroundColor(.white.opacity(0.5))
              Text(viewModel.userText)
                .font(.system(size: 15))
                .foregroundColor(.white.opacity(0.7))
              Spacer()
            }
            .padding(.horizontal, 24)
            .transition(.opacity)
          }

          if !viewModel.aiText.isEmpty {
            HStack {
              Image(systemName: "waveform")
                .font(.system(size: 12))
                .foregroundColor(.cyan.opacity(0.8))
              Text(viewModel.aiText)
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(.white)
              Spacer()
            }
            .padding(.horizontal, 24)
            .transition(.opacity)
          }
        }
        .animation(.easeInOut(duration: 0.3), value: viewModel.userText)
        .animation(.easeInOut(duration: 0.3), value: viewModel.aiText)

        Spacer()

        // Bottom status bar
        HStack(spacing: 16) {
          StatusDot(color: viewModel.state != .idle ? .green : .gray,
                    label: "DGX")
          StatusDot(color: LocationManager.shared.isTracking ? .green : .gray,
                    label: "GPS")
          StatusDot(color: CameraManager.shared.isActive ? .green : .gray,
                    label: "CAM")
          StatusDot(color: AudioManager.shared.isListening ? .green : .gray,
                    label: "MIC")
          StatusDot(color: ProactiveAlertManager.shared.isEnabled ? .green : .gray,
                    label: "ALERT")
        }
        .padding(.bottom, 40)
      }
    }
    .task {
      viewModel.start()
    }
    .onChange(of: scenePhase) { phase in
      switch phase {
      case .active:
        viewModel.appBecameActive()
      case .background:
        viewModel.appEnteredBackground()
      default:
        break
      }
    }
    .sheet(isPresented: $showingSettings) {
        AlertPreferencesView()
    }
  }

  // MARK: - Styling

  private var backgroundColors: [Color] {
    switch viewModel.state {
    case .idle:       return [Color(white: 0.08), Color(white: 0.05)]
    case .passive:    return [Color(white: 0.1), Color(white: 0.05)]
    case .active:     return [Color(red: 0.05, green: 0.1, blue: 0.15), Color(white: 0.05)]
    case .processing: return [Color(red: 0.1, green: 0.05, blue: 0.15), Color(white: 0.05)]
    case .responding: return [Color(red: 0.0, green: 0.08, blue: 0.12), Color(white: 0.05)]
    }
  }

  private var ringColor: Color {
    switch viewModel.state {
    case .idle:       return .gray
    case .passive:    return .white
    case .active:     return .cyan
    case .processing: return .purple
    case .responding: return .green
    }
  }

  private var stateIcon: String {
    switch viewModel.state {
    case .idle:       return "wifi.slash"
    case .passive:    return "mic.fill"
    case .active:     return "waveform"
    case .processing: return "brain"
    case .responding: return "speaker.wave.2.fill"
    }
  }
}

// MARK: - Components

struct StatusDot: View {
  let color: Color
  let label: String

  var body: some View {
    VStack(spacing: 4) {
      Circle()
        .fill(color)
        .frame(width: 8, height: 8)
      Text(label)
        .font(.system(size: 10, weight: .medium))
        .foregroundColor(.white.opacity(0.5))
    }
  }
}
