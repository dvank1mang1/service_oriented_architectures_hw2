from fastapi.responses import JSONResponse
from marketplace_api.models.error_response import ErrorResponse


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)

    def response(self):
        model = ErrorResponse(error_code=self.code, message=self.message, details=self.details)
        return JSONResponse(status_code=self.status, content=model.model_dump(mode="json", exclude_none=True))


def validation(field, message):
    return ApiError(
        400, "VALIDATION_ERROR", "invalid request", {"fields": [{"field": field, "message": message}]}
    )
