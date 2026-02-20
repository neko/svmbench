"use client"

import { useConnection, useWallet } from "@solana/wallet-adapter-react"
import { PhantomWalletAdapter, SolflareWalletAdapter } from "@solana/wallet-adapter-wallets"
import { useCallback, useEffect, useMemo, useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  calculatePrice,
  calculatePriceFromConfig,
  createPaymentTransaction,
  fetchPaymentConfig,
  verifyPayment,
  type EffortLevel,
  type PaymentConfig,
} from "@/lib/payment"

interface PaymentOptionProps {
  provider: "openai" | "openrouter" | "x402"
  effort: EffortLevel
  models: string[]
  apiKey: string
  onApiKeyChange: (key: string) => void
  onPaymentComplete: (paymentToken: string) => void
  onStartAnalysis: () => void
  disabled?: boolean
  keyPredefined?: boolean
  isSubmitting?: boolean
}

// Phantom logo SVG
const PhantomLogo = () => (
  <svg width="20" height="20" viewBox="0 0 128 128" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M64 128C99.3462 128 128 99.3462 128 64C128 28.6538 99.3462 0 64 0C28.6538 0 0 28.6538 0 64C0 99.3462 28.6538 128 64 128Z" fill="transparent"/>
    <path d="M110.584 64.9142H99.1421C99.1421 41.7651 80.173 23 56.7724 23C33.6612 23 14.8716 41.3057 14.4118 64.0814C13.9361 87.6035 34.7315 107.835 58.4003 107.835H62.5734C83.4184 107.835 110.584 88.9133 110.584 64.9142ZM40.2979 67.8656C40.2979 71.8246 37.0844 75.0335 33.1189 75.0335C29.1534 75.0335 25.9399 71.8246 25.9399 67.8656V59.3609C25.9399 55.4019 29.1534 52.193 33.1189 52.193C37.0844 52.193 40.2979 55.4019 40.2979 59.3609V67.8656ZM60.4247 67.8656C60.4247 71.8246 57.2112 75.0335 53.2457 75.0335C49.2802 75.0335 46.0667 71.8246 46.0667 67.8656V59.3609C46.0667 55.4019 49.2802 52.193 53.2457 52.193C57.2112 52.193 60.4247 55.4019 60.4247 59.3609V67.8656Z" fill="white"/>
  </svg>
)

// Solflare logo SVG
const SolflareLogo = () => (
  <svg width="20" height="20" viewBox="0 0 101 88" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path fillRule="evenodd" clipRule="evenodd" d="M100.5 44C100.5 53.1 96.1 61.1 89.4 66.4L83.3 57.9C87.3 54.5 90 49.5 90 44C90 33.5 81.5 25 71 25C65.1 25 59.8 27.6 56.3 31.8L46.8 26.3C52.4 18.6 61.1 13.5 71 13.5C87.9 13.5 101.5 27.1 100.5 44ZM50.5 88C22.6 88 0 68.4 0 44C0 19.6 22.6 0 50.5 0C56.9 0 63.1 1.2 68.8 3.4L63.6 14.1C59.5 12.4 55.1 11.5 50.5 11.5C29.5 11.5 12.5 25.8 12.5 44C12.5 62.2 29.5 76.5 50.5 76.5C62.3 76.5 72.8 71.6 79.8 63.8L89.4 69.3C80.3 80.6 66.2 88 50.5 88Z" fill="black"/>
  </svg>
)

export function PaymentOption({
  provider,
  effort,
  models,
  apiKey,
  onApiKeyChange,
  onPaymentComplete,
  onStartAnalysis,
  disabled,
  keyPredefined,
  isSubmitting,
}: PaymentOptionProps) {
  const { connection } = useConnection()
  const { publicKey, signTransaction, connected, wallet, select, wallets, disconnect } = useWallet()

  const [paymentConfig, setPaymentConfig] = useState<PaymentConfig | null>(null)
  const [isProcessing, setIsProcessing] = useState(false)
  const [paymentError, setPaymentError] = useState<string | null>(null)
  const [paymentSuccess, setPaymentSuccess] = useState(false)
  const [serverPrice, setServerPrice] = useState<number | null>(null)
  const [isLoadingPrice, setIsLoadingPrice] = useState(false)

  // Find Phantom and Solflare wallets
  const phantomWallet = useMemo(() =>
    wallets.find(w => w.adapter.name === 'Phantom'),
    [wallets]
  )
  const solflareWallet = useMemo(() =>
    wallets.find(w => w.adapter.name === 'Solflare'),
    [wallets]
  )

  const isPhantom = wallet?.adapter.name === 'Phantom'
  const isSolflare = wallet?.adapter.name === 'Solflare'

  // Quick client-side price from config
  const quickPrice = paymentConfig
    ? calculatePriceFromConfig(paymentConfig, models)
    : 0

  const displayPrice = serverPrice ?? quickPrice

  // Fetch payment config on mount
  useEffect(() => {
    if (provider === 'x402') {
      fetchPaymentConfig()
        .then((config) => {
          if (config.enabled) {
            setPaymentConfig(config)
          }
        })
        .catch(() => {})
    }
  }, [provider])

  // Fetch price when models change (x402 only)
  useEffect(() => {
    if (provider !== 'x402' || !paymentConfig?.enabled || models.length === 0) {
      setServerPrice(null)
      return
    }

    setIsLoadingPrice(true)
    calculatePrice(models)
      .then((result) => setServerPrice(result.total))
      .catch(() => setServerPrice(null))
      .finally(() => setIsLoadingPrice(false))
  }, [models, paymentConfig?.enabled, provider])

  // Reset payment state when models change
  useEffect(() => {
    setPaymentSuccess(false)
    setPaymentError(null)
  }, [models])

  const handleConnectPhantom = useCallback(() => {
    if (phantomWallet) {
      select(phantomWallet.adapter.name)
    } else {
      window.open('https://phantom.app/', '_blank')
    }
  }, [phantomWallet, select])

  const handleConnectSolflare = useCallback(() => {
    if (solflareWallet) {
      select(solflareWallet.adapter.name)
    } else {
      window.open('https://solflare.com/', '_blank')
    }
  }, [solflareWallet, select])

  const handlePaymentAndStart = useCallback(async () => {
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
      // Create and sign the transaction
      const transaction = await createPaymentTransaction(
        connection,
        publicKey,
        paymentConfig.receiver_wallet,
        priceToCharge,
      )

      const signedTransaction = await signTransaction(transaction)
      const signature = await connection.sendRawTransaction(
        signedTransaction.serialize(),
      )

      await connection.confirmTransaction(signature, "confirmed")

      // Verify with backend
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
        // Start analysis after payment
        onStartAnalysis()
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
    onStartAnalysis,
  ])

  // If key is predefined, don't show any payment options
  if (keyPredefined) {
    return null
  }

  // For OpenAI/OpenRouter - just show API key input
  if (provider !== 'x402') {
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

  // x402 mode - show wallet connection and payment
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">
          {models.length} model{models.length !== 1 ? "s" : ""}
        </span>
        <span className="font-medium text-foreground">
          {isLoadingPrice ? "..." : `$${displayPrice.toFixed(2)} USDC`}
        </span>
      </div>

      {!connected ? (
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleConnectPhantom}
            disabled={disabled}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2.5 font-medium text-white transition-all hover:opacity-90 disabled:opacity-50"
            style={{ backgroundColor: '#AB9FF2' }}
          >
            <PhantomLogo />
            <span className="text-sm">Phantom</span>
          </button>
          <button
            type="button"
            onClick={handleConnectSolflare}
            disabled={disabled}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2.5 font-medium text-black transition-all hover:opacity-90 disabled:opacity-50"
            style={{ backgroundColor: '#FCD535' }}
          >
            <SolflareLogo />
            <span className="text-sm">Solflare</span>
          </button>
        </div>
      ) : paymentSuccess ? (
        <div className="rounded-md border border-green-500/30 bg-green-500/10 px-3 py-2 text-xs text-green-500">
          Payment successful! Starting analysis...
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={handlePaymentAndStart}
            disabled={disabled || isProcessing || models.length === 0 || isLoadingPrice || isSubmitting}
            className="flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2.5 font-medium text-white transition-all hover:opacity-90 disabled:opacity-50"
            style={{ backgroundColor: isPhantom ? '#AB9FF2' : isSolflare ? '#FCD535' : '#AB9FF2' }}
          >
            {isPhantom && <PhantomLogo />}
            {isSolflare && <SolflareLogo />}
            <span className={`text-sm ${isSolflare ? 'text-black' : 'text-white'}`}>
              {isProcessing || isSubmitting ? "Processing..." : `Pay $${displayPrice.toFixed(2)} & Start Analysis`}
            </span>
          </button>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {publicKey?.toBase58().slice(0, 4)}...{publicKey?.toBase58().slice(-4)}
            </span>
            <button
              type="button"
              onClick={() => disconnect()}
              className="hover:text-foreground"
            >
              Disconnect
            </button>
          </div>
        </>
      )}

      {paymentError && (
        <div className="text-xs text-destructive">{paymentError}</div>
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
