FROM postgres:12-alpine
#FROM timescale/timescaledb-ha:pg14.9-ts2.12.2-all

# Include extra setup scripts (eg datastore)
ADD docker-entrypoint-initdb.d /docker-entrypoint-initdb.d
