#!/usr/bin/env bash
# record-setup.sh - Record setup.gif demo

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

# Show initial state
clear
echo "$ ls -la"
ls -la

echo ""
echo "$ envshield setup"
echo ""

# Create responses file
cat > responses.txt << 'EOF'
postgres://localhost/mydb
5000
sk_test_123456789abcdefghijk
EOF

# Pipe responses to setup
cat responses.txt | envshield setup

# Show the created .env
echo ""
echo "$ cat .env"
cat .env

echo ""
echo "$ envshield check .env"
envshield check .env
