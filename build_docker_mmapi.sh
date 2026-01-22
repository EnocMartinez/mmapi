#!/bin/bash
set -o errexit
set -o nounset

version=$(cat ./README.md | grep "\*\*version\*\*" | cut -d: -f 2)
version=$(echo "${version}" | sed 's/ //g' )
echo "version '${version}'"
tag="enocmartinez/mmapi:${version}"
echo "Building image with tag: $tag"
docker build -t "${tag}" . -f build/mmapi/Dockerfile

read -p "Do you want to push it to DockerHub? (yes/no): " response

if [[ "$response" =~ ^[Yy](es)?$ ]]; then
    echo "Pushing..."
    docker push "${tag}"
    echo "Operation completed!"
elif [[ "$response" =~ ^[Nn]o?$ ]]; then
    echo "Operation cancelled."
else
    echo "Invalid input. Please answer yes or no."
    exit 1
fi

