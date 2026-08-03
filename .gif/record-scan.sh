#!/usr/bin/env bash
# record-scan.sh - Record scan.gif demo

set -e

# Setup a project with a secret
rm -rf /tmp/scan_demo
mkdir -p /tmp/scan_demo
cd /tmp/scan_demo

# Create schema
cat > env.schema.toml << 'EOF'
[DATABASE_URL]
description = "DB connection"
secret = true

[STRIPE_KEY]
description = "Stripe API key"
secret = true
EOF

# Create code with a secret AND undeclared var
cat > app.py << 'EOF'
import os

# Oops! Hardcoded secret
STRIPE_SECRET = "sk_live_123456789abcdefghijk"

# Undeclared variable
DATABASE_URL = os.getenv("POSTGRES_URL")
EOF

# Create .env.example
cat > .env.example << 'EOF'
DATABASE_URL=
STRIPE_KEY=
EOF

# Show the problematic code
clear
echo "$ cat app.py"
cat app.py

echo ""
echo "$ envshield scan"
envshield scan || true

echo ""
echo "$ cat app.py  # (after fixing)"
cat > app.py << 'EOF'
import os

STRIPE_SECRET = os.getenv("STRIPE_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
EOF

cat app.py

echo ""
echo "$ envshield scan"
envshield scan
