"""Fixtures compartilhadas: GeoJSON de teste e um banco falso (sem MySQL)."""

import json

import pytest

from importacao import banco


def quadrado(x0, y0, x1, y1):
    return {"type": "Polygon",
            "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def feature(geom, fid=None, **props):
    f = {"type": "Feature", "geometry": geom, "properties": props}
    if fid is not None:
        f["id"] = fid
    return f


def colecao(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def escrever(caminho, doc):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(doc), encoding="utf-8")
    return str(caminho)


class CursorFalso:
    def __init__(self, conn):
        self.conn = conn
        self.lastrowid = 7

    def execute(self, sql, params=None):
        self.conn.log.append((sql, params))
        if self.conn.falhar_em and self.conn.falhar_em in sql:
            raise RuntimeError("falha simulada")

    def fetchone(self):
        return {"n": self.conn.n}

    def fetchall(self):
        return [{"COLUMN_NAME": "ID"}, {"COLUMN_NAME": "NOME"}]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ConexaoFalsa:
    def __init__(self, n=0, falhar_em=None):
        self.n = n
        self.falhar_em = falhar_em
        self.log = []
        self.commits = self.rollbacks = self.fechada = 0

    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.fechada += 1

    def sqls(self):
        return [s for s, _ in self.log]


@pytest.fixture
def conexao_falsa(monkeypatch):
    """Devolve uma fábrica: conexao_falsa(n=..., falhar_em=...) já instalada."""
    def instalar(n=0, falhar_em=None):
        conn = ConexaoFalsa(n=n, falhar_em=falhar_em)
        monkeypatch.setattr(banco, "connect", lambda nome, autocommit=False: conn)
        return conn
    return instalar
