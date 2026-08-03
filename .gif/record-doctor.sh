#!/usr/bin/env bash
# record-doctor.sh - Record doctor.gif demo (with pauses)

set -e

# Setup a project with some drift
rm -rf /tmp/doctor_demo
mkdir -p /tmp/doctor_demo
cd /tmp/doctor_demo

# Create schema with 3 vars
cat > env.schema.toml << 'EOF'
[DATABASE_URL]
description = "DB connection"
secret = true

[API_PORT]
description = "Port"
secret = false
defaultValue = "5000"

[NEW_FEATURE_FLAG]
description = "New feature toggle"
secret = false
EOF

# Create .env.example but leave it out of sync
cat > .env.example << 'EOF'
DATABASE_URL=
API_PORT=5000
EOF

# Create .git/hooks so doctor can check for it
mkdir -p .git/hooks

# Show initial state
clear
echo "$ cat env.schema.toml"
sleep 1
cat env.schema.toml
sleep 2

echo ""
echo "$ cat .env.example"
sleep 1
cat .env.example
sleep 2

echo ""
echo "❌ Notice: NEW_FEATURE_FLAG is in schema but NOT in .env.example"
sleep 2

echo ""
echo "$ envshield doctor"
sleep 2
envshield doctor
sleep 3

echo ""
echo "✅ Running schema sync to fix the drift..."
echo ""
echo "$ envshield schema sync"
sleep 2
envshield schema sync
sleep 2

echo ""
echo "$ cat .env.example"
sleep 1
cat .env.example
sleep 2

echo ""
echo "$ envshield doctor"
sleep 1
envshield doctor
sleep 2
