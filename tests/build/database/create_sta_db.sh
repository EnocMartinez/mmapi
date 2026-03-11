#!/bin/bash
# This script creates the database and the user for sensorthings in PostgresQL. It also creates the postgis extension

set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_DB" <<-EOSQL
  CREATE ROLE $sta_db_user WITH LOGIN CREATEDB PASSWORD '$sta_db_password' SUPERUSER;
  CREATE DATABASE $sta_db_name WITH OWNER "$sta_db_user";
  \connect '$sta_db_name'
  CREATE EXTENSION IF NOT EXISTS postgis;
EOSQL