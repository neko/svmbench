import {
  createTransferInstruction,
  getAssociatedTokenAddress,
  TOKEN_PROGRAM_ID,
} from "@solana/spl-token"
import { Connection, PublicKey, Transaction } from "@solana/web3.js"
import { API_BASE } from "@/lib/api"

// USDC on Solana mainnet
export const USDC_MINT = new PublicKey(
  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
)

export type EffortLevel = "low" | "medium" | "high"

export interface PaymentConfig {
  enabled: boolean
  receiver_wallet: string | null
  markup: number
  markup_percent: number
  requests_per_audit: number
  model_prices: Record<string, number> // model -> total price (includes markup)
}

export async function fetchPaymentConfig(): Promise<PaymentConfig> {
  const response = await fetch(`${API_BASE}/v1/payment/config`, {
    cache: "no-store",
  })

  if (!response.ok) {
    throw new Error("Failed to fetch payment config")
  }

  return response.json()
}

export interface CalculatePriceResponse {
  total: number
  breakdown: Record<string, number>
  requests_per_audit: number
  markup_percent: number
}

export async function calculatePrice(
  models: string[],
): Promise<CalculatePriceResponse> {
  const response = await fetch(`${API_BASE}/v1/payment/calculate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ models }),
  })

  if (!response.ok) {
    throw new Error("Failed to calculate price")
  }

  return response.json()
}

export async function createPaymentTransaction(
  connection: Connection,
  payerPublicKey: PublicKey,
  receiverWallet: string,
  amount: number, // in USDC (e.g., 1.0 = $1)
): Promise<Transaction> {
  const receiverPublicKey = new PublicKey(receiverWallet)

  // Get associated token accounts for USDC
  const payerAta = await getAssociatedTokenAddress(USDC_MINT, payerPublicKey)
  const receiverAta = await getAssociatedTokenAddress(
    USDC_MINT,
    receiverPublicKey,
  )

  // USDC has 6 decimals
  const amountLamports = Math.floor(amount * 1_000_000)

  const transaction = new Transaction()

  // Add transfer instruction
  transaction.add(
    createTransferInstruction(
      payerAta,
      receiverAta,
      payerPublicKey,
      amountLamports,
      [],
      TOKEN_PROGRAM_ID,
    ),
  )

  // Get recent blockhash
  const { blockhash, lastValidBlockHeight } =
    await connection.getLatestBlockhash()
  transaction.recentBlockhash = blockhash
  transaction.lastValidBlockHeight = lastValidBlockHeight
  transaction.feePayer = payerPublicKey

  return transaction
}

export interface VerifyPaymentRequest {
  signature: string
  payer_wallet: string
  amount: number
  models: string[]
  effort: EffortLevel // kept for compatibility
}

export interface VerifyPaymentResponse {
  valid: boolean
  payment_token: string
}

export async function verifyPayment(
  request: VerifyPaymentRequest,
): Promise<VerifyPaymentResponse> {
  const response = await fetch(`${API_BASE}/v1/payment/verify`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
    credentials: "include",
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.detail || "Payment verification failed")
  }

  return response.json()
}

// Client-side price calculation from cached config (for quick UI updates)
// x402 uses flat per-request pricing, so just sum the model prices
export function calculatePriceFromConfig(
  config: PaymentConfig,
  models: string[],
): number {
  let total = 0
  for (const model of models) {
    const price = config.model_prices[model]
    if (price !== undefined) {
      total += price
    }
  }
  return Math.round(total * 100) / 100
}
