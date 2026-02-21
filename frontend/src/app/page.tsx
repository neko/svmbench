"use client"

import { ArrowUpRight01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import { AppFooter } from "@/components/app-footer"
import { AppHeader } from "@/components/app-header"
import { FileUploader } from "@/components/file-uploader"
import { ApiKeyInput } from "@/components/payment-option"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/hooks/use-auth"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { API_BASE } from "@/lib/api"
import { startJob, startJobFromUrl } from "@/lib/jobs"
import { addRecentJob, type RecentJob } from "@/lib/recent-jobs"
import { inferPackageName } from "@/lib/upload-utils"
import { createZipFromFiles } from "@/lib/zip"
import { useUploadStore } from "@/store/upload-store"

// Available OpenRouter models
const MODELS = [
  { value: "google/gemini-3-pro-preview", label: "Gemini 3 Pro" },
  { value: "anthropic/claude-opus-4.6", label: "Claude Opus 4.6" },
  { value: "anthropic/claude-opus-4.5", label: "Claude Opus 4.5" },
  { value: "openai/gpt-5.1-codex", label: "GPT-5.1 Codex" },
  { value: "google/gemini-2.5-pro", label: "Gemini 2.5 Pro" },
]

export default function Page() {
  const router = useRouter()
  const { inputMode, files, packageName, sourceUrl, setInputMode, setUpload, setSourceUrl, clearUpload } = useUploadStore()
  const [selectedModel, setSelectedModel] = useState<string>(MODELS[0].value)

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [recentJobs, setRecentJobs] = useLocalStorage<RecentJob[]>("svmbench.recentJobs.v1", [])
  const { isAuthorized, isLoading: isAuthLoading, isConfigLoading } = useAuth()

  const fileCount = files?.length ?? 0
  const selectedLabel = useMemo(() => {
    if (packageName) return packageName
    if (files) return inferPackageName(files)
    return null
  }, [files, packageName])

  const hasValidInput = inputMode === "url"
    ? !!sourceUrl && sourceUrl.trim().length > 0
    : !!files && fileCount > 0

  const handleFilesSelected = useCallback((selected: File[]) => {
    setUpload(selected, inferPackageName(selected))
  }, [setUpload])

  const handleSubmit = useCallback(async (apiKey: string) => {
    if (!isAuthorized) {
      setSubmitError("Authorize with GitHub to start analysis.")
      return
    }
    if (!selectedModel) {
      setSubmitError("Select a model.")
      return
    }
    if (!apiKey) {
      setSubmitError("OpenRouter API key is required.")
      return
    }

    setIsSubmitting(true)
    setSubmitError(null)

    try {
      let response
      let name: string

      if (inputMode === "url" && sourceUrl) {
        name = extractNameFromUrl(sourceUrl)
        response = await startJobFromUrl(sourceUrl, selectedModel, apiKey)
      } else if (files && fileCount > 0) {
        name = selectedLabel ?? "files"
        const zipFile = await createZipFromFiles(files, name)
        response = await startJob(zipFile, selectedModel, apiKey)
      } else {
        setSubmitError("Please provide files or a URL")
        return
      }

      const job = response.jobs[0]
      setRecentJobs(addRecentJob({
        job_id: job.job_id,
        label: `${name} (${job.model})`,
        created_at_ms: Date.now(),
      }))
      router.push(`/results?batch_id=${response.batch_id}`)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Upload failed")
    } finally {
      setIsSubmitting(false)
    }
  }, [isAuthorized, selectedModel, inputMode, sourceUrl, files, fileCount, selectedLabel, setRecentJobs, router])

  const extractNameFromUrl = (url: string): string => {
    try {
      const parsed = new URL(url)
      if (parsed.hostname === "github.com") {
        const parts = parsed.pathname.split("/").filter(Boolean)
        if (parts.length >= 2) return parts[1]
      }
      const pathParts = parsed.pathname.split("/").filter(Boolean)
      if (pathParts.length > 0) {
        return pathParts[pathParts.length - 1].replace(/\.zip$/i, "")
      }
      return parsed.hostname
    } catch {
      return "source"
    }
  }

  return (
    <main className="flex min-h-screen w-screen flex-col">
      <AppHeader showLogo={false} showBorder={false} />
      <section className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-4xl">
          <div className="mx-auto grid max-w-sm gap-10 lg:max-w-none lg:grid-cols-5">
            <div className="space-y-6 lg:col-span-3">
              <div>
                <div className="-ms-2 mb-3 flex items-center gap-2">
                  <video autoPlay loop muted playsInline className="size-16 rounded-lg">
                    <source src="/dance.webm" type="video/webm" />
                  </video>
                </div>
                <h1 className="text-5xl leading-[1.1] font-serif text-foreground mb-1.5">svmbench</h1>
                <h2 className="text-2xl leading-[1.1] font-serif text-foreground mb-3">
                  evaluating ai performance on high-severity solana program findings
                </h2>
                <div className="space-y-2 text-base text-foreground/80">
                  <p className="leading-tight">
                    svmbench is an open benchmark that evaluates whether ai agents can detect, patch, and exploit
                    high-severity vulnerabilities in solana programs.
                  </p>
                  <p className="leading-tight">
                    this interface focuses on detection and only reports high-severity findings. upload a program
                    folder, provide your openrouter api key, and start a run.
                  </p>
                  <div className="flex flex-col items-start gap-0.5">
                    <a
                      href="https://github.com/neko/svmbench"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      repo
                      <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={2} className="size-3.5" />
                    </a>
                    <Link
                      href="/leaderboard"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      leaderboard
                      <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={2} className="size-3.5" />
                    </Link>
                    <Link
                      href="/results?job_id=628dc527-3711-47ce-8cc3-63703c33ebf6"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      example audit
                      <HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={2} className="size-3.5" />
                    </Link>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-6 lg:col-span-2">
              <FileUploader
                inputMode={inputMode}
                onInputModeChange={setInputMode}
                onFilesSelected={handleFilesSelected}
                files={files}
                selectedLabel={selectedLabel}
                fileCount={fileCount}
                sourceUrl={sourceUrl}
                onSourceUrlChange={setSourceUrl}
                disabled={isSubmitting}
                onClear={clearUpload}
              />

              <div className="grid gap-3 text-xs text-muted-foreground">
                <div className="grid gap-1">
                  <Label htmlFor="model-select" className="text-xs text-foreground">Model</Label>
                  <Select value={selectedModel} onValueChange={setSelectedModel}>
                    <SelectTrigger id="model-select" className="w-full">
                      <SelectValue placeholder="Select model" />
                    </SelectTrigger>
                    <SelectContent>
                      {MODELS.map((m) => (
                        <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {!isConfigLoading && (
                  <ApiKeyInput
                    onStartAnalysis={handleSubmit}
                    disabled={isSubmitting || !hasValidInput || !isAuthorized}
                    isSubmitting={isSubmitting}
                  />
                )}

                {!isAuthLoading && !isAuthorized && (
                  <span className="text-base font-serif text-muted-foreground">
                    <a
                      href={`${API_BASE}/v1/auth/`}
                      className="text-foreground underline underline-offset-2 hover:text-primary"
                    >
                      Authorize
                    </a>{" "}
                    to start analysis.
                  </span>
                )}

                {submitError && <div className="text-xs text-destructive">{submitError}</div>}

                {recentJobs.length > 0 && (
                  <div className="pt-1">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-xs text-muted-foreground">Recent runs</span>
                      <button
                        type="button"
                        onClick={() => setRecentJobs([])}
                        className="text-xs text-muted-foreground hover:text-foreground"
                      >
                        Clear
                      </button>
                    </div>
                    <div className="mt-2 space-y-1">
                      {recentJobs.slice(0, 6).map((job) => (
                        <button
                          key={job.job_id}
                          type="button"
                          onClick={() => router.push(`/results?job_id=${job.job_id}`)}
                          className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/40"
                          title={job.job_id}
                        >
                          <span className="min-w-0 flex-1 truncate text-foreground">{job.label}</span>
                          <span className="shrink-0 font-mono text-muted-foreground">{job.job_id.slice(0, 8)}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>
      <AppFooter showBorder={false} />
    </main>
  )
}
