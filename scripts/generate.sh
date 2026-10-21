#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
openapi-generator-cli generate \
  -i openapi/marketplace.yaml \
  -g python-fastapi \
  -o generated \
  -t codegen \
  --additional-properties=packageName=marketplace_api,hideGenerationTimestamp=true \
  --type-mappings=number=Decimal \
  --global-property=apiTests=false,modelTests=false,apiDocs=false,modelDocs=false
