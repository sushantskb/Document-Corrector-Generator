#!/bin/bash

echo "📦 Document Correction Platform - Phase 1 Setup"
echo "=============================================="
echo ""

# Check Node.js
echo "✓ Checking Node.js..."
if ! command -v node &> /dev/null; then
    echo "✗ Node.js not found. Install it first."
    exit 1
fi
echo "  Node.js $(node --version)"

# Check MongoDB
echo "✓ Checking MongoDB..."
if ! command -v mongosh &> /dev/null; then
    echo "⚠ MongoDB not found. Install with: brew install mongodb-community"
fi

# Setup Node.js app
echo ""
echo "📥 Installing Next.js dependencies..."
cd app
npm install 2>&1 | tail -5
cd ..

# Create .env if not exists
if [ ! -f ".env" ]; then
    echo ""
    echo "📝 Creating .env file..."
    cp .env.example .env
    echo "   Fill in your Cloudinary credentials in .env"
fi

echo ""
echo "✅ Phase 1 Setup Complete!"
echo ""
echo "🚀 To start:"
echo "   1. Make sure MongoDB is running: brew services start mongodb-community"
echo "   2. cd app && npm run dev"
echo "   3. Open http://localhost:3000"
echo ""
echo "📚 For Phase 2 setup, see PHASE1_QUICKSTART.md"
