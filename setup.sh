#!/bin/bash
# Setup script for GitHub Codespaces

echo "🚀 Setting up AI Equity Research environment..."

# Install dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# Initialize database
echo "🗄️ Initializing database..."
cd backend
python -c "from data.storage.db import init_db; init_db()"
cd ..

# Make run script executable
chmod +x run.sh

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start the application:"
echo "  Backend:  cd backend && uvicorn app:app --host 0.0.0.0 --port 8001"
echo "  Frontend: cd frontend && python -m http.server 3000"
echo ""
echo "Or run: ./run.sh"
