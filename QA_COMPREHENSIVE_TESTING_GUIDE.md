# EnvShield Comprehensive QA Testing Guide

## Senior QA Engineer's Manual

This document provides exhaustive testing procedures covering all features, scenarios, project types, and edge cases for EnvShield integration.

---

## Table of Contents

1. [Test Environment Setup](#test-environment-setup)
2. [Feature Test Matrix](#feature-test-matrix)
3. [Lifecycle Improvements Testing](#lifecycle-improvements-testing)
4. [C6 Diff-Aware Scanning Testing](#c6-diff-aware-scanning-testing)
5. [Multi-Service Projects Testing](#multi-service-projects-testing)
6. [Single-Service Projects Testing](#single-service-projects-testing)
7. [Edge Cases & Error Scenarios](#edge-cases--error-scenarios)
8. [Regression Testing](#regression-testing)
9. [Performance Testing](#performance-testing)
10. [Security Testing](#security-testing)

---

## Test Environment Setup

### 2.1 Prerequisites

```bash
# Install EnvShield from development directory
cd /home/rabbil/dev/envshield
pip install -e .

# Verify installation
envshield --version  # Should show version
which envshield      # Should show the executable path

# Install test dependencies
pip install pytest pytest-mock

# Verify git is available
git --version
```

### 2.2 Test Data Preparation

Create isolated test environments:

```bash
# Create master test directory
mkdir -p /tmp/envshield-qa-tests
cd /tmp/envshield-qa-tests

# Create subdirectories for different test types
mkdir -p multi-service-python
mkdir -p multi-service-typescript
mkdir -p single-service-python
mkdir -p single-service-typescript
mkdir -p edge-cases
mkdir -p regression-tests

# Initialize git repos for each
for dir in */; do
  cd "$dir"
  git init
  git config user.email "qa-test@example.com"
  git config user.name "QA Tester"
  cd ..
done
```

### 2.3 Safe Test Data Guidelines

```bash
# Use OBVIOUSLY FAKE secrets for testing
# Patterns that match detection but are clearly fake:

# For generic secret pattern:
"super_secret_key_with_long_content_12345"
"test_api_key_with_long_value_xyz123"

# For specific patterns that won't trigger real secret detection:
"old_fake_password_123"
"dev_api_token_test_value"

# NEVER use real-looking patterns like:
# - sk_live_* (Stripe)
# - AKIA* (AWS)
# - ghp_* (GitHub)
# - Any actual key formats

# Test verification command:
envshield scan test_file.py --config envshield.yml
# If it doesn't flag it, it's safe
```

---

## Feature Test Matrix

### Coverage Overview

| Feature | Success Cases | Fail Cases | Edge Cases | Multi-Service | Single-Service |
|---------|---------------|-----------|-----------|---|---|
| `envshield init` | 5 | 4 | 3 | ✓ | ✓ |
| `envshield service discover` | 6 | 5 | 4 | ✓ | - |
| `envshield setup` | 7 | 5 | 4 | ✓ | ✓ |
| Lifecycle hooks | 8 | 4 | 5 | ✓ | ✓ |
| C6 diff-aware scanning | 9 | 6 | 7 | ✓ | ✓ |
| Secret scanning | 6 | 5 | 5 | ✓ | ✓ |
| Config generation | 5 | 4 | 3 | ✓ | ✓ |

**Total test cases: 51 success, 33 fail, 31 edge cases**

---

## Lifecycle Improvements Testing

### Test Suite 1: `envshield init` Command

#### 1.1.1 SUCCESS: Fresh repository initialization

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-init-fresh
cd /tmp/envshield-qa-tests/test-init-fresh
git init
git config user.email "qa@test.com"
git config user.name "QA Test"
```

**Test Steps**:
1. Run `envshield init`
2. When prompted "Install git hooks?", answer **YES**
3. Verify all outputs
4. Verify file creation
5. Verify hook installation

**Expected Results**:
```
✓ EnvShield setup message shown
✓ Project type detected (generic or framework-specific)
✓ env.schema.toml created with default schema
✓ envshield.yml created with project metadata
✓ Hook prompt shown with default "Y"
✓ Pre-commit hook installed and executable
✓ Post-merge hook installed and executable
✓ Next-step guidance shown
✓ All files have correct permissions (755 for hooks)
```

**Verification Commands**:
```bash
# Check schema file
test -f env.schema.toml && echo "PASS: schema exists" || echo "FAIL"

# Check config file
test -f envshield.yml && echo "PASS: config exists" || echo "FAIL"

# Check hooks
test -x .git/hooks/pre-commit && echo "PASS: pre-commit executable"
test -x .git/hooks/post-merge && echo "PASS: post-merge executable"

# Verify hook content
grep -q "envshield scan --staged" .git/hooks/pre-commit && echo "PASS"
grep -q "envshield doctor" .git/hooks/post-merge && echo "PASS"

# Check permissions
stat -f "%OLp" .git/hooks/pre-commit  # Should show 755
```

---

#### 1.1.2 SUCCESS: Initialize with hook declination

**Test Steps**:
1. Same as 1.1.1, but answer **NO** to hook prompt
2. Verify files created but hooks not installed

**Expected Results**:
```
✓ env.schema.toml created
✓ envshield.yml created
✓ Pre-commit hook NOT installed
✓ Post-merge hook NOT installed
✓ Output shows hook installation was skipped
✓ User informed of alternative installation method
```

---

#### 1.1.3 SUCCESS: Initialize in non-git directory

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-init-no-git
cd /tmp/envshield-qa-tests/test-init-no-git
# NOTE: No git init

envshield init
```

**Expected Results**:
```
✓ env.schema.toml created
✓ envshield.yml created
⚠️  Hook installation skipped (not in git repo)
✓ Message shown about initializing git first
✓ No error/crash
```

---

#### 1.1.4 SUCCESS: Re-initialize existing project (without --force)

**Test Steps**:
```bash
cd /tmp/envshield-qa-tests/test-init-fresh
# Already has env.schema.toml and envshield.yml

envshield init
```

**Expected Results**:
```
⚠️  An EnvShield setup already exists
? Use '--force' to overwrite
✓ Command exits gracefully without changes
```

---

#### 1.1.5 SUCCESS: Force re-initialize

**Test Steps**:
```bash
cd /tmp/envshield-qa-tests/test-init-fresh
envshield init --force

# When prompted: "Are you sure?", answer YES
```

**Expected Results**:
```
? Are you sure? (files will be overwritten)
✓ Files overwritten with new defaults
✓ New hooks installed (prompting happens again)
✓ Old files completely replaced
```

---

#### 1.1.6 FAIL: Initialize with corrupted git state

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-init-bad-git
cd /tmp/envshield-qa-tests/test-init-bad-git
git init

# Corrupt git config
echo "CORRUPTED" > .git/config

envshield init
```

**Expected Results**:
```
✓ env.schema.toml and envshield.yml still created
⚠️  Hook installation fails gracefully
✓ User informed: "Could not install hooks: [error details]"
✓ No crash, exit code indicates failure
✓ Files created but hooks not installed
```

---

#### 1.1.7 FAIL: Initialize without write permissions

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-init-readonly
cd /tmp/envshield-qa-tests/test-init-readonly
git init

# Remove write permissions
chmod 555 .

envshield init
```

**Expected Results**:
```
✗ env.schema.toml creation fails
✗ Clear error message: "Permission denied" or similar
✓ Exits with error code 1
✗ No partial files left behind
```

**Cleanup**:
```bash
chmod 755 .
```

---

#### 1.1.8 FAIL: Initialize in git repo with existing hooks

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-init-existing-hooks
cd /tmp/envshield-qa-tests/test-init-existing-hooks
git init

# Create custom pre-commit hook
mkdir -p .git/hooks
echo '#!/bin/sh' > .git/hooks/pre-commit
echo 'echo "Custom hook"' >> .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

envshield init
# Answer YES to hook installation
```

**Expected Results**:
```
✓ Prompts: "A pre-commit hook already exists. Overwrite? (y/n)"
# If YES:
✓ Old hook backed up or replaced
✓ EnvShield hook installed
# If NO:
✓ Hook installation skipped
⚠️  User informed to manually add envshield command to existing hook
```

---

### Test Suite 2: `envshield service discover` Command

#### 1.2.1 SUCCESS: Discover Python services (Flask/Django pattern)

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-discover-python
cd /tmp/envshield-qa-tests/test-discover-python
git init
git config user.email "qa@test.com"
git config user.name "QA Test"

# Create Flask service structure
mkdir -p api/config
cat > api/config/env_config.py << 'EOF'
DATABASE_URL = ""
API_KEY = ""
DEBUG = False
CACHE_HOST = "localhost"
EOF

# Create Django-like service
mkdir -p dashboard/settings
cat > dashboard/settings/local.py << 'EOF'
SECRET_KEY = ""
DB_ENGINE = "postgresql"
ALLOWED_HOSTS = ["localhost"]
EOF

# Create dotenv service
mkdir -p worker
cat > worker/.env << 'EOF'
QUEUE_URL=""
WORKER_THREADS=4
LOG_LEVEL=INFO
EOF
```

**Test Steps**:
1. Run `envshield service discover`
2. Review discovered services table
3. Select "All" services
4. Answer YES to hook installation
5. Verify envshield.yml

**Expected Results**:
```
✓ Table shows 3 discovered services:
  - api (python format, config/env_config.py)
  - dashboard (python format, settings/local.py)
  - worker (dotenv format, .env)

✓ Can select individual services
✓ Can select "All"
✓ Can cancel without changes

✓ For each selected:
  ✓ Service added to envshield.yml
  ✓ Schema file created: api/env.schema.toml, etc.
  ✓ Schema pre-populated from source files

✓ Hook installation prompt appears
✓ Hooks installed if YES

✓ envshield.yml contains:
  services:
    api:
      path: api/env.schema.toml
      local_file: api/config/env_config.py
    dashboard:
      path: dashboard/env.schema.toml
      local_file: dashboard/settings/local.py
    worker:
      path: worker/env.schema.toml
```

**Verification**:
```bash
# Count variables discovered
grep -c "^\[" api/env.schema.toml       # Should be 4
grep -c "^\[" dashboard/env.schema.toml # Should be 3
grep -c "^\[" worker/env.schema.toml    # Should be 3

# Check schema structure
grep "secret = " api/env.schema.toml | wc -l  # Should have some
```

---

#### 1.2.2 SUCCESS: Discover Node.js services (TypeScript/JavaScript)

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-discover-node
cd /tmp/envshield-qa-tests/test-discover-node
git init
git config user.email "qa@test.com"
git config user.name "QA Test"

# Create Next.js service
mkdir -p frontend
cat > frontend/.env.local << 'EOF'
NEXT_PUBLIC_API_URL=http://localhost:3000
NEXT_PUBLIC_APP_NAME=MyApp
DATABASE_URL=
API_SECRET=
EOF

# Create Express backend
mkdir -p backend
cat > backend/.env << 'EOF'
PORT=3001
DATABASE_URL=
JWT_SECRET=
CORS_ORIGIN=http://localhost:3000
EOF

# Create service with package.json
mkdir -p services/notification
cat > services/notification/package.json << 'EOF'
{
  "name": "notification-service",
  "version": "1.0.0"
}
EOF
cat > services/notification/.env << 'EOF'
SMTP_HOST=
SMTP_USER=
SMTP_PASS=
EOF
```

**Test Steps**:
1. Run `envshield service discover`
2. Verify framework detection (Next.js, Express, etc.)
3. Select all services

**Expected Results**:
```
✓ Detected 3 services:
  - frontend (dotenv, .env.local)
  - backend (dotenv, .env)
  - notification (dotenv, .env)

✓ Schema created for each
✓ NEXT_PUBLIC_* vars NOT marked as secrets
✓ Regular secrets marked correctly
✓ Framework hints included in detection
```

---

#### 1.2.3 SUCCESS: Discover mixed Python and JavaScript monorepo

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-discover-mixed
cd /tmp/envshield-qa-tests/test-discover-mixed
git init

# Python service
mkdir -p backend/api
cat > backend/api/settings.py << 'EOF'
DATABASE_URL = ""
API_KEY = ""
EOF

# TypeScript service
mkdir -p frontend
cat > frontend/.env << 'EOF'
REACT_APP_API_URL=
REACT_APP_VERSION=1.0
SECRET_TOKEN=
EOF

# Another Python service
mkdir -p workers/email
cat > workers/email/config.py << 'EOF'
MAIL_SERVER = ""
MAIL_PASSWORD = ""
EOF
```

**Test Steps**:
1. Run `envshield service discover`
2. Verify all 3 services detected with correct formats

**Expected Results**:
```
✓ backend (python)
✓ frontend (dotenv)  
✓ workers/email (python)

✓ Language detection works across directories
✓ Can handle monorepo with mixed languages
```

---

#### 1.2.4 FAIL: Discover in empty directory

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-discover-empty
cd /tmp/envshield-qa-tests/test-discover-empty
git init

envshield service discover
```

**Expected Results**:
```
✓ Message: "No new service-like directories found"
✓ No error, graceful exit
✓ No files created or modified
```

---

#### 1.2.5 FAIL: Discover with malformed config files

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-discover-malformed
cd /tmp/envshield-qa-tests/test-discover-malformed
git init

mkdir -p badservice
cat > badservice/config.py << 'EOF'
import os
syntax error here!!!
DATABASE_URL = os.getenv
EOF

# Invalid .env
mkdir -p badenv
cat > badenv/.env << 'EOF'
KEY = VALUE
INVALID_LINE_WITHOUT_EQUALS
EOF
```

**Test Steps**:
1. Run `envshield service discover`

**Expected Results**:
```
✓ Services still discovered (malformed files are tolerated)
✓ Schema generated with available data
⚠️  Parsing skips invalid lines
✓ No crash, continues with other services
```

---

#### 1.2.6 FAIL: Discover without git repository

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-discover-no-git
cd /tmp/envshield-qa-tests/test-discover-no-git
# NO git init

mkdir -p service1
cat > service1/.env << 'EOF'
KEY=VALUE
EOF

envshield service discover
```

**Expected Results**:
```
✓ Services still discovered
✗ Hook installation prompt doesn't appear (not in git)
⚠️  Message: "Not in a git repository. Cannot install hooks."
✓ Services registered in envshield.yml
```

---

#### 1.2.7 SUCCESS: Re-discover after adding new service

**Setup**:
```bash
# Use test-discover-python from 1.2.1
cd /tmp/envshield-qa-tests/test-discover-python

# Add a new service AFTER initial discovery
mkdir -p notifications
cat > notifications/.env << 'EOF'
SLACK_TOKEN=
WEBHOOK_URL=
EOF

# Run discover again
envshield service discover
```

**Expected Results**:
```
✓ Only NEW service (notifications) shown in table
✓ Existing services (api, dashboard, worker) NOT shown again
✓ Can add notifications without re-registering old services
✓ envshield.yml updated with notifications entry
✓ Old entries preserved
```

---

#### 1.2.8 SUCCESS: Selective service registration

**Test Steps**:
```bash
# From test-discover-python
cd /tmp/envshield-qa-tests/test-discover-python

# Run discover
envshield service discover

# At the prompt, select ONLY 'api' (not all)
# Then select 'dashboard' in a second run
```

**Expected Results**:
```
✓ First run: Only api registered
✓ Second run: discovers worker, allows adding it
✓ envshield.yml now has: api, dashboard, worker
✓ Each run is additive, not destructive
```

---

### Test Suite 3: `envshield setup` Command

#### 1.3.1 SUCCESS: Setup single service with all required vars

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-setup-single
cd /tmp/envshield-qa-tests/test-setup-single
git init

mkdir -p myapi/config
cat > myapi/env.schema.toml << 'EOF'
[DATABASE_URL]
description = "PostgreSQL connection string"
secret = true

[API_PORT]
description = "Port the API runs on"
secret = false
defaultValue = "5000"

[DEBUG]
description = "Enable debug mode"
secret = false
defaultValue = "false"
EOF

cat > envshield.yml << 'EOF'
project_name: test-project
services:
  myapi:
    path: myapi/env.schema.toml
EOF
```

**Test Steps**:
1. Run `envshield setup --service myapi`
2. At DATABASE_URL prompt, enter a fake connection string
3. Accept default for API_PORT (press Enter)
4. Accept default for DEBUG (press Enter)
5. When hooks prompt appears, select YES

**Expected Results**:
```
✓ Prompts for DATABASE_URL with (password) indicator
✓ Input hidden with ••••••••••
✓ Prompts for API_PORT with default shown
✓ User can press Enter to accept defaults
✓ .env created with all values
✓ Hooks prompt appears after setup
✓ Hooks installed if YES

✓ myapi/.env contains:
  DATABASE_URL="postgres://..."
  API_PORT=5000
  DEBUG=false
```

**Verification**:
```bash
cat myapi/.env

# Should show all vars, no empty lines for vars with defaults
grep -c "^" myapi/.env  # Should have 3 lines

# Hooks installed
test -x .git/hooks/pre-commit && echo "PASS"
```

---

#### 1.3.2 SUCCESS: Setup multiple services interactively

**Test Steps**:
```bash
# Using test-discover-python setup
cd /tmp/envshield-qa-tests/test-discover-python

envshield setup
# At prompt: select "All" (or "all" depending on format)
```

**Expected Results**:
```
? Which service? (api / dashboard / worker / all)
> all

✓ Walks through api service setup
  - Prompts for all vars
  
✓ Then walks through dashboard service setup
  
✓ Then walks through worker service setup

✓ All three .env files created:
  api/.env
  dashboard/.env
  worker/.env

✓ Single hook installation prompt at the end
✓ Hook prompt mentions all services
```

---

#### 1.3.3 SUCCESS: Setup with optional variables

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-setup-optional
cd /tmp/envshield-qa-tests/test-setup-optional
git init

mkdir -p app

# Create schema with optional vars
cat > app/env.schema.toml << 'EOF'
[DATABASE_URL]
description = "Database connection"
secret = true

[CACHE_URL]
description = "Redis cache (optional)"
secret = true
required = false

[LOG_LEVEL]
description = "Logging level"
secret = false
defaultValue = "INFO"
required = false
EOF

cat > envshield.yml << 'EOF'
project_name: test
services:
  app:
    path: app/env.schema.toml
EOF
```

**Test Steps**:
1. Run `envshield setup --service app`
2. Provide DATABASE_URL
3. At CACHE_URL prompt, press Enter (skip optional)
4. Accept LOG_LEVEL default

**Expected Results**:
```
✓ DATABASE_URL: required, must provide value
✓ CACHE_URL: marked as (optional), can press Enter
✓ LOG_LEVEL: has default, can skip

✓ app/.env contains:
  DATABASE_URL="..."
  # CACHE_URL skipped (not in file)
  LOG_LEVEL=INFO
```

---

#### 1.3.4 SUCCESS: Setup with secret input masking

**Test Steps**:
```bash
# Using any setup test
# At a secret variable prompt, type password and watch for masking
envshield setup

# Type: mysecret123
# Should see: •••••••••••
```

**Expected Results**:
```
✓ Secret variables show (password) indicator
✓ Input is masked with dots
✓ Backspace removes dots but doesn't echo characters
✓ Value is stored correctly in .env
```

---

#### 1.3.5 SUCCESS: Setup with type coercion

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-setup-types
cd /tmp/envshield-qa-tests/test-setup-types
git init

mkdir -p app

cat > app/env.schema.toml << 'EOF'
[PORT]
description = "Server port"
secret = false
defaultValue = "3000"

[TIMEOUT_MS]
description = "Timeout in milliseconds"
secret = false
defaultValue = "5000"

[DEBUG]
description = "Debug mode"
secret = false
defaultValue = "false"
EOF

cat > envshield.yml << 'EOF'
services:
  app:
    path: app/env.schema.toml
EOF
```

**Test Steps**:
1. Run `envshield setup --service app`
2. At PORT, enter "8080" (string that should be int)
3. At TIMEOUT_MS, enter "10000"
4. At DEBUG, enter "true"

**Expected Results**:
```
✓ Values accepted and stored
✓ app/.env contains:
  PORT=8080
  TIMEOUT_MS=10000
  DEBUG=true
```

---

#### 1.3.6 FAIL: Setup without required variable

**Test Steps**:
```bash
# Using test-setup-single
cd /tmp/envshield-qa-tests/test-setup-single

envshield setup --service myapi

# At DATABASE_URL prompt, press Enter (leave empty)
```

**Expected Results**:
```
✓ Error message: "This is a required variable. Please provide a value."
✓ Re-prompts: "[DATABASE_URL] PostgreSQL connection string"
✓ User must provide value to continue
```

---

#### 1.3.7 FAIL: Setup in directory without schema

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-setup-no-schema
cd /tmp/envshield-qa-tests/test-setup-no-schema
git init

# No env.schema.toml, no envshield.yml

envshield setup
```

**Expected Results**:
```
✗ Error: "No schema found. Run envshield import or envshield init first."
✗ No .env file created
✓ Clear guidance on next steps
```

---

#### 1.3.8 FAIL: Setup with corrupted schema file

**Test Steps**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-setup-bad-schema
cd /tmp/envshield-qa-tests/test-setup-bad-schema
git init

mkdir -p app

cat > app/env.schema.toml << 'EOF'
This is not valid TOML!!!
[BROKEN
EOF

cat > envshield.yml << 'EOF'
services:
  app:
    path: app/env.schema.toml
EOF

envshield setup --service app
```

**Expected Results**:
```
✗ Error: "Failed to parse schema: [error details]"
✓ Helpful error message pointing to the line
✗ No .env created
```

---

### Test Suite 4: Git Hooks Lifecycle

#### 1.4.1 SUCCESS: Pre-commit hook blocks secrets

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-hook-precommit
cd /tmp/envshield-qa-tests/test-hook-precommit
git init
git config user.email "qa@test.com"
git config user.name "QA Test"

# Install hooks via init
envshield init --force
# Answer YES to hooks
```

**Test Steps**:
1. Create a file with a secret
```bash
cat > config.py << 'EOF'
API_KEY = "super_secret_key_with_long_content_here"
DEBUG = False
EOF
```

2. Try to commit
```bash
git add config.py
git commit -m "Add config"
```

**Expected Results**:
```
✗ Commit blocked
✗ Output:
  🚨 DANGER: Found 1 potential secret(s)!
  Line 1: API_KEY = "super_secret_key_with_long_content_here"
  Secret Type: Generic API Key
  
  Commit aborted. Please fix the issues above before committing.

✓ Exit code: 1
✓ File still in staging area
✓ Commit not created
```

---

#### 1.4.2 SUCCESS: Pre-commit hook allows clean commits

**Setup**:
```bash
# Use test-hook-precommit, but add clean files

cat > app.py << 'EOF'
def main():
    debug = os.getenv("DEBUG", "False")
    port = int(os.getenv("PORT", "5000"))
    return port
EOF

cat > .env << 'EOF'
DEBUG=false
PORT=5000
EOF
```

**Test Steps**:
```bash
git add .env app.py
git commit -m "Add clean config"
```

**Expected Results**:
```
✓ Commit succeeds
✓ Git shows files committed
✓ No hook output (clean)
✓ Commit appears in git log
```

---

#### 1.4.3 SUCCESS: Post-merge hook runs when schemas change

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-hook-postmerge
cd /tmp/envshield-qa-tests/test-hook-postmerge
git init
git config user.email "qa@test.com"
git config user.name "QA Test"

# Setup initial project
envshield init --force
# Answer YES

git add .
git commit -m "Initial envshield setup"

# Create feature branch
git checkout -b feature/add-vars
```

**Test Steps**:

1. Modify schema
```bash
echo "" >> env.schema.toml
echo "[NEW_VARIABLE]" >> env.schema.toml
echo "description = \"A new variable\"" >> env.schema.toml
git add env.schema.toml
git commit -m "Add new var to schema"
```

2. Merge back to main
```bash
git checkout main
git merge feature/add-vars
```

**Expected Results**:
```
✓ Merge completes
✓ Post-merge hook runs automatically
✓ Output:
  ⚠️  Your config is out of sync
  Missing: NEW_VARIABLE
  Run: envshield setup

✓ User knows action is needed
```

---

#### 1.4.4 SUCCESS: Post-merge hook silent when no schema changes

**Setup**:
```bash
# Use test-hook-postmerge state

git checkout -b feature/code-only

# Make code change (no schema)
echo "# new comment" >> README.md
git add README.md
git commit -m "Update docs"

git checkout main
git merge feature/code-only
```

**Expected Results**:
```
✓ Merge completes
✓ Post-merge hook runs
✓ NO output (schema unchanged)
✓ Clean merge message
```

---

#### 1.4.5 FAIL: Hook fails but commit can be forced

**Test Steps**:
```bash
# Create file with secret
cat > secret.py << 'EOF'
PASSWORD = "super_secret_key_with_long_content_here"
EOF

git add secret.py

# Try to commit
git commit -m "Fix"

# (Blocked by hook)

# Force push (if needed for special case)
git commit --no-verify -m "Force commit"
```

**Expected Results**:
```
✓ First commit: blocked by hook
✗ After --no-verify: committed anyway
⚠️  But secret is now in git history
```

**Note**: This test shows why --no-verify exists but shouldn't be used carelessly.

---

#### 1.4.6 FAIL: Manually deleted hook

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-hook-deleted
cd /tmp/envshield-qa-tests/test-hook-deleted
git init

envshield init --force
# Answer YES (hooks installed)

# Simulate accidental deletion
rm .git/hooks/pre-commit
rm .git/hooks/post-merge
```

**Test Steps**:
1. Create file with secret
2. Try to commit

**Expected Results**:
```
✗ Commit succeeds (hook missing!)
⚠️  Secret gets committed
✓ User should run `envshield install-hook` to reinstall
```

**Remediation**:
```bash
envshield install-hook
# Re-installs hooks

# Now try committing again with secret
git add secret.py
git commit -m "Try again"

# (Should be blocked)
```

---

## C6 Diff-Aware Scanning Testing

### Test Suite 5: Diff Detection

#### 2.1.1 SUCCESS: Detects newly-added lines in excluded file

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-c6-new-lines
cd /tmp/envshield-qa-tests/test-c6-new-lines
git init
git config user.email "qa@test.com"
git config user.name "QA Test"

# Create config with pre-existing fake secrets (like Zeus's env_config.local.py)
mkdir -p config
cat > config/env_config.local.py << 'EOF'
# Pre-existing fake secrets (15 of them)
DB_PASSWORD = 'fake_password_1'
API_KEY = 'fake_api_key_1'
SECRET_TOKEN = 'fake_secret_token_1'
# ... (imagine 12 more fake ones)
EOF

# Create envshield.yml with this file excluded
cat > envshield.yml << 'EOF'
project_name: test
secret_scanning:
  exclude_files:
    - "config/env_config.local.py"
EOF

# Initial commit with the fake secrets
git add .
git commit -m "Initial config with fakes"
```

**Test Steps**:
1. Add a REAL secret to the file
```bash
echo "PRODUCTION_SECRET = 'real_secret_key_with_long_production_content_here'" >> config/env_config.local.py
```

2. Stage the file
```bash
git add config/env_config.local.py
```

3. Run scan
```bash
envshield scan --staged
```

**Expected Results**:
```
✓ Output:
  ℹ️  env_config.local.py (excluded; diffs only: 1 new line(s))
  🚨 DANGER: Found 1 potential secret(s)!
  
✓ Only NEW secret is flagged
✓ The 15 pre-existing fakes are NOT flagged
✓ Commit is blocked
```

**Verification**:
```bash
# Confirm only 1 secret found, not 16
envshield scan --staged 2>&1 | grep -c "potential secret"  # Should be 1

# Undo and verify clean state
git checkout config/env_config.local.py
git add config/env_config.local.py
envshield scan --staged
# Should show: "No issues found"
```

---

#### 2.1.2 SUCCESS: No new lines = skip excluded file

**Test Steps**:
```bash
# Using test-c6-new-lines setup

# Stage file WITHOUT any changes
git add config/env_config.local.py

envshield scan --staged
```

**Expected Results**:
```
✓ Output:
  ℹ️  env_config.local.py (excluded; diffs only: 0 new line(s))
  ✓ No issues found

✓ File skipped (no new lines)
✓ Pre-existing fakes not scanned
```

---

#### 2.1.3 SUCCESS: Multiple new lines, scan only new ones

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-c6-multi-new
cd /tmp/envshield-qa-tests/test-c6-multi-new
git init

mkdir -p config

# Create file with 5 fake secrets
cat > config/settings.py << 'EOF'
FAKE_1 = 'fake_value_1'
FAKE_2 = 'fake_value_2'
FAKE_3 = 'fake_value_3'
FAKE_4 = 'fake_value_4'
FAKE_5 = 'fake_value_5'
EOF

cat > envshield.yml << 'EOF'
secret_scanning:
  exclude_files:
    - "config/settings.py"
EOF

git add .
git commit -m "Initial"
```

**Test Steps**:
1. Add 3 new lines to the file
```bash
cat >> config/settings.py << 'EOF'
REAL_SECRET_1 = 'real_secret_key_with_long_content_here'
REAL_SECRET_2 = 'another_real_secret_key_with_long_content'
DEBUG = 'false'
EOF
```

2. Stage and scan
```bash
git add config/settings.py
envshield scan --staged
```

**Expected Results**:
```
✓ Output:
  ℹ️  settings.py (excluded; diffs only: 3 new line(s))
  🚨 DANGER: Found 2 potential secret(s)!
  
✓ Only the 2 REAL secrets flagged
✓ The 3 DEBUG line not flagged (not a secret)
✓ The 5 pre-existing FAKE lines not flagged
```

---

#### 2.1.4 SUCCESS: Brand new file scanned in full despite exclusion

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-c6-brand-new
cd /tmp/envshield-qa-tests/test-c6-brand-new
git init

# Create envshield.yml that excludes a file that doesn't exist yet
cat > envshield.yml << 'EOF'
secret_scanning:
  exclude_files:
    - "new_config.py"
EOF

git add envshield.yml
git commit -m "Add config"
```

**Test Steps**:
1. Create completely new file with secret (NOT in git yet)
```bash
cat > new_config.py << 'EOF'
# This file was never in git before
SECRET = 'real_secret_key_with_long_content_here'
NORMAL = 'value'
EOF
```

2. Stage and scan
```bash
git add new_config.py
envshield scan --staged
```

**Expected Results**:
```
✓ Output:
  ℹ️  Scanning new file new_config.py (despite exclusion)
  🚨 DANGER: Found 1 potential secret(s)!

✓ Brand new files are scanned in full despite exclusion
✓ Reason: no baseline in HEAD to compare against
```

---

#### 2.1.5 FAIL: Real secret in excluded file not caught after scan --staged skips it

**Setup** (demonstrates the C6 fix):
```bash
mkdir -p /tmp/envshield-qa-tests/test-c6-baseline
cd /tmp/envshield-qa-tests/test-c6-baseline
git init

# WITHOUT C6, this would fail. WITH C6, this passes.

mkdir -p config

# Create baseline with fake secrets
cat > config/local.py << 'EOF'
FAKE_1 = 'fake'
FAKE_2 = 'fake'
EOF

cat > envshield.yml << 'EOF'
secret_scanning:
  exclude_files:
    - "config/local.py"
EOF

git add .
git commit -m "Baseline"

# Add REAL secret
echo "REAL = 'real_secret_key_with_long_content_here'" >> config/local.py

git add config/local.py

# Setup test fixture:
# - Without C6: scan --staged would skip the file, real secret gets through
# - With C6: scan --staged scans only new lines, catches real secret
```

**Verification**:
```bash
envshield scan --staged

# WITH C6 (current code):
# Expected: 🚨 Found real secret

# WITHOUT C6 (old code):
# Expected: ✓ No issues (silently missed the secret!)
```

---

#### 2.1.6 SUCCESS: Undeclared variables in new lines only

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-c6-undeclared
cd /tmp/envshield-qa-tests/test-c6-undeclared
git init

mkdir -p app

# Create schema with one known var
cat > app/env.schema.toml << 'EOF'
[KNOWN_VAR]
description = "Known"
EOF

# Create config with old undeclared var
cat > app/config.py << 'EOF'
OLD_UNKNOWN = os.getenv('OLD_UNKNOWN')
EOF

cat > envshield.yml << 'EOF'
secret_scanning:
  exclude_files:
    - "app/config.py"
EOF

git add .
git commit -m "Initial"
```

**Test Steps**:
1. Add new undeclared variable
```bash
echo "NEW_UNKNOWN = os.getenv('NEW_UNKNOWN')" >> app/config.py
```

2. Scan
```bash
git add app/config.py
envshield scan --staged
```

**Expected Results**:
```
✓ Output:
  ⚠️  WARNING: Found 1 undeclared variable!
  NEW_UNKNOWN
  
✓ Only NEW undeclared var flagged
✓ OLD_UNKNOWN (pre-existing) not flagged
```

---

## Multi-Service Projects Testing

### Test Suite 6: Python Multi-Service (Flask/FastAPI)

#### 3.1.1 SUCCESS: Python monorepo with 3+ services

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-multi-python
cd /tmp/envshield-qa-tests/test-multi-python
git init

# Create 3 Python services
mkdir -p api/config
cat > api/config/env_config.py << 'EOF'
DATABASE_URL = ""
API_PORT = "5000"
JWT_SECRET = ""
STRIPE_KEY = ""
EOF

mkdir -p worker/config
cat > worker/config/settings.py << 'EOF'
QUEUE_URL = ""
WORKER_THREADS = "4"
LOG_LEVEL = "INFO"
EOF

mkdir -p webhook/config
cat > webhook/config/local.py << 'EOF'
PORT = "8000"
SECRET = ""
WEBHOOK_ENDPOINT = ""
EOF
```

**Test Steps**:
1. Initialize EnvShield
```bash
envshield init
```

2. Discover services
```bash
envshield service discover
# Select: All
# Install hooks: YES
```

3. Setup all services
```bash
envshield setup
# Select: all
# Fill in values for each service
```

4. Verify setup
```bash
# Check all .env files created
ls -la api/.env worker/.env webhook/.env

# Check all schemas created
ls -la api/env.schema.toml worker/env.schema.toml webhook/env.schema.toml
```

**Expected Results**:
```
✓ All 3 services discovered correctly
✓ All 3 .env files created
✓ All 3 schemas generated
✓ envshield.yml contains all 3 services
✓ Hooks installed once at the end
✓ Multi-service setup completes successfully
```

---

#### 3.1.2 SUCCESS: Partial service setup

**Test Steps** (with test-multi-python state):
```bash
# Setup only api service
envshield setup --service api

# Verify only api/.env has values
cat api/.env      # Should have values
cat worker/.env   # Should be old/missing
```

**Expected Results**:
```
✓ Only api service prompted
✓ Only api/.env created/updated
✓ Other services untouched
```

---

#### 3.1.3 SUCCESS: Per-service secret scanning with C6

**Setup**:
```bash
# In test-multi-python, add excluded files

cat > api/config/local.py << 'EOF'
FAKE_SECRET_1 = 'fake_1'
FAKE_SECRET_2 = 'fake_2'
EOF

cat > envshield.yml << 'EOF'
project_name: test-multi
secret_scanning:
  exclude_files:
    - "api/config/local.py"
    - "worker/config/local.py"
services:
  api:
    path: api/env.schema.toml
  worker:
    path: worker/env.schema.toml
  webhook:
    path: webhook/env.schema.toml
EOF

git add .
git commit -m "Setup multi-service with exclusions"
```

**Test Steps**:
1. Add real secret to excluded file
```bash
echo "REAL_SECRET = 'real_secret_key_with_long_content_here'" >> api/config/local.py
```

2. Scan
```bash
git add api/config/local.py
envshield scan --staged --service api
```

**Expected Results**:
```
✓ Real secret caught in api service
✓ Fake secrets ignored
✓ C6 works correctly with multi-service
```

---

### Test Suite 7: JavaScript/TypeScript Multi-Service

#### 3.2.1 SUCCESS: Node.js monorepo (Next.js + Express + Workers)

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-multi-node
cd /tmp/envshield-qa-tests/test-multi-node
git init

# Next.js frontend
mkdir -p frontend
cat > frontend/.env.local << 'EOF'
NEXT_PUBLIC_API_URL=http://localhost:3001
NEXT_PUBLIC_VERSION=1.0
DATABASE_URL=
API_SECRET=
EOF

# Express backend
mkdir -p backend
cat > backend/.env << 'EOF'
PORT=3001
DATABASE_URL=
JWT_SECRET=
CORS_ORIGIN=http://localhost:3000
EOF

# Worker service
mkdir -p services/worker
cat > services/worker/.env << 'EOF'
QUEUE_URL=
WORKER_CONCURRENCY=4
LOG_LEVEL=info
EOF
```

**Test Steps**:
1. Discover services
```bash
envshield service discover
```

2. Select all and setup

**Expected Results**:
```
✓ frontend detected (Next.js, .env.local)
✓ backend detected (Express, .env)
✓ worker detected (.env in subdirectory)
✓ NEXT_PUBLIC_* NOT marked as secrets
✓ All 3 services setup successfully
```

---

#### 3.2.2 SUCCESS: Mixed language monorepo (Python API + TypeScript Frontend + Go Service)

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-multi-mixed
cd /tmp/envshield-qa-tests/test-multi-mixed
git init

# Python backend
mkdir -p api
cat > api/settings.py << 'EOF'
DATABASE_URL = ""
API_KEY = ""
EOF

# TypeScript frontend
mkdir -p web
cat > web/.env << 'EOF'
REACT_APP_API_URL=
SECRET_TOKEN=
EOF

# Note: Go services typically use different patterns, but for testing we can use .env
mkdir -p services/cache
cat > services/cache/.env << 'EOF'
REDIS_URL=
CACHE_TTL=3600
EOF
```

**Test Steps**:
1. Discover
2. Setup all

**Expected Results**:
```
✓ All services detected regardless of language
✓ Language-specific patterns handled (REACT_APP_*, etc.)
✓ All services setup correctly in one workflow
```

---

## Single-Service Projects Testing

### Test Suite 8: Single Python Service (Django/Flask)

#### 4.1.1 SUCCESS: Single Flask service full workflow

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-single-python-flask
cd /tmp/envshield-qa-tests/test-single-python-flask
git init

mkdir -p config
cat > config/settings.py << 'EOF'
SECRET_KEY = ""
DATABASE_URL = ""
MAIL_SERVER = ""
MAIL_PASSWORD = ""
REDIS_URL = ""
API_TIMEOUT = "30"
DEBUG = "False"
EOF
```

**Test Steps**:
1. Initialize
```bash
envshield init
# Install hooks: YES
```

2. Import existing config
```bash
envshield import config/settings.py
```

3. Review schema
```bash
cat env.schema.toml
```

4. Generate typed config
```bash
envshield generate --lang python
```

5. Setup
```bash
envshield setup
```

6. Verify
```bash
python -c "from config import env; print(env.SECRET_KEY)"
```

**Expected Results**:
```
✓ All steps complete successfully
✓ env.schema.toml has all 7 variables
✓ config.py generated with Pydantic models
✓ Type checking works
✓ Values correctly populated
```

---

#### 4.1.2 SUCCESS: Single Django service with complex settings

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-single-django
cd /tmp/envshield-qa-tests/test-single-django
git init

mkdir -p myapp/settings

# Django settings with environment overrides
cat > myapp/settings/base.py << 'EOF'
import os

SECRET_KEY = os.getenv('SECRET_KEY', 'insecure-dev-key')
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'myapp'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
    }
}
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost').split(',')
DEBUG = os.getenv('DEBUG', 'False') == 'True'
EOF
```

**Test Steps**:
1. Import
```bash
envshield import myapp/settings/base.py
```

2. Review
3. Setup
4. Verify type generation works

**Expected Results**:
```
✓ Django patterns recognized
✓ All env vars extracted
✓ Secret detection works
✓ Type generation succeeds
```

---

### Test Suite 9: Single TypeScript Service (Next.js)

#### 4.2.1 SUCCESS: Next.js single service

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-single-nextjs
cd /tmp/envshield-qa-tests/test-single-nextjs
git init

cat > .env.example << 'EOF'
NEXT_PUBLIC_API_URL=http://localhost:3000
NEXT_PUBLIC_ANALYTICS_ID=
DATABASE_URL=
NEXTAUTH_SECRET=
JWT_SECRET=
STRIPE_PUBLIC_KEY=
STRIPE_SECRET_KEY=
EOF
```

**Test Steps**:
1. Initialize and import
```bash
envshield init
envshield import .env.example
```

2. Generate TypeScript config
```bash
envshield generate --lang typescript
```

3. Setup
4. Verify

**Expected Results**:
```
✓ NEXT_PUBLIC_* recognized as non-secret
✓ Schema created with 7 variables
✓ config.ts generated with Zod validation
✓ TypeScript compilation succeeds
```

---

## Edge Cases & Error Scenarios

### Test Suite 10: Edge Cases

#### 5.1.1 Very large config files (1000+ variables)

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-edge-large
cd /tmp/envshield-qa-tests/test-edge-large
git init

# Generate 1000+ variables
python3 << 'PYTHON'
with open('large_config.py', 'w') as f:
    for i in range(1000):
        f.write(f'VAR_{i:04d} = "value_{i}"\n')
PYTHON
```

**Test Steps**:
```bash
envshield init
envshield import large_config.py
# Should handle gracefully
```

**Expected Results**:
```
✓ Completes without error
✓ Schema generated with 1000 variables
✓ Performance is acceptable (< 5 seconds)
```

---

#### 5.1.2 Deeply nested directory structures

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-edge-nested
cd /tmp/envshield-qa-tests/test-edge-nested
git init

# Create deeply nested service structure
mkdir -p services/api/v1/config
mkdir -p modules/auth/submodules/providers/config
mkdir -p apps/web/src/config/local

# Add config files at various depths
cat > services/api/v1/config/settings.py << 'EOF'
API_KEY = ""
EOF

cat > modules/auth/submodules/providers/config/settings.py << 'EOF'
AUTH_SECRET = ""
EOF

cat > apps/web/src/config/local/.env << 'EOF'
APP_URL=
EOF
```

**Test Steps**:
```bash
envshield service discover
```

**Expected Results**:
```
✓ All services found regardless of nesting depth
✓ Service paths correctly resolved
✓ Schema generation works
```

---

#### 5.1.3 Symlinks and aliases

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-edge-symlinks
cd /tmp/envshield-qa-tests/test-edge-symlinks
git init

mkdir -p real/config
cat > real/config/settings.py << 'EOF'
KEY = ""
EOF

# Create symlink
ln -s real actual

# Add both to envshield.yml
cat > envshield.yml << 'EOF'
services:
  real:
    path: real/env.schema.toml
  actual:
    path: actual/env.schema.toml
EOF
```

**Test Steps**:
```bash
envshield import real/config/settings.py --service real
envshield setup
```

**Expected Results**:
```
✓ Follows symlinks correctly
✓ Both services work correctly
✓ No duplicate data in .env files
```

---

#### 5.1.4 Unicode and special characters in config values

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-edge-unicode
cd /tmp/envshield-qa-tests/test-edge-unicode
git init

mkdir -p config
cat > config/settings.py << 'EOF'
# Test unicode characters
DATABASE_URL = "postgres://user:paß@localhost/db"  # German sharp s
API_ENDPOINT = "https://api.example.com/v1/données"  # French accents
WEBHOOK_PAYLOAD = '{"中文": "value"}'  # Chinese characters
SPECIAL_CHARS = "!@#$%^&*()"
EOF

cat > env.schema.toml << 'EOF'
[DATABASE_URL]
description = "Unicode in value: paß"
secret = true

[API_ENDPOINT]
description = "Endpoint with accents"
secret = false

[WEBHOOK_PAYLOAD]
description = "JSON with unicode"
secret = false

[SPECIAL_CHARS]
description = "Special chars test"
secret = false
EOF
```

**Test Steps**:
```bash
envshield setup

# At each prompt, values with unicode should be handled correctly
# Type: (accept defaults or enter values)

# Verify
cat .env
```

**Expected Results**:
```
✓ Unicode characters preserved
✓ Special characters escaped correctly
✓ All values stored and retrievable
✓ No encoding errors
```

---

#### 5.1.5 Empty files and directories

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-edge-empty
cd /tmp/envshield-qa-tests/test-edge-empty
git init

# Create empty files
touch empty_service/.env
touch empty_service/env_config.py
mkdir -p empty_app/config
touch empty_app/config/settings.py

# Empty schema
cat > envshield.yml << 'EOF'
services:
  empty:
    path: empty_service/env.schema.toml
EOF
```

**Test Steps**:
```bash
envshield service discover
```

**Expected Results**:
```
✓ Empty files handled gracefully
✓ No crash or error
⚠️  Service with no variables still registered
✓ Setup walks through (but with no vars to prompt)
```

---

### Test Suite 11: Error Scenarios

#### 5.2.1 Corrupted git repository

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-error-bad-git
cd /tmp/envshield-qa-tests/test-error-bad-git
git init

# Corrupt .git/config
echo "INVALID" > .git/config

envshield init
```

**Expected Results**:
```
✓ Graceful error: "Git repository is corrupted"
✗ No partial initialization
✓ Helpful message about recovery
```

---

#### 5.2.2 Insufficient permissions

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-error-permissions
cd /tmp/envshield-qa-tests/test-error-permissions
git init

# Create directory with no permissions
mkdir locked
chmod 000 locked

envshield service discover locked
```

**Expected Results**:
```
✓ Graceful error: "Permission denied"
✗ No crash
✓ Continues with other directories
```

---

#### 5.2.3 Out of disk space simulation

**Note**: This is hard to test safely, but the behavior should be:

```
✗ Error when writing files: "No space left on device"
✓ Cleanup of partial files
✓ Clear error message
✓ No corrupted state
```

---

#### 5.2.4 Race conditions (concurrent operations)

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-error-race
cd /tmp/envshield-qa-tests/test-error-race
git init

# Start setup in one terminal
# In another terminal, modify .env while setup is running
# This tests file locking and consistency
```

**Expected Results**:
```
✓ Setup completes without corruption
✓ Either reads the original or new file, but not mixed state
✓ No .env.lock files left behind
```

---

## Regression Testing

### Test Suite 12: Backward Compatibility

#### 6.1.1 Old projects without hooks still work

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-regression-old-project
cd /tmp/envshield-qa-tests/test-regression-old-project
git init

# Simulate an old project (pre-lifecycle improvements)
mkdir -p app
cat > app/settings.py << 'EOF'
KEY = ""
EOF

cat > envshield.yml << 'EOF'
services:
  app:
    path: app/env.schema.toml
EOF

# No hooks installed
```

**Test Steps**:
1. Run setup
```bash
envshield setup --service app
```

2. Hook should be offered
```
? Install git hooks? [Y/n]:
```

**Expected Results**:
```
✓ Old projects work
✓ New lifecycle features are offered but optional
✓ No breaking changes
```

---

#### 6.1.2 Projects with manual .env files still work

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-regression-manual-env
cd /tmp/envshield-qa-tests/test-regression-manual-env
git init

# Create schema
cat > env.schema.toml << 'EOF'
[KEY]
description = "API key"
secret = true
EOF

# Manually created .env (not from envshield setup)
cat > .env << 'EOF'
KEY=manual_value_123
EOF

# Run commands that interact with .env
envshield check .env
```

**Expected Results**:
```
✓ Existing .env files are respected
✓ Check validates the manual .env
✓ No overwriting without permission
```

---

## Performance Testing

### Test Suite 13: Performance

#### 7.1.1 Large repository with 100+ services

**Test**: Discover should complete in < 10 seconds

#### 7.1.2 Setup with 500+ variables

**Test**: Should complete in < 30 seconds

#### 7.1.3 Scan with 10MB+ codebase

**Test**: Should complete in < 5 seconds

---

## Security Testing

### Test Suite 14: Security

#### 8.1.1 Secret masking in logs

**Setup**:
```bash
mkdir -p /tmp/envshield-qa-tests/test-security-masking
cd /tmp/envshield-qa-tests/test-security-masking

# Create file with secrets
cat > config.py << 'EOF'
PASSWORD = 'super_secret_key_with_long_content_here'
DEBUG = False
EOF

# Run with debug output
envshield scan config.py -vvv
```

**Expected Results**:
```
✓ Secret values never logged
✓ Only secret TYPE shown, not value
✓ Even in debug mode, secrets masked
```

---

#### 8.1.2 .env files are not world-readable

**Setup**:
```bash
envshield setup
# Create .env

# Check permissions
stat -f "%OLp" .env
```

**Expected Results**:
```
✓ .env has 600 permissions (only owner readable)
✗ Not 644 (world-readable)
```

---

#### 8.1.3 Secrets not stored in shell history

**Setup**:
```bash
# Run setup interactively
envshield setup << 'INPUT'
my_secret_value
INPUT

# Check history
grep -i "secret" ~/.bash_history 2>/dev/null || echo "Not found (good!)"
```

**Expected Results**:
```
✓ Secret input not stored in shell history
✓ Safe for shared terminals
```

---

## Test Execution Checklist

### Before Testing

- [ ] Clean environment (`/tmp/envshield-qa-tests` cleared)
- [ ] EnvShield installed from source
- [ ] Git configured with user email
- [ ] All test subdirectories initialized

### During Testing

- [ ] Each test starts from a clean state
- [ ] All output captured for analysis
- [ ] Logs examined for errors/warnings
- [ ] File permissions verified
- [ ] Git state verified

### After Each Test

- [ ] Cleanup test directory
- [ ] Verify no artifacts left behind
- [ ] Note any unexpected behavior

---

## Success Criteria

### All Features
- [ ] 100% of success cases pass
- [ ] 100% of fail cases handled gracefully
- [ ] All edge cases handled
- [ ] No crashes or data corruption
- [ ] Performance acceptable (< 5s for most operations)

### Integration
- [ ] Multi-service projects work end-to-end
- [ ] Single-service projects work end-to-end
- [ ] Mixed language projects work
- [ ] Hooks integrate properly with git

### Security
- [ ] Secrets never logged
- [ ] Secret detection works correctly
- [ ] C6 diff-aware scanning works
- [ ] Backward compatibility maintained

---

## Quick Regression Test (5 minutes)

For quick validation:

```bash
# Test 1: Basic init + setup
mkdir /tmp/test-quick && cd /tmp/test-quick
git init
envshield init  # Should prompt for hooks
envshield setup # Should prompt for vars

# Test 2: Multi-service discovery
mkdir -p api worker
echo 'KEY=""' > api/.env
echo 'KEY=""' > worker/.env
envshield service discover  # Should find both

# Test 3: Hook functionality
echo 'SECRET="real_secret_key_with_long_content_here"' > secret.py
git add secret.py
git commit -m "test" || echo "✓ Hook blocked secret"

# Test 4: C6 diff-aware
cat > envshield.yml << 'EOF'
secret_scanning:
  exclude_files:
    - "old.py"
EOF
git add envshield.yml && git commit -m "add config"
echo 'FAKE="fake"' > old.py && git add . && git commit -m "baseline"
echo 'REAL="real_secret_key_with_long_content_here"' >> old.py
git add old.py
envshield scan --staged  # Should catch REAL, not FAKE

echo "✓ All quick tests passed"
```

---

## Notes

- **Test Data**: All test data uses obviously fake patterns to avoid triggering real alerts
- **Cleanup**: Each test should leave the filesystem clean
- **Isolation**: Tests are independent and can run in any order
- **Repeatability**: Tests should produce consistent results
- **Documentation**: Each test documents expected behavior clearly
