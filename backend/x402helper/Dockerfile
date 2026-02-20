FROM node:20-slim

WORKDIR /app

# Copy package files
COPY x402helper/package.json ./

# Install dependencies
RUN npm install --production

# Copy source
COPY x402helper/index.js ./

# Expose port
EXPOSE 8085

# Start server
CMD ["node", "index.js"]
