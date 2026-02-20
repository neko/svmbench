// Configuration from environment variables

export const config = {
  port: parseInt(process.env.BRIDGE_HELPER_PORT || '8085'),

  // Solana service wallet (receives USDC from users)
  solanaWalletAddress: process.env.SOLANA_SERVICE_WALLET_ADDRESS || '',
  solanaWalletKey: process.env.SOLANA_SERVICE_WALLET_KEY || '',

  // Base service wallet (destination for bridged USDC)
  baseWalletAddress: process.env.BASE_SERVICE_WALLET_ADDRESS || '',
  baseWalletKey: process.env.BASE_SERVICE_WALLET_KEY || '',

  // RPC endpoints
  solanaRpcUrl: process.env.SOLANA_RPC_URL || 'https://api.mainnet-beta.solana.com',
  baseRpcUrl: process.env.BASE_RPC_URL || 'https://mainnet.base.org',

  // USDC contract addresses
  solanaUsdcMint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
  baseUsdcContract: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', // USDC on Base
};

export function validateConfig() {
  const required = [
    'solanaWalletAddress',
    'solanaWalletKey',
    'baseWalletAddress',
  ] as const;

  for (const key of required) {
    if (!config[key]) {
      throw new Error(`Missing required config: ${key}`);
    }
  }
}
