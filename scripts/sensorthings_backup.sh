#!/bin/bash
#================================================================================#
#                            SensorThings Database Backup                        #
#================================================================================#
# This script creates a backup of the SensorThings Database, including the
# hypertables used for efficient storage of timeseries, profiles and detections.
# The secrests are taken from the /opt/odi/secrets.env file
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


secrets="/opt/odi/secrets.env"

echo -e "Loading ${GRN}ODI${RST} secrets from ${PRL}${secrets}${RST}"
source $secrets


# Configuration
DB_NAME="sensorthings"
STA_DB_USER="sensorthings"
DB_HOST="localhost"
DB_PORT="${PGPORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.bak"

export PGPASSWORD=${STA_DB_PASSWORD}



echo ""
echo "========================================="
echo "TimescaleDB Backup Script"
echo "========================================="
echo "Database: ${DB_NAME}"
echo "Host: ${DB_HOST}:${DB_PORT}"
echo "User: ${STA_DB_USER}"
echo "========================================="

# Create backup directory if it doesn't exist
mkdir -p "${BACKUP_DIR}"

# Perform backup
echo "Starting backup..."
pg_dump -h "${DB_HOST}" \
        -p "${DB_PORT}" \
        -U "${STA_DB_USER}" \
        -d "${DB_NAME}" \
        --format=c \
        --no-owner \
        --no-acl \
        -f  "${BACKUP_FILE}"

# Check if backup was successful
if [ $? -eq 0 ]; then
    BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    echo -e "${GRN}✓ Backup completed successfully${RST}"
    echo "Backup file: ${BACKUP_FILE}"
    echo "Backup size: ${BACKUP_SIZE}"

    # Optional: Remove backups older than 7 days
    # find "${BACKUP_DIR}" -name "${DB_NAME}_*.sql.gz" -mtime +7 -delete
else
    echo -e "${RED}✗ Backup failed${RST}"
    exit 1
fi

echo "========================================="