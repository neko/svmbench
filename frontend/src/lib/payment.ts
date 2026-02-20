import {
  createAssociatedTokenAccountInstruction,
  createTransferInstruction,
  getAssociatedTokenAddress,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
} from "@solana/spl-token"
import { Connection, PublicKey, Transaction } from "@solana/web3.js"
import { API_BASE } from "@/lib/api"

// USDC on Solana mainnet
export const USDC_MINT = new PublicKey(
  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
)

export type EffortLevel = "low" | "medium" | "high"

export interface X402ModelInfo {
  id: string
  name: string
  price: number // raw x402 price per request
  audit_price: number // total price for audit (with markup)
}

export interface PaymentConfig {
  enabled: boolean
  receiver_wallet: string | null
  markup: number
  markup_percent: number
  requests_per_effort: Record<EffortLevel, number> // effort -> API calls per model
  x402_models: X402ModelInfo[] // available x402 models with full info
  model_prices: Record<EffortLevel, Record<string, number>> // effort -> {model -> price}
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
  effort: EffortLevel
  requests_per_audit: number
  markup_percent: number
}

export async function calculatePrice(
  model: string,
  effort: EffortLevel = "medium",
): Promise<CalculatePriceResponse> {
  const response = await fetch(`${API_BASE}/v1/payment/calculate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ models: [model], effort }),
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

  // Check if receiver's ATA exists, if not create it
  const receiverAtaInfo = await connection.getAccountInfo(receiverAta)
  if (!receiverAtaInfo) {
    transaction.add(
      createAssociatedTokenAccountInstruction(
        payerPublicKey, // payer
        receiverAta, // ata address
        receiverPublicKey, // owner
        USDC_MINT, // mint
        TOKEN_PROGRAM_ID,
        ASSOCIATED_TOKEN_PROGRAM_ID,
      ),
    )
  }

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
  model: string
  effort: EffortLevel
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
// x402 uses flat per-request pricing, price varies by effort level
export function calculatePriceFromConfig(
  config: PaymentConfig,
  model: string,
  effort: EffortLevel = "medium",
): number {
  const effortPrices = config.model_prices[effort]
  if (!effortPrices || !model) {
    return 0
  }

  const price = effortPrices[model]
  return price !== undefined ? Math.round(price * 100) / 100 : 0
}
