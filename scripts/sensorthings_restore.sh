#!/bin/bash
#================================================================================#
#                            SensorThings Database Restore                       #
#================================================================================#
# Restores a SensorThings API Database (with hypertables)
#
# author: Enoc Martínez
# affiliation: Universitat Politècnica de Catalunya
# contact: enoc.martinez@upc.edu
#================================================================================#

# Exit on error and do not permit empty variables
set -o errexit
set -o nounset
# Colors for output
GRN='\033[0;32m'
RED='\033[0;31m'
YEL='\033[1;33m'
BLU='\e[0;34m'
CYN='\e[0;36m'
PRL='\e[0;35m'
WHT='\e[0;37m'
RST='\033[0m'


if [ $# -lt 1 ]; then
  echo -e "Usage: $0 <backup file>"
  exit
fi


# Configuration
DB_NAME="sensorthings"
DB_USER="sensorthings"
DB_HOST="localhost"
DB_PORT="${PGPORT:-5433}"
DB_PASSWORD="ChangeMe"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$1"

# Set password for psql if provided
if [ -n "${DB_PASSWORD}" ]; then
    export PGPASSWORD="${DB_PASSWORD}"
fi

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if backup file is provided
if [ -z "${BACKUP_FILE}" ]; then
    echo -e "${RED}Error: Backup file not specified${NC}"
    echo "Usage: $0 <backup_file> [database_name]"
    exit 1
fi

# Check if backup file exists
if [ ! -f "${BACKUP_FILE}" ]; then
    echo -e "${RED}Error: Backup file '${BACKUP_FILE}' not found${NC}"
    exit 1
fi

echo "========================================="
echo "TimescaleDB Restore Script"
echo "========================================="
echo "Backup file: ${BACKUP_FILE}"
echo "Database: ${DB_NAME}"
echo "Host: ${DB_HOST}:${DB_PORT}"
echo "User: ${DB_USER}"
echo "========================================="
echo -e "${YELLOW}WARNING: This will drop and recreate the database!${NC}"
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "${CONFIRM}" != "yes" ]; then
    echo "Restore cancelled."
    exit 0
fi

echo "Terminating existing connections to database..."
psql -h "${DB_HOST}" \
     -p "${DB_PORT}" \
     -U "${DB_USER}" \
     -d postgres \
     -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();" 2>/dev/null || true

echo "Dropping existing database..."
psql -h "${DB_HOST}" \
     -p "${DB_PORT}" \
     -U "${DB_USER}" \
     -d postgres \
     -c "DROP DATABASE IF EXISTS ${DB_NAME};"

echo "Creating database..."
psql -h "${DB_HOST}" \
     -p "${DB_PORT}" \
     -U "${DB_USER}" \
     -d postgres \
     -c "CREATE DATABASE ${DB_NAME};"

echo "Creating TimescaleDB extension..."
psql -h "${DB_HOST}" \
     -p "${DB_PORT}" \
     -U "${DB_USER}" \
     -d "${DB_NAME}" \
     -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

echo "Running timescaledb_pre_restore()..."
psql -h "${DB_HOST}" \
     -p "${DB_PORT}" \
     -U "${DB_USER}" \
     -d "${DB_NAME}" \
     -c "SELECT timescaledb_pre_restore();"

echo "Restoring database from backup..."
pg_restore -h "${DB_HOST}" \
     -p "${DB_PORT}" \
     -U "${DB_USER}" \
     -d "${DB_NAME}" \
     -Fc $BACKUP_FILE

# Check if restore was successful
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Restore completed successfully${NC}"

    # Verify hypertables
    echo "Verifying hypertables..."
    psql -h "${DB_HOST}" \
         -p "${DB_PORT}" \
         -U "${DB_USER}" \
         -d "${DB_NAME}" \
         -c "SELECT hypertable_name, num_chunks FROM timescaledb_information.hypertables;"
else
    echo -e "${RED}✗ Restore failed${NC}"
    exit 1
fi

echo "Running timescaledb_post_restore()..."
psql -h "${DB_HOST}" \
     -p "${DB_PORT}" \
     -U "${DB_USER}" \
     -d "${DB_NAME}" \
     -c "SELECT timescaledb_post_restore();"


echo "========================================="