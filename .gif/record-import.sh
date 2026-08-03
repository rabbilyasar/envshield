#!/usr/bin/env bash
# record-import.sh - Record import.gif demo

set -e

# Setup
rm -rf /tmp/import_demo
mkdir -p /tmp/import_demo
cd /tmp/import_demo

# Create a messy .env file
cat > .env << 'EOF'
DATABASE_URL=postgres://localhost/myapp
STRIPE_KEY=sk_test_123456789abcdefghijk
DEBUG=true
LOG_LEVEL=info
API_PORT=5000
JWT_SECRET=my-secret-key
EOF

# Show the current .env
clear
echo "$ cat .env"
cat .env

# Import it
echo ""
echo "$ envshield import .env"
echo ""

envshield import .env

# Show the generated schema
echo ""
echo "$ cat env.schema.toml"
cat env.schema.toml

# Show the sync
echo ""
echo "$ envshield schema sync"
envshield schema sync

echo ""
echo "$ ls -la *.toml .env.example 2>/dev/null || true"
ls -la *.toml .env.example 2>/dev/null || true
