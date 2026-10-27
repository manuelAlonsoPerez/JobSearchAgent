"""Easy Apply filling, submission, and recording."""

from .agent import apply_to_job
from .form import build_and_answer_form
from .record import record
from .submit import submit_application

__all__ = [
    "apply_to_job",
    "build_and_answer_form",
    "record",
    "submit_application",
]
