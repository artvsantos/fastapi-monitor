import secrets
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, utc_now


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True)
    # Sempre em minúsculas (o schema normaliza), então o unique já
    # impede "Ana@x.com" e "ana@x.com" ao mesmo tempo.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class Monitor(Base):
    __tablename__ = 'monitors'
    __table_args__ = (
        # Também é o que responde "Você já monitora esta URL" (409). E,
        # por começar pelo owner_id, já serve de índice para listar os
        # monitores de uma pessoa.
        UniqueConstraint('owner_id', 'url'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'),
    )
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(2048))
    interval_minutes: Mapped[int] = mapped_column(default=5)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    # A fila do worker: ele pega os monitores com next_check_at vencido.
    # O padrão "agora" faz o monitor novo ser checado na próxima rodada.
    next_check_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True,
    )
    # Resultado da última checagem. None = ainda não checado.
    is_up: Mapped[bool | None]
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Para onde vai o aviso quando o site cai ou volta (opcional).
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    # Assina os avisos, para quem recebe saber que vieram daqui.
    webhook_secret: Mapped[str] = mapped_column(
        String(64), default=new_webhook_secret,
    )


class Check(Base):
    """Uma checagem: o histórico de cada monitor."""

    __tablename__ = 'checks'
    __table_args__ = (
        # O histórico é sempre lido por monitor, do mais novo ao mais velho.
        Index('ix_checks_monitor_id_checked_at', 'monitor_id', 'checked_at'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(
        ForeignKey('monitors.id', ondelete='CASCADE'),
    )
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime)
    is_up: Mapped[bool]
    status_code: Mapped[int | None]
    response_time_ms: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(String(200))


class Alert(Base):
    """Aviso de que o site caiu ou voltou, na fila para ir ao webhook.

    Nasce na mesma transação da checagem que mudou o status: se o worker
    cair logo depois, o aviso não se perde. O envio vem depois, com novas
    tentativas se o webhook falhar.
    """

    __tablename__ = 'alerts'
    __table_args__ = (
        # O que o worker procura: avisos pendentes com a hora vencida.
        Index('ix_alerts_status_next_attempt_at', 'status', 'next_attempt_at'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(
        ForeignKey('monitors.id', ondelete='CASCADE'), index=True,
    )
    # Quando a checagem sai do histórico (30 dias), o aviso vai junto. O
    # índice evita que apagar checagens leia a tabela de avisos inteira.
    check_id: Mapped[int] = mapped_column(
        ForeignKey('checks.id', ondelete='CASCADE'), index=True,
    )
    event: Mapped[str] = mapped_column(String(10))  # down | up
    # pending | sent | failed (desistiu) | cancelled (webhook removido ou
    # URL trocada)
    status: Mapped[str] = mapped_column(String(10), default='pending')
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(200))
