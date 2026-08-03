#!/usr/bin/env bash
# record-setup.sh - Record setup.gif demo (with pauses for readability)

set -e

# Setup a project with schema and .env.example
rm -rf /tmp/setup_demo
mkdir -p /tmp/setup_demo
cd /tmp/setup_demo

# Create schema
cat > env.schema.toml << 'EOF'
[DATABASE_URL]
description = "PostgreSQL connection string"
secret = true

[API_PORT]
description = "Port the API listens on"
secret = false
defaultValue = "5000"

[STRIPE_KEY]
description = "Stripe API secret for payment processing"
secret = true
EOF

# Sync to create .env.example
envshield schema sync
sleep 1

# Show initial state
clear
echo "$ ls -la"
ls -la
sleep 2

echo ""
echo "$ cat env.schema.toml"
sleep 1
head -15 env.schema.toml
sleep 2

echo ""
echo "$ envshield setup"
echo ""
sleep 2

# Create responses file
cat > responses.txt << 'EOF'
postgres://localhost/mydb
5000
sk_test_123456789abcdefghijk
EOF

# Pipe responses to setup
cat responses.txt | envshield setup
sleep 3

# Show the created .env
echo ""
echo "$ cat .env"
sleep 1
cat .env
sleep 2

echo ""
echo "$ envshield check .env"
sleep 1
envshield check .env
sleep 2
