"use client"

import { useCallback, useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"

interface ApiKeyInputProps {
  onStartAnalysis: (apiKey: string) => void
  disabled?: boolean
  isSubmitting?: boolean
}

export function ApiKeyInput({
  onStartAnalysis,
  disabled,
  isSubmitting,
}: ApiKeyInputProps) {
  const [apiKey, setApiKey] = useState("")
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = useCallback(() => {
    const key = apiKey.trim()
    if (!key) {
      setError("OpenRouter API key is required")
      return
    }
    if (!key.startsWith("sk-or-")) {
      setError("Invalid OpenRouter API key format")
      return
    }
    setError(null)
    onStartAnalysis(key)
  }, [apiKey, onStartAnalysis])

  return (
    <div className="space-y-3">
      <div className="grid gap-1.5">
        <Label htmlFor="api-key" className="text-xs text-foreground">
          OpenRouter API Key
        </Label>
        <Input
          id="api-key"
          type="password"
          placeholder="sk-or-..."
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          disabled={disabled || isSubmitting}
          className="h-8 text-xs"
        />
        <p className="text-xs text-muted-foreground">
          Get your API key at{" "}
          <a
            href="https://openrouter.ai/keys"
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-foreground"
          >
            openrouter.ai/keys
          </a>
        </p>
      </div>

      <Button
        type="button"
        onClick={handleSubmit}
        disabled={disabled || isSubmitting || !apiKey.trim()}
        className="h-8 w-full text-xs"
      >
        {isSubmitting ? "Starting..." : "Start Analysis"}
      </Button>

      {error && <div className="text-xs text-destructive">{error}</div>}
    </div>
  )
}

// Keep this for backward compatibility with page.tsx
export function usePaymentMode() {
  const [apiKey, setApiKey] = useState<string | null>(null)

  const clearApiKey = useCallback(() => {
    setApiKey(null)
  }, [])

  return {
    paymentToken: apiKey,
    setPaymentToken: setApiKey,
    clearPaymentToken: clearApiKey,
    hasPayment: apiKey !== null,
  }
}
