"""Chem lab domain errors mapped to stable public error codes."""


class ChemLabError(ValueError):
    def __init__(self, code: str, message: str = "", status: int = 409):
        self.code, self.status = code, status
        super().__init__(message or code)
