from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    machines: Mapped[list['Machine']] = relationship('Machine', back_populates='user', cascade='all, delete-orphan')
    requests: Mapped[list['LicenseRequest']] = relationship('LicenseRequest', back_populates='user', cascade='all, delete-orphan', foreign_keys='LicenseRequest.user_id')
    issued_licenses: Mapped[list['IssuedLicense']] = relationship('IssuedLicense', back_populates='user', cascade='all, delete-orphan')
    access_requests: Mapped[list['ProductAccessRequest']] = relationship('ProductAccessRequest', back_populates='user', cascade='all, delete-orphan', foreign_keys='ProductAccessRequest.user_id')
    access_grants: Mapped[list['ProductAccessGrant']] = relationship('ProductAccessGrant', back_populates='user', cascade='all, delete-orphan', foreign_keys='ProductAccessGrant.user_id')


class Product(Base):
    __tablename__ = 'products'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), unique=True)
    app_root_path: Mapped[str] = mapped_column(String(1024))
    private_key_path: Mapped[str] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    releases: Mapped[list['ProductRelease']] = relationship('ProductRelease', back_populates='product', cascade='all, delete-orphan')
    requests: Mapped[list['LicenseRequest']] = relationship('LicenseRequest', back_populates='product', cascade='all, delete-orphan')
    issued_licenses: Mapped[list['IssuedLicense']] = relationship('IssuedLicense', back_populates='product', cascade='all, delete-orphan')
    access_requests: Mapped[list['ProductAccessRequest']] = relationship('ProductAccessRequest', back_populates='product', cascade='all, delete-orphan')
    access_grants: Mapped[list['ProductAccessGrant']] = relationship('ProductAccessGrant', back_populates='product', cascade='all, delete-orphan')


class ProductAccessRequest(Base):
    __tablename__ = 'product_access_requests'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), index=True)
    status: Mapped[str] = mapped_column(String(32), default='pending', index=True)
    requested_note: Mapped[str] = mapped_column(Text, default='')
    admin_note: Mapped[str] = mapped_column(Text, default='')
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship('User', back_populates='access_requests', foreign_keys=[user_id])
    product: Mapped[Product] = relationship('Product', back_populates='access_requests')
    reviewed_by: Mapped[User | None] = relationship('User', foreign_keys=[reviewed_by_user_id])


class ProductAccessGrant(Base):
    __tablename__ = 'product_access_grants'
    __table_args__ = (UniqueConstraint('user_id', 'product_id', name='uq_access_grant_user_product'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    granted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship('User', back_populates='access_grants', foreign_keys=[user_id])
    product: Mapped[Product] = relationship('Product', back_populates='access_grants')
    granted_by: Mapped[User | None] = relationship('User', foreign_keys=[granted_by_user_id])


class Machine(Base):
    __tablename__ = 'machines'
    __table_args__ = (UniqueConstraint('user_id', 'machine_id', name='uq_machine_user_machine_id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    machine_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship('User', back_populates='machines')
    requests: Mapped[list['LicenseRequest']] = relationship('LicenseRequest', back_populates='machine')
    issued_licenses: Mapped[list['IssuedLicense']] = relationship('IssuedLicense', back_populates='machine')


class LicenseRequest(Base):
    __tablename__ = 'license_requests'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), index=True)
    machine_id_ref: Mapped[int] = mapped_column(ForeignKey('machines.id'), index=True)
    status: Mapped[str] = mapped_column(String(32), default='pending', index=True)
    requested_note: Mapped[str] = mapped_column(Text, default='')
    admin_note: Mapped[str] = mapped_column(Text, default='')
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship('User', back_populates='requests', foreign_keys=[user_id])
    product: Mapped[Product] = relationship('Product', back_populates='requests')
    machine: Mapped[Machine] = relationship('Machine', back_populates='requests')
    reviewed_by: Mapped[User | None] = relationship('User', foreign_keys=[reviewed_by_user_id])
    issued_license: Mapped['IssuedLicense | None'] = relationship('IssuedLicense', back_populates='request', uselist=False)


class IssuedLicense(Base):
    __tablename__ = 'issued_licenses'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey('license_requests.id'), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), index=True)
    machine_id_ref: Mapped[int] = mapped_column(ForeignKey('machines.id'), index=True)
    issued_to: Mapped[str] = mapped_column(String(255))
    expiry_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    request: Mapped[LicenseRequest] = relationship('LicenseRequest', back_populates='issued_license')
    user: Mapped[User] = relationship('User', back_populates='issued_licenses')
    product: Mapped[Product] = relationship('Product', back_populates='issued_licenses')
    machine: Mapped[Machine] = relationship('Machine', back_populates='issued_licenses')


class ProductRelease(Base):
    __tablename__ = 'product_releases'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), index=True)
    version: Mapped[str] = mapped_column(String(64), index=True)
    notes: Mapped[str] = mapped_column(Text, default='')
    original_filename: Mapped[str] = mapped_column(String(255))
    installer_path: Mapped[str] = mapped_column(String(1024))
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    product: Mapped[Product] = relationship('Product', back_populates='releases')
