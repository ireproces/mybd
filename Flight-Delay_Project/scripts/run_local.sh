#!/bin/bash

# Enable script stop if a command fails
set -e

# colors for readable output
BLUE='\033[0;34m'
NC='\033[0m'

# Move the execution to the root folder of the project
cd "$(dirname "$0")/.."

echo -e "${BLUE}[1/2] Preparing the Docker environment (bigdata-env)...${NC}"
# Builds the image. Uses the cache if the layers haven't changed.
docker build -t bigdata-env .

echo -e "\\n${BLUE}[2/2] Starting the container and mounting the working directory...${NC}"
echo ""
echo "You are now in the isolated environment. Run your scripts from here."
echo "To exit, type: exit"

# Mount the current folder into /app in the container
docker run -it --rm -v $(pwd):/app bigdata-env