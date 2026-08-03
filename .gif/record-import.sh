#!/usr/bin/env bash
# record-import.sh - Record import.gif demo (with pauses for readability)

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
sleep 3

# Import it
echo ""
echo "$ envshield import .env"
echo ""
sleep 2

envshield import .env
sleep 3

# Show the generated schema
echo ""
echo "$ cat env.schema.toml"
sleep 1
cat env.schema.toml
sleep 3

# Show the sync
echo ""
echo "$ envshield schema sync"
echo ""
sleep 1
envshield schema sync
sleep 2

echo ""
echo "$ ls -la *.toml .env.example"
sleep 1
ls -la *.toml .env.example 2>/dev/null || true
sleep 2
