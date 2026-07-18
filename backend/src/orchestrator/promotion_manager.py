"""Typed internal transport for the isolated host-promotion authority."""

from __future__ import annotations

import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import Field

from orchestrator.commands import CommandError
from orchestrator.domain.primitives import StrictDomainModel


class PromotionApplication(Protocol):
    def create_preview(
        self,
        *,
        run_id: str,
        user_id: str,
        checkpoint_id: str | None,
        idempotency_key: str,
    ) -> dict[str, object]: ...

    def current(self, *, run_id: str, user_id: str) -> dict[str, object]: ...

    def list_promotions(
        self, *, run_id: str, user_id: str
    ) -> dict[str, object]: ...

    def confirm(
        self,
        *,
        run_id: str,
        user_id: str,
        preview_hash: str,
        confirm_preview_hash: str,
        confirmation_nonce: str,
        version: str,
        message: str,
        create_tag: bool,
        idempotency_key: str,
    ) -> dict[str, object]: ...


class PreviewCommand(StrictDomainModel):
    user_id: str = Field(pattern=r"^user_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    checkpoint_id: str | None = Field(
        default=None, pattern=r"^checkpoint_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
    )
    idempotency_key: str = Field(min_length=1, max_length=256)


class PromotionCommand(StrictDomainModel):
    user_id: str = Field(pattern=r"^user_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_nonce: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=512)
    create_tag: bool
    idempotency_key: str = Field(min_length=1, max_length=256)


def create_promotion_manager_app(
    service: PromotionApplication, *, auth_token: str
) -> FastAPI:
    if len(auth_token) < 24:
        raise ValueError("promotion manager token must contain at least 24 characters")
    app = FastAPI(title="Orchestrator Promotion Manager", version="v1")

    @app.exception_handler(CommandError)
    async def command_error(
        request: Request, error: CommandError
    ) -> JSONResponse:
        del request
        code = str(error)
        response_status = (
            status.HTTP_404_NOT_FOUND
            if code in {"run_not_found", "promotion_preview_not_found"}
            else status.HTTP_409_CONFLICT
        )
        return JSONResponse(status_code=response_status, content={"detail": code})

    def authorize(
        supplied: Annotated[
            str | None, Header(alias="X-Promotion-Manager-Token")
        ] = None,
    ) -> None:
        if supplied is None or not hmac.compare_digest(supplied, auth_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized")

    @app.get("/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.post(
        "/v1/runs/{run_id}/previews", dependencies=[Depends(authorize)]
    )
    def preview(run_id: str, command: PreviewCommand) -> dict[str, object]:
        return service.create_preview(
            run_id=run_id,
            user_id=command.user_id,
            checkpoint_id=command.checkpoint_id,
            idempotency_key=command.idempotency_key,
        )

    @app.get(
        "/v1/runs/{run_id}/previews/current", dependencies=[Depends(authorize)]
    )
    def current(run_id: str, user_id: str) -> dict[str, object]:
        return service.current(run_id=run_id, user_id=user_id)

    @app.get(
        "/v1/runs/{run_id}/promotions", dependencies=[Depends(authorize)]
    )
    def promotions(run_id: str, user_id: str) -> dict[str, object]:
        return service.list_promotions(run_id=run_id, user_id=user_id)

    @app.post(
        "/v1/runs/{run_id}/promotions", dependencies=[Depends(authorize)]
    )
    def promote(run_id: str, command: PromotionCommand) -> dict[str, object]:
        return service.confirm(
            run_id=run_id,
            user_id=command.user_id,
            preview_hash=command.preview_hash,
            confirm_preview_hash=command.confirm_preview_hash,
            confirmation_nonce=command.confirmation_nonce,
            version=command.version,
            message=command.message,
            create_tag=command.create_tag,
            idempotency_key=command.idempotency_key,
        )

    return app


class PromotionManagerHttpAdapter:
    """API-side client with no generic host mutation operation."""

    def __init__(
        self, base_url: str, auth_token: str, *, timeout_seconds: int = 60
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("promotion manager URL must be HTTP(S)")
        if len(auth_token) < 24 or not 5 <= timeout_seconds <= 300:
            raise ValueError("invalid promotion manager client configuration")
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._timeout_seconds = timeout_seconds

    def create_preview(
        self,
        *,
        run_id: str,
        user_id: str,
        checkpoint_id: str | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        return self._request(
            "POST",
            f"/v1/runs/{run_id}/previews",
            {
                "user_id": user_id,
                "checkpoint_id": checkpoint_id,
                "idempotency_key": idempotency_key,
            },
        )

    def current(self, *, run_id: str, user_id: str) -> dict[str, object]:
        query = urllib.parse.urlencode({"user_id": user_id})
        return self._request(
            "GET", f"/v1/runs/{run_id}/previews/current?{query}", None
        )

    def list_promotions(
        self, *, run_id: str, user_id: str
    ) -> dict[str, object]:
        query = urllib.parse.urlencode({"user_id": user_id})
        return self._request("GET", f"/v1/runs/{run_id}/promotions?{query}", None)

    def confirm(
        self,
        *,
        run_id: str,
        user_id: str,
        preview_hash: str,
        confirm_preview_hash: str,
        confirmation_nonce: str,
        version: str,
        message: str,
        create_tag: bool,
        idempotency_key: str,
    ) -> dict[str, object]:
        return self._request(
            "POST",
            f"/v1/runs/{run_id}/promotions",
            {
                "user_id": user_id,
                "preview_hash": preview_hash,
                "confirm_preview_hash": confirm_preview_hash,
                "confirmation_nonce": confirmation_nonce,
                "version": version,
                "message": message,
                "create_tag": create_tag,
                "idempotency_key": idempotency_key,
            },
        )

    def _request(
        self, method: str, path: str, payload: dict[str, object] | None
    ) -> dict[str, object]:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=(
                json.dumps(payload, separators=(",", ":")).encode()
                if payload is not None
                else None
            ),
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-Promotion-Manager-Token": self._auth_token,
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                raw = response.read(2_097_153)
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read(4_097))
                code = str(body.get("detail", "promotion_manager_request_failed"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                code = "promotion_manager_request_failed"
            raise CommandError(code) from error
        except (OSError, urllib.error.URLError) as error:
            raise CommandError("promotion_manager_unavailable") from error
        if len(raw) > 2_097_152:
            raise CommandError("promotion_manager_response_too_large")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CommandError("invalid_promotion_manager_response") from error
        if not isinstance(value, dict):
            raise CommandError("invalid_promotion_manager_response")
        return value
