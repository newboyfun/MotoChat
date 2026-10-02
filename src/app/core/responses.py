"""
统一错误处理 — 所有 API 错误走 APIError 异常 + 全局处理器
返回格式统一为: {"success": False, "error": "...", "detail": "..."}
"""
from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    """API 层统一异常。端点里 raise APIError(401, "未登录") 即可。"""
    def __init__(self, code: int, message: str, detail: str = ""):
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)

    def to_response(self) -> JSONResponse:
        body = {"success": False, "error": self.message}
        if self.detail:
            body["detail"] = self.detail
        return JSONResponse(status_code=self.code, content=body)


def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return exc.to_response()


def register_error_handlers(app):
    """在 FastAPI app 上注册全局异常处理器"""
    app.add_exception_handler(APIError, api_error_handler)
