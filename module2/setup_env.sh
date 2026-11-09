#!/bin/bash
# setup_env.sh — Load workshop environment and install Module 2 dependencies.
# Run once at the start of Module 2: source /workshop/module2/setup_env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load base environment from Module 1
source /workshop/module1/setup_env.sh

# Install Module 2 Python dependencies (only if not already installed)
if ! python -c "import strands" 2>/dev/null; then
  echo ""
  echo "Installing Module 2 dependencies..."
  pip install -q -r "$SCRIPT_DIR/requirements.txt"
  echo "  done"
fi

# Module 2 specific exports
export S3_VECTOR_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" \
  --output text)
export OPENSEARCH_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name workshop-infrastructure \
  --query "Stacks[0].Outputs[?OutputKey=='OpenSearchEndpoint'].OutputValue" \
  --output text)

echo "  S3_VECTOR_BUCKET=$S3_VECTOR_BUCKET"
echo "  OPENSEARCH_ENDPOINT=$OPENSEARCH_ENDPOINT"
