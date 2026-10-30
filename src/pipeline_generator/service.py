from contextlib import suppress
from uuid import UUID

import psycopg

from .errors import PipelineError
from .generator import PySparkGenerator
from .issuance import IssuanceStore
from .models import GeneratedArtifact
from .storage import PostgresRepository


class GenerationService:
    def __init__(
        self,
        repository: PostgresRepository,
        generator: PySparkGenerator,
        *,
        expiration_seconds: int = 600,
    ):
        self.repository = repository
        self.generator = generator
        self.issuance = IssuanceStore(repository, expiration_seconds)

    def generate(self, spec_id: UUID, request_id: UUID) -> GeneratedArtifact:
        """Called by an already-authorized backend, never with a caller-supplied verdict."""
        reserved = False
        try:
            context = self.repository.load_verified_context(spec_id)
            cached = self.issuance.reserve(context, request_id)
            if cached is not None:
                return cached
            reserved = True
            # No connection or transaction is held across this call.
            code = self.generator.generate(context)
            return self.issuance.finish(context, request_id, code)
        except (PipelineError, psycopg.Error) as original:
            error = (
                original
                if isinstance(original, PipelineError)
                else PipelineError(
                    "STORAGE_ERROR",
                    "Не удалось прочитать или сохранить данные PostgreSQL.",
                    retryable=True,
                )
            )
            if reserved:
                # A failed acknowledgement may follow a committed transaction: never rewrite success.
                with suppress(psycopg.Error):
                    self.issuance.fail(request_id, error)
            raise error from None
