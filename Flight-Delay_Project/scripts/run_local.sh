#!/bin/bash

# Enable script stop if a command fails
set -e

# Colors for readable output
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Move the execution to the root folder of the project
cd "$(dirname "$0")/.."

echo -e "${GREEN}[1/2] Preparing the Docker environment (bigdata-env)...${NC}"
# Builds the image. Uses the cache if the layers haven't changed.
docker build -t bigdata-env .

echo -e "${GREEN}[2/2] Starting the container and mounting the working directory...${NC}"
echo "You are now in the isolated environment. Run your scripts from here."
echo "To exit, type: exit"

# Mount the current folder (your project) into /app in the container
docker run -it --rm -v $(pwd):/app bigdata-env