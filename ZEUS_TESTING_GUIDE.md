# EnvShield Integration Testing Guide for Zeus

This guide provides comprehensive test scenarios for validating EnvShield functionality with the Zeus project, covering all features and edge cases.

## Prerequisites

```bash
# Install EnvShield (development version)
cd /home/rabbil/dev/envshield
pip install -e .

# Verify installation
envshield --version
```

## Part 1: Lifecycle Improvements Testing

### 1.1 Test `envshield init` Hook Prompting

**Scenario**: Fresh project initialization with hook installation prompt

**Steps**:
```bash
# Create a temporary test directory
mkdir /tmp/test-envshield-init
cd /tmp/test-envshield-init
git init

# Initialize EnvShield
envshield init

# Expected output:
# ✓ EnvShield setup message
# ? Install git hooks? (pre-commit, post-merge) [Y/n]: 
```

**Variations**:
- **Case 1a**: User answers "yes"
  ```
  ✓ Pre-commit hook installed
  ✓ Post-merge hook installed
  Next step: envshield setup
  ```

- **Case 1b**: User answers "no"
  ```
  (No hooks installed)
  Next step: envshield setup
  ```

- **Case 1c**: Already in git repo with existing hooks
  ```
  ? Install git hooks? (pre-commit, post-merge) [Y/n]: n
  ```

**Verify**:
```bash
# Check if hooks were installed
ls -la .git/hooks/pre-commit .git/hooks/post-merge 2>&1

# Should show hooks if user selected yes
# Should show "No such file" if user selected no
```

---

### 1.2 Test `envshield service discover` with Hook Prompting

**Scenario**: Discover services and prompt for hook installation

**Steps**:
```bash
# Create test project structure
mkdir /tmp/test-zeus-discover
cd /tmp/test-zeus-discover
git init

# Create service directories with config files
mkdir -p service-api/config
mkdir -p service-web/config

# Create minimal config files
echo 'API_KEY = ""' > service-api/config/env_config.py
echo 'DEBUG = False' > service-web/config/env_config.py

# Run discover
envshield service discover

# Expected output:
# Discovered Services
# ┌─────────────┬────────────┬────────┐
# │ Name        │ Directory  │ Format │
# ├─────────────┼────────────┼────────┤
# │ service-api │ service-api│ python │
# │ service-web │ service-web│ python │
# └─────────────┴────────────┴────────┘
#
# ? Add these services to envshield.yml?
# ? Install git hooks?
```

**Variations**:
- **Case 2a**: Select "All" services, install hooks
  ```
  ✓ Registered service-api
  ✓ Registered service-web
  ? Install git hooks? [Y/n]: y
  ✓ Hooks installed
  ```

- **Case 2b**: Select individual service, no hooks
  ```
  ✓ Registered service-api
  (no hooks installed)
  ```

- **Case 2c**: Cancel discovery
  ```
  Cancelled. (hooks never prompted)
  ```

**Verify**:
```bash
# Check envshield.yml was created
cat envshield.yml

# Check service schemas were generated
ls service-api/env.schema.toml
ls service-web/env.schema.toml

# Check hook status
ls .git/hooks/post-merge
```

---

### 1.3 Test `envshield setup` with Hook Prompting

**Scenario**: Configure local environment with interactive hook installation

**Steps**:
```bash
# In your test project from 1.2
envshield setup

# Expected flow:
# 🛡️  EnvShield Setup
# Which service? (or all)
# service-api
#
# Please provide values for:
# [API_KEY]
# API Key for authentication
# Enter value (password): ••••••••••
#
# ? Install git hooks? [Y/n]: y
# ✓ Hooks installed
# ✓ Configuration complete!
```

**Variations**:
- **Case 3a**: Setup single service with secrets
  ```bash
  envshield setup --service service-api
  ```

- **Case 3b**: Setup all services
  ```bash
  envshield setup
  # Walks through each service interactively
  ```

- **Case 3c**: Decline hooks
  ```bash
  ? Install git hooks? [Y/n]: n
  ✓ Configuration complete!
  (no hooks installed)
  ```

**Verify**:
```bash
# Check config was created
ls service-api/.env
ls service-web/.env

# Check hook status
git hook-list 2>/dev/null || ls .git/hooks/
```

---

### 1.4 Test Smart Post-Merge Hook Behavior

**Scenario**: Hook only runs when schema files change

**Setup**:
```bash
cd /tmp/test-zeus-discover  # Use project from 1.2
git add .
git commit -m "Initial setup"

# Create a branch
git checkout -b feature/add-var
```

**Case 4a**: Pull with NO schema changes (should be silent)
```bash
# Make a code change (not schema)
echo "# comment" >> service-api/config/app.py
git add service-api/config/app.py
git commit -m "Add comment"
git checkout main
git merge feature/add-var

# Expected output:
# Merge made...
# (No hook output - schema didn't change)
```

**Case 4b**: Pull with schema changes (should show output)
```bash
git checkout feature/add-var

# Modify schema
echo '[NEW_VAR]' >> service-api/env.schema.toml
echo 'description = "New variable"' >> service-api/env.schema.toml
git add service-api/env.schema.toml
git commit -m "Add new var to schema"

git checkout main
git merge feature/add-var

# Expected output:
# Merge made...
# ⚠️ Your config is out of sync
# Missing: NEW_VAR
# Run: envshield setup --service service-api
```

**Case 4c**: Brand new schema file (should trigger)
```bash
git checkout feature/add-var

# Create new schema
cat > new-service/env.schema.toml << 'EOF'
[API_KEY]
description = "API key"
secret = true
EOF

git add new-service/env.schema.toml
git commit -m "Add new service"

git checkout main
git merge feature/add-var

# Expected output:
# (Should detect schema change and run doctor)
```

**Verify hook logic**:
```bash
# Check the post-merge hook script
cat .git/hooks/post-merge

# Should contain:
# if git diff --name-only HEAD@{1}..HEAD | grep -qE "env.schema.toml"
```

---

## Part 2: C6 Diff-Aware Scanning Testing

### 2.1 Test Basic Diff Detection

**Scenario**: Detect newly-added lines vs HEAD

**Setup** (with actual Zeus):
```bash
cd /home/rabbil/dev/zeus

# Make sure athena config is in exclude_files
cat envshield.yml | grep -A 5 "secret_scanning"

# Expected:
# secret_scanning:
#   exclude_files:
#     - "athena/config/env_config.local.py"
#     - "hermes/config/env_config.local.py"
```

**Case 5a**: File with no changes (should skip)
```bash
# Just stage the file without changes
git add athena/config/env_config.local.py
git status

# Run scan
envshield scan --staged

# Expected:
# ✓ No issues found
# (File is excluded and has no new lines, so skipped)
```

**Case 5b**: File with new lines (should scan only new ones)
```bash
# Add a new line to the excluded file
echo 'NEW_VAR = "test_value_with_long_content"' >> athena/config/env_config.local.py

# Stage it
git add athena/config/env_config.local.py

# Run scan
envshield scan --staged

# Expected:
# ℹ️  env_config.local.py (excluded; diffs only: 1 new line(s))
# ✓ No issues found
# (Found no secrets in the NEW line)
```

**Case 5c**: Excluded file with real secret added
```bash
# Add a real secret to excluded file
cat >> athena/config/env_config.local.py << 'EOF'
REAL_SECRET = 'super_secret_key_with_long_content_here'
EOF

# Stage it
git add athena/config/env_config.local.py

# Run scan
envshield scan --staged

# Expected:
# ℹ️  env_config.local.py (excluded; diffs only: 1 new line(s))
# 🚨 DANGER: Found 1 potential secret(s)!
# Line N: REAL_SECRET = 'super_secret_key_with_...'
# Commit aborted. Please fix the issues above before committing.
```

**Verify**:
```bash
# Undo the real secret
git checkout athena/config/env_config.local.py

# The fake secrets should still be there (not scanned)
grep "FAKE" athena/config/env_config.local.py  # Should find pre-existing
```

---

### 2.2 Test Brand New Files (No HEAD Version)

**Scenario**: Completely new file staged - should scan all lines despite exclusion

**Setup**:
```bash
cd /tmp/test-c6-new-file
git init

# Create envshield.yml with this file in exclude_files
cat > envshield.yml << 'EOF'
secret_scanning:
  exclude_files:
    - "new_config.py"
EOF

# Create a brand new file with a secret
cat > new_config.py << 'EOF'
# This file doesn't exist in HEAD yet
API_KEY = 'super_secret_key_with_long_content_here'
NORMAL_VAR = 'value'
EOF

# Stage it
git add new_config.py

# Run scan
envshield scan --staged

# Expected:
# ℹ️  Scanning new file new_config.py (despite exclusion)
# 🚨 DANGER: Found 1 potential secret(s)!
# (Because it's a brand new file, scan all lines)
```

---

### 2.3 Test Pre-Existing Fake Secrets (Baseline Noise)

**Scenario**: Verify pre-existing secrets are NOT flagged, only new ones are

**Setup** (with actual Zeus):
```bash
cd /home/rabbil/dev/zeus

# Check athena config has ~15 pre-existing fake secrets
grep -c "FAKE\|fake\|test\|dev" athena/config/env_config.local.py

# These should be in the file already
head -20 athena/config/env_config.local.py
```

**Case 6a**: Run scan on existing content (should find nothing)
```bash
# Stage the file as-is
git add athena/config/env_config.local.py

# Run scan
envshield scan --staged

# Expected:
# ℹ️  env_config.local.py (excluded; diffs only: 0 new line(s))
# ✓ No issues found
# (All pre-existing fakes are ignored)
```

**Case 6b**: Add ONE new real secret among the fakes
```bash
# Add real secret at the end
echo 'PRODUCTION_SECRET = "real_secret_key_with_long_production_content"' >> athena/config/env_config.local.py

# Stage it
git add athena/config/env_config.local.py

# Run scan
envshield scan --staged

# Expected:
# ℹ️  env_config.local.py (excluded; diffs only: 1 new line(s))
# 🚨 DANGER: Found 1 potential secret(s)!
# Line N: PRODUCTION_SECRET = '...'
# (Only the NEW secret is flagged, not the 15 pre-existing ones)
```

**Verify**:
```bash
# Count how many potential secrets are flagged
# Should be exactly 1, not 16

# Undo the real secret
git checkout athena/config/env_config.local.py
```

---

### 2.4 Test Non-Staged Scans Still Filter Excluded Files

**Scenario**: Verify backward compatibility - excluded files still excluded outside of staged scans

**Steps**:
```bash
cd /home/rabbil/dev/zeus

# Run non-staged scan
envshield scan --service athena

# Expected behavior:
# Should NOT mention athena/config/env_config.local.py
# Because it's excluded for non-staged scans too
# Only scans tracked files in the repo
```

---

### 2.5 Test Undeclared Variables with Diff-Aware Scanning

**Scenario**: Undeclared variables in newly-added lines should also be detected

**Setup**:
```bash
cd /tmp/test-c6-undeclared
git init

# Create schema
cat > env.schema.toml << 'EOF'
[KNOWN_VAR]
description = "Known variable"
EOF

# Create config with old undeclared var (will be excluded)
cat > config.py << 'EOF'
OLD_UNDECLARED = os.getenv('OLD_UNKNOWN')
EOF

git add .
git commit -m "Initial"

# Add new undeclared var
echo "NEW_UNDECLARED = os.getenv('NEW_UNKNOWN')" >> config.py

# Add to exclude_files
cat > envshield.yml << 'EOF'
secret_scanning:
  exclude_files:
    - "config.py"
EOF

git add .
```

**Run scan**:
```bash
envshield scan --staged

# Expected:
# ℹ️  config.py (excluded; diffs only: 1 new line(s))
# ⚠️  WARNING: Found 1 undeclared variable(s)!
# NEW_UNKNOWN
# (Only the new undeclared var is flagged)
```

---

## Part 3: Integration with Real Zeus Services

### 3.1 Full Integration Test: Athena Service

**Prerequisites**:
```bash
cd /home/rabbil/dev/zeus

# Ensure athena service is set up
ls athena/config/env_config.py
ls athena/config/env_config.local.py
```

**Step 1: Import existing config**
```bash
envshield import athena/config/env_config.py --service athena --force

# Expected:
# ✓ Generated schema at athena/env.schema.toml
# 70+ variables extracted
```

**Step 2: Review generated schema**
```bash
cat athena/env.schema.toml | head -30

# Should show variables like:
# [DATABASE_URL]
# secret = true
# [API_PORT]
# secret = false
```

**Step 3: Generate typed config**
```bash
envshield generate --lang python --service athena --force

# Creates athena/config.py with Pydantic models
# Verify:
python -c "from athena.config import env; print(env.API_PORT)"
```

**Step 4: Test setup with new developer**
```bash
# Create a clean environment
python -m venv /tmp/test-dev-env
source /tmp/test-dev-env/bin/activate

cd /home/rabbil/dev/zeus
envshield setup --service athena

# Walk through the setup:
# - Prompted for each secret (hidden input)
# - Prompted for each variable (visible input)
# - Optional variables can be skipped
# - All values stored in athena/.env

# Expected time: < 2 minutes
```

**Step 5: Verify config works**
```bash
cd /home/rabbil/dev/zeus

# Try to use the generated config
python -c "from envshield.core.generator import *; import sys; sys.path.insert(0, 'athena'); from config import env; print(f'API_PORT: {env.API_PORT}')"

# Should print the configured value without errors
```

---

### 3.2 Full Integration Test: Hermes Service

**Repeat the same steps as 3.1 but for hermes**:

```bash
# Import
envshield import hermes/config/env_config.py --service hermes --force

# Generate
envshield generate --lang python --service hermes --force

# Setup
envshield setup --service hermes

# Verify
python -c "from hermes.config import env; print(f'API_PORT: {env.API_PORT}')"
```

---

### 3.3 Multi-Service Setup

**Scenario**: Setup both athena and hermes at once

```bash
cd /home/rabbil/dev/zeus

envshield setup

# Expected:
# ? Which service? (api / web / worker / all)
# Select: All services

# Then walks through athena, then hermes
# ? Install git hooks? [Y/n]: y

# Hooks should handle BOTH services:
# git show HEAD:athena/env.schema.toml
# git show HEAD:hermes/env.schema.toml
```

---

## Part 4: Edge Cases and Error Scenarios

### 4.1 Missing Required Variables

**Scenario**: Developer forgets to provide a required variable during setup

```bash
envshield setup --service athena

# At prompt for DATABASE_URL (required):
# [DATABASE_URL]
# Required PostgreSQL connection
# Enter value (password): 
# (Leave blank and press Enter)

# Expected:
# ⚠️  This is a required variable. Please provide a value.
# Enter value (password): 
# (Re-prompt until value is provided)
```

---

### 4.2 Type Validation During Setup

**Scenario**: Wrong type entered for a variable

```bash
envshield setup --service athena

# At prompt for API_PORT (integer):
# [API_PORT]
# Port the API listens on
# Enter value: "not_a_number"

# Expected behavior depends on Pydantic validation
# Either:
# - Auto-coerce if possible
# - Show error and re-prompt
```

---

### 4.3 Schema Out of Sync After Pull

**Scenario**: Developer pulls new schema variables

```bash
cd /home/rabbil/dev/zeus

# Simulate new variable in schema
echo "[NEW_FEATURE_FLAG]" >> athena/env.schema.toml
echo "description = \"Enable new feature\"" >> athena/env.schema.toml
git add athena/env.schema.toml
git commit -m "Add feature flag"

# Now check config
envshield check athena/.env --service athena

# Expected:
# ⚠️  Missing in Local: NEW_FEATURE_FLAG (required)
# Suggestion: Run envshield setup --service athena
```

---

### 4.4 Scanning Through Multiple Commits

**Scenario**: Secret added multiple commits ago, then removed, but still in history

```bash
cd /tmp/test-c6-history
git init

# Commit 1: Add secret
echo 'SECRET = "real_secret_here"' > config.py
git add .
git commit -m "Oops, add secret"

# Commit 2: Remove secret
echo 'SECRET = "value"' > config.py
git add .
git commit -m "Fix secret"

# Scan the current staged state
git add config.py
envshield scan --staged

# Expected:
# ✓ No issues found
# (Current staged content is clean)

# Note: Git history still contains secret
# That's a separate concern (git-filter-branch, etc.)
```

---

### 4.5 Pre-Commit Hook in CI Environment

**Scenario**: Verify hook works in CI (no interactive terminal)

```bash
# In CI (GitHub Actions, GitLab CI, etc.)
cd /home/rabbil/dev/zeus

# Make a commit
git add some-file.py
git commit -m "Test commit"

# Expected:
# (Hook runs automatically)
# ✓ No issues found
# or
# 🚨 Secrets detected
# (Depending on what was added)
```

---

### 4.6 Post-Merge Hook in Different Scenarios

**Scenario A**: Merge with no schema changes
```bash
git merge feature/code-only
# Expected: Silent (no hook output)
```

**Scenario B**: Merge with schema changes
```bash
git merge feature/new-vars
# Expected:
# ⚠️  Your config is out of sync
# Missing: VAR1, VAR2
```

**Scenario C**: Fast-forward merge
```bash
git merge feature/linear
# Expected: Hook still runs
```

**Scenario D**: Merge with conflicts
```bash
git merge feature/conflicting
# Conflict resolution...
# Complete the merge
# Expected: Hook runs after conflict resolution is complete
```

---

## Part 5: Verification Checklist

### ✅ Lifecycle Improvements

- [ ] `envshield init` prompts for hooks
- [ ] `envshield service discover` prompts for hooks
- [ ] `envshield setup` prompts for hooks
- [ ] Hooks are installed in `.git/hooks/` when accepted
- [ ] Hooks are NOT installed when declined
- [ ] Next-step guidance is shown
- [ ] Non-staged scans still exclude excluded files
- [ ] Post-merge hook runs only when schemas change
- [ ] Post-merge hook is silent when nothing changed

### ✅ C6 Diff-Aware Scanning

- [ ] Brand new files scanned in full despite exclusion
- [ ] Modified files scan only new lines
- [ ] Pre-existing fake secrets are ignored
- [ ] New real secrets are caught
- [ ] Undeclared variables filtered by new lines
- [ ] Error handling is conservative
- [ ] Works with Zeus's athena and hermes services
- [ ] Backward compatible with non-staged scans

### ✅ Zeus Integration

- [ ] athena service setup works
- [ ] hermes service setup works
- [ ] Multi-service setup works
- [ ] Generated config files are importable
- [ ] Typed config validation works
- [ ] Pre-existing fake secrets don't cause noise
- [ ] New real secrets are caught

---

## Part 6: Manual Testing Commands (Quick Reference)

```bash
# Setup fresh project
mkdir /tmp/envshield-test && cd /tmp/envshield-test && git init

# Test init
envshield init

# Test discover
mkdir api && echo 'API_KEY = ""' > api/env_config.py
envshield service discover

# Test setup
envshield setup

# Test hook scripts
cat .git/hooks/pre-commit
cat .git/hooks/post-merge

# Test secret scanning
echo 'SECRET = "super_secret_key_with_long_content_here"' > api/env_config.py
git add api/env_config.py
envshield scan --staged

# Test diff-aware (add to exclude_files first)
git checkout api/env_config.py
envshield scan --staged  # Should be clean

# Add to exclude, then add real secret
echo 'NEW_SECRET = "super_secret_key_with_long_content_here"' >> api/env_config.py
git add api/env_config.py
envshield scan --staged  # Should catch the new secret
```

---

## Notes

- **Time estimates**: Each feature should take < 5 minutes to test
- **Artifacts**: No artifacts should be left behind; use `/tmp/` directories
- **Rollback**: All test changes can be reverted with `git checkout`
- **Logging**: Check `git log` to verify commits and merges happened correctly
- **Secrets**: Test data uses obviously fake secrets; nothing real should be in test files
