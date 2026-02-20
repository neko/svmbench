"use client"

import { Delete02Icon, FolderAddIcon, Link01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  buildFileTree,
  FileTree,
  getTopLevelFolderPaths,
} from "@/components/file-tree"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

async function readDirectoryEntries(
  dirEntry: FileSystemDirectoryEntry,
  basePath: string,
): Promise<File[]> {
  const reader = dirEntry.createReader()
  const files: File[] = []

  const readBatch = (): Promise<FileSystemEntry[]> =>
    new Promise((resolve, reject) => {
      reader.readEntries(resolve, reject)
    })

  let entries: FileSystemEntry[] = []
  let batch: FileSystemEntry[]
  do {
    batch = await readBatch()
    entries = entries.concat(batch)
  } while (batch.length > 0)

  for (const entry of entries) {
    if (entry.isFile) {
      const fileEntry = entry as FileSystemFileEntry
      const file = await new Promise<File>((resolve, reject) => {
        fileEntry.file(resolve, reject)
      })
      const fileWithPath = new File([file], file.name, { type: file.type })
      Object.defineProperty(fileWithPath, "webkitRelativePath", {
        value: `${basePath}/${entry.name}`,
        writable: false,
      })
      files.push(fileWithPath)
    } else if (entry.isDirectory) {
      const subFiles = await readDirectoryEntries(
        entry as FileSystemDirectoryEntry,
        `${basePath}/${entry.name}`,
      )
      files.push(...subFiles)
    }
  }

  return files
}

async function processDroppedItems(
  dataTransfer: DataTransfer,
): Promise<File[]> {
  const items = dataTransfer.items
  const files: File[] = []

  for (let i = 0; i < items.length; i++) {
    const item = items[i]
    if (item.kind !== "file") continue

    const entry = item.webkitGetAsEntry?.()
    if (!entry) {
      const file = item.getAsFile()
      if (file) files.push(file)
      continue
    }

    if (entry.isDirectory) {
      const dirFiles = await readDirectoryEntries(
        entry as FileSystemDirectoryEntry,
        entry.name,
      )
      files.push(...dirFiles)
    } else if (entry.isFile) {
      const fileEntry = entry as FileSystemFileEntry
      const file = await new Promise<File>((resolve, reject) => {
        fileEntry.file(resolve, reject)
      })
      files.push(file)
    }
  }

  return files
}

type InputMode = "files" | "url"

interface FileUploaderProps {
  inputMode: InputMode
  onInputModeChange: (mode: InputMode) => void
  onFilesSelected: (files: File[]) => void
  files?: File[] | null
  selectedLabel?: string | null
  fileCount?: number | null
  sourceUrl?: string | null
  onSourceUrlChange?: (url: string) => void
  disabled?: boolean
  onClear?: () => void
}

export function FileUploader({
  inputMode,
  onInputModeChange,
  onFilesSelected,
  files,
  selectedLabel,
  fileCount,
  sourceUrl,
  onSourceUrlChange,
  disabled = false,
  onClear,
}: FileUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const zipInputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set())

  const hasFiles = !!files && files.length > 0
  const fileCountLabel =
    typeof fileCount === "number" && fileCount > 0 ? `${fileCount} files` : ""
  const selectedName = selectedLabel ?? "Selected folder"
  const fileTree = useMemo(() => {
    if (!files || files.length === 0) return []
    const fileData = files.map((file) => ({
      path: file.webkitRelativePath || file.name,
      content: "",
      size: file.size,
    }))
    return buildFileTree(fileData)
  }, [files])

  useEffect(() => {
    if (!fileTree.length) {
      setExpandedPaths(new Set())
      return
    }
    setExpandedPaths(new Set(getTopLevelFolderPaths(fileTree)))
  }, [fileTree])

  const triggerInput = useCallback(() => {
    if (disabled) return
    inputRef.current?.click()
  }, [disabled])

  const triggerZipInput = useCallback(() => {
    if (disabled) return
    zipInputRef.current?.click()
  }, [disabled])

  const handleFiles = useCallback(
    (files: File[] | null) => {
      if (!files || files.length === 0) return
      onFilesSelected([...files])
      if (inputRef.current) {
        inputRef.current.value = ""
      }
    },
    [onFilesSelected],
  )

  const handleDrop = useCallback(
    async (event: DragEvent<HTMLElement>) => {
      event.preventDefault()
      if (disabled) return
      setIsDragging(false)

      try {
        const files = await processDroppedItems(event.dataTransfer)
        handleFiles(files)
      } catch (error) {
        console.error("Failed to process dropped items:", error)
      }
    },
    [disabled, handleFiles],
  )

  const handleDragOver = useCallback(
    (event: DragEvent<HTMLElement>) => {
      event.preventDefault()
      if (disabled) return
      setIsDragging(true)
    },
    [disabled],
  )

  const handleDragLeave = useCallback((event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    setIsDragging(false)
  }, [])

  const handleToggleExpand = useCallback((path: string) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
  }, [])

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const selectedFiles = event.target.files
        ? Array.from(event.target.files)
        : null
      handleFiles(selectedFiles)
    },
    [handleFiles],
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (disabled) return
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault()
        triggerInput()
      }
    },
    [disabled, triggerInput],
  )

  const handleChangeFolder = useCallback(() => {
    triggerInput()
  }, [triggerInput])

  const handleClearFolder = useCallback(() => {
    onClear?.()
  }, [onClear])

  const [showUrlInput, setShowUrlInput] = useState(false)

  // If URL is set, switch to URL mode automatically
  useEffect(() => {
    if (sourceUrl && sourceUrl.trim().length > 0) {
      onInputModeChange("url")
    }
  }, [sourceUrl, onInputModeChange])

  return (
    <div className="w-full space-y-2">
      {/* File upload area (always visible when no files/url) */}
      {hasFiles ? (
        <section
          aria-label="Uploaded files"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          className={cn(
            "flex h-64 flex-col rounded-2xl border border-border bg-muted/20 transition-colors",
            isDragging && "border-primary",
            disabled && "opacity-60",
          )}
        >
          <div className="flex items-center justify-between gap-3 bg-muted/20 px-4 py-1 text-xs text-muted-foreground">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-foreground">{selectedName}</span>
              <span className="shrink-0 font-serif text-base">
                {fileCountLabel}
              </span>
            </div>
            <div className="flex items-center gap-0.5">
              <Tooltip>
                <TooltipTrigger
                  render={(triggerProps) => (
                    <Button
                      {...triggerProps}
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      onClick={(event) => {
                        triggerProps.onClick?.(event)
                        handleChangeFolder()
                      }}
                      disabled={disabled}
                      aria-label="Change folder"
                    >
                      <HugeiconsIcon icon={FolderAddIcon} strokeWidth={2} />
                    </Button>
                  )}
                />
                <TooltipContent sideOffset={6}>Change folder</TooltipContent>
              </Tooltip>
              {onClear && (
                <Tooltip>
                  <TooltipTrigger
                    render={(triggerProps) => (
                      <Button
                        {...triggerProps}
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        onClick={(event) => {
                          triggerProps.onClick?.(event)
                          handleClearFolder()
                        }}
                        disabled={disabled}
                        className="-me-2"
                        aria-label="Remove folder"
                      >
                        <HugeiconsIcon icon={Delete02Icon} strokeWidth={2} />
                      </Button>
                    )}
                  />
                  <TooltipContent sideOffset={6}>Remove</TooltipContent>
                </Tooltip>
              )}
            </div>
          </div>
          <div className="flex-1 min-h-0 bg-background border-t border-border/50 pl-1.5 pt-1 rounded-b-2xl">
            <ScrollArea className="h-full min-h-0 pr-2" fadeColor="transparent">
              <FileTree
                nodes={fileTree}
                selectedFile={null}
                onFileSelect={() => {}}
                focusedPath={null}
                onFocusChange={() => {}}
                expandedPaths={expandedPaths}
                onToggleExpand={handleToggleExpand}
              />
            </ScrollArea>
          </div>
        </section>
      ) : inputMode === "url" && sourceUrl ? (
        // URL mode - show entered URL
        <div
          className={cn(
            "flex h-64 flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-border bg-muted/20 px-6 py-8",
            disabled && "opacity-60",
          )}
        >
          <HugeiconsIcon
            icon={Link01Icon}
            strokeWidth={1.5}
            className="size-6 text-muted-foreground"
          />
          <div className="w-full max-w-sm space-y-2">
            <Input
              type="url"
              placeholder="https://github.com/user/repo or zip URL..."
              value={sourceUrl ?? ""}
              onChange={(e) => onSourceUrlChange?.(e.target.value)}
              disabled={disabled}
              className="text-center"
            />
            <div className="flex items-center justify-center gap-2">
              <button
                type="button"
                onClick={() => {
                  onSourceUrlChange?.("")
                  onInputModeChange("files")
                }}
                className="text-xs text-muted-foreground hover:text-foreground underline"
              >
                or upload files instead
              </button>
            </div>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={triggerInput}
          onKeyDown={handleKeyDown}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          className={cn(
            "flex h-64 w-full flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-muted/20 px-6 py-8 text-center transition-colors outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30",
            !disabled && "cursor-pointer",
            isDragging && "border-primary bg-muted/40",
            disabled && "cursor-not-allowed opacity-60",
          )}
        >
          <HugeiconsIcon
            icon={FolderAddIcon}
            strokeWidth={1.5}
            className="size-6 text-muted-foreground"
          />
          <div className="flex flex-col space-y-0.5">
            <span className="text-xs text-muted-foreground">
              DRAG A FOLDER OR ZIP
            </span>
            <span className="text-xs text-muted-foreground/70">
              or click to select a{" "}
              <span className="underline" onClick={(e) => { e.stopPropagation(); triggerInput() }}>folder</span>
              {" / "}
              <span className="underline" onClick={(e) => { e.stopPropagation(); triggerZipInput() }}>.zip</span>
            </span>
          </div>
        </button>
      )}

      {/* "or paste a link" - shows URL input on hover/focus */}
      {!hasFiles && inputMode !== "url" && (
        <div
          className="group relative"
          onMouseEnter={() => setShowUrlInput(true)}
          onMouseLeave={() => !sourceUrl && setShowUrlInput(false)}
        >
          {showUrlInput ? (
            <div className="flex items-center gap-2">
              <Input
                type="url"
                placeholder="https://github.com/user/repo..."
                value={sourceUrl ?? ""}
                onChange={(e) => {
                  onSourceUrlChange?.(e.target.value)
                  if (e.target.value.trim()) {
                    onInputModeChange("url")
                  }
                }}
                disabled={disabled}
                className="text-xs h-8"
                autoFocus
              />
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setShowUrlInput(true)}
              className="w-full text-center text-xs text-muted-foreground/70 hover:text-muted-foreground transition-colors py-1"
            >
              or paste a link
            </button>
          )}
        </div>
      )}

      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleInputChange}
        {...({
          webkitdirectory: "",
          directory: "",
        } as React.InputHTMLAttributes<HTMLInputElement>)}
      />
      <input
        ref={zipInputRef}
        type="file"
        className="hidden"
        accept=".zip,application/zip,application/x-zip-compressed"
        onChange={handleInputChange}
      />
    </div>
  )
}
