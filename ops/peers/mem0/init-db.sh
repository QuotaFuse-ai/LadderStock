#!/bin/bash
set -e

# 建 mem0_app 库（用户/auth/api-key 数据）；pgvector 记忆存默认 postgres 库。
# 与 mem0-dev 的 init-db.sh 同构——但落在 qf-mem0 自己的 postgres 实例里。
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE mem0_app'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mem0_app')\gexec
EOSQL
