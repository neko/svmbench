"use client"

import {
  ConnectionProvider,
  WalletProvider,
} from "@solana/wallet-adapter-react"
import { WalletModalProvider } from "@solana/wallet-adapter-react-ui"
import {
  PhantomWalletAdapter,
  SolflareWalletAdapter,
} from "@solana/wallet-adapter-wallets"
import { useMemo, type ReactNode } from "react"

import "@solana/wallet-adapter-react-ui/styles.css"

// Helius RPC endpoint for mainnet
const HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=dfa2ad47-9043-4b60-b60d-3bc1198f7489"

interface SolanaWalletProviderProps {
  children: ReactNode
}

export function SolanaWalletProvider({ children }: SolanaWalletProviderProps) {
  const endpoint = HELIUS_RPC

  // Explicit wallet adapters for proper connection flow
  const wallets = useMemo(
    () => [new PhantomWalletAdapter(), new SolflareWalletAdapter()],
    [],
  )

  return (
    <ConnectionProvider endpoint={endpoint}>
      <WalletProvider wallets={wallets} autoConnect={false}>
        <WalletModalProvider>{children}</WalletModalProvider>
      </WalletProvider>
    </ConnectionProvider>
  )
}
