// swift-tools-version: 5.10
import PackageDescription
let package = Package(name: "Sovereign", platforms: [.macOS(.v13)], products: [.executable(name: "Sovereign", targets: ["Sovereign"])], targets: [.executableTarget(name: "Sovereign")])
