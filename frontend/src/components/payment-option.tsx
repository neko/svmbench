"use client"

import { useConnection, useWallet } from "@solana/wallet-adapter-react"
import { useWalletModal } from "@solana/wallet-adapter-react-ui"
import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  calculatePrice,
  calculatePriceFromConfig,
  createPaymentTransaction,
  fetchPaymentConfig,
  formatTokenCount,
  verifyPayment,
  type EffortLevel,
  type PaymentConfig,
} from "@/lib/payment"

type PaymentMode = "api_key" | "wallet"

interface PaymentOptionProps {
  provider: "openai" | "openrouter"
  effort: EffortLevel
  models: string[]
  apiKey: string
  onApiKeyChange: (key: string) => void
  onPaymentComplete: (paymentToken: string) => void
  disabled?: boolean
  keyPredefined?: boolean
}

export function PaymentOption({
  provider,
  effort,
  models,
  apiKey,
  onApiKeyChange,
  onPaymentComplete,
  disabled,
  keyPredefined,
}: PaymentOptionProps) {
  const { connection } = useConnection()
  const { publicKey, signTransaction, connected } = useWallet()
  const { setVisible } = useWalletModal()

  const [paymentMode, setPaymentMode] = useState<PaymentMode>(
    keyPredefined ? "api_key" : "wallet",
  )
  const [paymentConfig, setPaymentConfig] = useState<PaymentConfig | null>(null)
  const [isProcessing, setIsProcessing] = useState(false)
  const [paymentError, setPaymentError] = useState<string | null>(null)
  const [paymentSuccess, setPaymentSuccess] = useState(false)
  const [serverPrice, setServerPrice] = useState<number | null>(null)
  const [isLoadingPrice, setIsLoadingPrice] = useState(false)

  // Quick client-side price (from cached config)
  const quickPrice = paymentConfig
    ? calculatePriceFromConfig(paymentConfig, models, effort)
    : 0

  // Use server price if available, otherwise quick price
  const displayPrice = serverPrice ?? quickPrice

  // Budget for current effort level
  const tokenBudget = paymentConfig?.token_budgets?.[effort]
  const requestBudget = paymentConfig?.requests_per_effort?.[effort]

  // Fetch payment config on mount
  useEffect(() => {
    fetchPaymentConfig()
      .then((config) => {
        if (config.enabled) {
          setPaymentConfig(config)
        } else {
          setPaymentMode("api_key")
        }
      })
      .catch(() => {
        setPaymentMode("api_key")
      })
  }, [])

  // Fetch accurate price from server when models/effort change
  useEffect(() => {
    if (!paymentConfig?.enabled || models.length === 0) {
      setServerPrice(null)
      return
    }

    setIsLoadingPrice(true)
    calculatePrice(models, effort)
      .then((result) => {
        setServerPrice(result.total)
      })
      .catch(() => {
        setServerPrice(null)
      })
      .finally(() => {
        setIsLoadingPrice(false)
      })
  }, [models, effort, paymentConfig?.enabled])

  // Reset payment success when models or effort changes
  useEffect(() => {
    setPaymentSuccess(false)
    setPaymentError(null)
  }, [models, effort])

  const handleConnectWallet = useCallback(() => {
    setVisible(true)
  }, [setVisible])

  const handlePayment = useCallback(async () => {
    if (!publicKey || !signTransaction || !paymentConfig?.receiver_wallet) {
      return
    }

    if (models.length === 0) {
      setPaymentError("Select at least one model")
      return
    }

    const priceToCharge = serverPrice ?? quickPrice
    if (priceToCharge <= 0) {
      setPaymentError("Invalid price")
      return
    }

    setIsProcessing(true)
    setPaymentError(null)

    try {
      // Create the transaction
      const transaction = await createPaymentTransaction(
        connection,
        publicKey,
        paymentConfig.receiver_wallet,
        priceToCharge,
      )

      // Sign the transaction
      const signedTransaction = await signTransaction(transaction)

      // Send and confirm the transaction
      const signature = await connection.sendRawTransaction(
        signedTransaction.serialize(),
      )

      // Wait for confirmation
      await connection.confirmTransaction(signature, "confirmed")

      // Verify payment with backend
      const result = await verifyPayment({
        signature,
        payer_wallet: publicKey.toBase58(),
        amount: priceToCharge,
        models,
        effort,
      })

      if (result.valid) {
        setPaymentSuccess(true)
        onPaymentComplete(result.payment_token)
      } else {
        setPaymentError("Payment verification failed")
      }
    } catch (error) {
      console.error("Payment error:", error)
      setPaymentError(
        error instanceof Error ? error.message : "Payment failed",
      )
    } finally {
      setIsProcessing(false)
    }
  }, [
    publicKey,
    signTransaction,
    paymentConfig,
    connection,
    serverPrice,
    quickPrice,
    models,
    effort,
    onPaymentComplete,
  ])

  // If key is predefined, don't show any payment options
  if (keyPredefined) {
    return null
  }

  // If payment config not available or not enabled, only show API key option
  if (!paymentConfig || !paymentConfig.enabled) {
    return (
      <div className="grid gap-1">
        <Label htmlFor="api-key" className="text-xs text-foreground">
          {provider === "openrouter" ? "OpenRouter API Key" : "OpenAI API Key"}
        </Label>
        <Input
          id="api-key"
          type="password"
          placeholder={provider === "openrouter" ? "sk-or-..." : "sk-..."}
          value={apiKey}
          onChange={(e) => onApiKeyChange(e.target.value)}
          disabled={disabled}
        />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setPaymentMode("wallet")}
          disabled={disabled}
          className={`flex-1 rounded-md border px-3 py-2 text-xs transition-colors ${
            paymentMode === "wallet"
              ? "border-primary bg-primary/10 text-foreground"
              : "border-input bg-input/20 text-muted-foreground hover:bg-input/40"
          }`}
        >
          Pay with USDC
        </button>
        <button
          type="button"
          onClick={() => setPaymentMode("api_key")}
          disabled={disabled}
          className={`flex-1 rounded-md border px-3 py-2 text-xs transition-colors ${
            paymentMode === "api_key"
              ? "border-primary bg-primary/10 text-foreground"
              : "border-input bg-input/20 text-muted-foreground hover:bg-input/40"
          }`}
        >
          Use API Key
        </button>
      </div>

      {paymentMode === "wallet" && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">
              {models.length} model{models.length !== 1 ? "s" : ""} ({effort})
            </span>
            <span className="font-medium text-foreground">
              {isLoadingPrice ? "..." : `$${displayPrice.toFixed(2)} USDC`}
            </span>
          </div>

          {requestBudget && (
            <div className="text-xs text-muted-foreground/70">
              ~{requestBudget} API requests per model
            </div>
          )}

          {!connected ? (
            <Button
              type="button"
              onClick={handleConnectWallet}
              disabled={disabled}
              variant="outline"
              className="w-full"
            >
              Connect Wallet
            </Button>
          ) : paymentSuccess ? (
            <div className="rounded-md border border-green-500/30 bg-green-500/10 px-3 py-2 text-xs text-green-500">
              Payment successful! Ready to start audit.
            </div>
          ) : (
            <Button
              type="button"
              onClick={handlePayment}
              disabled={disabled || isProcessing || models.length === 0 || isLoadingPrice}
              className="w-full"
            >
              {isProcessing ? "Processing..." : `Pay $${displayPrice.toFixed(2)} USDC`}
            </Button>
          )}

          {publicKey && !paymentSuccess && (
            <div className="text-xs text-muted-foreground">
              Connected: {publicKey.toBase58().slice(0, 4)}...
              {publicKey.toBase58().slice(-4)}
            </div>
          )}

          {paymentError && (
            <div className="text-xs text-destructive">{paymentError}</div>
          )}
        </div>
      )}

      {paymentMode === "api_key" && (
        <div className="grid gap-1">
          <Label htmlFor="api-key" className="text-xs text-foreground">
            {provider === "openrouter" ? "OpenRouter API Key" : "OpenAI API Key"}
          </Label>
          <Input
            id="api-key"
            type="password"
            placeholder={provider === "openrouter" ? "sk-or-..." : "sk-..."}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            disabled={disabled}
          />
        </div>
      )}
    </div>
  )
}

export function usePaymentMode() {
  const [paymentToken, setPaymentToken] = useState<string | null>(null)

  const clearPaymentToken = useCallback(() => {
    setPaymentToken(null)
  }, [])

  return {
    paymentToken,
    setPaymentToken,
    clearPaymentToken,
    hasPayment: paymentToken !== null,
  }
}
