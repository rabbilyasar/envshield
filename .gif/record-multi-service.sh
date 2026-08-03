#!/usr/bin/env bash
# record-multi-service.sh - Record multi-service.gif demo (with pauses)

set -e

# Setup a multi-service project
rm -rf /tmp/multi_service_demo
mkdir -p /tmp/multi_service_demo/{services/api,services/web}
cd /tmp/multi_service_demo

# Create envshield.yml
cat > envshield.yml << 'EOF'
project_name: ecommerce
services:
  api:
    path: services/api/env.schema.toml
    description: Backend API
  web:
    path: services/web/env.schema.toml
    description: Frontend
EOF

# Create .env files for each service
cat > services/api/.env << 'EOF'
DATABASE_URL=postgres://localhost/api
STRIPE_KEY=sk_test_123456789
JWT_SECRET=secret123
EOF

cat > services/web/.env << 'EOF'
REACT_APP_API_URL=http://localhost:5000
REACT_APP_DEBUG=true
EOF

# Show the project structure
clear
echo "$ cat envshield.yml"
sleep 1
cat envshield.yml
sleep 3

echo ""
echo "$ envshield import services/api/.env --service api"
sleep 2
envshield import services/api/.env --service api
sleep 2

echo ""
echo "$ envshield import services/web/.env --service web"
sleep 2
envshield import services/web/.env --service web
sleep 2

# Show both schemas
echo ""
echo "$ ls services/*/env.schema.toml"
sleep 1
ls services/*/env.schema.toml
sleep 2

echo ""
echo "$ envshield scan --service api"
sleep 1
envshield scan --service api
sleep 2

echo ""
echo "$ envshield scan --service web"
sleep 1
envshield scan --service web
sleep 2
