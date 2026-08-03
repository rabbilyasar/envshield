#!/usr/bin/env bash
# record-doctor.sh - Record doctor.gif demo

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
head -15 env.schema.toml

echo ""
echo "$ cat .env.example"
cat .env.example

echo ""
echo "$ envshield doctor"
envshield doctor

echo ""
echo "$ envshield schema sync"
envshield schema sync

echo ""
echo "$ cat .env.example"
cat .env.example

echo ""
echo "$ envshield doctor"
envshield doctor
