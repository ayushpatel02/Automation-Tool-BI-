"""Generation engine: two-stage (TMDL -> PBIR) pipeline with validate-and-retry."""

from app.generation.pipeline import GenerationOutcome, run_generation

__all__ = ["run_generation", "GenerationOutcome"]
