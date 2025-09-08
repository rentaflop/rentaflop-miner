#!/bin/bash

# Docker deployment script for rentaflop-containers
# Builds Docker image, pushes to ECR, and cleans up untagged images

set -e  # Exit on any error

ECR_REGISTRY="903538195140.dkr.ecr.us-east-1.amazonaws.com"
REPOSITORY_NAME="rentaflop-containers"
IMAGE_TAG="render"
REGION="us-east-1"

echo "Starting Docker deployment process..."

# Build Docker image
echo "Building Docker image..."
docker build -t "${REPOSITORY_NAME}:${IMAGE_TAG}" .

# Tag image for ECR
echo "Tagging image for ECR..."
docker tag "${REPOSITORY_NAME}:${IMAGE_TAG}" "${ECR_REGISTRY}/${REPOSITORY_NAME}:${IMAGE_TAG}"

# Login to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

# Push image to ECR
echo "Pushing image to ECR..."
docker push "${ECR_REGISTRY}/${REPOSITORY_NAME}:${IMAGE_TAG}"

# Delete untagged images to clean up ECR
echo "Cleaning up untagged images..."
aws ecr list-images --repository-name "${REPOSITORY_NAME}" --filter tagStatus=UNTAGGED --query 'imageIds[*]' --output json | \
aws ecr batch-delete-image --repository-name "${REPOSITORY_NAME}" --image-ids file:///dev/stdin

echo "Docker deployment completed successfully!"