import JSZip from "jszip"

import {
  createIgnore,
  createIgnoreFromGitignore,
  DEFAULT_IGNORE_PATTERNS,
} from "./gitignore"

export interface FileData {
  path: string
  content: string
  size: number
}

type FileWithPath = File & { webkitRelativePath?: string }

function getFilePath(file: FileWithPath): string {
  return file.webkitRelativePath || file.name
}

function getRootFolder(path: string): string | null {
  const parts = path.split("/")
  return parts.length > 1 ? parts[0] : null
}

function isZipFile(file: File): boolean {
  return (
    file.type === "application/zip" ||
    file.type === "application/x-zip-compressed" ||
    file.name.toLowerCase().endsWith(".zip")
  )
}

export async function readFilesFromZip(
  file: File | Blob,
): Promise<{ rootFolder: string | null; fileData: FileData[] }> {
  const zip = await JSZip.loadAsync(file)
  const fileData: FileData[] = []
  const paths: string[] = []

  const ig = createIgnore(DEFAULT_IGNORE_PATTERNS)

  for (const [path, entry] of Object.entries(zip.files)) {
    if (entry.dir) continue
    paths.push(path)
  }

  const firstPath = paths[0] ?? ""
  const rootFolder = getRootFolder(firstPath)

  for (const [path, entry] of Object.entries(zip.files)) {
    if (entry.dir) continue
    let relativePath = path
    if (rootFolder && relativePath.startsWith(`${rootFolder}/`)) {
      relativePath = relativePath.slice(rootFolder.length + 1)
    }
    if (!relativePath || ig.ignores(relativePath)) continue
    try {
      const content = await entry.async("string")
      if (content.includes("\0")) continue
      fileData.push({ path: relativePath, content, size: content.length })
    } catch {}
  }

  const fallbackName = file instanceof File ? file.name.replace(/\.zip$/i, "") : "source"
  return { rootFolder: rootFolder ?? fallbackName, fileData }
}

async function buildIgnore(
  files: File[],
): Promise<ReturnType<typeof createIgnore>> {
  let ig = createIgnore(DEFAULT_IGNORE_PATTERNS)
  for (const file of files) {
    if (file.name === ".gitignore") {
      try {
        const content = await file.text()
        ig = createIgnoreFromGitignore(content, true)
      } catch {}
      break
    }
  }
  return ig
}

export async function readFilesFromInput(
  files: File[],
): Promise<{ rootFolder: string | null; fileData: FileData[] }> {
  // handle single zip file upload
  if (files.length === 1 && isZipFile(files[0])) {
    return readFilesFromZip(files[0])
  }

  const fileData: FileData[] = []
  const ig = await buildIgnore(files)
  const firstPath = files[0] ? getFilePath(files[0] as FileWithPath) : ""
  const rootFolder = firstPath ? getRootFolder(firstPath) : null

  for (const file of files) {
    let relativePath = getFilePath(file as FileWithPath)
    if (rootFolder && relativePath.startsWith(`${rootFolder}/`)) {
      relativePath = relativePath.slice(rootFolder.length + 1)
    }
    if (ig.ignores(relativePath)) continue
    try {
      const content = await file.text()
      if (content.includes("\0")) continue
      fileData.push({ path: relativePath, content, size: file.size })
    } catch {}
  }

  return { rootFolder, fileData }
}
