import {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
  VersionedTransaction,
} from '@solana/web3.js';
import { getAssociatedTokenAddress, getAccount } from '@solana/spl-token';
import { fetchQuote, swapFromSolana, type Quote } from '@mayanfinance/swap-sdk';
import bs58 from 'bs58';
import { config } from './config.js';

export interface BridgeResult {
  success: boolean;
  txSignature?: string;
  error?: string;
  quote?: {
    expectedAmountOut: number;
    eta: number;
    bridgeFee: number;
  };
}

export interface BridgeStatus {
  status: 'pending' | 'completed' | 'failed';
  txSignature?: string;
  error?: string;
}

// Cache for tracking bridge operations
const bridgeStatusCache = new Map<string, BridgeStatus>();

/**
 * Get a quote for bridging USDC from Solana to Base
 */
export async function getBridgeQuote(amountUsdc: number): Promise<Quote[]> {
  // Amount in base units (USDC has 6 decimals)
  const amountIn64 = Math.floor(amountUsdc * 1_000_000).toString();

  const quotes = await fetchQuote({
    amountIn64,
    fromToken: config.solanaUsdcMint,
    fromChain: 'solana',
    toToken: config.baseUsdcContract,
    toChain: 'base',
    slippageBps: 'auto',
  });

  return quotes;
}

/**
 * Execute bridge from Solana USDC to Base USDC
 */
export async function bridgeSolanaToBase(
  amountUsdc: number,
  operationId: string
): Promise<BridgeResult> {
  try {
    // Update status to pending
    bridgeStatusCache.set(operationId, { status: 'pending' });

    // Get the service wallet keypair
    const secretKey = bs58.decode(config.solanaWalletKey);
    const keypair = Keypair.fromSecretKey(secretKey);

    // Get Solana connection
    const connection = new Connection(config.solanaRpcUrl, 'confirmed');

    // Get quote
    const quotes = await getBridgeQuote(amountUsdc);
    if (!quotes || quotes.length === 0) {
      throw new Error('No bridge route available');
    }

    // Use the best quote (first one)
    const quote = quotes[0];
    console.log(`[Bridge] Using quote: ${quote.type}, ETA: ${quote.etaSeconds}s, Fee: $${quote.bridgeFee}`);

    // Create sign function for the swap
    const signSolanaTransaction = async (
      tx: Transaction | VersionedTransaction
    ): Promise<Transaction | VersionedTransaction> => {
      if (tx instanceof VersionedTransaction) {
        tx.sign([keypair]);
        return tx;
      } else {
        tx.partialSign(keypair);
        return tx;
      }
    };

    // Execute the swap
    const result = await swapFromSolana(
      quote,
      keypair.publicKey.toBase58(),
      config.baseWalletAddress, // Destination on Base
      null, // No referrer
      signSolanaTransaction as any,
      connection
    );

    console.log(`[Bridge] Transaction submitted: ${result.signature}`);

    // Update status to completed
    bridgeStatusCache.set(operationId, {
      status: 'completed',
      txSignature: result.signature,
    });

    return {
      success: true,
      txSignature: result.signature,
      quote: {
        expectedAmountOut: quote.expectedAmountOut,
        eta: quote.etaSeconds,
        bridgeFee: quote.bridgeFee,
      },
    };
  } catch (error) {
    const errorMsg = error instanceof Error ? error.message : 'Unknown error';
    console.error(`[Bridge] Error: ${errorMsg}`);

    bridgeStatusCache.set(operationId, {
      status: 'failed',
      error: errorMsg,
    });

    return {
      success: false,
      error: errorMsg,
    };
  }
}

/**
 * Get the status of a bridge operation
 */
export function getBridgeStatus(operationId: string): BridgeStatus | null {
  return bridgeStatusCache.get(operationId) || null;
}

/**
 * Check USDC balance on Solana service wallet
 */
export async function getSolanaUsdcBalance(): Promise<number> {
  const connection = new Connection(config.solanaRpcUrl, 'confirmed');
  const walletPubkey = new PublicKey(config.solanaWalletAddress);
  const mintPubkey = new PublicKey(config.solanaUsdcMint);

  try {
    // Get associated token account
    const ata = await getAssociatedTokenAddress(mintPubkey, walletPubkey);
    const account = await getAccount(connection, ata);
    // USDC has 6 decimals
    return Number(account.amount) / 1_000_000;
  } catch {
    return 0;
  }
}

/**
 * Verify a Solana USDC payment was received
 */
export async function verifyPaymentReceived(
  txSignature: string,
  expectedAmount: number
): Promise<{ verified: boolean; actualAmount?: number; error?: string }> {
  const connection = new Connection(config.solanaRpcUrl, 'confirmed');

  try {
    const tx = await connection.getTransaction(txSignature, {
      commitment: 'confirmed',
      maxSupportedTransactionVersion: 0,
    });

    if (!tx) {
      return { verified: false, error: 'Transaction not found' };
    }

    if (tx.meta?.err) {
      return { verified: false, error: 'Transaction failed' };
    }

    // Check post token balances for USDC transfer to our wallet
    const postBalances = tx.meta?.postTokenBalances || [];
    const preBalances = tx.meta?.preTokenBalances || [];

    for (const post of postBalances) {
      if (
        post.mint === config.solanaUsdcMint &&
        post.owner === config.solanaWalletAddress
      ) {
        // Find matching pre-balance
        const pre = preBalances.find(
          (p) => p.accountIndex === post.accountIndex
        );
        const preAmount = pre?.uiTokenAmount?.uiAmount || 0;
        const postAmount = post.uiTokenAmount?.uiAmount || 0;
        const received = postAmount - preAmount;

        // Allow small tolerance for rounding
        const tolerance = 0.001;
        if (received >= expectedAmount - tolerance) {
          return { verified: true, actualAmount: received };
        }
      }
    }

    return { verified: false, error: 'Payment not found in transaction' };
  } catch (error) {
    return {
      verified: false,
      error: error instanceof Error ? error.message : 'Verification failed',
    };
  }
}
