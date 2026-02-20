import type { FileData } from "@/lib/file-loader"
import { matchPathFlexibly, normalizeFilePath } from "@/lib/paths"
import type { Vulnerability } from "@/types"

/**
 * Validate and fix file data against vulnerabilities.
 * - Clamps line numbers to valid ranges (AI sometimes hallucinates line numbers)
 * - Returns error only for missing files, not for invalid line numbers
 */
export function validateFileData(
  fileData: FileData[],
  vulnerabilities: Vulnerability[],
): string | null {
  if (vulnerabilities.length === 0) return null

  const lineCounts = new Map<string, number>()
  const pathSet = new Set<string>()
  for (const file of fileData) {
    const normalized = normalizeFilePath(file.path)
    const lines =
      file.content.length === 0 ? 1 : file.content.split(/\r\n|\r|\n/).length
    lineCounts.set(normalized, lines)
    pathSet.add(normalized)
  }

  const missingFiles: string[] = []

  for (const vuln of vulnerabilities) {
    for (const loc of vuln.description) {
      const matchedPath = matchPathFlexibly(loc.file, pathSet)
      const lineCount = matchedPath ? lineCounts.get(matchedPath) : null
      if (!lineCount) {
        missingFiles.push(loc.file)
      } else {
        // Clamp line numbers to valid range (AI may hallucinate line numbers)
        if (loc.line_end > lineCount) {
          loc.line_end = lineCount
        }
        if (loc.line_start > lineCount) {
          loc.line_start = lineCount
        }
        if (loc.line_start < 1) {
          loc.line_start = 1
        }
        if (loc.line_end < loc.line_start) {
          loc.line_end = loc.line_start
        }
      }
    }
  }

  if (missingFiles.length === 0) {
    return null
  }

  return `Missing ${missingFiles.length} file(s): ${missingFiles
    .slice(0, 4)
    .join(", ")}${missingFiles.length > 4 ? "…" : ""}.`
}
