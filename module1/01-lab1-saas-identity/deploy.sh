#!/bin/bash
# Deploy the Lambda Authorizer to AWS
# Usage: source /workshop/static/code/module1/setup_env.sh && bash deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/deploy"

# Allow test runner to supply pre-patched source paths without mutating the
# participant source files on disk.
AUTHORIZER_FILE="${AUTHORIZER_SRC:-$SCRIPT_DIR/authorizer.py}"
SAAS_IDENTITY_FILE="${SAAS_IDENTITY_SRC:-$SCRIPT_DIR/saas_identity.py}"

echo "Building authorizer package..."
rm -rf "$DEPLOY_DIR" && mkdir -p "$DEPLOY_DIR"

pip install PyJWT --quiet --target "$DEPLOY_DIR"
cp "$AUTHORIZER_FILE"  "$DEPLOY_DIR/index.py"
cp "$SAAS_IDENTITY_FILE"  "$DEPLOY_DIR/saas_identity.py"

cd "$DEPLOY_DIR" && zip -r ../authorizer.zip . > /dev/null && cd "$SCRIPT_DIR"
rm -rf "$DEPLOY_DIR"

echo "Deploying to Lambda: $AUTHORIZER_FUNCTION_NAME"
aws lambda update-function-code \
  --function-name "$AUTHORIZER_FUNCTION_NAME" \
  --zip-file fileb://authorizer.zip \
  --output text --query 'FunctionName'

aws lambda wait function-updated --function-name "$AUTHORIZER_FUNCTION_NAME"
rm -f "$SCRIPT_DIR/authorizer.zip"
echo "Done. Authorizer is live."
