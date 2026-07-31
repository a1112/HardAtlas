# SQL policy references

These files document the original kernel and workspace RLS contracts. Alembic
under `../alembic` is the authoritative, upgradeable database definition.

Do not mount this directory into `docker-entrypoint-initdb.d`: PostgreSQL runs
that directory only for a new data volume, so it cannot safely upgrade an
existing Atlas installation.
