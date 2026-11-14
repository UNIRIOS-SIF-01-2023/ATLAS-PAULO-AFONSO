"""Credenciais do banco, injetadas pelo Doppler (`doppler run -- ...`)."""

import os

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")              # ATLAS: produção, lido pela API
DB_NAME_STAGE = os.getenv("DB_NAME_STAGE")  # ATLAS_STAGE: rascunho da importação
DB_SSL_CA = os.getenv("DB_SSL_CA")          # Caminho do CA (ex: ca.pem da Aiven)

# SIRGAS 2000 geográfico: SRID das colunas GEOM, PONTO_ROTULO e PT.
SRID = 4674

# JWT do login.
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
