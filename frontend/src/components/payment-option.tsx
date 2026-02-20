"use client"

import { useConnection, useWallet } from "@solana/wallet-adapter-react"
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
  onStartAnalysis: (paymentToken?: string) => void
  disabled?: boolean
  keyPredefined?: boolean
  isSubmitting?: boolean
}

// Phantom logo SVG (official 2024 branding)
const PhantomLogo = () => (
  <svg width="14" height="12" viewBox="0 0 120 100" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path fillRule="evenodd" clipRule="evenodd" d="M47.8884 76.1416C41.0206 86.6729 29.5126 100 14.1995 100C6.96057 100 0 97.0177 0 84.0633C0 51.0717 45.0114 0 86.7745 0C110.533 0 120 16.4958 120 35.2282C120 59.273 104.408 86.766 88.9091 86.766C83.9903 86.766 81.5774 84.0633 81.5774 79.7763C81.5774 78.658 81.763 77.4464 82.1342 76.1416C76.8442 85.1817 66.6354 93.5694 57.0762 93.5694C50.1158 93.5694 46.5891 89.1892 46.5891 83.0382C46.5891 80.8015 47.0532 78.4715 47.8884 76.1416ZM83.8635 34.5785C83.8635 40.037 80.6455 42.7663 77.0455 42.7663C73.3909 42.7663 70.2274 40.037 70.2274 34.5785C70.2274 29.1199 73.3909 26.3907 77.0455 26.3907C80.6455 26.3907 83.8635 29.1199 83.8635 34.5785ZM104.316 34.5788C104.316 40.0373 101.098 42.7665 97.498 42.7665C93.8434 42.7665 90.6798 40.0373 90.6798 34.5788C90.6798 29.1204 93.8434 26.3911 97.498 26.3911C101.098 26.3911 104.316 29.1204 104.316 34.5788Z" fill="#FFFDF8"/>
  </svg>
)

// Solflare logo SVG (official 2024 branding)
const SolflareLogo = () => (
  <svg width="14" height="15" viewBox="0 0 94 100" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M47.35 53.9718L54.231 47.3133L67.0594 51.5186C75.4564 54.3227 79.6551 59.4627 79.6551 66.7056C79.6551 72.1965 77.5558 75.8179 73.3575 80.4909L72.0747 81.8926L72.541 78.6215C74.4067 66.7056 70.9084 61.5656 59.3624 57.8269L47.35 53.9718ZM30.0892 13.201L65.0771 24.8832L57.4961 32.1262L39.3027 26.0514C33.0051 23.9486 30.9058 20.5607 30.0892 13.4346V13.201ZM27.9899 72.5468L35.9205 64.953L50.8488 69.86C58.6624 72.43 61.3447 75.8179 60.5286 84.3459L27.9899 72.5468ZM17.9603 38.6681C17.9603 36.4488 19.1265 34.3459 21.1092 32.5933C23.2084 35.6309 26.8238 38.3177 32.5383 40.1871L44.9009 44.2757L38.0199 50.9347L25.8908 46.9625C20.2928 45.0936 17.9603 42.2895 17.9603 38.6681ZM54.5807 100C80.238 82.9441 94 71.3786 94 57.1262C94 47.6636 88.4019 42.4063 76.0398 38.3177L66.7097 35.1634L92.2509 10.6309L87.119 5.14022L79.5385 11.799L43.7346 0C32.6549 3.62152 18.66 14.2523 18.66 24.8832C18.66 26.0514 18.7767 27.2196 19.1266 28.5048C9.91313 33.7616 6.18114 38.668 6.18114 44.7428C6.18114 50.4671 9.21337 56.1915 18.8933 59.3459L26.5905 61.9159L0 87.4998L5.1315 92.9906L13.4119 85.3973L54.5807 100Z" fill="#02050A"/>
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

  const handleConnectPhantom = useCallback(async () => {
    if (phantomWallet) {
      try {
        select(phantomWallet.adapter.name)
        await phantomWallet.adapter.connect()
      } catch (error) {
        console.error("Failed to connect Phantom:", error)
      }
    } else {
      window.open('https://phantom.app/', '_blank')
    }
  }, [phantomWallet, select])

  const handleConnectSolflare = useCallback(async () => {
    if (solflareWallet) {
      try {
        select(solflareWallet.adapter.name)
        await solflareWallet.adapter.connect()
      } catch (error) {
        console.error("Failed to connect Solflare:", error)
      }
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
        // Start analysis after payment - pass token directly to avoid state timing issues
        onStartAnalysis(result.payment_token)
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
        <div className="flex flex-col gap-2">
          {/* Show Phantom if available, otherwise Solflare */}
          {phantomWallet ? (
            <button
              type="button"
              onClick={handleConnectPhantom}
              disabled={disabled}
              className="flex h-7 w-full items-center justify-center gap-1.5 rounded-md border px-3 text-xs font-medium text-white transition-all hover:opacity-90 disabled:opacity-50"
              style={{ backgroundColor: '#AB9FF2', borderColor: '#968CCF' }}
            >
              <PhantomLogo />
              <span>Connect Phantom</span>
            </button>
          ) : solflareWallet ? (
            <button
              type="button"
              onClick={handleConnectSolflare}
              disabled={disabled}
              className="flex h-7 w-full items-center justify-center gap-1.5 rounded-md border px-3 text-xs font-medium text-black transition-all hover:opacity-90 disabled:opacity-50"
              style={{ backgroundColor: '#FFEF46', borderColor: '#EEDA0F' }}
            >
              <SolflareLogo />
              <span>Connect Solflare</span>
            </button>
          ) : (
            <div className="text-center text-xs text-muted-foreground">
              Install <a href="https://phantom.app/" target="_blank" rel="noopener noreferrer" className="underline">Phantom</a> or <a href="https://solflare.com/" target="_blank" rel="noopener noreferrer" className="underline">Solflare</a> wallet
            </div>
          )}
        </div>
      ) : paymentSuccess ? (
        <div className="rounded-md border border-green-500/30 bg-green-500/10 px-3 py-1.5 text-xs text-green-500">
          Payment successful! Starting analysis...
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={handlePaymentAndStart}
            disabled={disabled || isProcessing || models.length === 0 || isLoadingPrice || isSubmitting}
            className="flex h-7 w-full items-center justify-center gap-1.5 rounded-md border px-3 text-xs font-medium transition-all hover:opacity-90 disabled:opacity-50"
            style={{
              backgroundColor: isPhantom ? '#AB9FF2' : isSolflare ? '#FFEF46' : '#AB9FF2',
              borderColor: isPhantom ? '#968CCF' : isSolflare ? '#EEDA0F' : '#968CCF',
              color: isSolflare ? '#02050A' : '#FFFDF8',
            }}
          >
            {isPhantom && <PhantomLogo />}
            {isSolflare && <SolflareLogo />}
            <span>
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
