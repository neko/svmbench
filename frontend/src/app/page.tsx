"use client"

import { ArrowUpRight01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import { AppFooter } from "@/components/app-footer"
import { AppHeader } from "@/components/app-header"
import { FileUploader } from "@/components/file-uploader"
import { PaymentOption, usePaymentMode } from "@/components/payment-option"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/hooks/use-auth"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { useSessionStorage } from "@/hooks/use-session-storage"
import { API_BASE } from "@/lib/api"
import { startJob, startJobFromUrl } from "@/lib/jobs"
import { addRecentJob, type RecentJob } from "@/lib/recent-jobs"
import { inferPackageName } from "@/lib/upload-utils"
import { createZipFromFiles } from "@/lib/zip"
import { useUploadStore } from "@/store/upload-store"

// x402 models - pay with USDC
const X402_MODELS = [
  { value: "llm-claude-opus", label: "Claude Opus 4.6" },
  { value: "llm-claude-sonnet", label: "Claude Sonnet 4.5" },
  { value: "llm-gpt-5.2-codex", label: "GPT-5.2 Codex" },
  { value: "llm-gpt-5.2", label: "GPT-5.2" },
  { value: "llm-deepseek", label: "DeepSeek V3" },
  { value: "llm-deepseek-r1", label: "DeepSeek R1" },
  { value: "llm-gemini-pro", label: "Gemini 2.5 Pro" },
  { value: "llm-grok", label: "Grok 4" },
  { value: "llm-kimi", label: "Kimi K2.5" },
  { value: "llm-claude-haiku", label: "Claude Haiku 4.5" },
  { value: "llm-gemini-flash", label: "Gemini 2.5 Flash" },
  { value: "llm-minimax", label: "MiniMax M2.5" },
  { value: "llm-glm", label: "GLM-5" },
  { value: "llm-llama", label: "Llama 3.3 70B" },
  { value: "llm-qwen", label: "Qwen3 235B" },
  { value: "llm-mistral", label: "Mistral Large 3" },
]

const OPENAI_MODELS = [
  { value: "codex-gpt-5.2", label: "codex-gpt-5.2" },
  { value: "codex-gpt-5.1-codex-max", label: "codex-gpt-5.1-codex-max" },
]

const OPENROUTER_MODELS = [
  { value: "anthropic/claude-opus-4.6", label: "anthropic/claude-opus-4.6" },
  { value: "anthropic/claude-opus-4.5", label: "anthropic/claude-opus-4.5" },
  { value: "openai/gpt-5.2-codex", label: "openai/gpt-5.2-codex" },
  { value: "openai/gpt-5.1-codex-max", label: "openai/gpt-5.1-codex-max" },
  { value: "deepseek/deepseek-v3.2", label: "deepseek/deepseek-v3.2" },
  { value: "google/gemini-3-flash-preview", label: "google/gemini-3-flash-preview" },
  { value: "x-ai/grok-4.1-fast", label: "x-ai/grok-4.1-fast" },
  { value: "minimax/minimax-m2.5", label: "minimax/minimax-m2.5" },
  { value: "moonshotai/kimi-k2.5", label: "moonshotai/kimi-k2.5" },
  { value: "z-ai/glm-5", label: "z-ai/glm-5" },
]

type Provider = "x402" | "openai" | "openrouter"

export default function Page() {
  const router = useRouter()
  const { inputMode, files, packageName, sourceUrl, setInputMode, setUpload, setSourceUrl, clearUpload } = useUploadStore()
  const [apiKey, setApiKey] = useSessionStorage("svmbench.apiKey", "")
  const [provider, setProvider] = useState<Provider>("x402")
  const [selectedModels, setSelectedModels] = useState<string[]>(["llm-claude-opus"])
  const [effort, setEffort] = useState<"low" | "medium" | "high">("medium")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [recentJobs, setRecentJobs] = useLocalStorage<RecentJob[]>(
    "svmbench.recentJobs.v1",
    [],
  )
  const {
    isAuthorized,
    isLoading: isAuthLoading,
    isConfigLoading,
    keyPredefined,
  } = useAuth()
  const { paymentToken, setPaymentToken, clearPaymentToken } = usePaymentMode()

  const fileCount = files?.length ?? 0
  const selectedLabel = useMemo(() => {
    if (packageName) return packageName
    if (files) return inferPackageName(files)
    return null
  }, [files, packageName])

  const models = provider === "x402"
    ? X402_MODELS
    : provider === "openrouter"
    ? OPENROUTER_MODELS
    : OPENAI_MODELS

  const hasValidInput = inputMode === "url"
    ? !!sourceUrl && sourceUrl.trim().length > 0
    : !!files && fileCount > 0

  // For x402, payment token is required; for others, API key is required
  const hasValidAuth = provider === "x402"
    ? !!paymentToken
    : keyPredefined || apiKey.trim().length > 0

  const canSubmit =
    hasValidInput && !isSubmitting && !isAuthLoading && isAuthorized && selectedModels.length > 0 && hasValidAuth

  const handleFilesSelected = useCallback(
    (selected: File[]) => {
      setUpload(selected, inferPackageName(selected))
    },
    [setUpload],
  )

  const handleProviderChange = useCallback(
    (value: Provider) => {
      setProvider(value)
      // Reset models to first available for new provider
      if (value === "x402") {
        setSelectedModels([X402_MODELS[0].value])
      } else if (value === "openrouter") {
        setSelectedModels([OPENROUTER_MODELS[0].value])
      } else {
        setSelectedModels([OPENAI_MODELS[0].value])
      }
      // Clear payment token when switching away from x402
      if (value !== "x402") {
        clearPaymentToken()
      }
    },
    [clearPaymentToken],
  )

  const handleModelToggle = useCallback(
    (modelValue: string, checked: boolean) => {
      setSelectedModels((prev) => {
        if (checked) {
          return [...prev, modelValue]
        }
        return prev.filter((m) => m !== modelValue)
      })
    },
    [],
  )

  const handleSubmit = useCallback(async () => {
    if (!isAuthorized) {
      setSubmitError("Authorize with GitHub to start analysis.")
      return
    }
    if (selectedModels.length === 0) {
      setSubmitError("Select at least one model.")
      return
    }

    setIsSubmitting(true)
    setSubmitError(null)

    try {
      let response
      let name: string

      // For x402, we pass the payment token; for others, the API key
      const authKey = provider === "x402" ? "" : apiKey.trim()
      const token = provider === "x402" ? paymentToken ?? undefined : undefined

      if (inputMode === "url" && sourceUrl) {
        name = extractNameFromUrl(sourceUrl)
        response = await startJobFromUrl(sourceUrl, selectedModels, authKey, provider, effort, token)
      } else if (files && fileCount > 0) {
        name = selectedLabel ?? "files"
        const zipFile = await createZipFromFiles(files, name)
        response = await startJob(zipFile, selectedModels, authKey, provider, effort, token)
      } else {
        setSubmitError("Please provide files or a URL")
        return
      }

      clearPaymentToken()

      for (const job of response.jobs) {
        addRecentJob({
          job_id: job.job_id,
          label: `${name} (${job.model})`,
          created_at_ms: Date.now(),
        })
      }
      setRecentJobs(addRecentJob({
        job_id: response.batch_id,
        label: `${name} (${selectedModels.length} models)`,
        created_at_ms: Date.now(),
      }))
      router.push(`/results?batch_id=${response.batch_id}`)
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Upload failed")
    } finally {
      setIsSubmitting(false)
    }
  }, [
    isAuthorized,
    selectedModels,
    provider,
    apiKey,
    paymentToken,
    inputMode,
    sourceUrl,
    files,
    fileCount,
    selectedLabel,
    effort,
    clearPaymentToken,
    setRecentJobs,
    router,
  ])

  const extractNameFromUrl = (url: string): string => {
    try {
      const parsed = new URL(url)
      if (parsed.hostname === "github.com") {
        const parts = parsed.pathname.split("/").filter(Boolean)
        if (parts.length >= 2) {
          return parts[1]
        }
      }
      const pathParts = parsed.pathname.split("/").filter(Boolean)
      if (pathParts.length > 0) {
        const lastPart = pathParts[pathParts.length - 1]
        return lastPart.replace(/\.zip$/i, "")
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
                  <video
                    autoPlay
                    loop
                    muted
                    playsInline
                    className="size-16 rounded-lg"
                  >
                    <source src="/dance.webm" type="video/webm" />
                  </video>
                </div>
                <h1 className="text-5xl leading-[1.1] font-serif text-foreground mb-1.5">
                  svmbench
                </h1>
                <h2 className="text-2xl leading-[1.1] font-serif text-foreground mb-3">
                  evaluating ai performance on high-severity solana program findings
                </h2>
                <div className="space-y-2 text-base text-foreground/80">
                  <p className="leading-tight">
                    svmbench is an open benchmark that evaluates whether ai
                    agents can detect, patch, and exploit high-severity
                    vulnerabilities in solana programs.
                  </p>
                  <p className="leading-tight">
                    this interface focuses on detection and only reports
                    high-severity findings. upload a program folder, connect your
                    wallet, and start a run.
                  </p>
                  <div className="flex flex-col items-start gap-0.5">
                    <a
                      href="https://github.com/neko/svmbench"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      repo
                      <HugeiconsIcon
                        icon={ArrowUpRight01Icon}
                        strokeWidth={2}
                        className="size-3.5"
                      />
                    </a>
                    <Link
                      href="/leaderboard"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      leaderboard
                      <HugeiconsIcon
                        icon={ArrowUpRight01Icon}
                        strokeWidth={2}
                        className="size-3.5"
                      />
                    </Link>
                    <Link
                      href="/results?job_id=628dc527-3711-47ce-8cc3-63703c33ebf6"
                      className="inline-flex items-center gap-0.5 font-serif leading-tight underline-offset-4 hover:text-foreground hover:underline"
                    >
                      example audit
                      <HugeiconsIcon
                        icon={ArrowUpRight01Icon}
                        strokeWidth={2}
                        className="size-3.5"
                      />
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
                  <Label
                    htmlFor="provider-select"
                    className="text-xs text-foreground"
                  >
                    Provider
                  </Label>
                  <Select value={provider} onValueChange={handleProviderChange}>
                    <SelectTrigger id="provider-select" className="w-full">
                      <SelectValue placeholder="Select provider" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="x402">x402 (Pay with USDC)</SelectItem>
                      <SelectItem value="openai">OpenAI</SelectItem>
                      <SelectItem value="openrouter">OpenRouter</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid gap-1">
                  <Label className="text-xs text-foreground">
                    Models
                  </Label>
                  <Popover>
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className="border-input bg-input/20 dark:bg-input/30 dark:hover:bg-input/50 focus-visible:border-ring focus-visible:ring-ring/30 flex h-7 w-full items-center justify-between gap-1.5 rounded-md border px-2 py-1.5 text-xs/relaxed transition-colors focus-visible:ring-2 outline-none"
                      >
                        <span className="truncate text-muted-foreground">
                          {selectedModels.length === 0
                            ? "Select models..."
                            : `${selectedModels.length} model${selectedModels.length > 1 ? "s" : ""} selected`}
                        </span>
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          width="14"
                          height="14"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="shrink-0 text-muted-foreground"
                        >
                          <path d="m7 15 5 5 5-5" />
                          <path d="m7 9 5-5 5 5" />
                        </svg>
                      </button>
                    </PopoverTrigger>
                    <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-1" align="start">
                      <div className="max-h-64 space-y-0.5 overflow-y-auto">
                        {models.map((m) => (
                          <label
                            key={m.value}
                            className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 hover:bg-muted/50"
                          >
                            <Checkbox
                              checked={selectedModels.includes(m.value)}
                              onCheckedChange={(checked) =>
                                handleModelToggle(m.value, checked === true)
                              }
                            />
                            <span className="text-xs">{m.label}</span>
                          </label>
                        ))}
                      </div>
                    </PopoverContent>
                  </Popover>
                </div>

                <div className="grid gap-1">
                  <Label
                    htmlFor="effort-select"
                    className="text-xs text-foreground"
                  >
                    Effort
                  </Label>
                  <Select value={effort} onValueChange={(v: "low" | "medium" | "high") => setEffort(v)}>
                    <SelectTrigger id="effort-select" className="w-full">
                      <SelectValue placeholder="Select effort" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="low">Low (~2 min)</SelectItem>
                      <SelectItem value="medium">Medium (~6 min)</SelectItem>
                      <SelectItem value="high">High (~12 min)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {!isConfigLoading && (
                  <PaymentOption
                    provider={provider}
                    effort={effort}
                    models={selectedModels}
                    apiKey={apiKey}
                    onApiKeyChange={setApiKey}
                    onPaymentComplete={setPaymentToken}
                    onStartAnalysis={handleSubmit}
                    disabled={isSubmitting || !hasValidInput || !isAuthorized}
                    keyPredefined={keyPredefined}
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

                {/* Show start button only for non-x402 providers */}
                {provider !== "x402" && (
                  <Button
                    onClick={handleSubmit}
                    disabled={!canSubmit}
                    className="w-full uppercase"
                  >
                    {isSubmitting ? "Uploading…" : "Start analysis"}
                  </Button>
                )}

                {submitError && (
                  <div className="text-xs text-destructive">{submitError}</div>
                )}

                {recentJobs.length > 0 && (
                  <div className="pt-1">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-xs text-muted-foreground">
                        Recent runs
                      </span>
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
                          onClick={() =>
                            router.push(`/results?job_id=${job.job_id}`)
                          }
                          className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted/40"
                          title={job.job_id}
                        >
                          <span className="min-w-0 flex-1 truncate text-foreground">
                            {job.label}
                          </span>
                          <span className="shrink-0 font-mono text-muted-foreground">
                            {job.job_id.slice(0, 8)}
                          </span>
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
