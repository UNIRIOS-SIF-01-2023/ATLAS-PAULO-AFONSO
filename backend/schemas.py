"""Contratos Pydantic da API (P5). O GeoJSON segue como formato no fio."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Geometry(BaseModel):
    type: str
    coordinates: Any


class BuildingPropertiesLight(BaseModel):
    """Mínimo para renderizar o mapa (P3): cor, filtro de pontos, 3D."""

    tipo: str = "desconhecido"
    fonte: Optional[str] = None
    num_pavimentos: Optional[int] = None


class BuildingPropertiesFull(BuildingPropertiesLight):
    """Detalhe completo sob demanda (GET /{id})."""

    id: str
    area_m2: Optional[float] = None
    endereco: Optional[str] = None
    bairro: Optional[str] = None
    estabelecimento: Optional[str] = None


class FeatureLight(BaseModel):
    type: str = "Feature"
    id: Any = None
    geometry: Geometry
    properties: BuildingPropertiesLight


class FeatureFull(BaseModel):
    type: str = "Feature"
    id: Any = None
    geometry: Geometry
    properties: BuildingPropertiesFull


class FeatureCollectionLight(BaseModel):
    type: str = "FeatureCollection"
    name: str = "buildings"
    features: List[FeatureLight] = Field(default_factory=list)


class FeatureCollectionFull(BaseModel):
    type: str = "FeatureCollection"
    name: str = "buildings"
    features: List[FeatureFull] = Field(default_factory=list)


class StatsResponse(BaseModel):
    total: int = 0
    by_tipo: Dict[str, int] = Field(default_factory=dict)
    by_bairro: Dict[str, int] = Field(default_factory=dict)
    area_total_m2: Optional[float] = None
    area_media_m2: Optional[float] = None


class UserCreate(BaseModel):
    """Cadastro: nome, e-mail e senha (6–72 caracteres; teto do bcrypt)."""

    nome: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=3, max_length=100)
    senha: str = Field(min_length=6, max_length=72)


class UserLogin(BaseModel):
    """Login: e-mail + senha."""

    email: str = Field(min_length=3, max_length=100)
    senha: str = Field(min_length=1, max_length=72)


class UserPublic(BaseModel):
    """O que a API devolve: nunca inclui a senha nem o hash."""

    id: int
    nome: str
    email: str
    criado_em: Any = None


class AuthToken(BaseModel):
    """Resposta do login/cadastro: token Bearer + quem entrou."""

    access_token: str
    token_type: str = "bearer"
    user: UserPublic
