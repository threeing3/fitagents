"""Authentication API: register, login, account profile, and current user."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from fast_api.app.core.auth import create_access_token, get_current_user
from fast_api.app.core.security import hash_password, verify_password
from fast_api.app.db import models
from fast_api.app.db.database import get_db

auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="Fitness User", min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    avatar_url: str | None = Field(default=None, max_length=1000)


class LoginRequest(BaseModel):
    email: str | None = None
    identifier: str | None = None
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    username: str | None = None
    display_name: str
    avatar_url: str | None = None


class UserResponse(BaseModel):
    user_id: str
    email: str
    username: str | None = None
    display_name: str
    avatar_url: str | None = None
    created_at: str


class AccountUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    avatar_url: str | None = Field(default=None, max_length=1000)
    timezone: str | None = Field(default=None, max_length=64)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _normalize_username(username: str | None) -> str | None:
    if username is None:
        return None
    username = username.strip().lower()
    return username or None


def _suggest_username(email: str, display_name: str, db: Session) -> str:
    base = _normalize_username(display_name.replace(" ", ".")) or email.split("@", 1)[0].lower()
    base = "".join(ch for ch in base if ch.isalnum() or ch in "_.-").strip("._-") or "fitness-user"
    candidate = base[:70]
    suffix = 0
    while db.scalar(select(models.User).where(func.lower(models.User.username) == candidate.lower())):
        suffix += 1
        candidate = f"{base[:64]}-{suffix}"
    return candidate


def _token_response(user: models.User) -> TokenResponse:
    token = create_access_token(user.id, user.email)
    return TokenResponse(
        access_token=token,
        user_id=str(user.id),
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
    )


def _user_response(user: models.User) -> UserResponse:
    return UserResponse(
        user_id=str(user.id),
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


@auth_router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new user account and return a JWT token."""
    email = _normalize_email(str(payload.email))
    username = _normalize_username(payload.username) or _suggest_username(email, payload.display_name, db)
    existing = db.scalar(
        select(models.User).where(
            or_(models.User.email == email, func.lower(models.User.username) == username.lower())
        )
    )
    if existing:
        detail = (
            "An account with this email already exists."
            if existing.email == email
            else "An account with this username already exists."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    user = models.User(
        email=email,
        username=username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        avatar_url=payload.avatar_url,
    )
    db.add(user)
    db.flush()

    profile = models.UserProfile(user_id=user.id)
    db.add(profile)
    db.commit()
    db.refresh(user)

    return _token_response(user)


@auth_router.post("/login", response_model=TokenResponse)
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate with email or username and password, return a JWT token."""
    identifier = (payload.identifier or payload.email or "").strip().lower()
    if not identifier:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Email or username is required.",
        )
    user = db.scalar(
        select(models.User).where(
            or_(models.User.email == identifier, func.lower(models.User.username) == identifier)
        )
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password.",
        )

    return _token_response(user)


@auth_router.get("/me", response_model=UserResponse)
def get_me(request: Request, current_user: models.User = Depends(get_current_user)):
    """Return the currently authenticated user's account profile."""
    return _user_response(current_user)


@auth_router.patch("/me", response_model=UserResponse)
def update_me(
    request: Request,
    payload: AccountUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the current user's public account profile."""
    if payload.display_name is not None:
        current_user.display_name = payload.display_name.strip()
    if payload.avatar_url is not None:
        current_user.avatar_url = payload.avatar_url.strip() or None
    if payload.timezone is not None:
        current_user.timezone = payload.timezone.strip() or current_user.timezone
    if payload.username is not None:
        username = _normalize_username(payload.username)
        if username and username != current_user.username:
            existing = db.scalar(
                select(models.User).where(
                    models.User.id != current_user.id,
                    func.lower(models.User.username) == username.lower(),
                )
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this username already exists.",
                )
            current_user.username = username
    db.commit()
    db.refresh(current_user)
    return _user_response(current_user)
