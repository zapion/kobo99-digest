// Print the text recognised in each image, left to right: "<path>\t<text> | <text>".
// Used by src/mail_ocr.py to read the weekday banners in Kobo's 99 email.
import AppKit
import Foundation
import Vision

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        FileHandle.standardError.write("cannot read \(path)\n".data(using: .utf8)!)
        exit(1)
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hant"]
    do {
        try VNImageRequestHandler(cgImage: cgImage).perform([request])
    } catch {
        FileHandle.standardError.write("OCR failed for \(path): \(error)\n".data(using: .utf8)!)
        exit(1)
    }
    let texts = (request.results ?? [])
        .sorted { $0.boundingBox.minX < $1.boundingBox.minX }
        .compactMap { $0.topCandidates(1).first?.string }
    print("\(path)\t\(texts.joined(separator: " | "))")
}
